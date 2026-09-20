from __future__ import annotations

import logging
from typing import Any

from .classifier import ActionRiskClassifier
from .profiles import (
    ActionRiskLevel,
    AuthorizationDecision,
    AutonomyProfile,
    PolicyEvaluationResult,
)

logger = logging.getLogger("alpha.security.autonomy")


class AutonomyPolicyEngine:
    """
    Adaptive Autonomy & Policy Engine.
    Enforces user autonomy profiles across execution boundaries,
    intercepting risky actions and triggering human approval where mandated.
    """

    def __init__(
        self,
        default_profile: AutonomyProfile = AutonomyProfile.OPERATOR,
        classifier: ActionRiskClassifier | None = None,
    ) -> None:
        self.current_profile = default_profile
        self.classifier = classifier or ActionRiskClassifier()

    def set_profile(self, profile: AutonomyProfile) -> None:
        logger.info(f"Switching user autonomy profile from {self.current_profile.value} to {profile.value}")
        self.current_profile = profile

    def evaluate_action(
        self,
        action_name: str,
        command_or_args: Any | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluates whether an action is permitted under the active autonomy profile."""
        if isinstance(command_or_args, str):
            risk = self.classifier.classify_shell_command(command_or_args)
            signature = command_or_args
        elif isinstance(command_or_args, dict):
            risk = self.classifier.classify_tool_call(action_name, command_or_args)
            signature = f"{action_name}({command_or_args})"
        else:
            risk = self.classifier.classify_tool_call(action_name)
            signature = action_name

        decision, reason = self._resolve_decision(self.current_profile, risk)

        return PolicyEvaluationResult(
            decision=decision,
            profile=self.current_profile,
            risk_level=risk,
            reason=reason,
            action_signature=signature,
        )

    def _resolve_decision(
        self,
        profile: AutonomyProfile,
        risk: ActionRiskLevel,
    ) -> tuple[AuthorizationDecision, str]:
        # 1. OBSERVER Profile
        if profile == AutonomyProfile.OBSERVER:
            if risk == ActionRiskLevel.LOW_SAFE:
                return AuthorizationDecision.ALLOW, "Observer: Read-only and safe inspection permitted."
            else:
                return (
                    AuthorizationDecision.REQUIRE_APPROVAL,
                    f"Observer: State-modifying action ({risk.value}) requires explicit human sign-off.",
                )

        # 2. ASSISTANT Profile
        elif profile == AutonomyProfile.ASSISTANT:
            if risk == ActionRiskLevel.LOW_SAFE:
                return AuthorizationDecision.ALLOW, "Assistant: Safe inspection permitted."
            elif risk == ActionRiskLevel.MEDIUM_RISK:
                return AuthorizationDecision.ALLOW, "Assistant: Routine file edits permitted."
            else:
                return (
                    AuthorizationDecision.REQUIRE_APPROVAL,
                    f"Assistant: Destructive action ({risk.value}) requires explicit human sign-off.",
                )

        # 3. OPERATOR Profile
        elif profile == AutonomyProfile.OPERATOR:
            if risk in (ActionRiskLevel.LOW_SAFE, ActionRiskLevel.MEDIUM_RISK):
                return AuthorizationDecision.ALLOW, "Operator: Standard development tasks and commands permitted."
            else:
                return (
                    AuthorizationDecision.REQUIRE_APPROVAL,
                    f"Operator: High-risk destructive action ({risk.value}) requires explicit human sign-off.",
                )

        # 4. AUTONOMOUS Profile
        elif profile == AutonomyProfile.AUTONOMOUS:
            if risk == ActionRiskLevel.HIGH_RISK_DESTRUCTIVE:
                # Even in autonomous mode, extreme system destruction is blocked or requires confirmation
                return (
                    AuthorizationDecision.REQUIRE_APPROVAL,
                    "Autonomous: Safety guardrail intercept on high-risk destructive action.",
                )
            return AuthorizationDecision.ALLOW, "Autonomous: Unbounded execution within safety boundaries."

        return AuthorizationDecision.REQUIRE_APPROVAL, "Default fail-closed posture."
