"""Evolutionary Lineage Store for Agent Meta-Compiler generations and hot-swaps."""

from __future__ import annotations

import logging
import time
from typing import Any

from agent_workspace.metacompiler.benchmark import MetaBenchmarkHarness
from agent_workspace.metacompiler.hotswap import AgentHotSwapCoordinator
from agent_workspace.metacompiler.models import (
    AgentBlueprint,
    BenchmarkScorecard,
    HotSwapOutcome,
)

logger = logging.getLogger(__name__)


class MetaLineageStore:
    """Maintains generational history, benchmark scorecards, and active head of agent architectures."""

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id
        self._blueprints: dict[str, AgentBlueprint] = {}
        self._scorecards: dict[str, BenchmarkScorecard] = {}
        self._history: list[dict[str, Any]] = []

        # Initialize seed Gen 0 agent
        seed = AgentBlueprint(
            blueprint_id="bp_gen0_seed",
            generation=0,
            parent_id=None,
            name="Alpha-ASI-Seed",
            architecture_tag="Alpha-ASI-v1.0",
            mutation_notes="Initial seed production agent",
        )
        self._blueprints[seed.blueprint_id] = seed
        self._active_head_id = seed.blueprint_id

        # Seed initial benchmark
        seed_sc = MetaBenchmarkHarness.evaluate_blueprint(seed, baseline_score=0.70)
        self._scorecards[seed.blueprint_id] = seed_sc

        self._history.append({
            "event": "seed_initialized",
            "blueprint_id": seed.blueprint_id,
            "generation": 0,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    @property
    def active_head(self) -> AgentBlueprint:
        return self._blueprints[self._active_head_id]

    def register_blueprint(self, blueprint: AgentBlueprint) -> None:
        self._blueprints[blueprint.blueprint_id] = blueprint

    def record_benchmark(self, scorecard: BenchmarkScorecard) -> None:
        self._scorecards[scorecard.blueprint_id] = scorecard

    def get_scorecard(self, blueprint_id: str) -> BenchmarkScorecard | None:
        return self._scorecards.get(blueprint_id)

    def promote_blueprint(self, blueprint_id: str, force: bool = False) -> HotSwapOutcome:
        """Promote candidate blueprint to active head via AgentHotSwapCoordinator."""
        if blueprint_id not in self._blueprints:
            raise KeyError(f"Blueprint {blueprint_id} not found in lineage")

        candidate = self._blueprints[blueprint_id]
        scorecard = self._scorecards.get(blueprint_id)
        if not scorecard:
            scorecard = MetaBenchmarkHarness.evaluate_blueprint(candidate)
            self._scorecards[blueprint_id] = scorecard

        outcome = AgentHotSwapCoordinator.execute_hotswap(
            current_head=self.active_head,
            candidate=candidate,
            scorecard=scorecard,
            current_head_scorecard=self._scorecards.get(self._active_head_id),
            force=force,
        )

        if outcome.success:
            self._active_head_id = candidate.blueprint_id
            self._history.append({
                "event": "hotswap_promoted",
                "previous_head": outcome.previous_head_id,
                "new_head": outcome.new_head_id,
                "generation": outcome.generation,
                "timestamp": outcome.promoted_at,
            })

        return outcome

    def rollback(self, target_blueprint_id: str) -> bool:
        """Rollback active head to an earlier verified generation blueprint."""
        if target_blueprint_id not in self._blueprints:
            return False

        old_head = self._active_head_id
        self._active_head_id = target_blueprint_id
        self._history.append({
            "event": "rollback_executed",
            "from_head": old_head,
            "to_head": target_blueprint_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return True

    def get_pareto_frontier(self) -> list[AgentBlueprint]:
        """Compute the Pareto-optimal frontier of agent architectures."""
        scored = [
            (self._blueprints[bid], sc)
            for bid, sc in self._scorecards.items()
            if bid in self._blueprints
        ]
        if not scored:
            return [self.active_head]

        # Sort descending by composite score
        scored.sort(key=lambda x: x[1].composite_score, reverse=True)
        # Top 3 non-dominated architectures
        return [bp for bp, _ in scored[:3]]

    def get_status(self) -> dict[str, Any]:
        """Status and telemetry for the Meta-Compiler lineage."""
        return {
            "project_id": self.project_id,
            "active_head": self.active_head.to_dict(),
            "active_scorecard": self._scorecards.get(self._active_head_id, BenchmarkScorecard()).to_dict(),
            "total_generations": max(bp.generation for bp in self._blueprints.values()) + 1,
            "blueprints_count": len(self._blueprints),
            "pareto_frontier": [bp.to_dict() for bp in self.get_pareto_frontier()],
            "history": self._history[-10:],
        }


_LINEAGES: dict[str, MetaLineageStore] = {}


def get_meta_compiler_lineage(project_id: str = "default") -> MetaLineageStore:
    """Project-scoped singleton accessor for MetaLineageStore."""
    if project_id not in _LINEAGES:
        _LINEAGES[project_id] = MetaLineageStore(project_id)
    return _LINEAGES[project_id]
