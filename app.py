"""FastAPI web UI: lists all generated episodes with embedded audio players."""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel

from audio_api import audio_router
from config import load_settings
from metadata import MetadataStore
from script_api import script_router
from watcher import URLS_FILE, enqueue_url

logger = logging.getLogger(__name__)

settings = load_settings()
store = MetadataStore()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="URL to Podcast", docs_url=None, redoc_url=None)

# Mount the two generation APIs under their own URI prefixes
app.include_router(script_router, prefix="/generate-script")
app.include_router(audio_router, prefix="/generate-audio")


class UrlSubmission(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    episodes = [ep for ep in store.load() if not ep.hidden]
    return templates.TemplateResponse(request, "index.html", {"episodes": episodes})


@app.get("/generate-script", response_class=HTMLResponse)
async def script_ui(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "script_ui.html", {"api_prefix": "/generate-script"})


@app.get("/generate-audio", response_class=HTMLResponse)
async def audio_ui(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "audio_ui.html", {"api_prefix": "/generate-audio"})


# ---------------------------------------------------------------------------
# Admin UI + API
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    episodes = store.load()
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"episodes": episodes},
    )


@app.post("/admin/api/episodes/{episode_id}/hide")
async def admin_hide(episode_id: str) -> JSONResponse:
    """Toggle hidden status for an episode."""
    ep = store.get_by_id(episode_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")
    ep.hidden = not ep.hidden
    store.update(ep)
    return JSONResponse({"id": ep.id, "hidden": ep.hidden})


@app.post("/admin/api/episodes/{episode_id}/delete")
async def admin_delete(episode_id: str) -> JSONResponse:
    """Permanently delete an episode and its audio file."""
    removed = store.delete(episode_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Episode not found")
    # Delete the MP3 file
    audio_file = settings.output_path / removed.audio_path
    if audio_file.exists():
        audio_file.unlink()
        logger.info("Deleted audio file: %s", audio_file)
    return JSONResponse({"id": removed.id, "deleted": True})


@app.post("/admin/api/episodes/{episode_id}/regenerate")
async def admin_regenerate(episode_id: str) -> JSONResponse:
    """Delete existing episode and re-process its URL through the full pipeline."""
    old = store.get_by_id(episode_id)
    if not old:
        raise HTTPException(status_code=404, detail="Episode not found")

    source_url = old.source_url

    # Delete old episode + audio
    store.delete(episode_id)
    audio_file = settings.output_path / old.audio_path
    if audio_file.exists():
        audio_file.unlink()

    # Run the pipeline in a background thread so we don't block the HTTP response
    from watcher import process_url
    def _regen() -> None:
        try:
            process_url(source_url, settings, store)
        except Exception:  # noqa: BLE001
            logger.exception("Regeneration failed for %s", source_url)

    thread = threading.Thread(target=_regen, daemon=True)
    thread.start()

    return JSONResponse({
        "status": "regenerating",
        "source_url": source_url,
        "message": "Episode deleted. Regeneration started in background.",
    })


@app.get("/audio/{filename}")
async def audio(filename: str) -> FileResponse:
    """Serve an MP3 file from the output directory."""
    # Prevent path traversal
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = settings.output_path / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(
        path=str(file_path),
        media_type="audio/mpeg",
        filename=safe_name,
    )


@app.get("/img")
async def image_proxy(url: str = Query(..., description="Remote image URL to proxy")) -> Response:
    """Proxy and cache remote thumbnail images to avoid CORS/mixed-content issues."""
    # Basic URL validation
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid image URL")

    # Cache to disk so we only fetch once
    cache_dir = settings.output_path / ".img_cache"
    cache_dir.mkdir(exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    # Preserve extension from URL for content-type hint
    ext = Path(parsed.path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        ext = ".jpg"
    cache_path = cache_dir / f"{url_hash}{ext}"

    if cache_path.exists():
        media_type = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp", ".svg": "image/svg+xml",
        }.get(ext, "image/jpeg")
        return Response(content=cache_path.read_bytes(), media_type=media_type)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "url-to-podcast/1.0"})
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch image")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Failed to fetch image")

    content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    cache_path.write_bytes(resp.content)

    return Response(content=resp.content, media_type=content_type)


@app.post("/api/urls")
async def submit_url(body: UrlSubmission) -> JSONResponse:
    url = body.url.strip()
    if store.is_processed(url):
        return JSONResponse({
            "status": "already_processed",
            "url": url,
            "message": "That URL has already been processed.",
        })

    try:
        added = enqueue_url(URLS_FILE, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not added:
        return JSONResponse({
            "status": "already_queued",
            "url": url,
            "message": "That URL is already waiting in urls.txt.",
        })

    return JSONResponse({
        "status": "queued",
        "url": url,
        "message": "URL queued for processing. The watcher will pick it up shortly.",
    })


@app.get("/health")
async def health() -> dict:
    episode_count = len(store.load())
    return {"status": "ok", "episodes": episode_count}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=settings.web_port,
        reload=False,
        log_level="info",
    )
