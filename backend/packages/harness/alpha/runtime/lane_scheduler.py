"""Multi-Lane Execution Scheduler and Transactional Writer Lease Fence.

Guarantees thread-level serialization, fair resource allocation across
heterogeneous execution workloads (interactive chat vs. background automations),
and corruption-free session persistence using lease-fenced writer tokens.

Also hosts :class:`RunAdmissionController`, the hard-capped admission gate the
Gateway uses to bound in-flight run tasks. ``LaneScheduler`` stays the
queue-oriented multi-lane model; it is deliberately *not* the gate, because
``LaneScheduler.admit_or_queue`` fails open for the interactive lane (it
increments past the cap and still reports ``admitted=True``) and nothing drains
its background queue but a caller that already holds a ``release_task`` handle.
A gate that cannot say "no" cannot bound concurrency, so
:class:`RunAdmissionController` owns the rejection decision and keeps the lane
split for observability.

A refusal is a *transport-level* answer, not a verdict on the work: the request
was well formed and is worth repeating once a slot frees. Every producer of
durable background work that flows through the Gateway's run admission path --
the scheduled-task poller and the MCP task notifier -- therefore has to be able
to tell a refusal apart from a genuine failure, or it records "capacity is
full right now" in a durable column that is only ever read as "this job
failed". :func:`is_run_capacity_refusal` is that discriminator, and
:func:`capacity_refusal_retry_after_seconds` is the server's own retry hint. Both
are defined here, next to the stable code they match on, so no producer has to
re-derive the contract (and none of them has to import the Gateway).
"""

from __future__ import annotations

import enum
import os
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final


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


# ---------------------------------------------------------------------------
# Gateway run admission control
# ---------------------------------------------------------------------------

#: Stable machine-readable rejection code. Clients, dashboards, and operator
#: alerts match on this string, so it never changes wording.
RUN_ADMISSION_REJECTED_CODE: Final[str] = "gateway_run_capacity_exhausted"

#: HTTP status the Gateway answers a refusal with. Mirrors
#: ``app.gateway.services.RUN_ADMISSION_REJECTED_STATUS``; the value lives here
#: so a producer can recognise a refusal without importing the Gateway (which
#: would pull the whole request graph in behind a 429). Equality against the
#: Gateway constant is pinned by a test, so the two cannot drift.
RUN_CAPACITY_REFUSAL_STATUS: Final[int] = 429

#: Environment override for the process-wide in-flight run budget. Intended for
#: operators who need to move the ceiling without a code change; when unset the
#: budget is derived from the ORM connection pool (see
#: :func:`default_max_concurrent_runs`).
MAX_CONCURRENT_RUNS_ENV_VAR: Final[str] = "ALPHA_GATEWAY_MAX_CONCURRENT_RUNS"

#: Fallback budget when the app config cannot be loaded (e.g. no ``config.yaml``
#: in a bare unit-test environment). Matches the shipped
#: ``DatabaseConfig.pool_size`` default of 5 scaled by
#: :data:`RUNS_PER_POOLED_CONNECTION`.
_DEFAULT_MAX_CONCURRENT_RUNS: Final[int] = 10

#: In-flight run tasks allowed per pooled DB connection. A run holds a
#: connection only transiently (status write, delivery receipt, completion
#: write), so exactly one run per connection under-utilises the pool; two keeps
#: it busy while bounding the finalization burst that causes pool starvation.
RUNS_PER_POOLED_CONNECTION: Final[int] = 2


@dataclass(frozen=True)
class AdmissionDecision:
    """Outcome of one :meth:`RunAdmissionController.try_admit` call."""

    admitted: bool
    code: str
    active: int
    limit: int
    lane: ExecutionLane

    def __bool__(self) -> bool:
        return self.admitted


class RunAdmissionRejected(RuntimeError):
    """Raised when a run cannot be admitted within the in-flight run budget.

    Carries the stable ``code`` plus the saturation numbers so the caller can
    answer with an honest status and a retry hint instead of admitting work it
    has no capacity to serve.
    """

    def __init__(self, decision: AdmissionDecision, *, retry_after_seconds: float) -> None:
        super().__init__(
            f"Gateway run capacity exhausted ({decision.active}/{decision.limit} runs in flight); retry in {retry_after_seconds:g}s"
        )
        self.code = decision.code
        self.decision = decision
        self.retry_after_seconds = retry_after_seconds


