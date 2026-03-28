"""Scrape API: fetches a URL and returns extracted article text + thumbnail.

Can be used in two ways:
  1. Direct import  — call ``scrape(url, settings)`` from ``scraper.py``.
  2. HTTP API       — via ``scrape_router`` (mounted by app.py at /scrape).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import load_settings
from job_queue import JobQueue, JobStatus
from scraper import ScraperError, scrape

logger = logging.getLogger(__name__)

_settings = load_settings()


def _scrape_worker(url: str) -> dict:
    result = scrape(url, _settings)
    return {
        "url": url,
        "text": result.text,
        "thumbnail_url": result.thumbnail_url,
        "char_count": len(result.text),
    }


_queue = JobQueue(_scrape_worker)


class ScrapeRequest(BaseModel):
    url: str


scrape_router = APIRouter()


@scrape_router.post("/submit")
async def submit_scrape(body: ScrapeRequest) -> JSONResponse:
    """Enqueue a scrape job. Returns ``{"job_id": "..."}`` immediately."""
    job_id = _queue.submit(url=body.url)
    return JSONResponse({"job_id": job_id})


@scrape_router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    """Poll job status. Includes ``queue_position`` when status is *pending*."""
    job = _queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    d = job.to_dict()
    if job.status == JobStatus.PENDING:
        d["queue_position"] = _queue.queue_position(job_id)
    return JSONResponse(d)
