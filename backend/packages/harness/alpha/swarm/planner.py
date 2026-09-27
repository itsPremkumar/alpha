"""Candidate swarm-plan scoring and deterministic selection.

Producing exactly one decomposition and shipping it means the first shape that
occurs to the decomposer wins.  This module scores several candidate DAGs for
the *same* goal on the numbers that decide real makespan — exposed parallelism,
depth beyond the ideal floor, coordination complexity, and task inflation — and
picks a winner with an explicit, reproducible tie-break.

Scoring is pure arithmetic over :class:`~alpha.swarm.topology.DagFeatures`.  It
never calls a model, never reads the goal text, and never rewards width past the
plan's own concurrency limit: a 40-way fan-out on ``max_concurrency=4`` is four
rounds of work, not forty-way parallelism, and scoring it higher would be
rewarding fake parallelism.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from alpha.swarm.models import SwarmMode, SwarmPlan
from alpha.swarm.topology import DagFeatures, compute_dag_features

__all__ = [
    "PlanCandidate",
    "PlanScore",
    "score_plan",
    "select_best_candidate",
]

# Candidate generation is a bounded search, not an open-ended one: every extra
# candidate is another full decomposition, and beyond a handful the marginal
# plan rarely differs meaningfully for one goal.
MAX_CANDIDATES = 4

_WEIGHT_PARALLELISM = 0.45
_WEIGHT_DEPTH = 0.25
_WEIGHT_COMPLEXITY = 0.20
_WEIGHT_OVERHEAD = 0.10
_TOPOLOGY_ENDORSEMENT = 0.15


@dataclass(frozen=True)
class PlanScore:
    """One candidate's score, with every term kept so a rejection is auditable."""

    parallelism: float
    depth: float
    complexity: float
    overhead: float
    endorsed: bool
    total: float
    task_count: int
    critical_path: int
    exposed_width: int
    lower_bound_rounds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "parallelism": self.parallelism,
            "depth": self.depth,
            "complexity": self.complexity,
            "overhead": self.overhead,
            "endorsed": self.endorsed,
            "total": self.total,
            "task_count": self.task_count,
            "critical_path": self.critical_path,
            "exposed_width": self.exposed_width,
            "lower_bound_rounds": self.lower_bound_rounds,
        }


@dataclass(frozen=True)
class PlanCandidate:
    """A decomposed plan under consideration, with its measured shape."""

    mode: SwarmMode
    plan: SwarmPlan
    features: DagFeatures
    score: PlanScore

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "swarm_id": self.plan.swarm_id,
            "score": self.score.to_dict(),
            "features": self.features.to_dict(),
        }


def score_plan(plan: SwarmPlan, *, max_concurrency: int = 8, endorsed: bool = False) -> PlanScore:
    """Score how well a plan exposes parallel work without inflating overhead.

    ``parallelism`` is ``1 - 1/speedup`` for the plan's own lower-bound
    makespan, so a strictly serial plan scores 0 and unbounded parallelism
    scores 1.  ``depth`` penalises levels beyond the ideal floor
    ``max(depth, ceil(n/concurrency))``; ``complexity`` is mean out-degree; and
    ``overhead`` is task inflation relative to what concurrency can use.
    """

    features = compute_dag_features(plan.tasks)
    concurrency = max(1, int(max_concurrency))
    n = features.task_count
    if n <= 0:
        return PlanScore(0.0, 0.0, 0.0, 0.0, endorsed, 0.0, 0, 0, 0, 0)

    effective_concurrency = min(concurrency, n)
    ideal_floor = max(features.depth, math.ceil(n / effective_concurrency))
    serial_lower_bound = n
    speedup = serial_lower_bound / max(ideal_floor, 1)
    # Two distinct facts, deliberately not conflated: how many tasks can run
    # *right now* (capped by the plan's own concurrency), and the idealised
    # lower bound on rounds it still has to spend.  A 16-way fan-out on 4 slots
    # exposes the same instant width as a 4-way fan-out but needs 4 rounds, so
    # only the second number keeps it from being scored as "more parallel".
    exposed_width = min(features.max_width, effective_concurrency)

    parallelism = 1.0 - (1.0 / max(speedup, 1.0))
    excess_depth = max(0, features.depth - math.ceil(n / effective_concurrency))
    depth_term = 1.0 - min(1.0, excess_depth / n)
    mean_out_degree = (features.edge_count / n) if n else 0.0
    complexity_term = 1.0 - min(1.0, mean_out_degree / 2.0)
    overhead_term = 1.0 - min(1.0, max(0, n - effective_concurrency) / n)

    base = (
        _WEIGHT_PARALLELISM * parallelism
        + _WEIGHT_DEPTH * depth_term
        + _WEIGHT_COMPLEXITY * complexity_term
        + _WEIGHT_OVERHEAD * overhead_term
    )
    total = min(1.0, base + (_TOPOLOGY_ENDORSEMENT if endorsed else 0.0))
    return PlanScore(
        parallelism=round(parallelism, 4),
        depth=round(depth_term, 4),
        complexity=round(complexity_term, 4),
        overhead=round(overhead_term, 4),
        endorsed=endorsed,
        total=round(total, 4),
        task_count=n,
        critical_path=features.depth,
        exposed_width=exposed_width,
        lower_bound_rounds=ideal_floor,
    )


def build_candidate(plan: SwarmPlan, *, max_concurrency: int = 8, endorsed: bool = False) -> PlanCandidate:
    """Measure and score one plan so it can enter candidate selection."""

    features = compute_dag_features(plan.tasks)
    return PlanCandidate(
        mode=plan.mode,
        plan=plan,
        features=features,
        score=score_plan(plan, max_concurrency=max_concurrency, endorsed=endorsed),
    )


def select_best_candidate(candidates: Sequence[PlanCandidate]) -> PlanCandidate | None:
    """Pick the highest-scoring candidate with a fully deterministic tie-break.

    Ties are broken by fewer tasks (less coordination for the same score), then
    by mode name, so the same candidate set always yields the same winner and a
    regression test can assert an exact mode rather than "one of".
    """

    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            -candidate.score.total,
            candidate.score.task_count,
            candidate.score.critical_path,
            candidate.mode.value,
        ),
    )
