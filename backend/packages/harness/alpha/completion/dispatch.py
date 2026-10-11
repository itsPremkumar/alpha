"""Launch waves: what was asked for, what the host confirmed, what came back.

The distinction this module exists to protect is one line: **a task is not
``IN_FLIGHT`` because someone asked for a launch.** Only the host adapter
confirming a handle moves it there. Everything before that is
``REQUESTED``, and a launch that never started is ``LAUNCH_FAILED`` — a
record, not a disappearance.

A wave with a partial failure is therefore never "terminal": the launched
handles stay live and the failed launches stay visible until recovery or an
explicit abandonment says otherwise. Fabricating the missing handles would
convert an accounting problem into a false completion, which is exactly what
this engine refuses.

Recovery is deliberately conservative (plan §10): unknown side-effect status
is treated as unknown. A lost worker whose work may not be idempotent is
``BLOCKED``/``handoff_required`` with a reason, never ``VERIFIED``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .errors import IllegalTransition, NotFound
from .models import CompletionScope, DispatchRecord, DispatchState, TaskNode, TaskState
from .state_machine import assert_task_transition


@dataclass(frozen=True)
class WaveReport:
    """Honest accounting for one launch wave."""

    wave_id: str
    requested: int = 0
    started: int = 0
    returned: int = 0
    launch_failed: int = 0
    cancelled: int = 0
    unknown: int = 0
    terminal: bool = False
    unresolved: tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.started and self.launch_failed)

    def to_dict(self) -> dict[str, object]:
        return {
            "wave_id": self.wave_id,
            "requested": self.requested,
            "started": self.started,
            "returned": self.returned,
            "launch_failed": self.launch_failed,
            "cancelled": self.cancelled,
            "unknown": self.unknown,
            "terminal": self.terminal,
            "partial": self.partial,
            "unresolved": list(self.unresolved),
        }


def new_wave_id(scope: CompletionScope) -> str:
    scope.counters["wave"] = int(scope.counters.get("wave", 0)) + 1
    return f"wave-{scope.counters['wave']}"


def new_dispatch_id(scope: CompletionScope) -> str:
    scope.counters["dispatch"] = int(scope.counters.get("dispatch", 0)) + 1
    return f"dp-{scope.counters['dispatch']}"


def request_launch(
    scope: CompletionScope,
    task: TaskNode,
    *,
    agent: str = "",
    wave_id: str | None = None,
    now: float | None = None,
) -> DispatchRecord:
    """Record that a launch was *asked for*. The task does not move yet.

    Raises if the task is not in ``READY``: dispatching something that is not
    dispatchable would bypass the dependency rules the scheduler just checked.
    """
    moment = time.time() if now is None else now
    if task.state is not TaskState.READY:
        raise IllegalTransition(
            f"task {task.task_id!r} is {task.state.value!r}; only a READY task may be dispatched",
            subject=task.task_id,
        )
    record = DispatchRecord(
        dispatch_id=new_dispatch_id(scope),
        wave_id=wave_id or new_wave_id(scope),
        scope_id=scope.scope_id,
        task_id=task.task_id,
        agent=agent,
        attempt=task.attempts + 1,
        state=DispatchState.REQUESTED,
        requested_at=moment,
        input_contract_revision=scope.request_revision,
    )
    scope.dispatches[record.dispatch_id] = record
    if record.dispatch_id not in task.dispatch_ids:
        task.dispatch_ids.append(record.dispatch_id)
    return record


def confirm_start(scope: CompletionScope, dispatch_id: str, handle: str, *, now: float | None = None) -> DispatchRecord:
    """The host confirmed the launch: *this* is what makes a task in flight."""
    moment = time.time() if now is None else now
    record = _dispatch(scope, dispatch_id)
    if record.state not in {DispatchState.REQUESTED, DispatchState.STARTED}:
        raise IllegalTransition(
            f"dispatch {dispatch_id!r} is {record.state.value!r}; a confirmed start must follow a request",
            subject=dispatch_id,
        )
    if not handle:
        raise NotFound(f"dispatch {dispatch_id!r} was confirmed without a host handle", subject=dispatch_id)
    record.state = DispatchState.STARTED
    record.handle = handle
    record.started_at = moment
    task = scope.task(record.task_id)
    if task.state is TaskState.READY:
        assert_task_transition(task.state, TaskState.IN_FLIGHT)
        task.state = TaskState.IN_FLIGHT
        task.attempts = max(task.attempts, record.attempt)
        task.updated_at = moment
    return record


def fail_launch(scope: CompletionScope, dispatch_id: str, error: str, *, now: float | None = None) -> DispatchRecord:
    """Record a launch that never started. The task returns to ``READY``.

    Never fabricates a handle: the failure is the record.
    """
    moment = time.time() if now is None else now
    record = _dispatch(scope, dispatch_id)
    record.state = DispatchState.LAUNCH_FAILED
    record.launch_error = error or "launch failed with no reason given"
    record.finished_at = moment
    task = scope.task(record.task_id)
    if task.state is TaskState.READY:
        task.blocked_reason = f"launch failed: {record.launch_error}"
        task.updated_at = moment
    return record


def mark_unknown(scope: CompletionScope, dispatch_id: str, reason: str, *, now: float | None = None) -> DispatchRecord:
    """The host's state could not be read. Unknown, never inferred as done."""
    moment = time.time() if now is None else now
    record = _dispatch(scope, dispatch_id)
    record.state = DispatchState.UNKNOWN
    record.terminal_reason = reason or "host state could not be read"
    record.finished_at = moment
    task = scope.task(record.task_id)
    if task.state is TaskState.IN_FLIGHT:
        assert_task_transition(task.state, TaskState.BLOCKED)
        task.state = TaskState.BLOCKED
        task.blocked_reason = f"worker state unknown: {record.terminal_reason}"
        task.updated_at = moment
    return record


