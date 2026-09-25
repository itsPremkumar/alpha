"""Deterministic, proposal-only retention policy.

Retention is a decision, not a deletion.  A store supplies a size/count
report and a budget; this module returns ``KEEP``, ``DEMOTE``, or ``EVICT``
decisions with a reason.  The store remains the only component authorized to
perform the corresponding operation, and a caller that does not own that
operation can still use the report for planning and capacity disclosure.

The ranking is intentionally simple and total: pinned records are never
proposed for eviction, then importance ascending, then oldest first, then
key.  There is no sampling, randomness, wall-clock lookup, or filesystem
mutation.  Repeating the same report and budget therefore produces the same
decisions, which makes eviction auditable and testable.

:class:`GlobalRetentionBudget` adds the cross-store guard needed when many
stores share one disk.  Stores request bytes in a deterministic name order and
receive an explicit allocation; an over-budget request is reported as
unfilled rather than silently borrowing from a reserve or another store.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "GlobalBudgetReport",
    "GlobalRetentionBudget",
    "RetentionAction",
    "RetentionBudget",
    "RetentionCandidate",
    "RetentionDecision",
    "RetentionItem",
    "RetentionPolicy",
    "RetentionReport",
    "decide_retention",
    "plan_global_retention",
    "plan_retention",
]


class RetentionAction(StrEnum):
    KEEP = "keep"
    DEMOTE = "demote"
    EVICT = "evict"


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    """Injected size/count facts about one persisted item."""

    key: str
    size_bytes: int
    age_seconds: float = 0.0
    importance: float = 0.0
    pinned: bool = False
    count: int = 1
    tier: str = "active"

    def __post_init__(self) -> None:
        if not str(self.key):
            raise ValueError("retention candidate key must be non-empty")
        if int(self.size_bytes) < 0 or int(self.count) < 1:
            raise ValueError("retention candidate size/count must be non-negative/positive")
        object.__setattr__(self, "size_bytes", int(self.size_bytes))
        object.__setattr__(self, "count", int(self.count))
        object.__setattr__(self, "age_seconds", float(self.age_seconds))
        object.__setattr__(self, "importance", float(self.importance))
        object.__setattr__(self, "pinned", bool(self.pinned))


RetentionItem = RetentionCandidate


@dataclass(frozen=True, slots=True)
class RetentionBudget:
    """Per-store budget; ``None`` means unbounded for that dimension."""

    max_bytes: int | None = None
    max_count: int | None = None
    reserve_bytes: int = 0

    def __post_init__(self) -> None:
        if self.max_bytes is not None and int(self.max_bytes) < 0:
            raise ValueError("max_bytes must be non-negative or None")
        if self.max_count is not None and int(self.max_count) < 0:
            raise ValueError("max_count must be non-negative or None")
        if int(self.reserve_bytes) < 0:
            raise ValueError("reserve_bytes must be non-negative")
        if self.max_bytes is not None and int(self.reserve_bytes) > int(self.max_bytes):
            raise ValueError("reserve_bytes must not exceed max_bytes")
        object.__setattr__(self, "max_bytes", None if self.max_bytes is None else int(self.max_bytes))
        object.__setattr__(self, "max_count", None if self.max_count is None else int(self.max_count))
        object.__setattr__(self, "reserve_bytes", int(self.reserve_bytes))


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    """One deterministic proposal."""

    key: str
    action: RetentionAction
    reason: str
    size_bytes: int
    age_seconds: float
    importance: float
    tier: str
    rank: int

    @property
    def keep(self) -> bool:
        return self.action is RetentionAction.KEEP

    @property
    def demote(self) -> bool:
        return self.action is RetentionAction.DEMOTE

    @property
    def evict(self) -> bool:
        return self.action is RetentionAction.EVICT

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "action": self.action.value,
            "reason": self.reason,
            "size_bytes": self.size_bytes,
            "tier": self.tier,
            "rank": self.rank,
        }


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """Complete proposal plus capacity disclosure."""

    decisions: tuple[RetentionDecision, ...]
    total_bytes: int
    total_count: int
    budget_bytes: int | None
    budget_count: int | None
    kept_bytes: int
    kept_count: int
    evicted_bytes: int
    demoted_bytes: int
    over_budget: bool
    reason: str = ""

    def __iter__(self):
        return iter(self.decisions)

    def __len__(self) -> int:
        return len(self.decisions)

    def by_action(self, action: RetentionAction | str) -> tuple[RetentionDecision, ...]:
        wanted = RetentionAction(action)
        return tuple(decision for decision in self.decisions if decision.action is wanted)

    @property
    def keys_to_keep(self) -> tuple[str, ...]:
        return tuple(decision.key for decision in self.decisions if decision.action is RetentionAction.KEEP)

    @property
    def keys_to_evict(self) -> tuple[str, ...]:
        return tuple(decision.key for decision in self.decisions if decision.action is RetentionAction.EVICT)

    @property
    def keys_to_demote(self) -> tuple[str, ...]:
        return tuple(decision.key for decision in self.decisions if decision.action is RetentionAction.DEMOTE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "total_count": self.total_count,
            "budget_bytes": self.budget_bytes,
            "budget_count": self.budget_count,
            "kept_bytes": self.kept_bytes,
            "kept_count": self.kept_count,
            "evicted_bytes": self.evicted_bytes,
            "demoted_bytes": self.demoted_bytes,
            "over_budget": self.over_budget,
            "reason": self.reason,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Pure policy turning candidate reports into decisions.

    ``demote_importance_at_or_below`` and ``demote_age_seconds`` are optional
    signals.  A demotion is informational: it proposes a cheaper tier but does
    not by itself free bytes, so an over-budget report still evicts when
    necessary.
    """

    budget: RetentionBudget = RetentionBudget()
    demote_importance_at_or_below: float | None = None
    demote_age_seconds: float | None = None

    def propose(
        self,
        candidates: Iterable[RetentionCandidate | Mapping[str, Any]],
        *,
        budget: RetentionBudget | None = None,
    ) -> RetentionReport:
        return plan_retention(candidates, budget or self.budget, policy=self)

    def decide(
        self,
        candidates: Iterable[RetentionCandidate | Mapping[str, Any]],
        *,
        budget: RetentionBudget | None = None,
    ) -> RetentionReport:
        return self.propose(candidates, budget=budget)


