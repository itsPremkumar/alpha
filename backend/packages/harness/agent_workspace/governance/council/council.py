from __future__ import annotations

import logging
from typing import Any

from .models import QuorumVerdict, RiskTier, VoteVerdict
from .roles import (
    IndependentCritic,
    InvariantVerifier,
    PresidingJudge,
    QualityReviewer,
    SecurityReviewer,
)

logger = logging.getLogger("agent_workspace.governance.council")


class QualityCouncil:
    """
    5-Deliberator Quality Council for Tier-1/Tier-2 Mission Artifacts and High-Risk Decisions.
    Enforces the Agent Prime quality gate rule:
    'Never let one worker both produce and certify high-risk output.'
    """

    def __init__(self) -> None:
        self.critic = IndependentCritic()
        self.verifier = InvariantVerifier()
        self.security = SecurityReviewer()
        self.quality = QualityReviewer()
        self.judge = PresidingJudge()

    def deliberate(
        self,
        artifact_name: str,
        content: str,
        risk_tier: RiskTier = RiskTier.TIER_2_STANDARD,
        metadata: dict[str, Any] | None = None,
    ) -> QuorumVerdict:
        meta = metadata or {}

        # 1. Specialist evaluations
        v_critic = self.critic.evaluate(artifact_name, content, meta)
        v_verifier = self.verifier.evaluate(artifact_name, content, meta)
        v_security = self.security.evaluate(artifact_name, content, meta)
        v_quality = self.quality.evaluate(artifact_name, content, meta)

        specialist_votes = [v_critic, v_verifier, v_security, v_quality]

        # 2. Presiding Judge synthesis
        v_judge = self.judge.evaluate_synthesis(artifact_name, content, specialist_votes)
        all_votes = specialist_votes + [v_judge]

        # 3. Quorum rules based on RiskTier
        approvals = sum(1 for v in all_votes if v.verdict == VoteVerdict.APPROVE)
        rejections = sum(1 for v in all_votes if v.verdict == VoteVerdict.REJECT)
        conditionals = sum(1 for v in all_votes if v.verdict == VoteVerdict.CONDITIONAL)

        if risk_tier == RiskTier.TIER_1_CRITICAL:
            quorum_needed = 4
            # Absolute zero-tolerance for security and invariant rejection on tier 1
            has_veto = (v_security.verdict == VoteVerdict.REJECT) or (v_verifier.verdict == VoteVerdict.REJECT)
            passed = (approvals >= quorum_needed) and not has_veto
        elif risk_tier == RiskTier.TIER_2_STANDARD:
            quorum_needed = 3
            has_veto = v_security.verdict == VoteVerdict.REJECT
            passed = ((approvals + conditionals) >= quorum_needed) and not has_veto
        else:
            quorum_needed = 2
            passed = (approvals + conditionals) >= quorum_needed

        # 4. Weighted score calculation
        total_conf = sum(v.confidence for v in all_votes)
        approved_conf = sum(v.confidence for v in all_votes if v.verdict == VoteVerdict.APPROVE)
        cond_conf = sum(v.confidence * 0.5 for v in all_votes if v.verdict == VoteVerdict.CONDITIONAL)
        weighted_score = (approved_conf + cond_conf) / max(1.0, total_conf)

        # 5. Collate dissenting concerns
        all_concerns: list[str] = []
        for v in all_votes:
            for c in v.concerns:
                all_concerns.append(f"[{v.role}] {c}")

        summary = (
            f"Council {'APPROVED' if passed else 'REJECTED'} '{artifact_name}' under {risk_tier.value.upper()}. "
            f"Votes: {approvals} Approve, {rejections} Reject, {conditionals} Conditional (Quorum needed: {quorum_needed}/5)."
        )

        logger.info(summary)

        return QuorumVerdict(
            passed=passed,
            risk_tier=risk_tier,
            quorum_required=quorum_needed,
            approvals_count=approvals,
            rejections_count=rejections,
            conditional_count=conditionals,
            weighted_score=weighted_score,
            votes=all_votes,
            dissenting_concerns=all_concerns,
            consensus_summary=summary,
        )