def cancel(scope: CompletionScope, dispatch_id: str, reason: str, *, now: float | None = None) -> DispatchRecord:
    moment = time.time() if now is None else now
    record = _dispatch(scope, dispatch_id)
    if record.state in {DispatchState.RETURNED, DispatchState.CANCELLED}:
        raise IllegalTransition(f"dispatch {dispatch_id!r} is already {record.state.value}", subject=dispatch_id)
    record.state = DispatchState.CANCELLED
    record.terminal_reason = reason or "cancelled"
    record.finished_at = moment
    task = scope.task(record.task_id)
    if task.state in {TaskState.IN_FLIGHT, TaskState.READY}:
        assert_task_transition(task.state, TaskState.ABANDONED)
        task.state = TaskState.ABANDONED
        task.blocked_reason = record.terminal_reason
        task.updated_at = moment
    return record


def record_return(
    scope: CompletionScope,
    dispatch_id: str,
    *,
    artifacts: list[str] | None = None,
    reason: str = "",
    now: float | None = None,
) -> tuple[DispatchRecord, TaskNode]:
    """A worker reported a result. The task becomes ``RETURNED``, not ``VERIFIED``."""
    moment = time.time() if now is None else now
    record = _dispatch(scope, dispatch_id)
    if record.state is DispatchState.RETURNED:
        raise IllegalTransition(f"dispatch {dispatch_id!r} already returned", subject=dispatch_id)
    record.state = DispatchState.RETURNED
    record.finished_at = moment
    record.terminal_reason = reason
    record.artifact_refs = list(artifacts or [])
    task = scope.task(record.task_id)
    if task.state in {TaskState.IN_FLIGHT, TaskState.READY}:
        assert_task_transition(task.state, TaskState.RETURNED)
        task.state = TaskState.RETURNED
        task.updated_at = moment
    return record, task


