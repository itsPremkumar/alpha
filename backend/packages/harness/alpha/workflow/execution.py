"""Bounded execution primitives for the Dynamic Workflow Engine.

This module owns the two things a workflow engine needs in order to *bound* and
*overlap* node work, which the deterministic scheduler alone cannot provide:

1. **Real node deadlines** (:func:`run_with_deadline`).
   ``WorkflowNode.timeout_seconds`` used to make a node *unconditionally fail*:
   ``runtime.py`` short-circuited any node that declared a timeout, because the
   bound synchronous ``node_runner`` had no cancellation seam.  A declared
   deadline therefore guaranteed failure rather than enforcing a limit.
   :func:`run_with_deadline` runs the call on a worker thread and enforces a
   genuine deadline.  On expiry the caller receives a timed-out result carrying
   the *measured* overrun, and any value the worker produces later is
   **discarded, never adopted** — a result produced after the deadline cannot be
   attributed to a run that has already reported the node failed.

   CPython cannot safely kill a thread, so the work is *fenced*, not killed.  The
   worker is a daemon thread: it never keeps the process alive, and the engine's
   only authority over it is refusing to accept its late result.  This is
   disclosed in the returned record and in the ``node_timeout`` event rather than
   presented as a cancelled execution.

2. **True wave parallelism** (:func:`execute_wave`).
   :meth:`alpha.workflow.scheduler.WorkflowScheduler.partition_into_waves`
   already partitions ready nodes into waves whose ``write_scope`` entries are
   pairwise disjoint, but the engine then executed each wave in a plain ``for``
   loop, so a "parallel" wave ran serially and the disjointness guarantee bought
   nothing.  :func:`execute_wave` runs one wave on a bounded thread pool so the
   disjoint scopes genuinely overlap.

   Concurrency here is **only safe because the engine serializes its own state
   mutations** under a per-run lock.  The executor call is the slow part (a model
   call, a sandbox run, an HTTP request); the run/graph bookkeeping around it is
   fast.  Callers must therefore pass an ``invoke`` that acquires the engine's
   state lock for every read-modify-write on ``run``/``graph`` and releases it
   around the executor call.  :func:`execute_wave` never holds a lock itself, and
   it captures a node's escaping exception instead of raising it, so one node's
   failure cannot abandon its siblings mid-wave and leave them stranded in
   ``RUNNING``.

The DY-R1 honesty contract is carried here too: a deadline that expires is a real
failure with a real measured reason, never a synthesized success.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

# Bounded so a pathological graph cannot spawn one OS thread per ready node.
DEFAULT_MAX_CONCURRENCY = 4
MIN_CONCURRENCY = 1
MAX_CONCURRENCY_CEILING = 32

T = TypeVar("T")
R = TypeVar("R")


def clamp_concurrency(value: Any, *, default: int = DEFAULT_MAX_CONCURRENCY) -> int:
    """Coerce a declared concurrency into ``[1, MAX_CONCURRENCY_CEILING]``.

    A node/definition may declare any value at all (config text, a model
    proposal, a patched graph), so the number is clamped rather than trusted.
    ``None`` and unparsable values fall back to ``default``; the returned value
    is always a usable positive integer, never an exception the caller has to
    guess the meaning of.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(MIN_CONCURRENCY, min(int(default), MAX_CONCURRENCY_CEILING))
    return max(MIN_CONCURRENCY, min(parsed, MAX_CONCURRENCY_CEILING))


@dataclass(frozen=True)
class DeadlineResult(Generic[T]):
    """Outcome of one deadline-bounded call.

    ``timed_out`` is the single authority on whether the deadline was missed.
    ``value``/``error`` are only populated when the call actually returned in
    time; a timed-out record never carries a value, so a caller cannot
    accidentally adopt a late result.
    """

    value: T | None = None
    error: BaseException | None = None
    timed_out: bool = False
    elapsed_seconds: float = 0.0
    timeout_seconds: float | None = None
    late_work_fenced: bool = False

    @property
    def ok(self) -> bool:
        """True when the call returned in time and did not raise."""
        return not self.timed_out and self.error is None

    @property
    def overrun_seconds(self) -> float:
        """Seconds spent past the deadline (``0.0`` when it was met)."""
        if not self.timed_out or self.timeout_seconds is None:
            return 0.0
        return max(0.0, self.elapsed_seconds - self.timeout_seconds)

    def describe_timeout(self, label: str) -> str:
        """An honest, measured failure reason for a missed deadline."""
        if not self.timed_out:
            return ""
        bound = "unbounded" if self.timeout_seconds is None else f"{self.timeout_seconds:g}s"
        return (
            f"{label} exceeded its {bound} deadline after {self.elapsed_seconds:.3f}s "
            f"({self.overrun_seconds:.3f}s over); the in-flight call was fenced and its late result was "
            f"discarded rather than adopted (CPython cannot kill a thread, so the work may still be "
            f"completing in the background)"
        )

    def rethrow(self) -> None:
        """Re-raise the caller's own exception so the original reason survives."""
        if self.error is not None:
            raise self.error


