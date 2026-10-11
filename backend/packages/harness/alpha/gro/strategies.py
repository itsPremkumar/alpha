"""GRO strategy catalogue: distinct, reviewable approaches per task type.

Strategies are **data, not generated shell** (plan section 11): an approach is
a bounded instruction line plus the verification signals it should produce.
``select_strategies`` deals distinct strategy ids only — a candidate group of
three identical strategies wastes the entire point of group-relative
comparison, so a duplicate is refused, not deduped silently.

The catalogue is deliberately small (7 coding / 5 research / 5 planning+tools).
Do not add strategies before the existing ones are measured: a strategy that
never wins a group is a budget line, not an option.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from alpha.gro.models import StrategySpec, TaskType

#: Ordered, reviewable catalogue (plan section 11). Order is deal order.
CODING_STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        strategy_id="coding.minimal_patch",
        task_type=TaskType.CODING,
        approach="Inspect the failure and change as little as possible; keep the diff minimal and targeted.",
        signals="targeted tests, regression tests, valid diff",
    ),
    StrategySpec(
        strategy_id="coding.root_cause_first",
        task_type=TaskType.CODING,
        approach="Trace the call path and state a testable root-cause hypothesis before editing anything.",
        signals="reproduced failure, hypothesis matches evidence, tests pass",
    ),
    StrategySpec(
        strategy_id="coding.test_first",
        task_type=TaskType.CODING,
        approach="Identify or add regression coverage first; the new test fails before the fix and passes after.",
        signals="new test fails before patch and passes after, when practical",
    ),
    StrategySpec(
        strategy_id="coding.static_analysis_first",
        task_type=TaskType.CODING,
        approach="Run lint/type/static checks before deep edits and let their output direct the change.",
        signals="static checks clean and test suite green",
    ),
    StrategySpec(
        strategy_id="coding.alternate_design",
        task_type=TaskType.CODING,
        approach="Explore a genuinely distinct implementation, justified by the constraints; satisfy the same acceptance tests.",
        signals="same acceptance tests, complexity policy, migration safety",
    ),
    StrategySpec(
        strategy_id="coding.repair_from_failure",
        task_type=TaskType.CODING,
        approach="Start from the structured verifier output and repair only what is proven to fail; no unrelated changes.",
        signals="previously failing gates now pass; no new regression",
    ),
    StrategySpec(
        strategy_id="coding.independent_review",
        task_type=TaskType.CODING,
        approach="Review the existing work for real defects and confirm findings with reproducible checks; do not rewrite working code.",
        signals="finds real issues or confirms via reproducible checks",
    ),
)

RESEARCH_STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        strategy_id="research.broad_then_narrow",
        task_type=TaskType.RESEARCH,
        approach="Map the topic broadly first, then inspect the most authoritative sources in depth.",
        signals="relevant primary sources, coverage, source dates",
    ),
    StrategySpec(
        strategy_id="research.primary_sources_first",
        task_type=TaskType.RESEARCH,
        approach="Prefer primary sources: papers, official documentation, repositories, specifications.",
        signals="claims trace to direct sources",
    ),
    StrategySpec(
        strategy_id="research.contradiction_hunt",
        task_type=TaskType.RESEARCH,
        approach="Actively search for counterevidence and expert disagreement; record conflicts rather than hiding them.",
        signals="conflicts recorded, positions attributed",
    ),
    StrategySpec(
        strategy_id="research.independent_query",
        task_type=TaskType.RESEARCH,
        approach="Use different search terms and source paths from the obvious ones; avoid duplicating links.",
        signals="incremental source coverage",
    ),
    StrategySpec(
        strategy_id="research.fact_check",
        task_type=TaskType.RESEARCH,
        approach="Verify every material claim against a source; build an explicit claim-to-source support table and drop unsupported claims.",
        signals="claim-to-source support table, unsupported claims removed",
    ),
)

PLANNING_STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        strategy_id="plan.dependency_first",
        task_type=TaskType.PLANNING,
        approach="Identify prerequisites and blocked dependencies first; the schedule must respect them.",
        signals="dependency graph acyclic where expected; feasible schedule",
    ),
    StrategySpec(
        strategy_id="plan.milestone_first",
        task_type=TaskType.PLANNING,
        approach="Partition the work into milestones, each with a testable exit condition.",
        signals="every milestone has a testable exit condition",
    ),
    StrategySpec(
        strategy_id="tools.read_only_diagnosis",
        task_type=TaskType.PLANNING,
        approach="Inspect and diagnose current state before proposing any change; gather evidence without external mutation.",
        signals="evidence gathered without external mutation",
    ),
    StrategySpec(
        strategy_id="tools.preview_then_apply",
        task_type=TaskType.PLANNING,
        approach="Dry-run or stage every effect; apply nothing that has not been previewed and verified.",
        signals="preview matches intended changes",
    ),
    StrategySpec(
        strategy_id="tools.alternate_safe_path",
        task_type=TaskType.PLANNING,
        approach="After a retryable failure, select another allowed tool or path and validate the outcome explicitly.",
        signals="explicit failure reason and validated outcome",
    ),
)

#: Failure-directed repair strategy (plan section 14.3). Used for the bounded
#: repair round on any task type; its prompt carries the proven shortfall.
REPAIR_STRATEGY = StrategySpec(
    strategy_id="gro.repair_from_failure",
    task_type=TaskType.GENERAL,
    approach="Repair only what the recorded verification proved unmet; leave proven-correct work untouched.",
    signals="previously failing gates now hold; no new regression",
)

_BY_TYPE: dict[TaskType, tuple[StrategySpec, ...]] = {
    TaskType.CODING: CODING_STRATEGIES,
    TaskType.RESEARCH: RESEARCH_STRATEGIES,
    TaskType.PLANNING: PLANNING_STRATEGIES,
    TaskType.GENERAL: CODING_STRATEGIES + RESEARCH_STRATEGIES + PLANNING_STRATEGIES,
}

#: Deal order for GENERAL tasks: interleave categories so a general group is
#: not three coding strategies in a row.
_GENERAL_DEAL_ORDER: tuple[StrategySpec, ...] = tuple(strategy for group in itertools.zip_longest(CODING_STRATEGIES, RESEARCH_STRATEGIES, PLANNING_STRATEGIES) for strategy in group if strategy is not None)

_BY_TYPE[TaskType.GENERAL] = _GENERAL_DEAL_ORDER


def catalogue(task_type: TaskType) -> tuple[StrategySpec, ...]:
    """Deal-ordered catalogue for ``task_type``."""

    return _BY_TYPE[task_type]


def select_strategies(task_type: TaskType, count: int) -> tuple[StrategySpec, ...]:
    """Deal ``count`` distinct strategies for ``task_type``.

    Refused (never clamped): a count beyond the catalogue leaves no distinct
    strategies to deal, so returning a padded or duplicated list would hand a
    candidate group the illusion of diversity it does not have.
    """

    if count < 1:
        raise ValueError(f"candidate count must be >= 1 (got {count})")
    pool = catalogue(task_type)
    if count > len(pool):
        raise ValueError(f"requested {count} distinct strategies for {task_type.value} tasks but only {len(pool)} exist; lower the count or use a more specific task type")
    return pool[:count]


def strategies_by_id(strategy_ids: Sequence[str]) -> tuple[StrategySpec, ...]:
    """Resolve explicit strategy ids (operator/caller override), refusing unknowns."""

    known: dict[str, StrategySpec] = {}
    for pool in _BY_TYPE.values():
        for spec in pool:
            known.setdefault(spec.strategy_id, spec)
    known[REPAIR_STRATEGY.strategy_id] = REPAIR_STRATEGY
    resolved: list[StrategySpec] = []
    for strategy_id in strategy_ids:
        spec = known.get(strategy_id)
        if spec is None:
            raise ValueError(f"unknown GRO strategy id {strategy_id!r}; available: {', '.join(sorted(known))}")
        if any(existing.strategy_id == spec.strategy_id for existing in resolved):
            raise ValueError(f"strategy {spec.strategy_id!r} was requested twice; candidates must use distinct strategies")
        resolved.append(spec)
    return tuple(resolved)
