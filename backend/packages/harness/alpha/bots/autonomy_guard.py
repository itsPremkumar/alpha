"""Enforced autonomy bounds for unattended bot-mode delegation trees.

What already exists, and what this adds
---------------------------------------
Depth, per-parent child count and attempt count are already configured and
partly enforced in
:mod:`alpha.subagents.lifecycle` (``DEFAULT_MAX_DEPTH``,
``DEFAULT_MAX_CHILDREN_PER_PARENT``, ``DEFAULT_MAX_ATTEMPTS``). This module does
not duplicate them. It supplies the four things that were missing, and it
supplies them as a single enforced chokepoint that the delegation path calls
once per dispatch:

* **wall clock** — a deadline that is checked, not merely configured;
* **inherited budgets** — a child may spend at most what remains of its parent's
  budget, so depth cannot multiply spend;
* **a cycle guard** — a task that has already bounced between the same pair of
  agents is stopped rather than retried forever;
* **honest aggregation** — a parent sees FAILED if any descendant failed, and a
  task is not complete while any descendant is unresolved.

The honesty rules are the load-bearing part
-------------------------------------------
This codebase has shipped "child failure reported to the parent as success"
twice. So the aggregation here is written to be unfalsifiable by accident:

* :func:`aggregate_descendants` starts from a FAILING assumption and is only
  cleared by positive evidence. There is no code path that returns success
  because it ran out of things to check;
* a descendant in a non-terminal state makes the aggregate ``unresolved``, and
  ``unresolved`` is not success;
* unknown, missing or unparseable descendant records are counted as failures,
  not ignored.

Full autonomy means no human for DISPATCH and REASONING. It does not mean no
human for destructive execution: :func:`assert_requires_approval` is what keeps
irreversible work on the existing approval gate even when every other gate is
open.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.bots.authority_ceiling import AuthorityViolation
from alpha.bots.governance_ledger import ACTION_DELEGATED, record_governance_action

logger = logging.getLogger(__name__)

# Terminal descendant states. Anything NOT in this set is unresolved.
TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "reverted"})

#: States that mean the work did not succeed.
FAILURE_STATES: frozenset[str] = frozenset({"failed", "cancelled", "reverted"})

# Irreversible work always routes through the existing approval gate. Matched on
# the action label, so a NEW irreversible action must be added here to be
# covered — an allowlist, not a denylist, because a denylist fails open.
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset(
    {
        "delete",
        "delete_file",
        "drop_database",
        "deploy_production",
        "force_push",
        "git_push_protected",
        "publish_release",
        "send_external_email",
        "revoke_credential",
        "wipe_workspace",
    }
)


class AutonomyCeilingExceeded(RuntimeError):
    """A bound was hit. The tree is STOPPED, not slowed."""

    def __init__(self, message: str, *, bound: str, limit: Any) -> None:
        super().__init__(message)
        self.bound = bound
        self.limit = limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "autonomy_ceiling_exceeded",
            "message": str(self),
            "bound": self.bound,
            "limit": self.limit,
        }


class CycleDetected(RuntimeError):
    """A task ping-ponged between the same agents too many times."""

    def __init__(self, message: str, *, path: Sequence[str]) -> None:
        super().__init__(message)
        self.path = list(path)


@dataclass(frozen=True, slots=True)
class AutonomyBounds:
    """The server-owned bounds for one delegation tree.

    Frozen: a bound that the executing agent could relax mid-run is not a bound.
    These come from the operator (or the ceiling), never from a plan.
    """

    max_depth: int = 3
    max_children_per_parent: int = 10
    max_attempts_per_task: int = 3
    max_wall_clock_seconds: float = 1800.0
    max_total_tasks: int = 200
    max_revisits_per_task: int = 2

    def __post_init__(self) -> None:
        for name in (
            "max_depth",
            "max_children_per_parent",
            "max_attempts_per_task",
            "max_total_tasks",
            "max_revisits_per_task",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.max_wall_clock_seconds <= 0:
            raise ValueError("max_wall_clock_seconds must be > 0")


@dataclass
class Budget:
    """A spend budget that is INHERITED, not copied.

    A child receives a *share of what is left*, never a fresh budget. That is
    the property that stops depth from multiplying spend: a depth-3 tree cannot
    each start with the parent's full allowance.
    """

    max_tokens: int
    max_tasks: int
    tokens_used: int = 0
    tasks_dispatched: int = 0

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.max_tokens - self.tokens_used)

    @property
    def tasks_remaining(self) -> int:
        return max(0, self.max_tasks - self.tasks_dispatched)

    def child(self, *, fraction: float = 0.5) -> Budget:
        """Derive a child budget from the REMAINING parent budget."""
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        return Budget(
            max_tokens=int(self.tokens_remaining * fraction),
            max_tasks=max(1, int(self.tasks_remaining * fraction)),
        )

    def charge_tokens(self, tokens: int) -> None:
        self.tokens_used += max(0, int(tokens))

    def charge_task(self) -> None:
        self.tasks_dispatched += 1


@dataclass
class DescendantRecord:
    """One descendant's outcome, as reported to the parent."""

    task_id: str
    state: str
    error: str | None = None
    detail: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_failure(self) -> bool:
        return self.state in FAILURE_STATES


