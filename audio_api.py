"""Audio generation API: converts a podcast script to an MP3 file.

Can be used in two ways:
  1. Direct import  — call ``generate_audio(script, output_path, settings)`` from Python code.
  2. HTTP API       — via ``audio_router`` (mounted by app.py at /generate-audio)
                      or standalone: run this module directly (port 8082 by default).
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import Settings, load_settings
from job_queue import JobQueue, JobStatus
from tts import TTSError, synthesize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Service layer (importable by other modules)
# ---------------------------------------------------------------------------


def generate_audio(script: str, output_path: Path, settings: Settings | None = None) -> Path:
    """Synthesize *script* to an MP3 file at *output_path*.

    Returns the path to the generated MP3.

    Raises:
        TTSError: if audio synthesis fails.
    """
    if settings is None:
        settings = load_settings()

    return synthesize(script, output_path, settings)


# ---------------------------------------------------------------------------
# Job queue (single worker — at most one generate_audio runs at a time)
# ---------------------------------------------------------------------------

_settings = load_settings()

# Audio files from API jobs are stored under output/api_audio/
_API_AUDIO_DIR = _settings.output_path / "api_audio"
_API_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _audio_worker(script: str, title: str) -> dict:
    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = _API_AUDIO_DIR / filename
    generate_audio(script, output_path, _settings)
    return {"filename": filename, "title": title}


_queue = JobQueue(_audio_worker)

# ---------------------------------------------------------------------------
# Router — mounted by app.py at /generate-audio, or included standalone at /
# ---------------------------------------------------------------------------


class AudioRequest(BaseModel):
    script: str
    title: str = "podcast"


audio_router = APIRouter()


@audio_router.post("/submit")
async def submit_audio(body: AudioRequest) -> JSONResponse:
    """Enqueue an audio generation job.  Returns ``{"job_id": "..."}`` immediately."""
    job_id = _queue.submit(script=body.script, title=body.title)
    return JSONResponse({"job_id": job_id})


@audio_router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    """Poll job status.  Includes ``queue_position`` when status is *pending*."""
    job = _queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    d = job.to_dict()
    if job.status == JobStatus.PENDING:
        d["queue_position"] = _queue.queue_position(job_id)
    if job.status == JobStatus.DONE and job.result:
        audio_path = _API_AUDIO_DIR / job.result["filename"]
        d["file_available"] = audio_path.exists()
    return JSONResponse(d)


@audio_router.get("/jobs/{job_id}/download")
async def download_audio(job_id: str) -> FileResponse:
    """Download the generated MP3 for a completed job."""
    job = _queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.DONE or not job.result:
        raise HTTPException(status_code=409, detail=f"Job is not done (status: {job.status})")

    audio_path = _API_AUDIO_DIR / job.result["filename"]
    if not audio_path.exists():
        raise HTTPException(status_code=410, detail="Audio file no longer available")

    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "_"
        for c in job.result.get("title", "podcast")
    ).strip() or "podcast"

    return FileResponse(
        path=str(audio_path),
        media_type="audio/mpeg",
        filename=f"{safe_title}.mp3",
    )


# ---------------------------------------------------------------------------
# Standalone app (python audio_api.py)
# ---------------------------------------------------------------------------

app = FastAPI(title="Audio Generation API", docs_url=None, redoc_url=None)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def standalone_ui(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "audio_ui.html", {"api_prefix": ""})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(audio_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "audio_api:app",
        host="0.0.0.0",
        port=_settings.audio_api_port,
        reload=False,
        log_level="info",
    )
