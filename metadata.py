"""Metadata store: persists episode records to a flat JSON file."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from models import Episode

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).parent / "metadata.json"


class MetadataStore:
    """Thread-safe, append-only JSON store for Episode records.

    The store writes atomically (write → rename) to prevent corruption
    on unexpected process termination.
    """

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, episode: Episode) -> None:
        """Persist *episode* to the store."""
        with self._lock:
            episodes = self._read()
            episodes.append(episode)
            self._write(episodes)
        logger.debug("Episode stored: id=%s url=%s", episode.id, episode.source_url)

    def load(self) -> list[Episode]:
        """Return all stored episodes, oldest first."""
        with self._lock:
            return self._read()

    def is_processed(self, url: str) -> bool:
        """Return True if *url* already has a corresponding episode."""
        with self._lock:
            episodes = self._read()
        return any(ep.source_url == url for ep in episodes)

    def get_by_id(self, episode_id: str) -> Episode | None:
        """Return a single episode by ID, or None."""
        with self._lock:
            for ep in self._read():
                if ep.id == episode_id:
                    return ep
        return None

    def update(self, episode: Episode) -> bool:
        """Replace the episode with matching ID. Returns True if found."""
        with self._lock:
            episodes = self._read()
            for i, ep in enumerate(episodes):
                if ep.id == episode.id:
                    episodes[i] = episode
                    self._write(episodes)
                    logger.debug("Episode updated: id=%s", episode.id)
                    return True
        return False

    def delete(self, episode_id: str) -> Episode | None:
        """Remove and return the episode with *episode_id*, or None if not found."""
        with self._lock:
            episodes = self._read()
            for i, ep in enumerate(episodes):
                if ep.id == episode_id:
                    removed = episodes.pop(i)
                    self._write(episodes)
                    logger.debug("Episode deleted: id=%s", episode_id)
                    return removed
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> list[Episode]:
        if not self._path.exists():
            return []
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                logger.warning("metadata.json is not a list — resetting")
                return []
            return [Episode.from_dict(item) for item in raw]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Failed to parse metadata.json: %s — treating as empty", exc)
            return []

    def _write(self, episodes: list[Episode]) -> None:
        tmp_path = self._path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                [ep.to_dict() for ep in episodes],
                f,
                indent=2,
                ensure_ascii=False,
            )
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(self._path)
        logger.debug("metadata.json written (%d episodes)", len(episodes))
