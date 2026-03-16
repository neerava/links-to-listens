"""Single-worker FIFO job queue for API background processing.

At most one job runs at a time per queue instance.  Extra submissions are
queued and processed in order.  Results (or errors) are stored in memory and
can be retrieved by job ID until the process restarts.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: Any = None   # job-specific output dict
    error: str | None = None

    def to_dict(self, queue_position: int = -1) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }
        if self.status == JobStatus.PENDING and queue_position >= 0:
            d["queue_position"] = queue_position
        return d


class JobQueue:
    """Single-worker FIFO job queue.

    Usage::

        def my_worker(x: int) -> dict:
            return {"square": x * x}

        q = JobQueue(my_worker)
        job_id = q.submit(x=5)
        job = q.get(job_id)   # poll until job.status == JobStatus.DONE
    """

    def __init__(self, worker_fn: Callable[..., Any]) -> None:
        self._worker_fn = worker_fn
        self._jobs: dict[str, Job] = {}
        self._pending: list[tuple[str, dict]] = []   # ordered (job_id, kwargs)
        self._lock = threading.Lock()
        self._event = threading.Event()
        t = threading.Thread(target=self._run, daemon=True, name="job-queue-worker")
        t.start()

    def submit(self, **kwargs: Any) -> str:
        """Enqueue a job; returns the job ID immediately."""
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._jobs[job_id] = job
            self._pending.append((job_id, kwargs))
        self._event.set()
        logger.info("Job %s enqueued (queue depth: %d)", job_id, len(self._pending))
        return job_id

    def get(self, job_id: str) -> Job | None:
        """Return the Job object or None if the ID is unknown."""
        return self._jobs.get(job_id)

    def queue_position(self, job_id: str) -> int:
        """0-indexed position of *job_id* among pending jobs (-1 if not pending)."""
        with self._lock:
            for i, (jid, _) in enumerate(self._pending):
                if jid == job_id:
                    return i
        return -1

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            self._event.wait()
            while True:
                with self._lock:
                    if not self._pending:
                        self._event.clear()
                        break
                    job_id, kwargs = self._pending.pop(0)
                    job = self._jobs[job_id]
                    job.status = JobStatus.RUNNING
                    job.started_at = datetime.now(timezone.utc).isoformat()

                logger.info("Starting job %s", job_id)
                try:
                    result = self._worker_fn(**kwargs)
                    with self._lock:
                        job.status = JobStatus.DONE
                        job.result = result
                        job.finished_at = datetime.now(timezone.utc).isoformat()
                    logger.info("Job %s completed", job_id)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        job.finished_at = datetime.now(timezone.utc).isoformat()
                    logger.error("Job %s failed: %s", job_id, exc)