def run_with_deadline(
    fn: Callable[[], T],
    *,
    timeout_seconds: float | None = None,
    label: str = "call",
) -> DeadlineResult[T]:
    """Run ``fn`` under a real deadline.

    ``timeout_seconds`` of ``None`` (or a non-positive value) means "no
    deadline", in which case ``fn`` is called inline on the calling thread so an
    unbounded node keeps its exact previous behaviour — no extra thread, no
    behaviour change.

    With a deadline the call moves to a daemon worker thread.  Two honest
    consequences, both surfaced in the returned record rather than hidden:

    - the call is **fenced, not cancelled** — CPython has no safe thread kill;
    - on expiry the record carries no value at all, so the engine fails the
      node with :meth:`DeadlineResult.describe_timeout` and discards whatever the
      worker later produces.
    """
    if timeout_seconds is None or timeout_seconds <= 0:
        started = time.monotonic()
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - the caller's reason is authoritative
            return DeadlineResult(
                error=exc,
                elapsed_seconds=time.monotonic() - started,
                timeout_seconds=None,
            )
        return DeadlineResult(
            value=value,
            elapsed_seconds=time.monotonic() - started,
            timeout_seconds=None,
        )

    box: dict[str, Any] = {}
    finished = threading.Event()

    def _target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported through the record
            box["error"] = exc
        finally:
            finished.set()

    started = time.monotonic()
    worker = threading.Thread(target=_target, name=f"dwe-node-{label}", daemon=True)
    worker.start()
    signalled = finished.wait(float(timeout_seconds))
    elapsed = time.monotonic() - started

    if not signalled:
        return DeadlineResult(
            timed_out=True,
            elapsed_seconds=elapsed,
            timeout_seconds=float(timeout_seconds),
            late_work_fenced=True,
        )
    return DeadlineResult(
        value=box.get("value"),
        error=box.get("error"),
        timed_out=False,
        elapsed_seconds=elapsed,
        timeout_seconds=float(timeout_seconds),
    )


@dataclass(frozen=True)
class WaveOutcome(Generic[R]):
    """Result of one node's invocation inside a wave.

    ``error`` is populated only when the invoke callable raised out of itself —
    the engine's own node handlers record their failures on the run instead, so a
    populated ``error`` means an unexpected fault that must be surfaced rather
    than swallowed.
    """

    item: R
    error: BaseException | None = None
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def execute_wave(
    items: Sequence[R],
    invoke: Callable[[R], Any],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[WaveOutcome[R]]:
    """Run ``invoke`` over ``items`` concurrently, bounded by ``max_concurrency``.

    Returns one :class:`WaveOutcome` per input item **in the submitted order**,
    so a caller can zip the result against the wave it planned.  Each item's
    exception is captured on its own outcome rather than raised: the pool must
    not abandon the remaining items, because a node stranded mid-wave would stay
    ``RUNNING`` forever and the run could never reach a terminal status.

    A single-item wave (or a concurrency of 1) runs inline on the calling
    thread. That keeps the overwhelmingly common small-wave path free of thread
    hand-off, and it means the sequential behaviour existing callers depend on is
    bit-for-bit preserved for graphs that never declare a wider wave.
    """
    if not items:
        return []

    concurrency = clamp_concurrency(max_concurrency)
    if len(items) == 1 or concurrency == MIN_CONCURRENCY:
        outcomes: list[WaveOutcome[R]] = []
        for item in items:
            started = time.monotonic()
            try:
                invoke(item)
            except BaseException as exc:  # noqa: BLE001 - captured per item
                outcomes.append(WaveOutcome(item=item, error=exc, elapsed_seconds=time.monotonic() - started))
            else:
                outcomes.append(WaveOutcome(item=item, elapsed_seconds=time.monotonic() - started))
        return outcomes

    results: list[WaveOutcome[R] | None] = [None] * len(items)

    def _run(index: int, item: R) -> None:
        started = time.monotonic()
        try:
            invoke(item)
        except BaseException as exc:  # noqa: BLE001 - captured per item
            results[index] = WaveOutcome(item=item, error=exc, elapsed_seconds=time.monotonic() - started)
        else:
            results[index] = WaveOutcome(item=item, elapsed_seconds=time.monotonic() - started)

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="dwe-wave") as pool:
        # ``list`` of futures is fine here: execute_wave is bounded by the wave
        # size, which the scheduler derived from a single graph's ready set.
        list(pool.map(lambda pair: _run(*pair), enumerate(items)))

    return [outcome for outcome in results if outcome is not None]


