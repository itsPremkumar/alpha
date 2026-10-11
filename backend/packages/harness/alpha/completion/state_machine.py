"""Legal state transitions — the only place a status string may be written.

Two state machines live here, both closed graphs:

* **Scope** (:class:`~alpha.completion.models.ScopeStatus`) — the user request.
* **Task** (:class:`~alpha.completion.models.TaskState`) — one unit of work.

Why a central validator instead of "just assign the field":

* An LLM, a subagent or an adapter cannot write ``verified`` directly into a
  task that never ran a check; the transition into ``VERIFIED`` only exists
  from ``VERIFYING``, and ``finalization`` is the only path to a scope's
  ``COMPLETE``.
* Illegal transitions become a typed refusal
  (:class:`~alpha.completion.errors.IllegalTransition`) carrying both states,
  so a caller learns *which* edge it tried rather than a silent no-op that
  leaves two components disagreeing about reality.
* Terminal states stay terminal: ``CANCELLED`` cannot be resumed, and
  ``ABANDONED`` is never a back door to ``READY`` — abandonment is an
  audited decision, not a retry queue.

The vocabulary deliberately mirrors the plan's §5.3 table, and ``RETURNED``
sits between ``IN_FLIGHT`` and ``VERIFYING`` precisely because "a worker sent
a result" is not "the result was checked".
"""

from __future__ import annotations

from .errors import IllegalTransition
from .models import ScopeStatus, TaskState

#: Scope lifecycle. ``COMPLETE`` is reachable only from ``VERIFYING``: the
#: engine must first evaluate evidence (moving the scope into ``VERIFYING``)
#: before anything may claim completion, which is what makes the finalization
#: guard a gate rather than a label.
SCOPE_TRANSITIONS: dict[ScopeStatus, frozenset[ScopeStatus]] = {
    ScopeStatus.PLANNING: frozenset({ScopeStatus.RUNNING, ScopeStatus.CANCELLED, ScopeStatus.BLOCKED}),
    ScopeStatus.RUNNING: frozenset(
        {
            ScopeStatus.VERIFYING,
            ScopeStatus.BLOCKED,
            ScopeStatus.FAILED,
            ScopeStatus.CANCELLED,
            ScopeStatus.HANDOFF_REQUIRED,
            ScopeStatus.PARTIAL,
        }
    ),
    ScopeStatus.VERIFYING: frozenset(
        {
            ScopeStatus.COMPLETE,
            ScopeStatus.PARTIAL,
            ScopeStatus.BLOCKED,
            ScopeStatus.FAILED,
            ScopeStatus.HANDOFF_REQUIRED,
            ScopeStatus.CANCELLED,
            # More work is needed after an inspection: go back to running.
            ScopeStatus.RUNNING,
        }
    ),
    # A reported-but-incomplete scope can be resumed (revised contract, a
    # repaired gate, a fresh worker), so these are not dead ends — but only
    # back into real work, never straight to COMPLETE.
    ScopeStatus.PARTIAL: frozenset({ScopeStatus.RUNNING, ScopeStatus.CANCELLED, ScopeStatus.HANDOFF_REQUIRED, ScopeStatus.VERIFYING}),
    ScopeStatus.BLOCKED: frozenset({ScopeStatus.RUNNING, ScopeStatus.CANCELLED, ScopeStatus.HANDOFF_REQUIRED, ScopeStatus.VERIFYING}),
    ScopeStatus.FAILED: frozenset({ScopeStatus.RUNNING, ScopeStatus.CANCELLED, ScopeStatus.HANDOFF_REQUIRED}),
    ScopeStatus.HANDOFF_REQUIRED: frozenset({ScopeStatus.RUNNING, ScopeStatus.CANCELLED}),
    ScopeStatus.COMPLETE: frozenset(),
    ScopeStatus.CANCELLED: frozenset(),
}

