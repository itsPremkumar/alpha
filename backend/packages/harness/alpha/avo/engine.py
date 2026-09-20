from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .knowledge import DomainKnowledgeBase
from .lineage import AVOLineage, VersionRecord
from .scoring import EvaluationVector
from .supervisor import AVOSupervisor
from .variation_agent import AgenticVariationLoop

logger = logging.getLogger("alpha.avo.engine")


class AVOEngine:
    """
    Autonomous Agentic Variation Operators (AVO) Optimization Engine.
    Executes evolutionary search with multi-dimensional vector evaluation,
    domain knowledge retrieval (K), autonomous multi-trial repair loops,
    and supervisory anti-stagnation intervention.
    """

    def __init__(
        self,
        lineage: AVOLineage | None = None,
        supervisor: AVOSupervisor | None = None,
        knowledge_base: DomainKnowledgeBase | None = None,
        agent_loop: AgenticVariationLoop | None = None,
    ) -> None:
        self.lineage = lineage or AVOLineage()
        self.supervisor = supervisor or AVOSupervisor()
        self.knowledge_base = knowledge_base or DomainKnowledgeBase()
        self.agent_loop = agent_loop or AgenticVariationLoop()
        self.iteration_count: int = 0

    def run_iteration(
        self,
        hypothesis: str,
        modification: str,
        evaluate_fn: Callable[[], dict[str, Any]],
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Single-step iteration for backwards compatibility.
        """
        self.iteration_count += 1
        effective_parent = parent_id or self.lineage.head_id

        # Evaluate candidate empirically
        eval_result = evaluate_fn()
        correctness = bool(eval_result.get("correctness", False))
        perf = float(eval_result.get("performance", 0.0))
        qual = float(eval_result.get("quality", 0.0))

        # Build EvaluationVector
        metrics = {
            "performance": perf,
            "quality": qual,
        }
        if "metrics" in eval_result and isinstance(eval_result["metrics"], dict):
            metrics.update(eval_result["metrics"])

        vector = EvaluationVector(
            metrics=metrics,
            correctness=correctness,
            metadata=eval_result.get("metadata", {}),
        )

        candidate = VersionRecord(
            parent_id=effective_parent,
            hypothesis=hypothesis,
            modification=modification,
            correctness=correctness,
            performance_score=perf,
            quality_score=qual,
            vector=vector,
            metadata=eval_result.get("metadata", {}),
        )

        committed = self.lineage.commit_candidate(candidate)
        signature = f"{modification[:30]}_{correctness}"
        stagnated, directive, diag = self.supervisor.observe_step(
            improved=committed,
            signature=signature,
            backtrack_candidate=effective_parent,
        )

        if committed:
            self.knowledge_base.record_positive_pattern(
                hypothesis=hypothesis,
                modification_summary=modification,
                measured_gain=f"geomean={vector.geometric_mean()}",
            )
        else:
            self.knowledge_base.record_negative_lesson(
                attempt_hypothesis=hypothesis,
                failure_reason=candidate.rejection_reason or "Score regression",
            )

        return {
            "iteration": self.iteration_count,
            "version_id": candidate.version_id,
            "committed": committed,
            "composite_score": candidate.composite_score,
            "vector": vector.to_dict(),
            "current_head": self.lineage.head_id,
            "stagnation_detected": stagnated,
            "diagnostic": diag,
            "active_directive": directive.to_dict() if directive else None,
            "pareto_frontier_size": len(self.lineage.get_pareto_frontier()),
        }

    def run_agentic_variation(
        self,
        base_hypothesis: str,
        edit_fn: Callable[[str, dict[str, Any]], tuple[str, str]],
        evaluate_fn: Callable[[str], EvaluationVector],
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Full autonomous variation step with internal repair loop and domain knowledge K.
        """
        self.iteration_count += 1
        result = self.agent_loop.run_variation_step(
            base_hypothesis=base_hypothesis,
            edit_fn=edit_fn,
            evaluate_fn=evaluate_fn,
            lineage=self.lineage,
            knowledge_base=self.knowledge_base,
            supervisor=self.supervisor,
            parent_id=parent_id,
        )
        result["iteration"] = self.iteration_count
        return result

    def stats(self) -> dict[str, Any]:
        return {
            "iteration_count": self.iteration_count,
            "lineage": self.lineage.stats(),
            "supervisor": self.supervisor.stats(),
            "knowledge_base": self.knowledge_base.stats(),
            "pareto_frontier_size": len(self.lineage.get_pareto_frontier()),
        }