def _refusal_code(obj: Any) -> str | None:
    """Best-effort stable code carried by a refusal-shaped object.

    Recognises both shapes a refusal arrives in: the bare
    :class:`RunAdmissionRejected`, and the ``HTTPException`` the Gateway raises
    in its place, whose ``detail`` is a mapping carrying the code.
    """
    code = getattr(obj, "code", None)
    if isinstance(code, str) and code:
        return code
    detail = getattr(obj, "detail", None)
    if isinstance(detail, Mapping):
        detail_code = detail.get("code")
        if isinstance(detail_code, str) and detail_code:
            return detail_code
    return None


def is_run_capacity_refusal(exc: BaseException) -> bool:
    """Is *exc* the Gateway declining to admit a run for lack of capacity?

    The honest answer matters because a capacity refusal is *retryable* while a
    run failure is *terminal*. A producer that cannot tell them apart writes
    "the server is busy" into the same durable column that means "this job
    failed", and the job is then destroyed by whatever drains that column --
    a dead-letter budget, a ``last_error``, a ``launch_failed`` journal entry.

    Matching is on the stable code, not on prose: a 429 whose body is some
    other kind of rate limit is *not* a capacity refusal and must not be
    mistaken for one. The bare :class:`RunAdmissionRejected` is accepted too so
    a caller that catches the pre-translation error behaves identically.
    """
    if isinstance(exc, RunAdmissionRejected):
        return True
    if getattr(exc, "status_code", None) != RUN_CAPACITY_REFUSAL_STATUS:
        return False
    return _refusal_code(exc) == RUN_ADMISSION_REJECTED_CODE


def capacity_refusal_retry_after_seconds(exc: BaseException, *, default: float = 1.0) -> float:
    """The server's own retry hint for a refusal, in seconds.

    Prefers what the refusal actually carried over *default*, because the hint
    is computed from the saturation the caller is looking at. A missing or
    nonsensical hint falls back rather than raising: backoff is a courtesy to
    the busy system, never a reason to fail the worker's control flow.
    """
    candidates: list[Any] = [getattr(exc, "retry_after_seconds", None)]
    detail = getattr(exc, "detail", None)
    if isinstance(detail, Mapping):
        candidates.append(detail.get("retry_after_seconds"))
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            continue
        if candidate > 0:
            return float(candidate)
    return float(default)


def capacity_refusal_detail(exc: BaseException) -> str:
    """Operator-readable one-liner for a refusal, safe to persist as an error.

    Carries the stable code first so a durable row that records it can be
    matched on the same string a client would have seen.
    """
    decision = getattr(exc, "decision", None)
    active = getattr(decision, "active", None)
    limit = getattr(decision, "limit", None)
    if active is None or limit is None:
        detail = getattr(exc, "detail", None)
        if isinstance(detail, Mapping):
            active = detail.get("active_runs")
            limit = detail.get("max_concurrent_runs")
    if isinstance(active, int) and isinstance(limit, int):
        return f"{RUN_ADMISSION_REJECTED_CODE}: {active}/{limit} runs in flight; the occurrence is queued, not failed"
    return f"{RUN_ADMISSION_REJECTED_CODE}: the occurrence is queued, not failed"


