"""Data models for Continuous Autonomous Goals and Milestones.

The legacy continuous-goal surface is **retired** (see
``LEGACY_RETIREMENT_REASON``). Nothing in production dispatches these records:
``RunManager`` is the sole run-lifecycle owner, and a run only becomes real work
when it is admitted through it. A record here therefore proves that an objective
was *written down* — never that a step ran, a milestone passed, or any work was
verified. The read surfaces project every record through
``describe_goal`` so that a stale row carrying ``status="executing"`` cannot be
read as live execution.
"""

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
    "retired",
]

MilestoneStatus = Literal["pending", "in_progress", "verified", "failed"]

#: The one reason every legacy record is retired. Kept here, in the leaf module
#: both the model tool and the ``/loop:*`` commands import, so the three
#: user-facing surfaces cannot drift into three different excuses.
LEGACY_RETIREMENT_REASON = "The legacy continuous-goal surface is retired: no Gateway worker dispatches these records, and this record is not evidence that any step ran or that any work was verified."

#: What to do instead. Naming the real path is the whole point of refusing —
#: a bare "unsupported" leaves the caller with nowhere to go.
LEGACY_MIGRATION_GUIDANCE = (
    "Send the objective as a normal message in a thread: the Gateway run path (RunManager) is what "
    "actually executes it. Declare acceptance_criteria on that run to get a measured verdict at "
    "GET /api/threads/{thread_id}/runs/{run_id}/verification — a completed run is still not a "
    "verified one."
)


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
        m.verification_checks = [VerificationCheck(**vc) if isinstance(vc, dict) else vc for vc in raw_checks]
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
    # Additive retirement stamp. It records that an operator explicitly marked
    # this row retired on disk; it is *not* what makes a row read as retired —
    # `describe_goal` retires every legacy record unconditionally, so a fresh
    # install and a store written before this field existed both read honestly.
    retired: bool = False
    retired_at: str | None = None
    retired_reason: str | None = None

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
