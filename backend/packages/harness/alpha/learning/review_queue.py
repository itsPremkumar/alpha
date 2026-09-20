"""Idle-deferred review queue for post-turn learning forks.

When the machine is busy, spawning a review fork steals the resources the
next live turn needs — and the next turn cancels it (decode cost paid,
learning lost). So busy-bound reviews queue up: one slot per session,
newest snapshot wins (a review replays the whole conversation, so
coalescing is dedup, not loss). Aged-out items dispatch regardless of
idleness. In-memory best-effort, like the immediate fork.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_SECONDS = 30 * 60.0


@dataclass
class QueuedReview:
    session_id: str
    snapshot: dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)

    def age(self, now: float) -> float:
        return max(0.0, now - self.enqueued_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReviewQueue:
    """Coalescing deferral queue: newest snapshot wins per session."""

    def __init__(self, *, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS):
        self.max_age_seconds = max_age_seconds
        self._slots: dict[str, QueuedReview] = {}
        self._lock = threading.Lock()

    def defer(self, session_id: str, snapshot: dict[str, Any], *, now: float | None = None) -> QueuedReview:
        """Queue (or replace) a review. Returns the stored entry."""
        with self._lock:
            entry = QueuedReview(session_id=session_id, snapshot=snapshot, enqueued_at=now if now is not None else time.time())
            self._slots[session_id] = entry
            return entry

    def pending(self) -> list[QueuedReview]:
        with self._lock:
            return list(self._slots.values())

    def pending_ids(self) -> list[str]:
        with self._lock:
            return list(self._slots.keys())

    def pop_if_due(self, session_id: str, *, is_idle: bool = True, now: float | None = None) -> QueuedReview | None:
        """Pop one entry when idle, aged-out, or forced. None when not due."""
        moment = now if now is not None else time.time()
        with self._lock:
            entry = self._slots.get(session_id)
            if entry is None:
                return None
            if not (is_idle or entry.age(moment) >= self.max_age_seconds):
                return None
            return self._slots.pop(session_id)

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._slots.pop(session_id, None) is not None

    def drain(
        self,
        is_idle: Callable[[], bool],
        handler: Callable[[QueuedReview], None],
        *,
        now: float | None = None,
        force: bool = False,
    ) -> list[str]:
        """Dispatch due reviews: all when idle, only aged-out otherwise.

        Returns the drained session ids. Handler errors are isolated per
        entry so one bad snapshot cannot wedge the queue.
        """
        moment = now if now is not None else time.time()
        idle = False
        try:
            idle = bool(is_idle())
        except Exception:
            logger.debug("Idle predicate failed; treating as busy", exc_info=True)
        with self._lock:
            due = [s for s, e in self._slots.items() if force or idle or e.age(moment) >= self.max_age_seconds]
            entries = [self._slots.pop(s) for s in due]
        drained: list[str] = []
        for entry in entries:
            try:
                handler(entry)
                drained.append(entry.session_id)
            except Exception:
                logger.warning("Deferred review handler failed for %s", entry.session_id, exc_info=True)
        return drained


_queue: ReviewQueue | None = None
_queue_lock = threading.Lock()


def get_review_queue(*, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> ReviewQueue:
    """Process-wide review queue (in-memory, best-effort like the fork)."""
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = ReviewQueue(max_age_seconds=max_age_seconds)
        return _queue
