"""Conflict-aware fan-in, explicit consensus, and evidence disclosure."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from alpha.bots.quality_gate import evaluate_quality_gate
from alpha.runtime.runs.verification import verify_acceptance_criteria
from alpha.swarm.consensus import ConsensusPolicy, evaluate_consensus, votes_from_evidence
from alpha.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState


class SwarmAggregator:
    """Aggregates worker outputs without treating textual agreement as proof."""

    @staticmethod
    def apply_acceptance_verification(
        task: SwarmTaskNode,
        evidence: Sequence[Mapping[str, object]] | None,
    ) -> None:
        """Record the deterministic acceptance overlay on a completed task.

        Execution state and acceptance state are intentionally separate.  A
        malformed or incomplete evidence bundle therefore downgrades only the
        acceptance projection; it must not turn a real worker result into a
        retry or claim that execution failed.
        """

        if not task.acceptance_criteria:
            return
        try:
            verification = verify_acceptance_criteria(task.acceptance_criteria, evidence or [])
        except (AttributeError, TypeError, ValueError) as exc:
            task.acceptance_status = "failed"
            task.verification = {
                "verified": False,
                "error": str(exc),
                "verdicts": [],
            }
            return
        task.verification = {
            "verified": verification.verified,
            "verdicts": [
                {
                    "criterion_id": verdict.criterion_id,
                    "verified": verdict.verified,
                    "reason": verdict.reason,
                    "evidence_references": list(verdict.evidence_references),
                }
                for verdict in verification.verdicts
            ],
        }
        task.acceptance_status = "passed" if verification.verified else "failed"

    @classmethod
    def aggregate(cls, plan: SwarmPlan) -> dict[str, Any]:
        completed_tasks = [task for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED]
        failed_tasks = [task for task in plan.tasks.values() if task.state == TaskNodeState.FAILED]
        cancelled_tasks = [task for task in plan.tasks.values() if task.state == TaskNodeState.CANCELLED]
        acceptance_failed_tasks = [task for task in completed_tasks if task.acceptance_status == "failed" or task.verification.get("verified") is False]
        acceptance_unverified_tasks = [task for task in completed_tasks if task.acceptance_criteria and task.acceptance_status not in {"passed", "failed"}]

        raw_summaries = [task.result_summary for task in completed_tasks if task.result_summary]
        deduped_summaries = cls._deduplicate_findings(raw_summaries)
        artifacts: list[str] = []
        for task in completed_tasks:
            for artifact in task.output_artifacts:
                if artifact not in artifacts:
                    artifacts.append(artifact)

        conflicts = cls._detect_conflicts(completed_tasks)
        consensus_result: dict[str, Any] | None = None
        if plan.requires_consensus:
            evidence: list[Mapping[str, Any]] = []
            for task in completed_tasks:
                evidence.extend(task.evidence)
                if isinstance(task.result_payload, Mapping):
                    evidence.append(task.result_payload)
            votes = votes_from_evidence(evidence)
            consensus_result = evaluate_consensus(votes, ConsensusPolicy(min_voters=2, quorum=0.5, threshold=0.75, require_evidence=True)).to_dict()
            plan.consensus = consensus_result

        report_lines = [
            f"# Swarm Synthesis: {plan.goal}",
            "",
            "## Execution Metrics",
            f"- **Swarm Mode**: `{plan.mode.value}`",
            f"- **Speedup Factor**: `{plan.estimated_speedup}x` (estimated DAG speedup)",
            f"- **Critical Path Duration**: `{plan.critical_path_seconds:.1f}s` (estimated)",
            f"- **Tasks Completed**: {len(completed_tasks)}/{len(plan.tasks)}",
        ]
        if failed_tasks:
            report_lines.append(f"- **Failed Tasks**: {len(failed_tasks)} ({', '.join(task.task_id for task in failed_tasks)})")
        if cancelled_tasks:
            report_lines.append(f"- **Cancelled Tasks**: {len(cancelled_tasks)} ({', '.join(task.task_id for task in cancelled_tasks)})")
        if acceptance_failed_tasks:
            report_lines.append(f"- **Acceptance Failures**: {len(acceptance_failed_tasks)} ({', '.join(task.task_id for task in acceptance_failed_tasks)})")
        if acceptance_unverified_tasks:
            report_lines.append(f"- **Acceptance Unverified**: {len(acceptance_unverified_tasks)} ({', '.join(task.task_id for task in acceptance_unverified_tasks)})")

        report_lines.extend(["", "## Consolidated Deliverables & Findings"])
        if deduped_summaries:
            for index, finding in enumerate(deduped_summaries, 1):
                report_lines.append(f"{index}. {finding}")
        else:
            report_lines.append("- No successful worker summaries were recorded.")

        if conflicts:
            report_lines.extend(["", "## Reconciled Contradictions (not auto-reconciled)"])
            for conflict in conflicts:
                report_lines.append(f"- **Conflict in [{conflict['task_a']} vs {conflict['task_b']}]**: {conflict['reason']}")
        if consensus_result is not None:
            report_lines.extend(["", "## Consensus Gate"])
            report_lines.append(f"- **Status**: `{consensus_result['status']}` — {consensus_result['reason']}")
            report_lines.append(f"- **Agreement**: {consensus_result['agreement'] if consensus_result['agreement'] is not None else 'unavailable'}")
        if artifacts:
            report_lines.extend(["", "## Generated Artifacts"])
            for artifact in artifacts:
                report_lines.append(f"- [{artifact}]({artifact})")

        deliverable_text = "\n".join(report_lines)
        criteria = [f"Complete all subtasks for: {plan.goal}", "Zero unhandled task failures"]
        quality_gate = evaluate_quality_gate(deliverable_text, criteria)
        plan.final_result = deliverable_text
        score = quality_gate.get("score")
        plan.quality_score = float(score) if isinstance(score, (int, float)) else 0.0

        if failed_tasks and not completed_tasks:
            plan.status = "failed"
        elif cancelled_tasks or failed_tasks or conflicts or acceptance_failed_tasks or acceptance_unverified_tasks:
            plan.status = "partial_success"
        elif (plan.requires_consensus and consensus_result is None) or (consensus_result is not None and not consensus_result.get("approved")):
            plan.status = "partial_success"
        elif quality_gate.get("verdict") == "passed":
            plan.status = "completed"
        else:
            plan.status = "partial_success"

        return {
            "deliverable": deliverable_text,
            "quality_gate": quality_gate,
            "quality_basis": "keyword_acceptance_gate_v1; not independent task verification",
            "completed_tasks": len(completed_tasks),
            "failed_tasks": len(failed_tasks),
            "cancelled_tasks": len(cancelled_tasks),
            "acceptance_failed_tasks": len(acceptance_failed_tasks),
            "acceptance_unverified_tasks": len(acceptance_unverified_tasks),
            "conflicts_detected": len(conflicts),
            "artifacts": artifacts,
            "consensus": consensus_result,
            "execution_status": plan.status,
        }

    @classmethod
    def _deduplicate_findings(cls, summaries: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for summary in summaries:
            normalized = re.sub(r"\s+", " ", summary.strip().lower())
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(summary.strip())
        return deduped

    @classmethod
    def _detect_conflicts(cls, tasks: list[Any]) -> list[dict[str, str]]:
        conflicts: list[dict[str, str]] = []
        positive_pattern = re.compile(r"\b(success|supported|verified|true|ready|viable|pass)\b", re.IGNORECASE)
        negative_pattern = re.compile(r"\b(failed|unsupported|rejected|false|broken|unviable|fail)\b", re.IGNORECASE)
        for index, task_a in enumerate(tasks):
            summary_a = task_a.result_summary or ""
            for task_b in tasks[index + 1 :]:
                summary_b = task_b.result_summary or ""
                polarity_conflict = (positive_pattern.search(summary_a) and negative_pattern.search(summary_b)) or (positive_pattern.search(summary_b) and negative_pattern.search(summary_a))
                if polarity_conflict:
                    conflicts.append(
                        {
                            "task_a": task_a.task_id,
                            "task_b": task_b.task_id,
                            "reason": "polarity assertions disagree; no model-free reconciliation was performed",
                        }
                    )
        return conflicts
