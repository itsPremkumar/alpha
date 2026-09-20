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


@dataclass
class DeliberatorVote:
    """Individual vote and critique from a Council deliberator."""
    deliberator_name: str
    role: str  # "Critic", "InvariantVerifier", "SecurityReviewer", "QualityReviewer", "PresidingJudge"
    verdict: VoteVerdict
    confidence: float  # 0.0 to 1.0
    reasoning: str
    concerns: list[str] = field(default_factory=list)
    required_modifications: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deliberator_name": self.deliberator_name,
            "role": self.role,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
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
    weighted_score: float  # Weighted confidence score 0.0 to 1.0
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
            "weighted_score": round(self.weighted_score, 4),
            "dissenting_concerns": self.dissenting_concerns,
            "consensus_summary": self.consensus_summary,
            "votes": [v.to_dict() for v in self.votes],
            "timestamp": self.timestamp,
        }
