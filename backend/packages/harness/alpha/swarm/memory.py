"""Tri-Tier Scoped Swarm Memory Engine.

Maintains strict cognitive boundaries between ephemeral worker reasoning,
shared swarm blackboard operational facts, and permanent organizational memory.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from alpha.bots.events import get_org_event_store
from alpha.swarm.judge import VERDICT_PASSED
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

    def record_fact(self, swarm_id: str, fact_key: str, fact_value: Any, confidence: float = 0.5) -> None:
        """Record a caller-supplied fact; omitted confidence is neutral, not verified."""

        if not fact_key or not str(fact_key).strip():
            raise ValueError("fact_key must be non-empty")
        numeric_confidence = float(confidence)
        if not 0.0 <= numeric_confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        bb = self._ensure_blackboard(swarm_id)
        bb["facts"][fact_key] = {
            "value": fact_value,
            "confidence": numeric_confidence,
            "confidence_method": "caller_supplied" if confidence != 0.5 else "neutral_default",
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

    def record_task_result(
        self,
        swarm_id: str,
        task_id: str,
        *,
        summary: str,
        evidence: list[dict[str, Any]] | None = None,
        artifacts: list[str] | None = None,
    ) -> None:
        """Store a bounded task result in the shared swarm blackboard tier."""

        if not task_id or not str(task_id).strip():
            raise ValueError("task_id must be non-empty")
        bb = self._ensure_blackboard(swarm_id)
        results = bb.setdefault("task_results", [])
        if any(item.get("task_id") == task_id for item in results):
            return
        results.append(
            {
                "task_id": task_id,
                "summary": str(summary or "")[:12_000],
                "evidence": list(evidence or [])[-20:],
                "artifacts": list(dict.fromkeys(str(item) for item in (artifacts or []) if item))[:100],
                "timestamp": time.time(),
            }
        )
        if len(results) > 256:
            del results[:-256]

    def get_task_results(self, swarm_id: str) -> list[dict[str, Any]]:
        return list(self._ensure_blackboard(swarm_id).get("task_results", []))

    # -- Tier 3: Organization Memory Promotion ---------------------------

    def promote_to_org_memory(self, swarm_id: str, plan: SwarmPlan, min_quality: float = 0.8) -> bool:
        """Promotes a deliverable to permanent organization memory only when a
        measured quality score clears the threshold.

        Phase 3.3: ``quality_score`` is judge-measured, so ``None`` means no
        judge ever ran — an unmeasured deliverable is never promoted, and never
        logged as a fabricated 0.0 quality figure.  A recorded judge verdict
        other than ``passed`` blocks promotion for the same reason: a
        deliverable the judge left ``unverified`` or rejected is not accepted
        work.
        """
        verdict = plan.judge_report.get("verdict") if plan.judge_report else None
        if verdict is not None and verdict != VERDICT_PASSED:
            logger.info(f"Swarm {swarm_id} acceptance verdict is '{verdict}'; promotion requires an accepted deliverable. Skipped.")
            return False
        score = plan.quality_score
        if score is None:
            logger.info(f"Swarm {swarm_id} deliverable has no judged quality score (no judge ran); promotion needs a measured score >= {min_quality:.2f}. Skipped.")
            return False
        if score < min_quality or not plan.final_result:
            logger.info(f"Swarm {swarm_id} deliverable quality ({score:.2f}) below promotion threshold ({min_quality:.2f}). Skipped.")
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
                "quality_score": score,
                "speedup_factor": plan.estimated_speedup,
                "deliverable_summary": plan.final_result[:500],
            },
        )
        logger.info(f"Swarm {swarm_id} successfully promoted to Organization Memory with score {score:.2f}.")
        return True
