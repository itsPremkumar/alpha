"""Conflict-aware fan-in, explicit consensus, and evidence disclosure."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from alpha.bots.quality_gate import evaluate_quality_gate
from alpha.runtime.runs.verification import verify_acceptance_criteria
from alpha.swarm.consensus import ConsensusPolicy, evaluate_consensus, votes_from_evidence
from alpha.swarm.deliberation import DeliberationPolicy, rounds_from_evidence, run_deliberation
from alpha.swarm.grounding import grounding_unresolved
from alpha.swarm.judge import VERDICT_PASSED, VERDICT_UNVERIFIED, JudgeInvoke, judge_required, run_acceptance_judge
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
        if verification.verified:
            # Claim grounding (S2): a task that explicitly asked for acceptance
            # is not accepted while its summary cites paths that contradict the
            # workspace.  The ``unverifiable`` class (absolute / virtual-sandbox
            # paths) deliberately does NOT fail the task -- a claim this module
            # cannot check must not read as a failed one -- and the verdicts
            # stay visible beside the downgrade.
            unresolved_claims = grounding_unresolved(task)
            if unresolved_claims:
                task.acceptance_status = "failed"
                task.verification = {
                    **task.verification,
                    "verified": False,
                    "grounding_unresolved": unresolved_claims,
                    "error": (f"{unresolved_claims} claim(s) in the accepted result did not resolve against the workspace; a cited path that cannot be corroborated must not read as accepted"),
                }

    @classmethod
    def _apply_deliberation(cls, consensus_result: dict[str, Any], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Run sequential deliberation over the same evidence, guards can only block.

        Deliberation is strictly a *downgrade* channel: it may turn a clean
        approval into ``manual_review`` when a guard fires or when the
        sequential test flatly disagrees with the explicit policy, and it
        attaches its full report either way.  It can never raise ``rejected``
        to ``approved`` — an approval the explicit policy already refused is
        not up for a second opinion.
        """

        try:
            report = run_deliberation(
                rounds_from_evidence(evidence),
                DeliberationPolicy(min_votes=2, target_approve_rate=0.75, require_evidence=True),
            )
        except (TypeError, ValueError) as exc:
            # A malformed policy/vote record must not take aggregation down;
            # disclose the failure and leave the explicit verdict untouched.
            return {**consensus_result, "deliberation": {"status": "error", "reason": str(exc)}}
        payload = report.to_dict()
        blocking = sorted(set(report.guards))
        if report.status == "rejected" and consensus_result.get("approved"):
            blocking = sorted(set(blocking) | {"sequential_test_disagreement"})
        if blocking and consensus_result.get("approved"):
            return {
                **consensus_result,
                "approved": False,
                "status": "manual_review",
                "reason": f"explicit consensus approved, but deliberation blocked it: {', '.join(blocking)}",
                "blocked_by": blocking,
                "deliberation": payload,
            }
        return {**consensus_result, "deliberation": payload}

    @classmethod
    def compose_team(cls, plan: SwarmPlan, *, conflicts: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """Record and render the team section of a plan's synthesis.

        Called from :meth:`aggregate` so the deliverable an operator reads and
        the ``team`` key in the aggregate result are produced by the same code
        on the same state -- a report that could describe a different run than
        the one being summarized is worse than no report. The conflicts
        argument lets the caller pass the list it already computed, so the two
        cannot disagree.

        Returns the team report, and stores the machine-readable part in
        ``plan.metrics["team"]`` (adding the conflicts it was given) so a later
        read, including after a restart, sees the same membership.
        """

        from alpha.swarm.team import build_team_report, render_team_report_markdown

        if conflicts is not None:
            team_metrics = plan.metrics.get("team")
            if isinstance(team_metrics, dict):
                team_metrics["conflicts"] = [dict(item) for item in conflicts]
        report = build_team_report(plan)
        report["markdown"] = render_team_report_markdown(report)
        return report

    @classmethod
    def aggregate(
        cls,
        plan: SwarmPlan,
        *,
        judge_invoke: JudgeInvoke | None = None,
        judge_model: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate a plan's task facts and decide its terminal status.

        ``judge_invoke`` is the model round-trip for a plan whose
        ``acceptance_mode`` asked for the rubric judge (phase 3.3).  It is
        caller-supplied on purpose: aggregation is synchronous and can run
        under the coordinator's state lock, so no model call may be opened
        here.  Without it a judge-requiring plan reports ``unverified`` and
        never ``completed``.
        """
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
        # The team report is composed from the SAME `conflicts` and the same
        # plan this aggregation is about, so the operator-facing team section
        # and the aggregate result cannot describe two different runs.
        consensus_result: dict[str, Any] | None = None
        if plan.requires_consensus:
            evidence: list[Mapping[str, Any]] = []
            for task in completed_tasks:
                evidence.extend(task.evidence)
                if isinstance(task.result_payload, Mapping):
                    evidence.append(task.result_payload)
            votes = votes_from_evidence(evidence)
            consensus_result = evaluate_consensus(votes, ConsensusPolicy(min_voters=2, quorum=0.5, threshold=0.75, require_evidence=True)).to_dict()
            consensus_result = cls._apply_deliberation(consensus_result, evidence)
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
            deliberation = consensus_result.get("deliberation")
            if isinstance(deliberation, Mapping):
                report_lines.append(f"- **Sequential Deliberation**: `{deliberation.get('status', 'unknown')}` — {deliberation.get('explanation') or deliberation.get('reason') or 'no explanation recorded'}")
                guards = deliberation.get("guards") or []
                if guards:
                    report_lines.append(f"- **Deliberation Guards**: {', '.join(str(guard) for guard in guards)}")
                if deliberation.get("stopped_early"):
                    report_lines.append(f"- **Early Stop**: test crossed a boundary after {deliberation.get('rounds_used')} of {deliberation.get('policy', {}).get('max_rounds')} round(s)")
        if artifacts:
            report_lines.extend(["", "## Generated Artifacts"])
            for artifact in artifacts:
                report_lines.append(f"- [{artifact}]({artifact})")

        if not plan.tasks:
            # ``SwarmScheduler.is_swarm_finished()`` treats an empty plan as
            # finished, so aggregation IS reached with nothing to report.  The
            # keyword gate below passes on the boilerplate deliverable text
            # ("No successful worker summaries were recorded."), which let a
            # swarm that executed ZERO nodes publish ``status="completed"`` and
            # a SWARM_COMPLETED event.  A run that did no work has produced no
            # deliverable, so it can never be a completed one.  Disclose it in
            # the report body as well, not only in the status field.
            report_lines.extend(
                [
                    "",
                    "## No Work Executed",
                    "- This swarm plan contained no task nodes, so nothing was executed and no deliverable was produced.",
                ]
            )

        deliverable_text = "\n".join(report_lines)
        criteria = [f"Complete all subtasks for: {plan.goal}", "Zero unhandled task failures"]
        # Computed for the report only.  Its verdict is ADVISORY and never
        # decides the terminal status: 40% keyword overlap with the goal text
        # cannot establish that work succeeded, and it passed on the
        # "no successful worker summaries" boilerplate.  Status below is
        # derived from task facts (executed? completed? acceptance passed?).
        # ``method``/``advisory`` are stamped by the gate itself, so this
        # caller cannot drift from the module that computes them.
        quality_gate = evaluate_quality_gate(deliverable_text, criteria)
        plan.final_result = deliverable_text
        # Phase 3.3: ``quality_score`` is measured by the rubric judge and by
        # nothing else.  The keyword gate above still answers its own question
        # in the report (``result["quality_gate"]``, advisory and stamped by
        # the gate itself), but its number no longer masquerades as a quality
        # measurement.  A plan that never opted into a judge reports ``None``
        # — "not judged" — rather than a fabricated score.
        if judge_required(plan.acceptance_mode):
            plan.judge_report = run_acceptance_judge(plan, invoke=judge_invoke, model=judge_model)
            judge_score = plan.judge_report.get("score")
            plan.quality_score = float(judge_score) if isinstance(judge_score, (int, float)) else None
        else:
            plan.judge_report = {}
            plan.quality_score = None

        if not plan.tasks:
            # A plan with no nodes executed no work; it can never be completed.
            plan.status = "partial_success"
            plan.terminal_reason = "no_work"
        elif failed_tasks and not completed_tasks:
            plan.status = "failed"
        elif cancelled_tasks or failed_tasks or conflicts or acceptance_failed_tasks:
            plan.status = "partial_success"
        elif acceptance_unverified_tasks:
            # Tasks ran but their declared criteria were not verified.  That is
            # a distinct outcome from failure, and it is not success.
            plan.status = "partial_success"
            plan.terminal_reason = "acceptance_unverified"
        elif (plan.requires_consensus and consensus_result is None) or (consensus_result is not None and not consensus_result.get("approved")):
            plan.status = "partial_success"
        elif not completed_tasks:
            # Defensive twin of the ``not plan.tasks`` branch above: a plan
            # whose nodes exist but none of them completed must not read as
            # ``completed``.
            plan.status = "partial_success"
            plan.terminal_reason = "no_work"
        else:
            plan.status = "completed"

        # Phase 3.3 acceptance gate: a judge that ran and did not pass may
        # never read as ``completed``, whatever the execution ladder above
        # concluded — "completed requires accepted tasks" now holds for the
        # model judgement as well as the per-task one.  The guard can only
        # *demote*: a judge withholds acceptance, it never grants status the
        # task facts did not already earn.
        judge_verdict = plan.judge_report.get("verdict") if plan.judge_report else None
        if plan.status == "completed" and judge_verdict is not None and judge_verdict != VERDICT_PASSED:
            plan.status = "partial_success"
            plan.terminal_reason = "judge_unverified" if judge_verdict == VERDICT_UNVERIFIED else "judge_rejected"

        # The team report is composed LAST, after the terminal status is
        # decided, so the `execution_status` it prints is the final one rather
        # than the transient `aggregating` the plan held a moment earlier. It
        # is appended to `plan.final_result` and NOT to `deliverable_text`,
        # because `deliverable_text` is what the advisory keyword gate scores:
        # letting a new section change that score would silently move the
        # number a reader sees, even though the status no longer depends on it.
        team_report = cls.compose_team(plan, conflicts=conflicts)
        plan.final_result = f"{deliverable_text}\n\n{team_report.get('markdown') or ''}".rstrip()

        return {
            "deliverable": deliverable_text,
            "quality_gate": quality_gate,
            # The judge's report travels with every aggregate result so a
            # caller can tell "judged and passed" from "never judged" without
            # reading ``quality_score`` alone (empty = no judge ran).
            "judge": dict(plan.judge_report),
            "quality_basis": "task_facts_v2; quality_score is judge-measured only (None = not judged); the keyword gate is advisory and never decides status",
            "completed_tasks": len(completed_tasks),
            "failed_tasks": len(failed_tasks),
            "cancelled_tasks": len(cancelled_tasks),
            "acceptance_failed_tasks": len(acceptance_failed_tasks),
            "acceptance_unverified_tasks": len(acceptance_unverified_tasks),
            "conflicts_detected": len(conflicts),
            "artifacts": artifacts,
            "consensus": consensus_result,
            "execution_status": plan.status,
            "team": team_report,
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
    def detect_conflicts(cls, tasks: list[Any]) -> list[dict[str, str]]:
        """Public name for the polarity-conflict detector.

        The team report needs the *same* conflict list the aggregation used, so
        it calls this rather than keeping a second, possibly-divergent copy of
        the regexes. The private alias below stays so existing internal callers
        are unaffected.
        """

        return cls._detect_conflicts(tasks)

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
