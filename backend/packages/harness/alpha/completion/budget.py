"""Scope budgets: hard ceilings that are refused with the numbers named.

A budget here is a *ceiling*, not a suggestion and never a clamp: crossing it
produces :class:`~alpha.completion.errors.BudgetExhausted` carrying the
measured figures, and the scope reports exhaustion rather than quietly
granting itself more headroom. Exhaustion also never converts into a pass —
it is a blocker like any other (``finalization.py``).

Children share the scope's budget; none inherits a fresh copy. That is the
plan's "do not allocate each child the full uncapped parent budget" rule.

What is deliberately *not* here: token and spend accounting. Providers do not
all report it, and inventing a number would be the kind of fabricated figure
this repository keeps refusing — an unmeasured cost is ``unknown``, recorded
nowhere here rather than approximated.
"""

from __future__ import annotations

import time

from .errors import BudgetExhausted
from .models import CompletionScope, ScopeBudget
from .policy import CompletionSettings


def budget_for(settings: CompletionSettings) -> ScopeBudget:
    """The scope budget a new scope starts with (policy-derived)."""
    return ScopeBudget(
        max_tasks=settings.max_tasks,
        max_depth=settings.max_depth,
        max_concurrency=settings.max_concurrency,
        max_gate_runs=settings.max_gate_runs,
        wall_clock_seconds=settings.wall_clock_seconds,
        deadline_at=None,
    )


def start_deadline(scope: CompletionScope, *, now: float | None = None) -> None:
    """Arm the wall-clock deadline the first time work actually starts."""
    moment = time.time() if now is None else now
    if scope.budget.deadline_at is None:
        scope.budget.deadline_at = moment + scope.budget.wall_clock_seconds
        scope.started_at = scope.started_at or moment


def charge_gate_run(scope: CompletionScope) -> int:
    """Consume one gate execution. Raises once the ceiling is reached."""
    budget = scope.budget
    if budget.gate_runs_used >= budget.max_gate_runs:
        budget.exhausted = True
        budget.exhaustion_reason = f"gate-run budget exhausted ({budget.gate_runs_used}/{budget.max_gate_runs})"
        raise BudgetExhausted(budget.exhaustion_reason, subject=scope.scope_id)
    budget.gate_runs_used += 1
    return budget.gate_runs_used


def check_deadline(scope: CompletionScope, *, now: float | None = None) -> bool:
    """True when the scope is past its deadline (and records the reason)."""
    moment = time.time() if now is None else now
    budget = scope.budget
    if budget.deadline_at is None:
        return False
    if moment > budget.deadline_at:
        budget.exhausted = True
        budget.exhaustion_reason = f"wall-clock deadline passed at {budget.deadline_at:g} (elapsed {moment - (scope.started_at or budget.deadline_at - budget.wall_clock_seconds):.0f}s of {budget.wall_clock_seconds:g}s)"
        return True
    return False


def assert_can_add_task(scope: CompletionScope) -> None:
    """Refuse a plan that would exceed the task ceiling (never clamp)."""
    budget = scope.budget
    if len(scope.tasks) >= budget.max_tasks:
        budget.exhausted = True
        budget.exhaustion_reason = f"task budget exhausted ({len(scope.tasks)}/{budget.max_tasks})"
        raise BudgetExhausted(
            f"scope {scope.scope_id!r} already holds {len(scope.tasks)} tasks, at its ceiling of {budget.max_tasks}; raise completion.max_tasks deliberately rather than growing without bound",
            subject=scope.scope_id,
        )


def assert_depth(depth: int, scope: CompletionScope) -> None:
    """Refuse a tree deeper than the configured maximum."""
    budget = scope.budget
    if depth > budget.max_depth:
        raise BudgetExhausted(
            f"task depth {depth} exceeds the scope ceiling of {budget.max_depth}; stop decomposing and integrate or hand off",
            subject=scope.scope_id,
        )


def exhaustion(scope: CompletionScope) -> str:
    """The recorded exhaustion reason, or ``""``."""
    return scope.budget.exhaustion_reason if scope.budget.exhausted else ""


__all__ = [
    "assert_can_add_task",
    "assert_depth",
    "budget_for",
    "charge_gate_run",
    "check_deadline",
    "exhaustion",
    "start_deadline",
]