#: Task lifecycle. Note ``VERIFIED -> VERIFYING``: evidence can go stale, and
#: a verified task must be re-openable for re-verification rather than
#: becoming an eternal green tick.
TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.WAITING: frozenset({TaskState.READY, TaskState.BLOCKED, TaskState.ABANDONED, TaskState.FAILED}),
    TaskState.READY: frozenset(
        {
            # IN_FLIGHT only after a host confirms the launch (dispatch.py).
            TaskState.IN_FLIGHT,
            TaskState.WAITING,
            TaskState.BLOCKED,
            TaskState.ABANDONED,
            TaskState.FAILED,
        }
    ),
    TaskState.IN_FLIGHT: frozenset({TaskState.RETURNED, TaskState.BLOCKED, TaskState.ABANDONED, TaskState.FAILED}),
    TaskState.RETURNED: frozenset(
        {
            TaskState.VERIFYING,
            TaskState.IN_FLIGHT,  # an explicit permitted retry
            TaskState.BLOCKED,
            TaskState.ABANDONED,
            TaskState.FAILED,
        }
    ),
    TaskState.VERIFYING: frozenset(
        {
            TaskState.VERIFIED,
            TaskState.RETURNED,  # gates failed: hand it back for repair
            TaskState.BLOCKED,
            TaskState.ABANDONED,
            TaskState.FAILED,
        }
    ),
    TaskState.VERIFIED: frozenset({TaskState.VERIFYING, TaskState.BLOCKED}),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.WAITING, TaskState.ABANDONED, TaskState.FAILED}),
    # A failed task may be requeued *explicitly* (new attempt) or abandoned.
    TaskState.FAILED: frozenset({TaskState.READY, TaskState.ABANDONED}),
    TaskState.ABANDONED: frozenset(),
}


def can_transition_scope(current: ScopeStatus, target: ScopeStatus) -> bool:
    """Whether ``current -> target`` is an edge of the scope graph."""
    return target in SCOPE_TRANSITIONS.get(current, frozenset())


def can_transition_task(current: TaskState, target: TaskState) -> bool:
    """Whether ``current -> target`` is an edge of the task graph."""
    return target in TASK_TRANSITIONS.get(current, frozenset())


def assert_scope_transition(current: ScopeStatus, target: ScopeStatus) -> None:
    """Raise :class:`IllegalTransition` unless the edge exists.

    A no-op transition (``current is target``) is refused too: "the scope is
    already complete" is not evidence that completion was re-earned.
    """
    if current is target:
        raise IllegalTransition(f"scope is already {current.value!r}; a no-op transition carries no new evidence", subject=current.value)
    if not can_transition_scope(current, target):
        allowed = ", ".join(sorted(s.value for s in SCOPE_TRANSITIONS.get(current, frozenset()))) or "(none: terminal state)"
        raise IllegalTransition(f"scope cannot move {current.value!r} -> {target.value!r}; allowed from here: {allowed}", subject=current.value)


def assert_task_transition(current: TaskState, target: TaskState) -> None:
    """Raise :class:`IllegalTransition` unless the edge exists."""
    if current is target:
        raise IllegalTransition(f"task is already {current.value!r}; a no-op transition carries no new evidence", subject=current.value)
    if not can_transition_task(current, target):
        allowed = ", ".join(sorted(s.value for s in TASK_TRANSITIONS.get(current, frozenset()))) or "(none: terminal state)"
        raise IllegalTransition(f"task cannot move {current.value!r} -> {target.value!r}; allowed from here: {allowed}", subject=current.value)


def is_terminal_scope(status: ScopeStatus) -> bool:
    """``COMPLETE`` and ``CANCELLED`` are closed; nothing re-opens them."""
    return not SCOPE_TRANSITIONS.get(status)


def is_terminal_task(state: TaskState) -> bool:
    """``ABANDONED`` is the only terminal task state (scope cancel sweeps the rest)."""
    return not TASK_TRANSITIONS.get(state)


def task_progression_rank(state: TaskState) -> int:
    """Monotone rank used for display order and readiness heuristics.

    Not a transition rule — ``BLOCKED``/``FAILED`` rank above ``READY``
    because they are *decided* states, while ``WAITING`` is not.
    """
    order = {
        TaskState.WAITING: 0,
        TaskState.READY: 1,
        TaskState.IN_FLIGHT: 2,
        TaskState.RETURNED: 3,
        TaskState.VERIFYING: 4,
        TaskState.BLOCKED: 5,
        TaskState.FAILED: 6,
        TaskState.ABANDONED: 7,
        TaskState.VERIFIED: 8,
    }
    return order[state]


__all__ = [
    "SCOPE_TRANSITIONS",
    "TASK_TRANSITIONS",
    "assert_scope_transition",
    "assert_task_transition",
    "can_transition_scope",
    "can_transition_task",
    "is_terminal_scope",
    "is_terminal_task",
    "task_progression_rank",
]