@dataclass
class ConcurrencyGovernor:
    """Per-run admission control for in-flight node executions.

    A run's ``policies`` may cap how many of its nodes may execute at once
    (a sandbox can host N concurrent sessions, an MCP server may accept N
    in-flight calls).  The governor is the single place that cap is enforced, so
    the wave executor, a host's own worker pool, and a future cross-process
    dispatcher cannot each invent a different limit.

    ``acquire`` is bounded and never blocks forever: a caller that cannot be
    admitted within ``wait_seconds`` gets ``False`` so it can fail the node with
    a real "capacity unavailable" reason instead of hanging the run.
    """

    limit: int = DEFAULT_MAX_CONCURRENCY
    wait_seconds: float = 30.0
    _in_flight: int = field(default=0, init=False, repr=False)
    _peak: int = field(default=0, init=False, repr=False)
    _rejected: int = field(default=0, init=False, repr=False)
    _condition: threading.Condition = field(default_factory=threading.Condition, init=False, repr=False)

    def __post_init__(self) -> None:
        self.limit = clamp_concurrency(self.limit)

    @property
    def in_flight(self) -> int:
        with self._condition:
            return self._in_flight

    @property
    def peak_in_flight(self) -> int:
        with self._condition:
            return self._peak

    @property
    def rejected(self) -> int:
        with self._condition:
            return self._rejected

    def acquire(self, *, wait_seconds: float | None = None) -> bool:
        """Admit one execution, or return ``False`` when capacity is unavailable."""
        budget = self.wait_seconds if wait_seconds is None else max(0.0, float(wait_seconds))
        deadline = time.monotonic() + budget
        with self._condition:
            while self._in_flight >= self.limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._rejected += 1
                    return False
                self._condition.wait(remaining)
            self._in_flight += 1
            self._peak = max(self._peak, self._in_flight)
            return True

    def release(self) -> None:
        with self._condition:
            if self._in_flight > 0:
                self._in_flight -= 1
            self._condition.notify()

    def stats(self) -> dict[str, Any]:
        """Measured governor telemetry for the run's observability payload."""
        with self._condition:
            return {
                "limit": self.limit,
                "in_flight": self._in_flight,
                "peak_in_flight": self._peak,
                "admission_rejections": self._rejected,
            }

    def reset(self) -> None:
        with self._condition:
            self._in_flight = 0
            self._peak = 0
            self._rejected = 0


class _Slot:
    """A context manager form of :meth:`ConcurrencyGovernor.acquire`.

    ``with governor.slot() as ok:`` releases the slot on exit even when the body
    raises, so an unexpected fault cannot leak admission capacity and wedge every
    later node of the same run.
    """

    __slots__ = ("_governor", "_held")

    def __init__(self, governor: ConcurrencyGovernor) -> None:
        self._governor = governor
        self._held = False

    def __enter__(self) -> bool:
        self._held = self._governor.acquire()
        return self._held

    def __exit__(self, *_exc: object) -> None:
        if self._held:
            self._governor.release()
            self._held = False


def governor_slot(governor: ConcurrencyGovernor) -> _Slot:
    """Return a ``with``-able admission slot for ``governor``."""
    return _Slot(governor)


def first_error(outcomes: Iterable[WaveOutcome[Any]]) -> BaseException | None:
    """The first captured exception in ``outcomes``, or ``None``.

    Wave execution deliberately does not raise; callers use this to surface an
    unexpected fault on the wave as a real run-level failure reason.
    """
    for outcome in outcomes:
        if outcome.error is not None:
            return outcome.error
    return None


# Per-thread record of whether a deadline was missed.  A node body funnels
# through many helpers (the default path, MAP, REDUCE, RACE, QUORUM) and each of
# them would otherwise have to thread a "did we time out" flag back out to the
# timing layer.  Thread-local state carries it honestly: a node runs on exactly
# one thread, so the flag cannot leak between nodes, and the worker thread a
# timed-out call is fenced onto is a DIFFERENT thread — so the flag is never set
# by the late work itself, only by the thread that observed the miss.
_TIMEOUT_STATE = threading.local()


def mark_timeout_occurred() -> None:
    """Record that a deadline was missed on the CURRENT thread."""
    _TIMEOUT_STATE.missed = True


def timeout_occurred() -> bool:
    """Whether a deadline was missed on this thread since the last reset."""
    return bool(getattr(_TIMEOUT_STATE, "missed", False))


def reset_timeout_flag() -> None:
    """Clear the current thread's deadline-miss record."""
    _TIMEOUT_STATE.missed = False


__all__ = [
    "DEFAULT_MAX_CONCURRENCY",
    "MAX_CONCURRENCY_CEILING",
    "MIN_CONCURRENCY",
    "ConcurrencyGovernor",
    "DeadlineResult",
    "WaveOutcome",
    "clamp_concurrency",
    "execute_wave",
    "first_error",
    "governor_slot",
    "mark_timeout_occurred",
    "reset_timeout_flag",
    "run_with_deadline",
    "timeout_occurred",
]
