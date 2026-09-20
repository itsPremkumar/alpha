"""Tri-Tier Scoped Swarm Memory Engine.

Maintains strict cognitive boundaries between ephemeral worker reasoning,
shared swarm blackboard operational facts, and permanent organizational memory.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from alpha.bots.events import get_org_event_store
from alpha.swarm.models import SwarmPlan

logger = logging.getLogger(__name__)

_GLOBAL_MEMORY_MANAGER: SwarmMemoryManager | None = None


def get_swarm_memory_manager() -> SwarmMemoryManager:
    global _GLOBAL_MEMORY_MANAGER
    if _GLOBAL_MEMORY_MANAGER is None:
        _GLOBAL_MEMORY_MANAGER = SwarmMemoryManager()
    return _GLOBAL_MEMORY_MANAGER


class SwarmMemoryManager:
    """Manages Tier 1 (Ephemeral Scratchpad), Tier 2 (Blackboard), and Tier 3 (Org Promotion)."""

    def __init__(self):
        # Tier 1: task_id -> dict
        self._scratchpads: dict[str, dict[str, Any]] = {}
        # Tier 2: swarm_id -> {"facts": dict, "artifacts": list, "decisions": list}
        self._blackboards: dict[str, dict[str, Any]] = {}

    # -- Tier 1: Ephemeral Task Scratchpad -------------------------------

    def set_task_scratchpad(self, task_id: str, key: str, value: Any) -> None:
        if task_id not in self._scratchpads:
            self._scratchpads[task_id] = {}
        self._scratchpads[task_id][key] = value

    def get_task_scratchpad(self, task_id: str) -> dict[str, Any]:
        return self._scratchpads.get(task_id, {})

    def clear_task_scratchpad(self, task_id: str) -> None:
        """Purges transient worker scratchpad immediately on task completion."""
        if task_id in self._scratchpads:
            del self._scratchpads[task_id]

    # -- Tier 2: Swarm Blackboard ----------------------------------------

    def _ensure_blackboard(self, swarm_id: str) -> dict[str, Any]:
        if swarm_id not in self._blackboards:
            self._blackboards[swarm_id] = {
                "facts": {},
                "artifacts": [],
                "decisions": [],
                "created_at": time.time(),
            }
        return self._blackboards[swarm_id]

    def record_fact(self, swarm_id: str, fact_key: str, fact_value: Any, confidence: float = 1.0) -> None:
        bb = self._ensure_blackboard(swarm_id)
        bb["facts"][fact_key] = {
            "value": fact_value,
            "confidence": confidence,
            "timestamp": time.time(),
        }

    def get_facts(self, swarm_id: str) -> dict[str, Any]:
        bb = self._ensure_blackboard(swarm_id)
        return {k: v["value"] for k, v in bb["facts"].items()}

    def record_artifact(self, swarm_id: str, artifact_uri: str, description: str = "") -> None:
        bb = self._ensure_blackboard(swarm_id)
        if not any(a["uri"] == artifact_uri for a in bb["artifacts"]):
            bb["artifacts"].append(
                {
                    "uri": artifact_uri,
                    "description": description,
                    "timestamp": time.time(),
                }
            )

    def get_artifacts(self, swarm_id: str) -> list[dict[str, Any]]:
        bb = self._ensure_blackboard(swarm_id)
        return list(bb["artifacts"])

    # -- Tier 3: Organization Memory Promotion ---------------------------

    def promote_to_org_memory(self, swarm_id: str, plan: SwarmPlan, min_quality: float = 0.8) -> bool:
        """Promotes verified deliverables to permanent organization memory only if passing quality gate."""
        if plan.quality_score < min_quality or not plan.final_result:
            logger.info(f"Swarm {swarm_id} deliverable quality ({plan.quality_score:.2f}) below promotion threshold ({min_quality:.2f}). Skipped.")
            return False

        # Record into permanent organizational event store
        store = get_org_event_store()
        store.append_event(
            event_type="swarm_deliverable_promoted",
            actor="SwarmCoordinator",
            target="OrganizationMemory",
            details={
                "swarm_id": swarm_id,
                "goal": plan.goal,
                "quality_score": plan.quality_score,
                "speedup_factor": plan.estimated_speedup,
                "deliverable_summary": plan.final_result[:500],
            },
        )
        logger.info(f"Swarm {swarm_id} successfully promoted to Organization Memory with score {plan.quality_score:.2f}.")
        return True
