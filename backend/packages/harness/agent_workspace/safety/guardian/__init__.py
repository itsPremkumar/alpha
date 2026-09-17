"""Smart Approvals & Guardian Review engine inspired by Hermes Agent."""

from agent_workspace.safety.guardian.circuit_breaker import DenialCircuitBreaker, get_denial_breaker
from agent_workspace.safety.guardian.floors import PermanentAllowlist, get_permanent_allowlist
from agent_workspace.safety.guardian.smart import (
    GuardianReviewResult,
    evaluate_command_safety,
    strip_shell_comments,
)

__all__ = [
    "strip_shell_comments",
    "GuardianReviewResult",
    "evaluate_command_safety",
    "PermanentAllowlist",
    "get_permanent_allowlist",
    "DenialCircuitBreaker",
    "get_denial_breaker",
]
