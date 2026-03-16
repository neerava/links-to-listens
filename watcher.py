"""URL watcher and pipeline orchestrator.

Polls urls.txt on a configurable interval, processes each new URL through
the full pipeline (scrape → summarize → TTS → store), and recovers
gracefully from per-URL failures without stopping.
"""
from __future__ import annotations

import logging
import re
import signal
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from audio_api import generate_audio
from config import Settings, load_settings
from metadata import MetadataStore
from models import Episode
from script_api import generate_script
from scraper import ScraperError
from tts import TTSError

logger = logging.getLogger(__name__)

URLS_FILE = Path(__file__).parent / "urls.txt"

# URLs that failed during this process's lifetime — prevents infinite retry
# loops when a URL consistently fails (e.g. TTS not installed).
_failed_urls: set[str] = set()


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


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "episode"


def _build_audio_filename(title: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slugify(title)
    return f"{slug}-{ts}.mp3"


# ---------------------------------------------------------------------------
# Per-URL pipeline
# ---------------------------------------------------------------------------

def process_url(url: str, settings: Settings, store: MetadataStore) -> Episode | None:
    """Run the full pipeline for a single URL and persist the result.

    Returns the created Episode, or None if any step failed.
    Public so the admin API can call it for regeneration.
    """
    logger.info("Processing: %s", url)

    # 1. Scrape + summarize → podcast script
    try:
        script_result = generate_script(url, settings)
    except ScraperError as exc:
        logger.error("Scrape failed for %s: %s", url, exc)
        _failed_urls.add(url)
        return None
    except Exception as exc:  # SummarizerError or unexpected
        logger.error("Script generation failed for %s: %s", url, exc)
        _failed_urls.add(url)
        return None

    title = script_result.title
    description = script_result.description
    thumbnail_url = script_result.thumbnail_url
    logger.info("Article metadata: %r", title)

    # 2. Build output path
    filename = _build_audio_filename(title)
    output_path = settings.output_path / filename

    # 3. TTS → MP3
    try:
        generate_audio(script_result.script, output_path, settings)
    except TTSError as exc:
        logger.error("TTS failed for %s: %s", url, exc)
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
    logger.info("✓ Episode ready: %r ← %s", title, url)
    return episode


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def _run_once(settings: Settings, store: MetadataStore) -> None:
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
            process_url(url, settings, store)
        except Exception as exc:  # noqa: BLE001
            # Belt-and-suspenders: catch anything not already caught inside
            logger.exception("Unexpected error processing %s: %s", url, exc)


def run(settings: Settings | None = None) -> None:
    """Start the watcher loop.  Runs until SIGINT or SIGTERM."""
    if settings is None:
        settings = load_settings()

    store = MetadataStore()
    logger.info(
        "Watcher started — polling %s every %ds",
        URLS_FILE,
        settings.poll_interval_sec,
    )

    # Graceful shutdown
    _shutdown = {"requested": False}

    def _handle_signal(signum: int, _frame: object) -> None:  # noqa: ANN001
        logger.info("Shutdown signal received (%s) — stopping after current poll", signum)
        _shutdown["requested"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not _shutdown["requested"]:
        _run_once(settings, store)
        # Sleep in small increments so we react to shutdown signals promptly
        slept = 0.0
        while slept < settings.poll_interval_sec and not _shutdown["requested"]:
            time.sleep(0.5)
            slept += 0.5

    logger.info("Watcher stopped.")


if __name__ == "__main__":
    run()
