"""Autonomous Work Discovery Triggers: Routine, Event, and Threshold-Driven Swarms."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.estimator import SwarmBenefitEstimator

logger = logging.getLogger(__name__)


class AutonomousWorkTrigger:
    """Monitors workloads and triggers autonomous agent swarms without manual prompts."""

    @classmethod
    def evaluate_and_trigger_routine(
        cls,
        bot_name: str,
        routine_name: str,
        goal: str,
        backlog_items: Sequence[str] | None = None,
        max_concurrency: int = 8,
    ) -> dict[str, Any]:
        """Evaluates whether an automated routine warrants a swarm, and auto-provisions if beneficial."""
        decision = SwarmBenefitEstimator.estimate(goal, items=backlog_items)

        if not decision.should_swarm:
            logger.info(f"Autonomous routine '{routine_name}' by @{bot_name} does not warrant a swarm ({decision.reason}).")
            return {
                "triggered": False,
                "reason": decision.reason,
                "bot_name": bot_name,
                "routine": routine_name,
            }

        coordinator = get_swarm_coordinator()
        plan = coordinator.create_swarm(
            goal=f"[{bot_name}:{routine_name}] {goal}",
            mode=decision.mode,
            items=backlog_items,
            max_concurrency=max_concurrency,
        )

        logger.info(f"Autonomous routine '{routine_name}' triggered swarm {plan.swarm_id} ({plan.mode.value}) for @{bot_name}.")

        return {
            "triggered": True,
            "swarm_id": plan.swarm_id,
            "mode": plan.mode.value,
            "estimated_speedup": plan.estimated_speedup,
            "tasks_count": len(plan.tasks),
            "bot_name": bot_name,
            "routine": routine_name,
        }
