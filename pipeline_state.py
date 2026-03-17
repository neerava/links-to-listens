"""Lightweight pipeline state machine for the URL-to-podcast watcher.

Each URL processed by the watcher gets its own run directory under
``output/pipeline/{run-id}/`` containing:

  state.json      — current stage, timestamps, file paths, error text
  script.txt      — raw Ollama-generated script  (pruned after retention_days)
  tts_input.txt   — VibeVoice-formatted script with Speaker labels
                    (pruned after retention_days)

The final MP3 lives in the normal ``output/`` tree and is never pruned here.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------

class Stage(str, Enum):
    PENDING   = "pending"    # run created, not yet started
    SCRIPT    = "script"     # scraping URL + summarising with Ollama
    TTS       = "tts"        # converting script to audio with VibeVoice
    DONE      = "done"       # MP3 produced successfully
    FAILED    = "failed"     # unrecoverable error


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------

@dataclass
class PipelineRun:
    id: str
    url: str
    stage: Stage
    created_at: str          # ISO-8601 UTC
    updated_at: str          # ISO-8601 UTC
    run_dir: str    = ""     # absolute path to output/pipeline/{run-id}/
    script_path: str = ""    # output/pipeline/{run-id}/script.txt (empty if pruned)
    tts_input_path: str = "" # output/pipeline/{run-id}/tts_input.txt (empty if pruned)
    audio_path: str = ""     # absolute path to the final MP3
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stage"] = self.stage.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PipelineRun":
        d = dict(d)
        d["stage"] = Stage(d["stage"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class PipelineStateStore:
    """Thread-safe manager for pipeline run state and intermediate files."""

    def __init__(self, pipeline_dir: Path, retention_days: int = 3) -> None:
        self._dir = pipeline_dir
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _run_dir(self, run_id: str) -> Path:
        return self._dir / run_id

    def _state_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "state.json"

    def _write(self, run: PipelineRun) -> None:
        """Atomically write run state to state.json."""
        path = self._state_path(run.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def create(self, url: str) -> PipelineRun:
        """Create a new run record for *url* and persist it."""
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        run_dir = self._run_dir(run_id)
        run = PipelineRun(
            id=run_id,
            url=url,
            stage=Stage.PENDING,
            created_at=now,
            updated_at=now,
            run_dir=str(run_dir),
        )
        with self._lock:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._write(run)
        logger.debug("Pipeline run %s created for %s", run_id[:8], url)
        return run

    def transition(self, run: PipelineRun, stage: Stage, **kwargs) -> PipelineRun:
        """Advance *run* to *stage*, optionally updating other fields."""
        run.stage = stage
        run.updated_at = datetime.now(timezone.utc).isoformat()
        for key, value in kwargs.items():
            setattr(run, key, value)
        with self._lock:
            self._write(run)
        logger.info("Pipeline %s [%s] → %s", run.id[:8], run.url[:60], stage.value)
        return run

    # ------------------------------------------------------------------ #
    # Intermediate file helpers
    # ------------------------------------------------------------------ #

    def script_path(self, run: PipelineRun) -> Path:
        return Path(run.run_dir) / "script.txt"

    def tts_input_path(self, run: PipelineRun) -> Path:
        return Path(run.run_dir) / "tts_input.txt"

    def save_script(self, run: PipelineRun, text: str) -> Path:
        """Write *text* to script.txt and record the path in state."""
        p = self.script_path(run)
        p.write_text(text, encoding="utf-8")
        self.transition(run, run.stage, script_path=str(p))
        logger.debug("Script saved → %s (%d chars)", p, len(text))
        return p

    def save_tts_input(self, run: PipelineRun, text: str) -> Path:
        """Write *text* to tts_input.txt and record the path in state."""
        p = self.tts_input_path(run)
        p.write_text(text, encoding="utf-8")
        self.transition(run, run.stage, tts_input_path=str(p))
        logger.debug("TTS input saved → %s (%d chars)", p, len(text))
        return p

    # ------------------------------------------------------------------ #
    # Pruning
    # ------------------------------------------------------------------ #

    def prune_intermediates(self) -> None:
        """Delete script.txt and tts_input.txt for runs older than retention_days.

        state.json and the final MP3 are never deleted here.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        pruned_files = 0
        pruned_runs = 0

        for run_dir in self._dir.iterdir():
            if not run_dir.is_dir():
                continue
            state_file = run_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                created_at = datetime.fromisoformat(data["created_at"])
                if created_at >= cutoff:
                    continue

                run_pruned = False
                for name in ("script.txt", "tts_input.txt"):
                    f = run_dir / name
                    if f.exists():
                        f.unlink()
                        pruned_files += 1
                        run_pruned = True

                if run_pruned:
                    # Clear the paths in state.json so readers know files are gone
                    data["script_path"] = ""
                    data["tts_input_path"] = ""
                    tmp = state_file.with_suffix(".tmp")
                    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    tmp.replace(state_file)
                    pruned_runs += 1

            except Exception as exc:
                logger.warning("Prune check failed for %s: %s", run_dir.name, exc)

        if pruned_files:
            logger.info(
                "Pruned %d intermediate file(s) from %d run(s) older than %d day(s)",
                pruned_files, pruned_runs, self._retention_days,
            )
