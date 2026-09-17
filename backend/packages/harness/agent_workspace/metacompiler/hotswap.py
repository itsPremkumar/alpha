"""Preview-only hot-swap evaluation; no deployment adapter is configured."""

from __future__ import annotations

import math

from agent_workspace.metacompiler.models import AgentBlueprint, BenchmarkScorecard, HotSwapOutcome


class AgentHotSwapCoordinator:
    """Reports candidate eligibility without claiming a live deployment."""

    @staticmethod
    def execute_hotswap(
        current_head: AgentBlueprint,
        candidate: AgentBlueprint,
        scorecard: BenchmarkScorecard,
        current_head_scorecard: BenchmarkScorecard | None = None,
        force: bool = False,
    ) -> HotSwapOutcome:
        baseline = current_head_scorecard.composite_score if current_head_scorecard else 0.80
        evidence_kind = getattr(scorecard, "evidence_kind", "unknown")
        preview_qualifies = (
            math.isfinite(scorecard.composite_score)
            and math.isfinite(baseline)
            and math.isfinite(scorecard.robustness_score)
            and scorecard.composite_score >= baseline - 0.01
            and 0.80 <= scorecard.robustness_score <= 1.0
            and 0.0 <= scorecard.composite_score <= 1.0
            and 0.0 <= baseline <= 1.0
        )
        if scorecard.blueprint_id != candidate.blueprint_id or scorecard.generation != candidate.generation:
            reason = "Scorecard does not match the candidate blueprint and generation."
            preview_qualifies = False
        elif evidence_kind != "measured":
            reason = "Simulated or unknown evidence cannot authorize promotion, including with force."
        elif not scorecard.passed_regression_suite or not preview_qualifies:
            reason = "Candidate failed regression checks or regressed below the baseline."
        else:
            reason = "No deployment adapter is configured; no live promotion was performed."
        return HotSwapOutcome(
            success=False,
            previous_head_id=current_head.blueprint_id,
            new_head_id=current_head.blueprint_id,
            generation=current_head.generation,
            migrated_tasks=0,
            promoted_at="",
            telemetry={
                "rejection_reason": reason,
                "scorecard": scorecard.to_dict(),
                "baseline_score": baseline,
                "evidence_kind": evidence_kind,
                "preview_qualifies": preview_qualifies,
                "force_requested": force,
                "promotion_verified": False,
                "deployed": False,
            },
        )
