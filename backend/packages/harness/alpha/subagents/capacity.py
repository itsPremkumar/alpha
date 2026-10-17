"""Shared process-local admission control for native subagent execution."""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from alpha.config.subagent_runtime_config import SubagentRuntimeConfig

#: Admission priority bands.
#:
#: These are **named on purpose**. A bare float at a call site hides the actual
#: policy; a named band makes the intent reviewable and makes a wrong value
#: obvious in a diff. They are ordering bands, not weights: only their relative
#: order matters, and the gap between them is deliberately wide so an
#: intermediate band can be added later without renumbering.
#:
#: The executor never guesses. It takes an `admission_priority` from the caller
#: that actually knows what kind of work it is admitting, and defaults to
#: ``PRIORITY_BACKGROUND`` so an unadopted call site behaves exactly as before.
PRIORITY_BACKGROUND = 0.0
#: A parent turn is blocked on this work: the operator is waiting for it, so it
#: must not sit behind background work that was queued first.
PRIORITY_FOREGROUND = 10.0
#: The operator explicitly asked for this run (an Arena tournament, a GRO
#: comparison). Nothing else in the queue outranks a thing a person started.
PRIORITY_OPERATOR = 20.0


class SubagentCapacityError(RuntimeError):
    """Base class for explicit admission failures."""


class SubagentCapacityRejected(SubagentCapacityError):
    """The process queue is full or configured to reject when saturated."""


class SubagentCapacityTimeout(SubagentCapacityError):
    """A queued execution did not receive a slot before its deadline."""


@dataclass(frozen=True)
class SubagentCapacitySnapshot:
    max_running: int
    running: int
    max_queued: int
    queued: int
    admission_policy: str
    #: Waiters ordered ahead of the oldest, i.e. `queued - fifo_position_of_oldest`.
    #: `0` means the queue is still effectively FIFO, which is what every
    #: caller that does not pass a priority produces.
    priority_preemptions: int = 0


