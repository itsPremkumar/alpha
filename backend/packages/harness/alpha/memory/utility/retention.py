"""Pure retention recommendations driven by utility and host budget pressure.

This module has no store or host-memory handle.  It classifies records into
keep/demote/evict/quarantine bands and returns numbers plus reasons; applying an
evict recommendation remains the caller's authorized responsibility.  A record
with contradictory evidence is always quarantined before score bands are
considered, so conflict review cannot be bypassed by a high score.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .config import UtilityConfig
from .models import (
    BudgetState,
    FeedbackPolicy,
    PolicyDecision,
    RetentionAction,
    RetentionDecision,
    UtilityRecord,
)


def _config(value: UtilityConfig | Mapping[str, Any] | None) -> UtilityConfig:
    if value is None:
        return UtilityConfig()
    if isinstance(value, UtilityConfig):
        return value
    return UtilityConfig.from_mapping(value)


def _budget(value: BudgetState | Mapping[str, Any] | None) -> BudgetState:
    if value is None:
        return BudgetState()
    if isinstance(value, BudgetState):
        return value
    return BudgetState.model_validate(dict(value))


def _record(value: UtilityRecord | Mapping[str, Any]) -> UtilityRecord:
    if isinstance(value, UtilityRecord):
        return value
    return UtilityRecord.model_validate(dict(value))


def _policy(config: UtilityConfig, override: FeedbackPolicy | Mapping[str, Any] | None) -> FeedbackPolicy:
    if isinstance(override, FeedbackPolicy):
        return override
    if isinstance(override, Mapping):
        return FeedbackPolicy.model_validate(dict(override))
    return FeedbackPolicy(
        keep_threshold=config.keep_threshold,
        demote_threshold=config.demote_threshold,
        evict_threshold=config.evict_threshold,
        demote_budget_pressure=config.demote_budget_pressure,
        evict_budget_pressure=config.evict_budget_pressure,
    )


def decide_retention(
    record: UtilityRecord | Mapping[str, Any],
    *,
    budget_state: BudgetState | Mapping[str, Any] | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    policy: FeedbackPolicy | Mapping[str, Any] | None = None,
) -> RetentionDecision:
    """Classify one record without changing any data."""

    active_config = _config(config)
    active_policy = _policy(active_config, policy)
    active_budget = _budget(budget_state)
    current = _record(record)
    contradictory = current.contradiction_count > 0 or current.event_counts.get("contradicted", 0) > 0
    pressure = active_budget.pressure
    utility = current.score

    if contradictory and active_policy.quarantine_on_contradiction:
        action = RetentionAction.QUARANTINE
        threshold = active_policy.evict_threshold
        reason = f"contradictory evidence: contradiction_count={current.contradiction_count}; utility={utility:.6f}; quarantine_threshold=contradiction>0; no deletion performed"
    elif utility >= active_policy.keep_threshold:
        threshold = active_policy.keep_threshold
        if pressure >= active_policy.evict_budget_pressure:
            action = RetentionAction.EVICT
            reason = f"keep band overridden by budget pressure={pressure:.6f} >= evict_budget_pressure={active_policy.evict_budget_pressure:.6f}; utility={utility:.6f}"
        elif pressure >= active_policy.demote_budget_pressure:
            action = RetentionAction.DEMOTE
            reason = f"keep band softened by budget pressure={pressure:.6f} >= demote_budget_pressure={active_policy.demote_budget_pressure:.6f}; utility={utility:.6f}"
        else:
            action = RetentionAction.KEEP
            reason = f"keep band: utility={utility:.6f} >= keep_threshold={active_policy.keep_threshold:.6f}; budget_pressure={pressure:.6f}"
    elif utility >= active_policy.evict_threshold:
        threshold = active_policy.evict_threshold if utility < active_policy.demote_threshold else active_policy.demote_threshold
        if pressure >= active_policy.evict_budget_pressure:
            action = RetentionAction.EVICT
            reason = f"demote band promoted by budget pressure={pressure:.6f} >= evict_budget_pressure={active_policy.evict_budget_pressure:.6f}; utility={utility:.6f}"
        else:
            action = RetentionAction.DEMOTE
            if utility >= active_policy.demote_threshold:
                reason = f"demote band: {active_policy.demote_threshold:.6f} <= utility={utility:.6f} < keep_threshold={active_policy.keep_threshold:.6f}; budget_pressure={pressure:.6f}"
            else:
                reason = f"low-confidence demote band: evict_threshold={active_policy.evict_threshold:.6f} <= utility={utility:.6f} < demote_threshold={active_policy.demote_threshold:.6f}; budget_pressure={pressure:.6f}"
    else:
        threshold = active_policy.evict_threshold
        action = RetentionAction.EVICT
        reason = f"evict band: utility={utility:.6f} < evict_threshold={active_policy.evict_threshold:.6f}; budget_pressure={pressure:.6f}; host authorization required"

    return RetentionDecision(
        record_id=current.record_id,
        action=action,
        reason=reason,
        utility=utility,
        threshold=threshold,
        budget_pressure=pressure,
        disclosure=("heuristic recommendation; contradictory records are quarantined and no record is deleted by this module"),
    )


def retention_decisions(
    records: Iterable[UtilityRecord | Mapping[str, Any]],
    *,
    budget_state: BudgetState | Mapping[str, Any] | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    policy: FeedbackPolicy | Mapping[str, Any] | None = None,
) -> list[RetentionDecision]:
    """Return deterministic recommendations sorted by record id."""

    parsed = sorted((_record(item) for item in records), key=lambda item: item.record_id)
    return [decide_retention(item, budget_state=budget_state, config=config, policy=policy) for item in parsed]


def policy_decision(
    record: UtilityRecord | Mapping[str, Any],
    *,
    budget_state: BudgetState | Mapping[str, Any] | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    policy: FeedbackPolicy | Mapping[str, Any] | None = None,
) -> PolicyDecision:
    """Return the same decision in the policy-envelope contract."""

    decision = decide_retention(record, budget_state=budget_state, config=config, policy=policy)
    return PolicyDecision(
        record_id=decision.record_id,
        action=decision.action,
        reason=decision.reason,
        utility=decision.utility,
        threshold=decision.threshold,
        budget_pressure=decision.budget_pressure,
        disclosure=decision.disclosure,
    )


class RetentionPolicy:
    """Reusable pure policy object for host adapters."""

    def __init__(
        self,
        config: UtilityConfig | Mapping[str, Any] | None = None,
        *,
        policy: FeedbackPolicy | Mapping[str, Any] | None = None,
    ) -> None:
        self.config = _config(config)
        self.policy = _policy(self.config, policy)

    def decide(
        self,
        record: UtilityRecord | Mapping[str, Any],
        *,
        budget_state: BudgetState | Mapping[str, Any] | None = None,
    ) -> RetentionDecision:
        return decide_retention(record, budget_state=budget_state, config=self.config, policy=self.policy)

    def decisions(
        self,
        records: Iterable[UtilityRecord | Mapping[str, Any]],
        *,
        budget_state: BudgetState | Mapping[str, Any] | None = None,
    ) -> list[RetentionDecision]:
        return retention_decisions(records, budget_state=budget_state, config=self.config, policy=self.policy)

    def policy_decision(
        self,
        record: UtilityRecord | Mapping[str, Any],
        *,
        budget_state: BudgetState | Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        return policy_decision(record, budget_state=budget_state, config=self.config, policy=self.policy)


def retention_decision(
    record: UtilityRecord | Mapping[str, Any],
    *,
    budget_state: BudgetState | Mapping[str, Any] | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
) -> RetentionDecision:
    return decide_retention(record, budget_state=budget_state, config=config)


decide = decide_retention


__all__ = [
    "decide",
    "RetentionPolicy",
    "decide_retention",
    "policy_decision",
    "retention_decision",
    "retention_decisions",
]
