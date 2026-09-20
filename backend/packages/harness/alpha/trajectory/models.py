"""Trajectory and Step Audit Models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class StepRecord:
    """A deterministic execution step in an autonomous agent trajectory."""

    step_id: str
    goal_id: str
    step_index: int
    thought: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str = ""
    milestone_id: str = ""
    status: str = "success"  # success, failure, retry
    error: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrajectoryTrace:
    """Full execution trajectory trace for a goal."""

    goal_id: str
    steps: list[StepRecord] = field(default_factory=list)
    total_steps: int = 0
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "steps": [s.to_dict() for s in self.steps],
            "total_steps": len(self.steps),
            "created_at": self.created_at,
        }
