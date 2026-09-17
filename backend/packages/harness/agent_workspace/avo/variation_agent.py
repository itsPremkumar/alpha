from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .knowledge import DomainKnowledgeBase
from .lineage import AVOLineage, VersionRecord
from .scoring import EvaluationVector
from .supervisor import AVOSupervisor, StrategicPivotDirective

logger = logging.getLogger("agent_workspace.avo.variation_agent")


class AgenticVariationLoop:
    """
    Autonomous multi-turn variation agent loop for Autonomous AVO.
    Implements Vary(P_t) = Agent(P_t, K, f).
    
    Rather than a single-shot generation, the agent conducts an internal iterative loop:
      1. Inspects lineage P_t and consults knowledge base K.
      2. Generates candidate variation.
      3. Evaluates with vector scoring function f(x).
      4. Diagnoses failures or regressions using profiler/compiler feedback.
      5. Recursively repairs and re-evaluates within an internal step budget.
      6. Commits strictly on matches-or-improves criteria.
    """

    def __init__(self, max_internal_trials: int = 4) -> None:
        self.max_internal_trials = max_internal_trials

    def run_variation_step(
        self,
        base_hypothesis: str,
        edit_fn: Callable[[str, dict[str, Any]], tuple[str, str]],
        evaluate_fn: Callable[[str], EvaluationVector],
        lineage: AVOLineage,
        knowledge_base: DomainKnowledgeBase,
        supervisor: AVOSupervisor,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Executes a complete AVO variation step.
        edit_fn takes (current_code, context_dict) -> (new_code, modification_summary)
        evaluate_fn takes (new_code) -> EvaluationVector
        """
        effective_parent_id = parent_id or lineage.head_id
        parent_record = lineage.get_version(effective_parent_id) if effective_parent_id else None
        current_code = parent_record.metadata.get("code", "") if parent_record else ""

        internal_trials: list[dict[str, Any]] = []
        committed_record: VersionRecord | None = None
        directive: StrategicPivotDirective | None = supervisor.last_directive
        current_hypothesis = base_hypothesis

        for trial_idx in range(1, self.max_internal_trials + 1):
            # 1. Consult knowledge base K for relevant patterns or anti-patterns
            relevant_knowledge = knowledge_base.query(current_hypothesis)
            context = {
                "trial": trial_idx,
                "parent_id": effective_parent_id,
                "parent_vector": parent_record.vector.to_dict() if parent_record and parent_record.vector else None,
                "relevant_patterns": [k.to_dict() for k in relevant_knowledge],
                "supervisor_directive": directive.to_dict() if directive else None,
            }

            # 2. Implement variation
            try:
                candidate_code, mod_summary = edit_fn(current_code, context)
            except Exception as e:
                logger.error(f"Error during variation synthesis: {e}")
                candidate_code = current_code
                mod_summary = f"Edit failed: {e}"

            # 3. Grounded evaluation f(x)
            vector = evaluate_fn(candidate_code)

            # 4. Build version record
            candidate_record = VersionRecord(
                parent_id=effective_parent_id,
                hypothesis=current_hypothesis,
                modification=mod_summary,
                correctness=vector.correctness,
                performance_score=vector.effective_metric("performance"),
                quality_score=vector.effective_metric("quality"),
                vector=vector,
                diff_summary=mod_summary,
                metadata={"code": candidate_code, "trial": trial_idx},
            )

            # 5. Commit policy check
            is_committed = lineage.commit_candidate(candidate_record)

            # Observe supervisor
            signature = f"{mod_summary[:40]}_{vector.correctness}"
            stagnated, new_directive, diag = supervisor.observe_step(
                improved=is_committed,
                signature=signature,
                backtrack_candidate=effective_parent_id,
            )
            if new_directive:
                directive = new_directive

            internal_trials.append({
                "trial": trial_idx,
                "hypothesis": current_hypothesis,
                "modification": mod_summary,
                "correctness": vector.correctness,
                "geometric_mean": vector.geometric_mean(),
                "committed": is_committed,
                "stagnated": stagnated,
                "diagnostic": diag,
            })

            if is_committed:
                committed_record = candidate_record
                # Record positive pattern in knowledge base K
                knowledge_base.record_positive_pattern(
                    hypothesis=current_hypothesis,
                    modification_summary=mod_summary,
                    measured_gain=f"geomean={vector.geometric_mean()}",
                    tags=["committed", "variation-step"],
                )
                logger.info(f"Variation successfully committed at trial {trial_idx}: {candidate_record.version_id}")
                break
            else:
                # Negative feedback: diagnose and prepare next internal trial
                reason = candidate_record.rejection_reason or "Non-improving metrics"
                knowledge_base.record_negative_lesson(
                    attempt_hypothesis=current_hypothesis,
                    failure_reason=reason,
                    tags=["trial-failure"],
                )
                # Revise hypothesis for next trial
                current_hypothesis = f"Repair ({reason}): {base_hypothesis}"

        return {
            "success": committed_record is not None,
            "committed_version_id": committed_record.version_id if committed_record else None,
            "current_head": lineage.head_id,
            "trials_conducted": len(internal_trials),
            "trials": internal_trials,
            "active_directive": directive.to_dict() if directive else None,
            "pareto_frontier_size": len(lineage.get_pareto_frontier()),
        }
