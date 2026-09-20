"""Multi-Lane Execution Scheduler and Transactional Writer Lease Fence.

Guarantees thread-level serialization, fair resource allocation across
heterogeneous execution workloads (interactive chat vs. background automations),
and corruption-free session persistence using lease-fenced writer tokens.
"""

from __future__ import annotations

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class ExecutionLane(enum.StrEnum):
    """Execution priority and concurrency categories."""

    USER_INTERACTION = "user_interaction"
    BACKGROUND_AUTONOMOUS = "background_autonomous"
    SYSTEM_MAINTENANCE = "system_maintenance"


class FenceContentionError(Exception):
    """Raised when an operation cannot acquire an active writer fence lease."""


@dataclass
class WriterFenceToken:
    """Represents a leased write lock on a specific thread's transcript/state."""

    token_id: str
    thread_id: str
    owner: str
    acquired_at: float
    lease_duration_seconds: float
    is_released: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.is_released and (time.time() < self.acquired_at + self.lease_duration_seconds)

    def renew(self, extension_seconds: float = 10.0) -> None:
        if self.is_released:
            raise ValueError("Cannot renew a released writer fence token.")
        self.lease_duration_seconds += extension_seconds


class WriterFence:
    """Thread-safe transactional writer lease fence per conversation thread."""

    def __init__(self, default_lease_seconds: float = 15.0) -> None:
        self._lock = threading.RLock()
        self._default_lease = default_lease_seconds
        # thread_id -> WriterFenceToken
        self._active_leases: dict[str, WriterFenceToken] = {}

    def acquire(
        self,
        thread_id: str,
        owner: str = "runner",
        lease_seconds: float | None = None,
        timeout: float = 0.0,
    ) -> WriterFenceToken:
        """Acquire an exclusive writer fence lease for the given thread."""
        lease_duration = lease_seconds or self._default_lease
        start_time = time.time()

        while True:
            with self._lock:
                current = self._active_leases.get(thread_id)
                # If existing token expired or was released, clean up
                if current and not current.is_valid:
                    self._active_leases.pop(thread_id, None)
                    current = None

                if current is None:
                    token = WriterFenceToken(
                        token_id=f"fence_{uuid.uuid4().hex[:8]}",
                        thread_id=thread_id,
                        owner=owner,
                        acquired_at=time.time(),
                        lease_duration_seconds=lease_duration,
                    )
                    self._active_leases[thread_id] = token
                    return token

            if time.time() - start_time >= timeout:
                owner_info = f"'{current.token_id}' (owner: {current.owner})" if current else "another writer"
                raise FenceContentionError(f"Thread '{thread_id}' is locked by active writer lease {owner_info}.")
            time.sleep(0.05)

    def release(self, token: WriterFenceToken | None) -> None:
        """Release an active writer lease."""
        if token is None:
            return
        with self._lock:
            token.is_released = True
            current = self._active_leases.get(token.thread_id)
            if current and current.token_id == token.token_id:
                self._active_leases.pop(token.thread_id, None)

    def is_locked(self, thread_id: str) -> bool:
        """Check whether a thread is actively fenced by a valid writer lease."""
        with self._lock:
            current = self._active_leases.get(thread_id)
            return current is not None and current.is_valid


@dataclass
class QueuedTask:
    """Represents a workload queued for dispatch in an execution lane."""

    task_id: str
    thread_id: str
    lane: ExecutionLane
    payload: Any
    enqueued_at: float = field(default_factory=time.time)


class LaneScheduler:
    """Manages parallel multi-lane execution limits and queue serialization."""

    def __init__(
        self,
        max_background_concurrency: int = 3,
        max_interactive_concurrency: int = 16,
        max_background_queue: int = 64,
    ) -> None:
        self._lock = threading.RLock()
        self.max_background_concurrency = max_background_concurrency
        self.max_interactive_concurrency = max_interactive_concurrency
        self.max_background_queue = max_background_queue

        self._active_counts: dict[ExecutionLane, int] = {lane: 0 for lane in ExecutionLane}
        self._background_queue: list[QueuedTask] = []
        self._fence = WriterFence()

    @property
    def fence(self) -> WriterFence:
        return self._fence

    def can_admit(self, lane: ExecutionLane) -> bool:
        """Determine if a task in the specified lane can be admitted immediately."""
        with self._lock:
            if lane == ExecutionLane.SYSTEM_MAINTENANCE:
                return True
            if lane == ExecutionLane.USER_INTERACTION:
                return self._active_counts[lane] < self.max_interactive_concurrency
            if lane == ExecutionLane.BACKGROUND_AUTONOMOUS:
                return self._active_counts[lane] < self.max_background_concurrency
            return False

    def admit_or_queue(
        self,
        thread_id: str,
        lane: ExecutionLane,
        payload: Any = None,
    ) -> tuple[bool, str]:
        """Attempt immediate admission; otherwise queue background work or reject."""
        with self._lock:
            task_id = f"task_{uuid.uuid4().hex[:8]}"

            if self.can_admit(lane):
                self._active_counts[lane] += 1
                return True, task_id

            if lane == ExecutionLane.BACKGROUND_AUTONOMOUS:
                if len(self._background_queue) >= self.max_background_queue:
                    raise OverflowError("Background autonomous execution lane queue is at capacity.")
                self._background_queue.append(QueuedTask(task_id=task_id, thread_id=thread_id, lane=lane, payload=payload))
                return False, task_id

            # User interactions fail open if max capacity reached or wait
            self._active_counts[lane] += 1
            return True, task_id

    def release_task(self, lane: ExecutionLane) -> QueuedTask | None:
        """Mark an active run finished and return the next queued task if capacity permits."""
        with self._lock:
            if self._active_counts[lane] > 0:
                self._active_counts[lane] -= 1

            if lane == ExecutionLane.BACKGROUND_AUTONOMOUS and self._background_queue:
                if self.can_admit(ExecutionLane.BACKGROUND_AUTONOMOUS):
                    next_task = self._background_queue.pop(0)
                    self._active_counts[ExecutionLane.BACKGROUND_AUTONOMOUS] += 1
                    return next_task

            return None

    def get_lane_stats(self) -> dict[str, Any]:
        """Return real-time metrics across all lanes."""
        with self._lock:
            return {
                "active": {k.value: v for k, v in self._active_counts.items()},
                "queued_background": len(self._background_queue),
                "limits": {
                    "max_background": self.max_background_concurrency,
                    "max_interactive": self.max_interactive_concurrency,
                    "max_background_queue": self.max_background_queue,
                },
            }