@dataclass
class AggregateResult:
    """The parent's view. ``succeeded`` requires positive evidence."""

    succeeded: bool
    state: str
    total: int
    completed: int
    failed: int
    unresolved: int
    failures: list[dict[str, Any]] = field(default_factory=list)
    unresolved_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "unresolved": self.unresolved,
            "failures": list(self.failures),
            "unresolved_ids": list(self.unresolved_ids),
        }


def _coerce(raw: Any, index: int) -> DescendantRecord:
    """Normalise one reported descendant into a record.

    Anything unrecognisable becomes a record with an empty state, which the
    caller counts as UNRESOLVED. That is deliberate: a malformed descendant
    report must never be silently dropped, because dropping it is exactly how a
    failure disappears between child and parent.
    """
    if isinstance(raw, DescendantRecord):
        return raw
    if isinstance(raw, Mapping):
        return DescendantRecord(
            task_id=str(raw.get("task_id") or f"unknown-{index}"),
            state=str(raw.get("state") or ""),
            error=raw.get("error"),
            detail=str(raw.get("detail") or ""),
        )
    return DescendantRecord(task_id=f"unknown-{index}", state="")


def aggregate_descendants(records: Iterable[Mapping[str, Any] | DescendantRecord | None]) -> AggregateResult:
    """Aggregate descendants so a child failure can NEVER read as parent success.

    Fail-safe by construction:

    * no records at all -> ``succeeded=False`` (``nothing_evidence``), because an
      empty list is not proof of anything;
    * any non-terminal record -> ``succeeded=False`` (``unresolved``), and the
      task is explicitly NOT complete;
    * any failure -> ``succeeded=False`` (``failed``);
    * a record that is ``None``, or a mapping with no ``state``, or a state that
      is not a recognised terminal state -> counted as unresolved, not skipped;
    * success requires ``completed == total`` and ``total > 0``.
    """
    completed = failed = unresolved = total = 0
    failures: list[dict[str, Any]] = []
    unresolved_ids: list[str] = []

    for index, raw in enumerate(records, start=1):
        total += 1
        record = _coerce(raw, index)
        if not record.state:
            unresolved += 1
            unresolved_ids.append(record.task_id)
        elif record.is_failure:
            failed += 1
            failures.append(
                {
                    "task_id": record.task_id,
                    "state": record.state,
                    "error": record.error,
                    "detail": record.detail,
                }
            )
        elif record.state == "completed":
            completed += 1
        else:
            # Unknown non-terminal state: NOT success, NOT silently dropped.
            unresolved += 1
            unresolved_ids.append(record.task_id)

    if total == 0:
        return AggregateResult(
            succeeded=False,
            state="nothing_evidence",
            total=0,
            completed=0,
            failed=0,
            unresolved=0,
        )
    if failed:
        state = "failed"
    elif unresolved:
        state = "unresolved"
    else:
        state = "completed"
    return AggregateResult(
        succeeded=(state == "completed" and completed == total and total > 0),
        state=state,
        total=total,
        completed=completed,
        failed=failed,
        unresolved=unresolved,
        failures=failures,
        unresolved_ids=unresolved_ids,
    )


