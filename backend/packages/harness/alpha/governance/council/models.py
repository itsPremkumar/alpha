from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class RiskTier(enum.Enum):
    """Risk tier determining the required quorum threshold for the Quality Council."""
    TIER_1_CRITICAL = "tier_1_critical"    # Requires >= 4/5 or 5/5 approvals
    TIER_2_STANDARD = "tier_2_standard"    # Requires >= 3/5 approvals
    TIER_3_LOW = "tier_3_low"              # Requires >= 2/5 approvals


class VoteVerdict(enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CONDITIONAL = "conditional"


#: Neutral, disclosed baseline used when no real evidence backs a score.
#: Mirrors the deliberation engine's SINGLE-strategy 0.5 baseline: an honest
#: "unknown", never a measured confidence.
NEUTRAL_CONFIDENCE_BASELINE = 0.5

#: How a confidence value was produced. Always serialized next to the value so
#: consumers can tell a disclosed heuristic from real evidence from an honest
#: unverified baseline.
CONFIDENCE_METHOD_EVIDENCE = "evidence"      # grounded in supplied test/exit-code evidence
CONFIDENCE_METHOD_HEURISTIC = "heuristic"    # uncalibrated rule-based scan
CONFIDENCE_METHOD_UNVERIFIED = "unverified"  # no evidence available; neutral baseline


@dataclass
class DeliberatorVote:
    """Individual vote and critique from a Council deliberator."""
    deliberator_name: str
    role: str  # "Critic", "InvariantVerifier", "SecurityReviewer", "QualityReviewer", "PresidingJudge"
    verdict: VoteVerdict
    confidence: float  # score in [0.0, 1.0]; NOT a measured probability — see confidence_method/confidence_note
    reasoning: str
    concerns: list[str] = field(default_factory=list)
    required_modifications: list[str] = field(default_factory=list)
    #: "evidence" | "heuristic" | "unverified" — how `confidence` was produced.
    confidence_method: str = CONFIDENCE_METHOD_HEURISTIC
    #: Explicit disclosure of the formula/baseline behind `confidence`.
    confidence_note: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deliberator_name": self.deliberator_name,
            "role": self.role,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "confidence_method": self.confidence_method,
            "confidence_note": self.confidence_note,
            "reasoning": self.reasoning,
            "concerns": self.concerns,
            "required_modifications": self.required_modifications,
            "timestamp": self.timestamp,
        }


@dataclass
class QuorumVerdict:
    """Consolidated verdict issued by the 5-Deliberator Quality Council."""
    passed: bool
    risk_tier: RiskTier
    quorum_required: int
    approvals_count: int
    rejections_count: int
    conditional_count: int
    #: Disclosed heuristic aggregate of the vote scores (0.0-1.0). It is NOT a
    #: measured confidence; the formula is exported verbatim in ``to_dict``.
    weighted_score: float
    votes: list[DeliberatorVote] = field(default_factory=list)
    dissenting_concerns: list[str] = field(default_factory=list)
    consensus_summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "risk_tier": self.risk_tier.value,
            "quorum_required": self.quorum_required,
            "approvals_count": self.approvals_count,
            "rejections_count": self.rejections_count,
            "conditional_count": self.conditional_count,
            # 2dp: an uncalibrated heuristic does not warrant 4-decimal precision.
            "weighted_score": round(self.weighted_score, 2),
            "weighted_score_method": CONFIDENCE_METHOD_HEURISTIC,
            "weighted_score_formula": (
                "(sum of approve confidences + 0.5 * sum of conditional confidences) "
                "/ sum of all vote confidences"
            ),
            "confidence_baseline_disclosed": True,
            "confidence_note": (
                "weighted_score aggregates disclosed rule-based vote scores (unverified "
                "votes hold the neutral 0.5 baseline); it is an uncalibrated heuristic, "
                "not a measured probability."
            ),
            "dissenting_concerns": self.dissenting_concerns,
            "consensus_summary": self.consensus_summary,
            "votes": [v.to_dict() for v in self.votes],
            "timestamp": self.timestamp,
        }
