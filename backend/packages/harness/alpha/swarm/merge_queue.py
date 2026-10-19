"""Serialized merge queue for coding swarm worktree isolation (bot plan 4.3).

When multiple coding tasks run in parallel, each creates its own worktree and
simulates a merge against the shared repository. Without serialization, these
merge simulations can interfere with each other — git's index lock, temporary
worktree collisions, and inconsistent conflict detection.

The merge queue ensures only one merge simulation runs at a time. It is
process-local (a single Gateway runs one swarm at a time) and uses an asyncio
Lock to serialize access.

The queue is deliberately simple: it does not prioritize, reorder, or batch
merges. It just ensures that when a merge simulation runs, no other merge
simulation is running against the same repository.
"""

from __future__ import annotations

import asyncio
import logging

from alpha.sandbox.merge_simulation import MergeSimulation
from alpha.sandbox.worktrees import WorktreeManager

logger = logging.getLogger(__name__)


class MergeQueue:
    """Serializes merge simulations against the shared git repository.

    Each merge simulation creates a detached worktree, runs ``git merge``, and
    checks for conflicts. These operations must not overlap because they all
    operate on the same git repository and can interfere with each other's
    temporary worktrees and index locks.

    The queue is process-local: a single Gateway process runs one swarm at a
    time, so an asyncio Lock is sufficient. No cross-process coordination is
    needed (or claimed).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue_size = 0
        self._completed = 0
        self._conflicted = 0
        self._failed = 0

    @property
    def stats(self) -> dict[str, int]:
        """Return queue statistics for telemetry."""
        return {
            "queued": self._queue_size,
            "completed": self._completed,
            "conflicted": self._conflicted,
            "failed": self._failed,
        }

    async def simulate_merge(
        self,
        manager: WorktreeManager,
        *,
        base_ref: str,
        head_ref: str,
        name: str,
    ) -> MergeSimulation:
        """Run a merge simulation under the queue lock.

        The simulation runs in the default executor because git operations
        are blocking. The lock ensures only one simulation runs at a time.
        """
        self._queue_size += 1
        async with self._lock:
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: manager.simulate_merge(base_ref=base_ref, head_ref=head_ref, name=name),
                )
                self._completed += 1
                if result.outcome == "conflicted":
                    self._conflicted += 1
                return result
            except Exception:
                self._failed += 1
                raise
            finally:
                self._queue_size -= 1


#: Process-local merge queue instance. One per Gateway process.
_DEFAULT_QUEUE: MergeQueue | None = None


def get_merge_queue() -> MergeQueue:
    """Return the process-local merge queue, creating it if necessary."""
    global _DEFAULT_QUEUE
    if _DEFAULT_QUEUE is None:
        _DEFAULT_QUEUE = MergeQueue()
    return _DEFAULT_QUEUE


__all__ = ["MergeQueue", "get_merge_queue"]
