from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class AutonomyProfile(enum.Enum):
    """
    4-Tier User Autonomy Profiles from Chapter 47 of the Master Blueprint.
    Enforces dynamic human-in-the-loop policies.
    """
    OBSERVER = "observer"        # Read-only; all state modifications require explicit human sign-off
    ASSISTANT = "assistant"      # Routine code edits and tests auto-approved; shell commands require sign-off
    OPERATOR = "operator"        # Standard development, testing, git branches auto-approved; destructive actions require sign-off
    AUTONOMOUS = "autonomous"    # Full independent execution within bounded budgets, timeouts, and invariants


class ActionRiskLevel(enum.Enum):
    LOW_SAFE = "low_safe"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK_DESTRUCTIVE = "high_risk_destructive"


class AuthorizationDecision(enum.Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class PolicyEvaluationResult:
    decision: AuthorizationDecision
    profile: AutonomyProfile
    risk_level: ActionRiskLevel
    reason: str
    action_signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "profile": self.profile.value,
            "risk_level": self.risk_level.value,
            "reason": self.reason,
            "action_signature": self.action_signature,
        }