class RunAdmissionController:
    """Hard-bounded, await-free admission control for in-flight run tasks.

    Why this exists
    ---------------
    The Gateway used to attach one ``asyncio.Task`` per accepted run with no
    ceiling. Every one of those tasks ends in a burst of durable writes -- run
    status, delivery receipt, completion row, thread title, workspace changes --
    and all of them draw from one SQLAlchemy engine whose
    ``database.pool_size`` defaults to 5 (``DatabaseConfig.pool_size``) with
    ``database.command_timeout`` at 30s. More concurrent runs than pooled
    connections means those finalization writes queue behind each other until
    the command timeout expires, which is how a saturated Gateway starts
    returning failed runs and, worse, leaves receipts unwritten.

    Why it is synchronous
    ---------------------
    Run admission must not await between durable admission and task
    attachment: a cancellation that lands in that window would strand the run
    with no worker to observe it (see the comment above ``asyncio.create_task``
    in ``app.gateway.services.start_run``). An ``asyncio.Semaphore`` would put
    an await exactly there, so the gate is a plain counter under a
    ``threading.Lock``. That is correct on the single event-loop thread that
    calls it and stays correct if another thread ever reaches it.

    Scope
    -----
    The budget is **per process**, which is the same granularity as the
    connection pool it protects: N Gateway workers each allow
    ``max_concurrent_runs`` in-flight runs. A cross-process budget would need a
    durable lease keyed on the pool itself, which no component owns today.
    """

    def __init__(self, *, max_concurrent_runs: int, retry_after_seconds: float = 1.0) -> None:
        if isinstance(max_concurrent_runs, bool) or not isinstance(max_concurrent_runs, int) or max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be an integer >= 1")
        if retry_after_seconds <= 0:
            raise ValueError("retry_after_seconds must be positive")
        self._lock = threading.Lock()
        self.max_concurrent_runs = max_concurrent_runs
        self.retry_after_seconds = float(retry_after_seconds)
        self._active = 0
        self._peak = 0
        self._admitted = 0
        self._rejected = 0
        self._lane_active: dict[ExecutionLane, int] = {lane: 0 for lane in ExecutionLane}
        self._lane_rejected: dict[ExecutionLane, int] = {lane: 0 for lane in ExecutionLane}

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @property
    def available(self) -> int:
        """Slots a caller could take right now, without taking one.

        A read, so it is advisory: a caller that uses this to decide whether it
        is worth attempting an admission is racing every other caller, and a
        refusal remains the authoritative answer. What it buys is that a
        *background* producer which reuses a deterministic idempotency key can
        decline to walk into the race at all -- refusing is free, asking is not,
        because a refused admission has already consumed durable state.
        """
        with self._lock:
            return max(0, self.max_concurrent_runs - self._active)

    def try_admit(self, *, lane: ExecutionLane = ExecutionLane.USER_INTERACTION) -> AdmissionDecision:
        """Take one in-flight slot, or refuse without taking one.

        Never raises and never blocks: a caller that cannot be admitted still
        gets a decision it can turn into an honest response.
        """
        with self._lock:
            if self._active >= self.max_concurrent_runs:
                self._rejected += 1
                self._lane_rejected[lane] = self._lane_rejected.get(lane, 0) + 1
                return AdmissionDecision(
                    admitted=False,
                    code=RUN_ADMISSION_REJECTED_CODE,
                    active=self._active,
                    limit=self.max_concurrent_runs,
                    lane=lane,
                )
            self._active += 1
            self._admitted += 1
            if self._active > self._peak:
                self._peak = self._active
            self._lane_active[lane] = self._lane_active.get(lane, 0) + 1
            return AdmissionDecision(
                admitted=True,
                code="",
                active=self._active,
                limit=self.max_concurrent_runs,
                lane=lane,
            )

    def admit_or_raise(self, *, lane: ExecutionLane = ExecutionLane.USER_INTERACTION) -> AdmissionDecision:
        """Take one in-flight slot or raise :class:`RunAdmissionRejected`."""
        decision = self.try_admit(lane=lane)
        if not decision.admitted:
            raise RunAdmissionRejected(decision, retry_after_seconds=self.retry_after_seconds)
        return decision

    def release(self, *, lane: ExecutionLane = ExecutionLane.USER_INTERACTION) -> int:
        """Return one in-flight slot. Returns the remaining count.

        Over-releasing is a no-op rather than an error: a release for a run
        that never took a slot must not drive the counter negative and hand out
        capacity that does not exist.
        """
        with self._lock:
            if self._active > 0:
                self._active -= 1
            lane_active = self._lane_active.get(lane, 0)
            if lane_active > 0:
                self._lane_active[lane] = lane_active - 1
            return self._active

    def snapshot(self) -> dict[str, Any]:
        """Saturation metrics for logs and diagnostics.

        ``rejected_by_lane`` is the number that answers "is scheduled work being
        starved?". A flat ``rejected`` count cannot: it cannot separate a
        background run the operator should care about from an interactive burst
        that is simply the cap doing its job, and it keeps no history of which
        producer is losing the race for slots.
        """
        with self._lock:
            return {
                "active": self._active,
                "available": max(0, self.max_concurrent_runs - self._active),
                "peak": self._peak,
                "limit": self.max_concurrent_runs,
                "admitted": self._admitted,
                "rejected": self._rejected,
                "active_by_lane": {lane.value: count for lane, count in self._lane_active.items()},
                "rejected_by_lane": {lane.value: count for lane, count in self._lane_rejected.items()},
            }


