"""URL watcher and pipeline orchestrator.

Polls urls.txt on a configurable interval, processes each new URL through
the full pipeline (scrape → summarize → TTS → store), and recovers
gracefully from per-URL failures without stopping.
"""
from __future__ import annotations

import logging
import re
import signal
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from audio_api import generate_audio
from config import Settings, load_settings
from metadata import MetadataStore
from models import Episode
from pipeline_state import PipelineStateStore, Stage
from script_api import generate_script
from scraper import ScraperError
from summarizer import extract_metadata, summarize
from tts import TTSError

logger = logging.getLogger(__name__)

URLS_FILE = Path(__file__).parent / "urls.txt"

# URLs that failed during this process's lifetime — prevents infinite retry
# loops when a URL consistently fails (e.g. TTS not installed).
_failed_urls: set[str] = set()
_urls_file_lock = threading.Lock()



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_urls(path: Path) -> list[str]:
    """Read non-empty, non-comment lines from *path*."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def enqueue_url(path: Path, url: str) -> bool:
    """Append *url* to *path* unless it is already queued.

    Returns ``True`` when the URL is added and ``False`` when it was already
    present. Raises ``ValueError`` for invalid URLs.
    """
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must be a valid HTTP or HTTPS address")

    with _urls_file_lock:
        existing = set(_read_urls(path))
        if normalized in existing:
            return False

        path.parent.mkdir(parents=True, exist_ok=True)
        should_prefix_newline = path.exists() and path.stat().st_size > 0
        with open(path, "a", encoding="utf-8") as f:
            if should_prefix_newline:
                f.write("\n")
            f.write(normalized)
        return True


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "episode"


def _build_audio_filename(title: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slugify(title)
    return f"{slug}-{ts}.mp3"


# ---------------------------------------------------------------------------
# Per-URL pipeline
# ---------------------------------------------------------------------------

def process_url(
    url: str,
    settings: Settings,
    store: MetadataStore,
    pipeline_store: PipelineStateStore | None = None,
) -> Episode | None:
    """Run the full pipeline for a single URL and persist the result.

    Returns the created Episode, or None if any step failed.
    Public so the admin API can call it for regeneration.
    """
    logger.info("Processing: %s", url)

    run = pipeline_store.create(url) if pipeline_store else None

    # 1. Scrape + summarize → podcast script
    if run:
        pipeline_store.transition(run, Stage.SCRIPT)
    try:
        script_result = generate_script(url, settings)
    except ScraperError as exc:
        logger.error("Scrape failed for %s: %s", url, exc)
        if run:
            pipeline_store.transition(run, Stage.FAILED, error=str(exc))
        _failed_urls.add(url)
        return None
    except Exception as exc:  # SummarizerError or unexpected
        logger.error("Script generation failed for %s: %s", url, exc)
        if run:
            pipeline_store.transition(run, Stage.FAILED, error=str(exc))
        _failed_urls.add(url)
        return None

    # Save intermediates: scraped text, Ollama prompt, and generated script
    if run:
        pipeline_store.save_input_text(run, script_result.input_text)
        pipeline_store.save_prompt(run, script_result.ollama_prompt)
        pipeline_store.save_script(run, script_result.script)
        # Save article metadata into the run for TTS-only retries
        pipeline_store.transition(run, run.stage,
            title=script_result.title,
            description=script_result.description,
            thumbnail_url=script_result.thumbnail_url)

    title = script_result.title
    description = script_result.description
    thumbnail_url = script_result.thumbnail_url
    logger.info("Article metadata: %r", title)

    # 2. Build output path
    filename = _build_audio_filename(title)
    output_path = settings.output_path / filename

    # 3. TTS → MP3
    if run:
        pipeline_store.transition(run, Stage.TTS)
    tts_input_path = pipeline_store.tts_input_path(run) if run else None
    try:
        generate_audio(script_result.script, output_path, settings,
                       tts_input_path=tts_input_path)
    except TTSError as exc:
        logger.error("TTS failed for %s: %s", url, exc)
        if run:
            pipeline_store.transition(run, Stage.FAILED, error=str(exc))
        _failed_urls.add(url)
        return None

    # 4. Store metadata
    episode = Episode(
        id=str(uuid.uuid4()),
        title=title,
        description=description,
        source_url=url,
        timestamp=datetime.now(timezone.utc).isoformat(),
        audio_path=filename,
        thumbnail_url=thumbnail_url,
    )
    store.append(episode)
    if run:
        pipeline_store.transition(run, Stage.DONE, audio_path=str(output_path))
    logger.info("✓ Episode ready: %r ← %s", title, url)
    return episode


def resume_pipeline(
    run_id: str,
    from_stage: Stage,
    settings: Settings,
    store: MetadataStore,
    pipeline_store: PipelineStateStore,
) -> Episode | None:
    """Resume a failed pipeline run from the given stage.

    If from_stage is SCRIPT: re-runs scrape+summarize+TTS (full pipeline, reuses run record).
    If from_stage is TTS: reads existing script.txt + saved metadata, runs TTS only.

    Returns the created Episode, or None if any step failed.
    """
    run = pipeline_store.load_run(run_id)
    if not run:
        logger.error("Resume: run %s not found", run_id[:8])
        return None
    if run.stage != Stage.FAILED:
        logger.error("Resume: run %s is not in FAILED state (stage=%s)", run_id[:8], run.stage.value)
        return None

    url = run.url
    logger.info("Resuming run %s for %s from stage %s", run_id[:8], url, from_stage.value)

    # Clear error state
    pipeline_store.transition(run, from_stage, error="", failed_at_stage="")

    if from_stage == Stage.SCRIPT:
        # Re-run full pipeline from scrape+summarize
        try:
            script_result = generate_script(url, settings)
        except Exception as exc:
            logger.error("Resume script failed for %s: %s", url, exc)
            pipeline_store.transition(run, Stage.FAILED, error=str(exc))
            return None

        pipeline_store.save_input_text(run, script_result.input_text)
        pipeline_store.save_prompt(run, script_result.ollama_prompt)
        pipeline_store.save_script(run, script_result.script)
        pipeline_store.transition(run, Stage.SCRIPT,
            title=script_result.title,
            description=script_result.description,
            thumbnail_url=script_result.thumbnail_url)

        title = script_result.title
        description = script_result.description
        thumbnail_url = script_result.thumbnail_url
        script_text = script_result.script

    elif from_stage == Stage.TTS:
        # TTS-only: read existing script and metadata from the run
        script_file = Path(run.script_path) if run.script_path else None
        if not script_file or not script_file.exists():
            err = "Cannot resume from TTS: script.txt has been pruned. Retry from SCRIPT instead."
            logger.error(err)
            pipeline_store.transition(run, Stage.FAILED, error=err)
            return None

        script_text = script_file.read_text(encoding="utf-8")
        title = run.title
        description = run.description
        thumbnail_url = run.thumbnail_url

        if not title:
            title = url  # fallback
    else:
        logger.error("Resume: unsupported from_stage %s", from_stage.value)
        return None

    # TTS → MP3
    filename = _build_audio_filename(title)
    output_path = settings.output_path / filename
    pipeline_store.transition(run, Stage.TTS)
    tts_input_path = pipeline_store.tts_input_path(run)
    try:
        generate_audio(script_text, output_path, settings, tts_input_path=tts_input_path)
    except TTSError as exc:
        logger.error("Resume TTS failed for %s: %s", url, exc)
        pipeline_store.transition(run, Stage.FAILED, error=str(exc))
        return None

    # Store metadata
    episode = Episode(
        id=str(uuid.uuid4()),
        title=title,
        description=description,
        source_url=url,
        timestamp=datetime.now(timezone.utc).isoformat(),
        audio_path=filename,
        thumbnail_url=thumbnail_url,
    )
    store.append(episode)
    pipeline_store.transition(run, Stage.DONE, audio_path=str(output_path))
    logger.info("Resume complete: %r <- %s", title, url)
    return episode


def restart_pipeline(
    run_id: str,
    from_stage: Stage,
    settings: Settings,
    store: MetadataStore,
    pipeline_store: PipelineStateStore,
    input_text: str = "",
    script_text: str = "",
    title: str = "",
    description: str = "",
    thumbnail_url: str = "",
) -> Episode | None:
    """Restart a pipeline run from any stage with optional custom inputs.

    Unlike resume_pipeline(), works on ANY run status (not just FAILED).
    Custom text overrides skip the corresponding pipeline step:
      - input_text provided → skip scraping, use this text for summarization
      - script_text provided → skip scraping+summarization, use this script for TTS
    """
    run = pipeline_store.load_run(run_id)
    if not run:
        logger.error("Restart: run %s not found", run_id[:8])
        return None

    if run.stage in (Stage.PENDING, Stage.SCRIPT, Stage.TTS):
        logger.error("Restart: run %s is currently active (stage=%s)", run_id[:8], run.stage.value)
        return None

    url = run.url
    logger.info("Restarting run %s for %s from stage %s", run_id[:8], url, from_stage.value)

    # Clear error state
    pipeline_store.transition(run, from_stage, error="", failed_at_stage="")

    if from_stage == Stage.SCRIPT:
        if input_text:
            # Custom scraped text — skip scraping, run summarize directly
            pipeline_store.save_input_text(run, input_text)
            try:
                meta = extract_metadata(input_text, settings)
                prompt_text = f"{settings.ollama_prompt}\n\n---\n\n{input_text}"
                pipeline_store.save_prompt(run, prompt_text)
                script = summarize(input_text, settings)
            except Exception as exc:
                logger.error("Restart script (custom text) failed for %s: %s", url, exc)
                pipeline_store.transition(run, Stage.FAILED, error=str(exc))
                return None

            pipeline_store.save_script(run, script)
            final_title = title or meta.title
            final_desc = description or meta.description
            final_thumb = thumbnail_url or run.thumbnail_url
            pipeline_store.transition(run, Stage.SCRIPT,
                title=final_title, description=final_desc, thumbnail_url=final_thumb)
            script_text_for_tts = script
        else:
            # No custom text — full scrape + summarize
            try:
                script_result = generate_script(url, settings)
            except Exception as exc:
                logger.error("Restart script failed for %s: %s", url, exc)
                pipeline_store.transition(run, Stage.FAILED, error=str(exc))
                return None

            pipeline_store.save_input_text(run, script_result.input_text)
            pipeline_store.save_prompt(run, script_result.ollama_prompt)
            pipeline_store.save_script(run, script_result.script)
            final_title = title or script_result.title
            final_desc = description or script_result.description
            final_thumb = thumbnail_url or script_result.thumbnail_url
            pipeline_store.transition(run, Stage.SCRIPT,
                title=final_title, description=final_desc, thumbnail_url=final_thumb)
            script_text_for_tts = script_result.script

    elif from_stage == Stage.TTS:
        if script_text:
            pipeline_store.save_script(run, script_text)
            script_text_for_tts = script_text
        else:
            script_file = Path(run.script_path) if run.script_path else None
            if not script_file or not script_file.exists():
                err = "Cannot restart from TTS: script.txt has been pruned. Restart from SCRIPT instead."
                logger.error(err)
                pipeline_store.transition(run, Stage.FAILED, error=err)
                return None
            script_text_for_tts = script_file.read_text(encoding="utf-8")

        final_title = title or run.title
        final_desc = description or run.description
        final_thumb = thumbnail_url or run.thumbnail_url

        if not final_title:
            final_title = url
    else:
        logger.error("Restart: unsupported from_stage %s", from_stage.value)
        return None

    # TTS → MP3
    filename = _build_audio_filename(final_title)
    output_path = settings.output_path / filename
    pipeline_store.transition(run, Stage.TTS)
    tts_input_path = pipeline_store.tts_input_path(run)
    try:
        generate_audio(script_text_for_tts, output_path, settings, tts_input_path=tts_input_path)
    except TTSError as exc:
        logger.error("Restart TTS failed for %s: %s", url, exc)
        pipeline_store.transition(run, Stage.FAILED, error=str(exc))
        return None

    # Store metadata
    episode = Episode(
        id=str(uuid.uuid4()),
        title=final_title,
        description=final_desc,
        source_url=url,
        timestamp=datetime.now(timezone.utc).isoformat(),
        audio_path=filename,
        thumbnail_url=final_thumb,
    )
    store.append(episode)
    pipeline_store.transition(run, Stage.DONE, audio_path=str(output_path))
    logger.info("Restart complete: %r <- %s", final_title, url)
    return episode


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def _run_once(
    settings: Settings,
    store: MetadataStore,
    pipeline_store: PipelineStateStore | None = None,
) -> None:
    """Single poll cycle: check urls.txt and process any new URLs."""
    urls = _read_urls(URLS_FILE)
    new_urls = [
        u for u in urls
        if not store.is_processed(u) and u not in _failed_urls
    ]

    if not new_urls:
        return

    logger.info("Found %d new URL(s) to process", len(new_urls))
    for url in new_urls:
        try:
            process_url(url, settings, store, pipeline_store)
        except Exception as exc:  # noqa: BLE001
            # Belt-and-suspenders: catch anything not already caught inside
            logger.exception("Unexpected error processing %s: %s", url, exc)


def run(settings: Settings | None = None) -> None:
    """Start the watcher loop.  Runs until SIGINT or SIGTERM."""
    if settings is None:
        settings = load_settings()

    store = MetadataStore()
    pipeline_store = PipelineStateStore(
        pipeline_dir=settings.pipeline_path,
        retention_days=settings.intermediate_retention_days,
    )
    logger.info(
        "Watcher started — polling %s every %ds (pipeline state: %s)",
        URLS_FILE,
        settings.poll_interval_sec,
        settings.pipeline_path,
    )

    # Prune stale intermediates at startup then once per day
    pipeline_store.prune_intermediates()
    last_prune = datetime.now(timezone.utc)

    # Graceful shutdown
    _shutdown = {"requested": False}

    def _handle_signal(signum: int, _frame: object) -> None:  # noqa: ANN001
        logger.info("Shutdown signal received (%s) — stopping after current poll", signum)
        _shutdown["requested"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not _shutdown["requested"]:
        _run_once(settings, store, pipeline_store)

        # Daily prune pass
        now = datetime.now(timezone.utc)
        if (now - last_prune).total_seconds() >= 86400:
            pipeline_store.prune_intermediates()
            last_prune = now

        # Sleep in small increments so we react to shutdown signals promptly
        slept = 0.0
        while slept < settings.poll_interval_sec and not _shutdown["requested"]:
            time.sleep(0.5)
            slept += 0.5

    logger.info("Watcher stopped.")


if __name__ == "__main__":
    run()
