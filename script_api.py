"""Script generation API: converts a URL to a podcast script.

Can be used in two ways:
  1. Direct import  — call ``generate_script(url, settings)`` from Python code.
  2. HTTP API       — via ``script_router`` (mounted by app.py at /generate-script)
                      or standalone: run this module directly (port 8081 by default).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import Settings, load_settings
from job_queue import JobQueue, JobStatus
from scraper import ScraperError, scrape
from summarizer import SummarizerError, extract_metadata, summarize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Service layer (importable by other modules)
# ---------------------------------------------------------------------------


@dataclass
class ScriptResult:
    title: str
    description: str
    thumbnail_url: str
    script: str
    input_text: str = ""       # scraped article text fed to the LLM
    ollama_prompt: str = ""    # full prompt sent to Ollama for script generation


def generate_script(url: str, settings: Settings | None = None) -> ScriptResult:
    """Scrape *url* and produce a podcast script.

    Raises:
        ScraperError: if the URL cannot be fetched or parsed.
        SummarizerError: if the LLM call fails.
    """
    if settings is None:
        settings = load_settings()

    scrape_result = scrape(url, settings)
    meta = extract_metadata(scrape_result.text, settings)
    input_text = scrape_result.text
    ollama_prompt = f"{settings.ollama_prompt}\n\n---\n\n{input_text}"
    script = summarize(input_text, settings)

    return ScriptResult(
        title=meta.title,
        description=meta.description,
        thumbnail_url=scrape_result.thumbnail_url or "",
        script=script,
        input_text=input_text,
        ollama_prompt=ollama_prompt,
    )


# ---------------------------------------------------------------------------
# Job queue (single worker — at most one generate_script runs at a time)
# ---------------------------------------------------------------------------

_settings = load_settings()


def _script_worker(url: str) -> dict:
    result = generate_script(url, _settings)
    return {
        "title": result.title,
        "description": result.description,
        "thumbnail_url": result.thumbnail_url,
        "script": result.script,
    }


_queue = JobQueue(_script_worker)

# ---------------------------------------------------------------------------
# Router — mounted by app.py at /generate-script, or included standalone at /
# ---------------------------------------------------------------------------


class ScriptRequest(BaseModel):
    url: str


script_router = APIRouter()


@script_router.post("/submit")
async def submit_script(body: ScriptRequest) -> JSONResponse:
    """Enqueue a script generation job.  Returns ``{"job_id": "..."}`` immediately."""
    job_id = _queue.submit(url=body.url)
    return JSONResponse({"job_id": job_id})


@script_router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    """Poll job status.  Includes ``queue_position`` when status is *pending*."""
    job = _queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    d = job.to_dict()
    if job.status == JobStatus.PENDING:
        d["queue_position"] = _queue.queue_position(job_id)
    return JSONResponse(d)


# ---------------------------------------------------------------------------
# Standalone app (python script_api.py)
# ---------------------------------------------------------------------------

app = FastAPI(title="Script Generation API", docs_url=None, redoc_url=None)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def standalone_ui(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "script_ui.html", {"api_prefix": ""})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(script_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "script_api:app",
        host="0.0.0.0",
        port=_settings.script_api_port,
        reload=False,
        log_level="info",
    )
