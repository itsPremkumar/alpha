"""Data models for Theory of Mind (ToM) user mental model modeling."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RiskTolerance(str, Enum):
    LOW = "low"  # Production/critical: zero breakage, full backwards compatibility
    MEDIUM = "medium"  # Standard engineering: balanced progress with validation
    HIGH = "high"  # Prototyping / experimental: move fast, major rewrites tolerated


class PriorityDomain(str, Enum):
    CORRECTNESS = "correctness"
    PERFORMANCE = "performance"
    BACKWARD_COMPATIBILITY = "backward_compatibility"
    MINIMAL_DIFF = "minimal_diff"
    READABILITY = "readability"
    TEST_COVERAGE = "test_coverage"
    SECURITY = "security"
    EVIDENCE = "evidence"
    REVERSIBILITY = "reversibility"


@dataclass
class IntentHypothesis:
    """Hypothesized human mental state and implicit requirements."""

    stated_goal: str
    inferred_intent: str
    risk_tolerance: RiskTolerance
    top_priorities: list[PriorityDomain] = field(default_factory=list)
    unstated_expectations: list[str] = field(default_factory=list)
    pitfalls_to_avoid: list[str] = field(default_factory=list)
    recommended_constraints: list[str] = field(default_factory=list)
    confidence_score: float = 0.85

    def to_markdown(self) -> str:
        lines = [
            "### Theory of Mind: User Intent Model",
            f"- **Stated Goal**: {self.stated_goal}",
            f"- **Inferred Intent**: {self.inferred_intent}",
            f"- **Risk Tolerance**: {self.risk_tolerance.value.upper()}",
            f"- **Top Priorities**: {', '.join(p.value for p in self.top_priorities)}",
            "",
            "#### Unstated Expectations:",
        ]
        for exp in self.unstated_expectations:
            lines.append(f"  * {exp}")

        lines.append("\n#### Pitfalls to Avoid:")
        for pit in self.pitfalls_to_avoid:
            lines.append(f"  * {pit}")

        lines.append("\n#### Recommended Constraints:")
        for con in self.recommended_constraints:
            lines.append(f"  * {con}")

        return "\n".join(lines)