def default_max_concurrent_runs() -> int:
    """Resolve the in-flight run budget for this process.

    Prefers ``ALPHA_GATEWAY_MAX_CONCURRENT_RUNS`` so an operator can retune the
    ceiling without a code change, then falls back to
    ``AppConfig.database.pool_size`` scaled by
    :data:`RUNS_PER_POOLED_CONNECTION`, then to a constant. Every step tolerates
    an unloadable or stubbed config, because admission must never be the thing
    that takes the Gateway down.
    """
    raw_override = os.environ.get(MAX_CONCURRENT_RUNS_ENV_VAR)
    if raw_override is not None:
        override = _positive_int(raw_override)
        if override is not None:
            return override
    pool_size = _configured_pool_size()
    if pool_size is None:
        return _DEFAULT_MAX_CONCURRENT_RUNS
    return max(1, pool_size * RUNS_PER_POOLED_CONNECTION)


def _positive_int(raw: str) -> int | None:
    try:
        value = int(raw.strip())
    except (AttributeError, ValueError):
        return None
    return value if value >= 1 else None


def _configured_pool_size() -> int | None:
    try:
        from alpha.config.app_config import get_app_config

        pool_size = get_app_config().database.pool_size
    except Exception:
        return None
    if isinstance(pool_size, bool) or not isinstance(pool_size, int) or pool_size < 1:
        return None
    return pool_size


_run_admission_controller: RunAdmissionController | None = None
_run_admission_controller_lock = threading.Lock()


def get_run_admission_controller() -> RunAdmissionController:
    """Return the process-wide in-flight run admission controller."""
    global _run_admission_controller
    controller = _run_admission_controller
    if controller is not None:
        return controller
    with _run_admission_controller_lock:
        if _run_admission_controller is None:
            _run_admission_controller = RunAdmissionController(max_concurrent_runs=default_max_concurrent_runs())
        return _run_admission_controller


def set_run_admission_controller(controller: RunAdmissionController | None) -> RunAdmissionController | None:
    """Install or clear the process-wide controller; returns the previous one.

    Exists for config reloads and for tests that need a deterministic budget.
    Passing ``None`` makes the next :func:`get_run_admission_controller` call
    re-resolve from configuration.
    """
    global _run_admission_controller
    with _run_admission_controller_lock:
        previous = _run_admission_controller
        _run_admission_controller = controller
    return previous


def resolve_execution_lane(*, autonomous: bool, background: bool = False) -> ExecutionLane:
    """Classify a run for admission accounting.

    Only observability depends on this: the in-flight budget is global, so a
    background run is refused exactly like an interactive one rather than
    queueing unboundedly behind a lane that never drains.
    """
    if background:
        return ExecutionLane.BACKGROUND_AUTONOMOUS
    if autonomous:
        return ExecutionLane.SYSTEM_MAINTENANCE
    return ExecutionLane.USER_INTERACTION


def has_run_capacity(*, controller: RunAdmissionController | None = None) -> bool:
    """Is the in-flight run budget believed to have a free slot right now?

    For producers whose admission is *not* idempotent-safe to repeat -- a
    background job that hands the Gateway a deterministic idempotency key, so a
    refused admission has already consumed durable state -- walking into a
    saturated gateway converts "I am early" into "my key is burnt". Asking
    first is strictly cheaper than being refused.

    Advisory by construction, and deliberately total: any surprise resolving the
    controller reads as capacity available, so this can never be the reason a
    scheduled occurrence fails to start. A refusal at
    :meth:`RunAdmissionController.try_admit` remains the authority.
    """
    try:
        resolved = controller if controller is not None else get_run_admission_controller()
    except Exception:
        return True
    try:
        return resolved.available > 0
    except Exception:
        return True