def _candidate(value: RetentionCandidate | Mapping[str, Any]) -> RetentionCandidate:
    if isinstance(value, RetentionCandidate):
        return copy.copy(value)
    if isinstance(value, Mapping):
        return RetentionCandidate(
            key=str(value.get("key", value.get("id", ""))),
            size_bytes=int(value.get("size_bytes", value.get("bytes", 0))),
            age_seconds=float(value.get("age_seconds", value.get("age", 0.0))),
            importance=float(value.get("importance", 0.0)),
            pinned=bool(value.get("pinned", False)),
            count=int(value.get("count", 1)),
            tier=str(value.get("tier", "active")),
        )
    raise TypeError("retention candidates must be RetentionCandidate or mapping")


def plan_retention(
    candidates: Iterable[RetentionCandidate | Mapping[str, Any]],
    budget: RetentionBudget | Mapping[str, Any] | None = None,
    *,
    policy: RetentionPolicy | None = None,
) -> RetentionReport:
    """Return deterministic keep/demote/evict proposals for one scope."""

    if budget is None:
        selected_budget = RetentionBudget()
    elif isinstance(budget, RetentionBudget):
        selected_budget = budget
    elif isinstance(budget, Mapping):
        selected_budget = RetentionBudget(**dict(budget))
    else:
        raise TypeError("budget must be RetentionBudget, mapping, or None")
    active_policy = policy or RetentionPolicy(selected_budget)
    items = [_candidate(value) for value in candidates]
    keys = [item.key for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError("retention candidate keys must be unique")
    items.sort(key=lambda item: item.key)
    total_bytes = sum(item.size_bytes for item in items)
    total_count = sum(item.count for item in items)
    effective_bytes = None if selected_budget.max_bytes is None else max(0, selected_budget.max_bytes - selected_budget.reserve_bytes)
    effective_count = selected_budget.max_count
    evictable = [item for item in items if not item.pinned]
    # Least valuable first: low importance, then oldest, then a stable key.
    evictable.sort(key=lambda item: (item.importance, -item.age_seconds, item.tier, item.key))
    decisions: dict[str, RetentionDecision] = {}
    rank = 0
    for item in items:
        if item.pinned:
            decisions[item.key] = RetentionDecision(
                item.key,
                RetentionAction.KEEP,
                "pinned",
                item.size_bytes,
                item.age_seconds,
                item.importance,
                item.tier,
                rank,
            )
        else:
            decisions[item.key] = RetentionDecision(
                item.key,
                RetentionAction.KEEP,
                "within_budget",
                item.size_bytes,
                item.age_seconds,
                item.importance,
                item.tier,
                rank,
            )
        rank += 1

    kept_bytes = total_bytes
    kept_count = total_count
    evicted: list[RetentionCandidate] = []
    for item in evictable:
        bytes_over = effective_bytes is not None and kept_bytes > effective_bytes
        count_over = effective_count is not None and kept_count > effective_count
        if not bytes_over and not count_over:
            break
        evicted.append(item)
        kept_bytes -= item.size_bytes
        kept_count -= item.count
    for item in evicted:
        current = decisions[item.key]
        decisions[item.key] = RetentionDecision(
            current.key,
            RetentionAction.EVICT,
            "deterministic_capacity_eviction",
            current.size_bytes,
            current.age_seconds,
            current.importance,
            current.tier,
            current.rank,
        )

    for item in items:
        if item.pinned or item.key in {candidate.key for candidate in evicted}:
            continue
        should_demote = (active_policy.demote_importance_at_or_below is not None and item.importance <= active_policy.demote_importance_at_or_below) or (
            active_policy.demote_age_seconds is not None and item.age_seconds >= active_policy.demote_age_seconds
        )
        if not should_demote:
            continue
        current = decisions[item.key]
        decisions[item.key] = RetentionDecision(
            current.key,
            RetentionAction.DEMOTE,
            "low_value_or_age_signal",
            current.size_bytes,
            current.age_seconds,
            current.importance,
            current.tier,
            current.rank,
        )

    ordered = tuple(decisions[item.key] for item in items)
    demoted_bytes = sum(decision.size_bytes for decision in ordered if decision.action is RetentionAction.DEMOTE)
    over_budget = (effective_bytes is not None and kept_bytes > effective_bytes) or (effective_count is not None and kept_count > effective_count)
    reason = ""
    if over_budget:
        reason = "pinned_or_unevictable_content_exceeds_budget"
    elif evicted:
        reason = "bounded_capacity"
    elif any(decision.action is RetentionAction.DEMOTE for decision in ordered):
        reason = "demotion_proposed"
    else:
        reason = "within_budget"
    return RetentionReport(
        decisions=ordered,
        total_bytes=total_bytes,
        total_count=total_count,
        budget_bytes=selected_budget.max_bytes,
        budget_count=selected_budget.max_count,
        kept_bytes=kept_bytes,
        kept_count=kept_count,
        evicted_bytes=sum(item.size_bytes for item in evicted),
        demoted_bytes=demoted_bytes,
        over_budget=over_budget,
        reason=reason,
    )


def decide_retention(
    candidates: Iterable[RetentionCandidate | Mapping[str, Any]],
    budget: RetentionBudget | Mapping[str, Any] | None = None,
    *,
    policy: RetentionPolicy | None = None,
) -> RetentionReport:
    """Readable alias for :func:`plan_retention`."""

    return plan_retention(candidates, budget, policy=policy)


@dataclass(frozen=True, slots=True)
class GlobalBudgetReport:
    """Per-store allocation under one shared disk budget."""

    budget_bytes: int
    reserve_bytes: int
    allocations: Mapping[str, int]
    unfilled: Mapping[str, int]
    total_allocated: int
    over_budget: bool

    @property
    def remaining(self) -> int:
        return max(0, self.budget_bytes - self.reserve_bytes - self.total_allocated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_bytes": self.budget_bytes,
            "reserve_bytes": self.reserve_bytes,
            "allocations": dict(self.allocations),
            "unfilled": dict(self.unfilled),
            "total_allocated": self.total_allocated,
            "remaining": self.remaining,
            "over_budget": self.over_budget,
        }


@dataclass(frozen=True, slots=True)
class GlobalRetentionBudget:
    """Shared byte ceiling for any number of independent stores."""

    budget_bytes: int
    reserve_bytes: int = 0

    def __post_init__(self) -> None:
        if int(self.budget_bytes) < 0 or int(self.reserve_bytes) < 0:
            raise ValueError("global budget values must be non-negative")
        if int(self.reserve_bytes) > int(self.budget_bytes):
            raise ValueError("reserve_bytes must not exceed budget_bytes")
        object.__setattr__(self, "budget_bytes", int(self.budget_bytes))
        object.__setattr__(self, "reserve_bytes", int(self.reserve_bytes))

    def allocate(self, requests: Mapping[str, int]) -> GlobalBudgetReport:
        """Allocate requested bytes in lexical store-name order."""

        return plan_global_retention(requests, self)


def plan_global_retention(
    requests: Mapping[str, int],
    budget: GlobalRetentionBudget | Mapping[str, Any] | int,
) -> GlobalBudgetReport:
    """Deterministically allocate a shared disk budget across stores.

    Sequential lexical allocation is chosen over implicit proportional
    fairness because it is explainable and stable as stores come and go.  A
    store that receives less than it asked for can apply its own per-store
    retention policy to the returned allocation.
    """

    if isinstance(budget, GlobalRetentionBudget):
        active = budget
    elif isinstance(budget, Mapping):
        active = GlobalRetentionBudget(**dict(budget))
    else:
        active = GlobalRetentionBudget(int(budget))
    available = max(0, active.budget_bytes - active.reserve_bytes)
    allocations: dict[str, int] = {}
    unfilled: dict[str, int] = {}
    for name in sorted(requests):
        requested = max(0, int(requests[name]))
        granted = min(requested, available)
        allocations[name] = granted
        if granted < requested:
            unfilled[name] = requested - granted
        available -= granted
    return GlobalBudgetReport(
        budget_bytes=active.budget_bytes,
        reserve_bytes=active.reserve_bytes,
        allocations=allocations,
        unfilled=unfilled,
        total_allocated=sum(allocations.values()),
        over_budget=bool(unfilled),
    )
