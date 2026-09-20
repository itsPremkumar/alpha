"""Data models for Continuous Autonomous Goals and Milestones."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

GoalStatus = Literal[
    "planning",
    "executing",
    "verifying",
    "adapting",
    "achieved",
    "blocked",
    "paused",
]

MilestoneStatus = Literal["pending", "in_progress", "verified", "failed"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class VerificationCheck:
    """Verifiable condition that must be satisfied for milestone completion."""

    check_id: str
    description: str
    passed: bool = False
    evidence: str = ""
    checked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Milestone:
    """A concrete milestone within an autonomous goal."""

    milestone_id: str
    goal_id: str
    title: str
    description: str = ""
    status: MilestoneStatus = "pending"
    dependencies: list[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 5
    verification_checks: list[VerificationCheck] = field(default_factory=list)
    strategy_history: list[str] = field(default_factory=list)
    last_error: str | None = None
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verification_checks"] = [vc.to_dict() if isinstance(vc, VerificationCheck) else vc for vc in self.verification_checks]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Milestone:
        raw_checks = data.pop("verification_checks", [])
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        m = cls(**filtered)
        m.verification_checks = [
            VerificationCheck(**vc) if isinstance(vc, dict) else vc for vc in raw_checks
        ]
        return m


@dataclass
class Goal:
    """A high-level autonomous goal pursued continuously until achieved."""

    goal_id: str
    title: str
    description: str = ""
    status: GoalStatus = "planning"
    milestones: dict[str, Milestone] = field(default_factory=dict)
    current_milestone_id: str | None = None
    iteration: int = 0
    max_iterations: int = 100
    strategy_notes: list[str] = field(default_factory=list)
    heartbeat_at: str = field(default_factory=_now)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["milestones"] = [m.to_dict() for m in self.milestones.values()]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        raw_ms = data.pop("milestones", [])
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        g = cls(**filtered)
        g.milestones = {m["milestone_id"]: Milestone.from_dict(m) for m in raw_ms}
        return g