def wave_report(scope: CompletionScope, wave_id: str) -> WaveReport:
    """Accounting for one wave, with the unresolved launches named.

    ``terminal`` is true only when every dispatch in the wave reached a state
    that needs no recovery: a partial wave reports ``terminal=False``.
    """
    members = [dispatch for dispatch in scope.dispatches.values() if dispatch.wave_id == wave_id]
    unresolved = tuple(dispatch.dispatch_id for dispatch in members if dispatch.state in {DispatchState.REQUESTED, DispatchState.UNKNOWN})
    counts = {state: sum(1 for dispatch in members if dispatch.state is state) for state in DispatchState}
    return WaveReport(
        wave_id=wave_id,
        requested=counts[DispatchState.REQUESTED],
        started=counts[DispatchState.STARTED],
        returned=counts[DispatchState.RETURNED],
        launch_failed=counts[DispatchState.LAUNCH_FAILED],
        cancelled=counts[DispatchState.CANCELLED],
        unknown=counts[DispatchState.UNKNOWN],
        terminal=bool(members) and not unresolved and counts[DispatchState.STARTED] == 0,
        unresolved=unresolved,
    )


def unresolved_waves(scope: CompletionScope) -> list[WaveReport]:
    """Every wave that still owes an answer, de-duplicated by wave id."""
    seen: dict[str, WaveReport] = {}
    for dispatch in scope.dispatches.values():
        if dispatch.wave_id in seen:
            continue
        report = wave_report(scope, dispatch.wave_id)
        if not report.terminal:
            seen[dispatch.wave_id] = report
    return list(seen.values())


def reconcile(scope: CompletionScope, host_states: dict[str, str], *, now: float | None = None) -> list[str]:
    """Fold what the host reports back into the ledger at reconnect.

    ``host_states`` maps dispatch id -> ``active`` / ``finished`` / ``lost`` /
    ``unknown``. Anything the host cannot answer becomes ``UNKNOWN`` (a
    blocked task with a reason), never ``RETURNED``: inventing a terminal
    state for an unreadable worker is how a restart turns into a false pass.

    Returns the ids whose state changed.
    """
    moment = time.time() if now is None else now
    changed: list[str] = []
    for dispatch in scope.dispatches.values():
        if dispatch.state in {DispatchState.RETURNED, DispatchState.CANCELLED, DispatchState.LAUNCH_FAILED}:
            continue
        host = host_states.get(dispatch.dispatch_id, "unknown")
        if host == "active":
            if dispatch.state is DispatchState.REQUESTED:
                # The host says it runs, but we have no handle to name it by.
                continue
            continue
        if host == "finished":
            record_return(scope, dispatch.dispatch_id, reason="host reports finished on reconnect", now=moment)
            changed.append(dispatch.dispatch_id)
        elif host == "lost":
            mark_unknown(scope, dispatch.dispatch_id, "host no longer knows this session", now=moment)
            changed.append(dispatch.dispatch_id)
        elif dispatch.state is DispatchState.REQUESTED:
            # Asked for and never confirmed, and the host has no record of it.
            fail_launch(scope, dispatch.dispatch_id, "host has no record of this launch", now=moment)
            changed.append(dispatch.dispatch_id)
        else:
            mark_unknown(scope, dispatch.dispatch_id, "host state could not be read", now=moment)
            changed.append(dispatch.dispatch_id)
    return changed


def _dispatch(scope: CompletionScope, dispatch_id: str) -> DispatchRecord:
    try:
        return scope.dispatches[dispatch_id]
    except KeyError:
        raise NotFound(f"dispatch {dispatch_id!r} does not exist in scope {scope.scope_id!r}", subject=dispatch_id) from None


__all__ = [
    "WaveReport",
    "cancel",
    "confirm_start",
    "fail_launch",
    "mark_unknown",
    "new_dispatch_id",
    "new_wave_id",
    "record_return",
    "reconcile",
    "request_launch",
    "unresolved_waves",
    "wave_report",
]
