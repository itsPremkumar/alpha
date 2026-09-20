"""Priority-driven persistent job queue for background OS tasks."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from alpha.jobs.models import JobPriority, JobResult, JobSpec, JobStatus

logger = logging.getLogger(__name__)

_PRIORITY_WEIGHTS = {
    JobPriority.CRITICAL: 0,
    JobPriority.HIGH: 1,
    JobPriority.NORMAL: 2,
    JobPriority.LOW: 3,
}


class PersistentJobQueue:
    """Thread-safe priority job queue managing decoupled background task execution."""

    def __init__(self, max_concurrency: int = 4):
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        self._max_concurrency = max_concurrency
        self._lock = threading.Lock()
        # job_id -> JobSpec
        self._specs: dict[str, JobSpec] = {}
        # job_id -> JobResult
        self._results: dict[str, JobResult] = {}
        # List of queued job IDs ordered by priority and creation time
        self._queued_ids: list[str] = []
        # Set of currently running job IDs
        self._running_ids: set[str] = set()
        # Set of cancelled job IDs
        self._cancelled_ids: set[str] = set()
        # Callback hooks for job state transitions: (job_id, status) -> None
        self._listeners: list[Callable[[str, JobStatus], None]] = []

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def add_listener(self, listener: Callable[[str, JobStatus], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, JobStatus], None]) -> None:
        with self._lock:
            self._listeners.remove(listener)

    def _notify(self, job_id: str, status: JobStatus) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(job_id, status)
            except Exception as exc:
                logger.warning(f"Error in job listener for {job_id}: {exc}")

    def enqueue(self, spec: JobSpec) -> JobSpec:
        """Enqueue a new job specification based on priority."""
        with self._lock:
            if spec.job_id in self._specs:
                raise ValueError("Job ID already exists")
            self._specs[spec.job_id] = spec.model_copy(deep=True)
            self._results[spec.job_id] = JobResult(
                job_id=spec.job_id,
                status=JobStatus.QUEUED,
                metadata={"title": spec.title, "priority": spec.priority.value},
            )
            # Insert maintaining priority order (lowest weight first, then FIFO)
            spec_weight = _PRIORITY_WEIGHTS.get(spec.priority, 2)
            inserted = False
            for idx, existing_id in enumerate(self._queued_ids):
                existing_weight = _PRIORITY_WEIGHTS.get(self._specs[existing_id].priority, 2)
                if spec_weight < existing_weight:
                    self._queued_ids.insert(idx, spec.job_id)
                    inserted = True
                    break
            if not inserted:
                self._queued_ids.append(spec.job_id)

        self._notify(spec.job_id, JobStatus.QUEUED)
        return spec

    def dequeue_next(self, *, job_ids: set[str] | None = None) -> JobSpec | None:
        """Dequeue the next available job if concurrency limit allows."""
        with self._lock:
            if len(self._running_ids) >= self._max_concurrency:
                return None

            for job_id in self._queued_ids:
                if job_ids is not None and job_id not in job_ids:
                    continue
                self._queued_ids.remove(job_id)

                self._running_ids.add(job_id)
                self._results[job_id].status = JobStatus.RUNNING
                self._results[job_id].started_at = time.time()
                spec = self._specs[job_id]

                break
            else:
                return None

        self._notify(job_id, JobStatus.RUNNING)
        return spec

    def complete_job(self, result: JobResult) -> None:
        """Record completion of a job and update registry."""
        with self._lock:
            self._running_ids.discard(result.job_id)
            if result.job_id in self._queued_ids:
                self._queued_ids.remove(result.job_id)
            previous = self._results.get(result.job_id)
            if previous is not None:
                result.metadata = {**previous.metadata, **result.metadata}
                if result.started_at is None:
                    result.started_at = previous.started_at
                if result.job_id in self._cancelled_ids:
                    result.status = JobStatus.CANCELLED
                    result.error = previous.error
                    result.completed_at = time.time()
            self._results[result.job_id] = result

        self._notify(result.job_id, result.status)

    def cancel_job(self, job_id: str, reason: str = "User cancelled") -> bool:
        """Cancel a queued or running job."""
        with self._lock:
            if job_id not in self._specs:
                return False
            if self._results[job_id].status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                return False

            self._cancelled_ids.add(job_id)
            if job_id in self._queued_ids:
                self._queued_ids.remove(job_id)

            res = self._results.get(job_id)
            if res and res.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                res.status = JobStatus.CANCELLED
                res.error = reason
                if job_id not in self._running_ids:
                    res.completed_at = time.time()

        self._notify(job_id, JobStatus.CANCELLED)
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled_ids

    def get_status(self, job_id: str, *, owner_id: str | None = None) -> JobResult | None:
        with self._lock:
            if owner_id is not None:
                spec = self._specs.get(job_id)
                if spec is None or spec.owner_id != owner_id:
                    return None
            return self._results.get(job_id)

    def get_spec(self, job_id: str) -> JobSpec | None:
        with self._lock:
            return self._specs.get(job_id)

    def list_jobs(
        self,
        status: JobStatus | None = None,
        tag: str | None = None,
        limit: int = 50,
        *,
        owner_id: str | None = None,
    ) -> list[JobResult]:
        with self._lock:
            results = [result for job_id, result in self._results.items() if owner_id is None or (job_id in self._specs and self._specs[job_id].owner_id == owner_id)]

        if status:
            results = [r for r in results if r.status == status]
        if tag:
            results = [r for r in results if tag in self._specs.get(r.job_id, JobSpec(command=[])).tags]

        # Reverse order to return most recent first
        return results[::-1][:limit]