class SubagentExecutionCapacity:
    """Async capacity controller; queued work never owns a thread.

    Admission is **priority-ordered with a FIFO tiebreak**. Every waiter carries
    a `(priority, sequence)` pair: a released slot goes to the highest priority
    waiting, and among equal priorities to the longest-waiting. A caller that
    passes no priority therefore sees exactly the previous FIFO behaviour, which
    is why enabling the mechanism changes nothing until a call site opts in.

    Why priority rather than plain FIFO: the pool is shared by every mechanism
    that runs through `SubagentExecutor` (ordinary `task`, durable batches, Arena
    competitors and GRO candidate lanes), so a cheap background batch can
    currently sit ahead of a run the operator is waiting on. Priority is the
    mechanism for "background yields to foreground" without a second pool.

    Every boundedness rule is unchanged: `max_running`, `max_queued`, the
    `reject` policy, `queue_timeout_seconds`, and the cancellation/timeout
    transfer nuance where a release and an expiry race.
    """

    def __init__(self, config: SubagentRuntimeConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._running = 0
        # (priority, sequence, future). Ordered by `(-priority, sequence)` when a
        # slot frees up; kept in arrival order otherwise so `len()` stays a
        # truthful queue depth.
        self._waiters: deque[tuple[float, int, asyncio.Future[None]]] = deque()
        self._sequence = itertools.count()
        #: Cumulative count of slots handed to a waiter that was not the
        #: longest-waiting. A real contention signal, not a per-run tally.
        self._preemptions = 0

    def snapshot(self) -> SubagentCapacitySnapshot:
        # snapshot() is read from non-loop threads (notably
        # configure_subagent_execution_capacity), while the loop thread mutates
        # the waiters deque under its own asyncio lock — iterating cross-thread
        # can raise "deque mutated during iteration". ``len()`` reads the
        # deque's size atomically instead. The raw length may count a waiter
        # that just timed out but has not removed itself yet; that only makes
        # the "capacity busy" answer more conservative, never less.
        return SubagentCapacitySnapshot(
            max_running=self._config.max_running,
            running=self._running,
            max_queued=self._config.max_queued,
            queued=len(self._waiters),
            admission_policy=self._config.admission_policy,
            priority_preemptions=self._preemptions,
        )

    def _next_waiter_locked(self) -> asyncio.Future[None] | None:
        """Pick the highest-priority pending waiter, FIFO on ties.

        Returns ``None`` when nothing is pending. Cancelled/done futures are
        dropped, so a waiter that timed out but has not yet removed itself does
        not receive a slot it will never use.
        """

        pending = [entry for entry in self._waiters if not entry[2].done()]
        if not pending:
            return None
        # `min` on (-priority, sequence): highest priority first, then oldest.
        return min(pending, key=lambda entry: (-entry[0], entry[1]))[2]

    async def _acquire(self, priority: float = 0.0) -> None:
        waiter: asyncio.Future[None] | None = None
        sequence = next(self._sequence)
        async with self._lock:
            if self._running < self._config.max_running:
                self._running += 1
                return
            queued = sum(not entry[2].done() for entry in self._waiters)
            if self._config.admission_policy == "reject" or queued >= self._config.max_queued:
                raise SubagentCapacityRejected(f"Subagent execution capacity is full ({self._config.max_running} running, {queued} queued)")
            waiter = asyncio.get_running_loop().create_future()
            self._waiters.append((priority, sequence, waiter))

        try:
            await asyncio.wait_for(
                waiter,
                timeout=self._config.queue_timeout_seconds,
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            async with self._lock:
                entry = next((item for item in self._waiters if item[2] is waiter), None)
                if entry is None:
                    # A release transferred the slot just as the timeout fired.
                    # If the future completed, this caller owns that transfer and
                    # must release it before reporting the timeout.
                    if waiter.done() and not waiter.cancelled():
                        self._release_locked()
                else:
                    self._waiters.remove(entry)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SubagentCapacityTimeout(f"Timed out after {self._config.queue_timeout_seconds}s waiting for a subagent execution slot") from exc

    def _release_locked(self) -> None:
        # Drop already-finished waiters first, then hand the slot to the
        # highest-priority pending one. `min` scans instead of popping, so the
        # deque itself keeps arrival order for the depth count.
        for entry in [item for item in self._waiters if item[2].done()]:
            try:
                self._waiters.remove(entry)
            except ValueError:  # pragma: no cover - removed concurrently under the lock
                pass
        waiter = self._next_waiter_locked()
        if waiter is not None:
            for index, (priority, sequence, candidate) in enumerate(self._waiters):
                if candidate is waiter:
                    if index != 0:
                        # The slot went to someone who was not the longest
                        # waiter: a real contention signal for the snapshot.
                        self._preemptions += 1
                    break
            # Transfer the existing slot; _running intentionally stays flat.
            waiter.set_result(None)
            return
        if self._running <= 0:
            raise RuntimeError("Subagent execution capacity released without an owner")
        self._running -= 1

    async def _release(self) -> None:
        async with self._lock:
            self._release_locked()

    @asynccontextmanager
    async def slot(self, priority: float = 0.0) -> AsyncIterator[None]:
        """Hold one execution slot.

        `priority` orders admission only; it never changes how many slots exist.
        Higher runs first on release, and an equal priority preserves FIFO.
        """

        await self._acquire(priority)
        try:
            yield
        finally:
            await self._release()


_config = SubagentRuntimeConfig()
_controller: SubagentExecutionCapacity | None = None
_controller_loop: asyncio.AbstractEventLoop | None = None
_state_lock = threading.Lock()


def configure_subagent_execution_capacity(config: SubagentRuntimeConfig) -> None:
    """Install the startup snapshot used by the lazily-created loop controller."""
    global _config, _controller, _controller_loop
    candidate = config.model_copy(deep=True)
    with _state_lock:
        # Gateway startup and embedded clients may initialize the same process.
        # Treat installing the same frozen startup configuration as a no-op so
        # those entry points cannot reset a live queue.
        if _config == candidate:
            return
        if _controller is not None:
            snapshot = _controller.snapshot()
            if snapshot.running or snapshot.queued:
                raise RuntimeError("Cannot reconfigure subagent capacity while executions are active")
        _config = candidate
        _controller = None
        _controller_loop = None


def get_subagent_execution_capacity() -> SubagentExecutionCapacity:
    """Return the controller bound to the current execution loop."""
    global _controller, _controller_loop
    loop = asyncio.get_running_loop()
    with _state_lock:
        if _controller is None:
            _controller = SubagentExecutionCapacity(_config)
            _controller_loop = loop
        elif _controller_loop is not loop:
            snapshot = _controller.snapshot()
            if snapshot.running or snapshot.queued:
                raise RuntimeError("Native subagent capacity cannot move event loops while executions are active")
            # Direct async consumers (notably embedded callers and tests) may
            # legitimately use a new event loop after the previous idle loop
            # has closed. Rebind only while completely idle; production sync
            # and background paths still share the persistent isolated loop.
            _controller = SubagentExecutionCapacity(_config)
            _controller_loop = loop
        return _controller


def configured_subagent_max_running() -> int:
    """Return the startup snapshot without requiring an event loop."""
    with _state_lock:
        return _config.max_running
def configured_subagent_admission_schedule() -> SubagentRuntimeConfig:
    """Return the armed admission schedule without requiring an event loop.

    Callers that must **disclose** what the admission plane can realize at
    plan time (the swarm coordinator's advisory) want the *schedule* — the
    startup-frozen ``subagent_runtime`` snapshot every ``SubagentExecutor``
    inherits — not a live busy/queued count, which answers the different
    question of what this instant is running, and not a hot-reloadable config
    read, which could disagree with the cluster the process actually armed.
    The return is a copy: the schedule is disclosed state, and a caller that
    mutates it must not re-arm the process.
    """
    with _state_lock:
        return _config.model_copy(deep=True)
