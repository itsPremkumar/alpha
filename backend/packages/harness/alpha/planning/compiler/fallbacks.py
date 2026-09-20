from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecoveryFallbackTree:
    """
    P19 Recovery Planning: Pre-computed fallbacks generated BEFORE execution.
    Eliminates runtime panic and hallucinations on tool/subgoal failure.
    """
    primary_strategy: str
    fallback_a: str  # First fallback: alternate tool, library or algorithmic approach
    fallback_b: str  # Second fallback: graceful degradation, reduced scope, or simplification
    escalation_threshold: int = 2
    escalation_directive: str = "Escalate to Human Operator or Senior Supervisor Agent"
    failure_history: list[str] = field(default_factory=list)

    def resolve_action(self, failure_count: int) -> str:
        """Determines the exact pre-computed action based on failure count."""
        if failure_count == 0:
            return self.primary_strategy
        elif failure_count == 1:
            return self.fallback_a
        elif failure_count == 2:
            return self.fallback_b
        else:
            return self.escalation_directive

    def record_failure(self, reason: str) -> None:
        self.failure_history.append(reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_strategy": self.primary_strategy,
            "fallback_a": self.fallback_a,
            "fallback_b": self.fallback_b,
            "escalation_threshold": self.escalation_threshold,
            "escalation_directive": self.escalation_directive,
            "failure_history": self.failure_history,
        }