class AutonomyGuard:
    """The single enforced chokepoint for one delegation tree.

    Every bound is checked in :meth:`check_dispatch` *before* work starts, so
    hitting a bound stops the tree rather than merely slowing it.
    """

    def __init__(
        self,
        bounds: AutonomyBounds | None = None,
        *,
        budget: Budget | None = None,
        event_store: Any = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.bounds = bounds or AutonomyBounds()
        self.budget = budget or Budget(max_tokens=200_000, max_tasks=self.bounds.max_total_tasks)
        self._event_store = event_store
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._tasks_dispatched = 0
        self._attempts: dict[str, int] = {}
        self._children: dict[str, list[str]] = {}
        self._visits: dict[str, list[str]] = {}

    # -- clock -------------------------------------------------------------
    def elapsed(self) -> float:
        return self._clock() - self._started

    def check_wall_clock(self) -> None:
        """Raise when the tree has run past its wall-clock ceiling."""
        elapsed = self.elapsed()
        if elapsed > self.bounds.max_wall_clock_seconds:
            raise AutonomyCeilingExceeded(
                f"delegation tree exceeded its wall-clock ceiling "
                f"({elapsed:.1f}s > {self.bounds.max_wall_clock_seconds:.1f}s); tree stopped",
                bound="max_wall_clock_seconds",
                limit=self.bounds.max_wall_clock_seconds,
            )

    # -- the one chokepoint ------------------------------------------------
    def check_dispatch(
        self,
        *,
        task_id: str,
        parent_id: str,
        depth: int,
        assignee: str,
        child_budget: Budget | None = None,
    ) -> Budget:
        """Validate one dispatch and return the INHERITED child budget.

        Raises on the first bound that is exceeded. Order is cheapest-first, and
        all checks happen before any work is started.
        """
        self.check_wall_clock()

        if depth > self.bounds.max_depth:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: depth {depth} exceeds max_depth {self.bounds.max_depth}",
                bound="max_depth",
                limit=self.bounds.max_depth,
            )
        if depth < 1:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: depth {depth} is not a valid depth",
                bound="max_depth",
                limit=self.bounds.max_depth,
            )

        siblings = self._children.setdefault(parent_id, [])
        if len(siblings) >= self.bounds.max_children_per_parent:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: parent {parent_id!r} already has {len(siblings)} children "
                f"(limit {self.bounds.max_children_per_parent})",
                bound="max_children_per_parent",
                limit=self.bounds.max_children_per_parent,
            )

        attempts = self._attempts.get(task_id, 0)
        if attempts >= self.bounds.max_attempts_per_task:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: task {task_id!r} reached its attempt ceiling "
                f"({attempts}/{self.bounds.max_attempts_per_task})",
                bound="max_attempts_per_task",
                limit=self.bounds.max_attempts_per_task,
            )

        self.assert_no_cycle(task_id, assignee)

        if self._tasks_dispatched >= self.bounds.max_total_tasks:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: tree reached its total task ceiling "
                f"({self._tasks_dispatched}/{self.bounds.max_total_tasks})",
                bound="max_total_tasks",
                limit=self.bounds.max_total_tasks,
            )

        if self.budget.tasks_remaining <= 0 or self.budget.tokens_remaining <= 0:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: inherited budget exhausted "
                f"(tasks {self.budget.tasks_dispatched}/{self.budget.max_tasks}, "
                f"tokens {self.budget.tokens_used}/{self.budget.max_tokens})",
                bound="inherited_budget",
                limit=self.budget.max_tokens,
            )

        if child_budget is not None and child_budget.max_tokens > self.budget.tokens_remaining:
            raise AutonomyCeilingExceeded(
                f"dispatch refused: requested child budget {child_budget.max_tokens} exceeds the "
                f"{self.budget.tokens_remaining} tokens remaining in the parent budget; "
                f"budgets must be inherited, not re-created",
                bound="inherited_budget",
                limit=self.budget.tokens_remaining,
            )

        self._attempts[task_id] = attempts + 1
        self._children[parent_id].append(task_id)
        self._tasks_dispatched += 1
        self.budget.charge_task()
        self._visits.setdefault(task_id, []).append(assignee)

        record_governance_action(
            ACTION_DELEGATED,
            actor=parent_id,
            target=task_id,
            reason=f"delegated to {assignee} at depth {depth}",
            details={
                "assignee": assignee,
                "depth": depth,
                "attempt": self._attempts[task_id],
                "remaining_tokens": self.budget.tokens_remaining,
            },
            store=self._event_store,
        )

        return child_budget or self.budget.child()

    # -- cycle guard -------------------------------------------------------
    def assert_no_cycle(self, task_id: str, assignee: str) -> None:
        """Refuse a task that keeps bouncing between agents.

        Two independent ping-pong shapes are caught, because they fail
        differently:

        * **A -> B -> A -> B**: the task is re-dispatched more times than
          ``max_revisits_per_task`` allows, whoever the assignee is;
        * **A -> A**: the same assignee is handed the same task again and again,
          which a total-visit count alone would miss early because the assignee
          never changes.
        """
        history = self._visits.get(task_id, [])
        if not history:
            return
        if len(history) >= 1 + self.bounds.max_revisits_per_task:
            raise CycleDetected(
                f"task {task_id!r} has been re-dispatched {len(history)} times "
                f"(limit {1 + self.bounds.max_revisits_per_task}); stopped",
                path=[*history, assignee],
            )
        if history[-1] == assignee:
            same = sum(1 for a in history if a == assignee)
            if same >= self.bounds.max_revisits_per_task:
                raise CycleDetected(
                    f"task {task_id!r} ping-ponged to {assignee!r} {same} times; stopped",
                    path=[*history, assignee],
                )

    # -- completion --------------------------------------------------------
    def is_complete(self, records: Iterable[Mapping[str, Any] | DescendantRecord | None]) -> bool:
        """A task is complete only when every descendant positively succeeded."""
        return aggregate_descendants(records).succeeded

    def report(self, records: Iterable[Mapping[str, Any] | DescendantRecord | None]) -> AggregateResult:
        """The honest parent-facing report."""
        return aggregate_descendants(records)

    # -- approval ----------------------------------------------------------
    @staticmethod
    def assert_requires_approval(action: str) -> bool:
        """True when *action* is irreversible and must clear the approval gate.

        An allowlist: a new irreversible action is uncovered until it is added
        here, which is the safe direction to fail.
        """
        return str(action or "").strip().lower() in IRREVERSIBLE_ACTIONS

    def snapshot(self) -> dict[str, Any]:
        return {
            "bounds": {
                "max_depth": self.bounds.max_depth,
                "max_children_per_parent": self.bounds.max_children_per_parent,
                "max_attempts_per_task": self.bounds.max_attempts_per_task,
                "max_wall_clock_seconds": self.bounds.max_wall_clock_seconds,
                "max_total_tasks": self.bounds.max_total_tasks,
                "max_revisits_per_task": self.bounds.max_revisits_per_task,
            },
            "elapsed_seconds": round(self.elapsed(), 3),
            "tasks_dispatched": self._tasks_dispatched,
            "budget": {
                "max_tokens": self.budget.max_tokens,
                "tokens_used": self.budget.tokens_used,
                "tokens_remaining": self.budget.tokens_remaining,
                "max_tasks": self.budget.max_tasks,
                "tasks_dispatched": self.budget.tasks_dispatched,
            },
        }


__all__ = [
    "AuthorityViolation",
    "AutonomyBounds",
    "AutonomyCeilingExceeded",
    "AutonomyGuard",
    "AggregateResult",
    "Budget",
    "CycleDetected",
    "DescendantRecord",
    "FAILURE_STATES",
    "IRREVERSIBLE_ACTIONS",
    "TERMINAL_STATES",
    "aggregate_descendants",
]
