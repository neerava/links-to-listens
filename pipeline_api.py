"""Pipeline API: list, retry, restart, and delete pipeline runs.

Mounted by app.py at /pipeline/api.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import load_settings
from metadata import MetadataStore
from pipeline_state import PipelineStateStore, Stage

logger = logging.getLogger(__name__)

_settings = load_settings()
_pipeline_store = PipelineStateStore(
    pipeline_dir=_settings.pipeline_path,
    retention_days=_settings.intermediate_retention_days,
)
_metadata_store = MetadataStore()

pipeline_router = APIRouter()


@pipeline_router.get("/runs")
async def list_runs() -> JSONResponse:
    """Return all pipeline runs as a JSON array, most recent first."""
    runs = _pipeline_store.load_all_runs()
    return JSONResponse([r.to_dict() for r in runs])


@pipeline_router.get("/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    """Return a single run with intermediate file contents."""
    run = _pipeline_store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    d = run.to_dict()

    # Read file contents if they exist on disk
    for field, content_key in [
        ("input_text_path", "input_text_content"),
        ("prompt_path", "prompt_content"),
        ("script_path", "script_content"),
        ("tts_input_path", "tts_input_content"),
    ]:
        path_str = getattr(run, field, "")
        if path_str and Path(path_str).exists():
            try:
                d[content_key] = Path(path_str).read_text(encoding="utf-8")
            except Exception:
                d[content_key] = None
        else:
            d[content_key] = None

    return JSONResponse(d)


class RetryRequest(BaseModel):
    from_stage: str = ""  # optional override: "script" or "tts"


@pipeline_router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, body: RetryRequest | None = None) -> JSONResponse:
    """Retry a failed run from the stage where it failed (or a specified stage)."""
    run = _pipeline_store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.stage != Stage.FAILED:
        raise HTTPException(status_code=400, detail=f"Run is not in FAILED state (stage={run.stage.value})")

    if body and body.from_stage:
        try:
            from_stage = Stage(body.from_stage)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid stage: {body.from_stage}")
    elif run.failed_at_stage:
        from_stage = Stage(run.failed_at_stage)
    else:
        from_stage = Stage.SCRIPT

    from watcher import resume_pipeline

    def _retry() -> None:
        resume_pipeline(run_id, from_stage, _settings, _metadata_store, _pipeline_store)

    thread = threading.Thread(target=_retry, daemon=True)
    thread.start()

    return JSONResponse({
        "status": "retrying",
        "run_id": run_id,
        "from_stage": from_stage.value,
    })


class RestartRequest(BaseModel):
    from_stage: str           # "script" or "tts"
    input_text: str = ""      # custom scraped text (skip scraping)
    script_text: str = ""     # custom script (skip summarization)
    title: str = ""           # metadata override
    description: str = ""
    thumbnail_url: str = ""


@pipeline_router.post("/runs/{run_id}/restart")
async def restart_run(run_id: str, body: RestartRequest) -> JSONResponse:
    """Restart a run from any stage with optional custom inputs. Works on any status."""
    run = _pipeline_store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.stage in (Stage.PENDING, Stage.SCRIPT, Stage.TTS):
        raise HTTPException(status_code=409, detail=f"Run is currently active (stage={run.stage.value})")

    try:
        from_stage = Stage(body.from_stage)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid stage: {body.from_stage}")

    if from_stage not in (Stage.SCRIPT, Stage.TTS):
        raise HTTPException(status_code=400, detail="from_stage must be 'script' or 'tts'")

    from watcher import restart_pipeline

    def _restart() -> None:
        restart_pipeline(
            run_id, from_stage, _settings, _metadata_store, _pipeline_store,
            input_text=body.input_text,
            script_text=body.script_text,
            title=body.title,
            description=body.description,
            thumbnail_url=body.thumbnail_url,
        )

    thread = threading.Thread(target=_restart, daemon=True)
    thread.start()

    return JSONResponse({
        "status": "restarting",
        "run_id": run_id,
        "from_stage": from_stage.value,
    })


@pipeline_router.post("/runs/{run_id}/delete")
async def delete_run(run_id: str) -> JSONResponse:
    """Delete a pipeline run and all its files."""
    deleted = _pipeline_store.delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")
    return JSONResponse({"run_id": run_id, "deleted": True})
