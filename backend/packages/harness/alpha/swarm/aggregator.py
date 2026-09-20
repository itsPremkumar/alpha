"""Swarm Aggregator: Conflict-aware fan-in, evidence ranking, and quality verification."""

from __future__ import annotations

import re
from typing import Any

from alpha.bots.quality_gate import evaluate_quality_gate
from alpha.swarm.models import SwarmPlan, TaskNodeState


class SwarmAggregator:
    """Aggregates distributed worker outputs, detects conflicts, and verifies deliverables."""

    @classmethod
    def aggregate(cls, plan: SwarmPlan) -> dict[str, Any]:
        """Synthesizes worker results into a coherent final deliverable."""
        completed_tasks = [t for t in plan.tasks.values() if t.state == TaskNodeState.COMPLETED]
        failed_tasks = [t for t in plan.tasks.values() if t.state == TaskNodeState.FAILED]

        # 1. Collect and deduplicate summaries
        raw_summaries = [t.result_summary for t in completed_tasks if t.result_summary]
        deduped_summaries = cls._deduplicate_findings(raw_summaries)

        # 2. Collect artifacts
        artifacts: list[str] = []
        for t in completed_tasks:
            for art in t.output_artifacts:
                if art not in artifacts:
                    artifacts.append(art)

        # 3. Detect contradictions / conflicts
        conflicts = cls._detect_conflicts(completed_tasks)

        # 4. Formulate executive synthesis markdown
        report_lines = [
            f"# Swarm Synthesis: {plan.goal}",
            "",
            "## Execution Metrics",
            f"- **Swarm Mode**: `{plan.mode.value}`",
            f"- **Speedup Factor**: `{plan.estimated_speedup}x`",
            f"- **Critical Path Duration**: `{plan.critical_path_seconds:.1f}s`",
            f"- **Tasks Completed**: {len(completed_tasks)}/{len(plan.tasks)}",
        ]
        if failed_tasks:
            report_lines.append(f"- **Failed Tasks**: {len(failed_tasks)} ({', '.join(t.task_id for t in failed_tasks)})")

        report_lines.extend(["", "## Consolidated Deliverables & Findings"])
        for idx, finding in enumerate(deduped_summaries, 1):
            report_lines.append(f"{idx}. {finding}")

        if conflicts:
            report_lines.extend(["", "## Reconciled Contradictions"])
            for conf in conflicts:
                report_lines.append(f"- ⚠️ **Conflict in [{conf['task_a']} vs {conf['task_b']}]**: {conf['reason']}")

        if artifacts:
            report_lines.extend(["", "## Generated Artifacts"])
            for art in artifacts:
                report_lines.append(f"- [{art}]({art})")

        deliverable_text = "\n".join(report_lines)

        # 5. Evaluate Quality Gate
        criteria = [f"Complete all subtasks for: {plan.goal}", "Zero unhandled task failures"]
        q_result = evaluate_quality_gate(deliverable_text, criteria)

        plan.final_result = deliverable_text
        plan.quality_score = q_result.get("score", 0.8)
        plan.status = "completed" if q_result.get("verdict") == "passed" and not failed_tasks else "partial_success"

        return {
            "deliverable": deliverable_text,
            "quality_gate": q_result,
            "completed_tasks": len(completed_tasks),
            "failed_tasks": len(failed_tasks),
            "conflicts_detected": len(conflicts),
            "artifacts": artifacts,
        }

    @classmethod
    def _deduplicate_findings(cls, summaries: list[str]) -> list[str]:
        seen = set()
        deduped = []
        for s in summaries:
            normalized = re.sub(r"\s+", " ", s.strip().lower())
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(s.strip())
        return deduped

    @classmethod
    def _detect_conflicts(cls, tasks: list) -> list[dict[str, str]]:
        conflicts = []
        # Check for polarized terms between workers
        positive_pattern = re.compile(r"\b(success|supported|verified|true|ready|viable|pass)\b", re.IGNORECASE)
        negative_pattern = re.compile(r"\b(failed|unsupported|rejected|false|broken|unviable|fail)\b", re.IGNORECASE)

        for i in range(len(tasks)):
            for j in range(i + 1, len(tasks)):
                ta = tasks[i]
                tb = tasks[j]
                sa = ta.result_summary or ""
                sb = tb.result_summary or ""

                if positive_pattern.search(sa) and negative_pattern.search(sb):
                    # Potential polarity contradiction
                    conflicts.append(
                        {
                            "task_a": ta.task_id,
                            "task_b": tb.task_id,
                            "reason": f"Discrepancy detected between outcome assertions in {ta.task_id} and {tb.task_id}.",
                        }
                    )
        return conflicts
