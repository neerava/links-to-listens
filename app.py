"""FastAPI web UI: lists all generated episodes with embedded audio players."""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel

from audio_api import audio_router
from config import load_settings
from metadata import MetadataStore
from pipeline_state import PipelineStateStore
from script_api import script_router
from watcher import URLS_FILE, enqueue_url

logger = logging.getLogger(__name__)

settings = load_settings()
store = MetadataStore()
# Own pipeline store for the web-app process — points to the same directory as
# the watcher's store so all runs (watcher-triggered and admin-triggered) are
# visible in the same output/pipeline/ tree.
pipeline_store = PipelineStateStore(
    pipeline_dir=settings.pipeline_path,
    retention_days=settings.intermediate_retention_days,
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Links to Listens", docs_url=None, redoc_url=None)

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
        {"episodes": episodes, "podbean_enabled": settings.podbean_enabled},
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
    """Re-process an episode's URL through the full pipeline.

    The old episode is kept in the metadata store until the new one is
    successfully created.  This is the cross-process guard: while the old
    record exists, store.is_processed(url) returns True so the watcher will
    skip the URL and not create a duplicate.
    """
    old = store.get_by_id(episode_id)
    if not old:
        raise HTTPException(status_code=404, detail="Episode not found")

    source_url = old.source_url
    old_audio_path = settings.output_path / old.audio_path

    from watcher import process_url

    def _regen() -> None:
        new_episode = process_url(source_url, settings, store, pipeline_store)
        if new_episode:
            # New episode is in the store — now safe to remove the old one.
            store.delete(episode_id)
            if old_audio_path.exists():
                old_audio_path.unlink()
            logger.info("Regeneration complete for %s", source_url)
        else:
            # Pipeline failed — old episode remains so the watcher continues
            # to skip it and the user still sees the previous version.
            logger.warning("Regeneration failed for %s — old episode retained", source_url)

    thread = threading.Thread(target=_regen, daemon=True)
    thread.start()

    return JSONResponse({
        "status": "regenerating",
        "source_url": source_url,
        "message": "Regeneration started. The episode will be updated when complete.",
    })


@app.post("/admin/api/episodes/{episode_id}/publish-podbean")
async def admin_publish_podbean(
    episode_id: str,
    title: str = Form(default=""),
    description: str = Form(default=""),
    logo: UploadFile | None = File(default=None),
) -> JSONResponse:
    """Publish an episode to Podbean (upload MP3 + create episode).

    Accepts multipart form with optional title, description, and logo image overrides.
    """
    if not settings.podbean_enabled:
        raise HTTPException(status_code=400, detail="Podbean is not configured")

    ep = store.get_by_id(episode_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    if ep.podbean_episode_id:
        raise HTTPException(status_code=409, detail="Episode already published to Podbean")

    audio_file = settings.output_path / ep.audio_path
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Use overrides from form, fall back to episode data
    pub_title = title.strip() or ep.title
    pub_description = description.strip() or ep.description or ep.title

    # Save uploaded logo to a temp file if provided
    logo_path: Path | None = None
    if logo and logo.filename:
        import tempfile
        suffix = Path(logo.filename).suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".gif"):
            raise HTTPException(status_code=400, detail="Logo must be JPG, PNG, or GIF")
        logo_data = await logo.read()
        if len(logo_data) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Logo must be under 2 MB")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(settings.output_path))
        tmp.write(logo_data)
        tmp.close()
        logo_path = Path(tmp.name)

    from podbean import PodbeanError, publish_episode

    def _publish() -> None:
        try:
            pod_id, pod_url = publish_episode(
                client_id=settings.podbean_client_id,
                client_secret=settings.podbean_client_secret,
                mp3_path=audio_file,
                title=pub_title,
                description=pub_description,
                logo_path=logo_path,
            )
            ep.podbean_episode_id = pod_id
            ep.podbean_episode_url = pod_url
            store.update(ep)
            logger.info("Published to Podbean: %s -> %s", ep.id, pod_url)
        except PodbeanError:
            logger.exception("Podbean publish failed for episode %s", ep.id)
        finally:
            if logo_path and logo_path.exists():
                logo_path.unlink(missing_ok=True)

    thread = threading.Thread(target=_publish, daemon=True)
    thread.start()

    return JSONResponse({
        "status": "publishing",
        "message": "Publishing to Podbean. The episode will be updated when complete.",
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
            resp = await client.get(url, headers={"User-Agent": "links-to-listens/1.0"})
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
