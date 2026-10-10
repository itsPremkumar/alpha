"""Task Contract: normalized task with requirements, constraints, acceptance checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    CODING = "coding"
    RESEARCH = "research"
    QA = "qa"
    PLANNING = "planning"
    WRITING = "writing"
    ANALYSIS = "analysis"
    ACTION = "action"


class RequirementPriority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class VerificationMethod(StrEnum):
    UNIT_TEST = "unit_test"
    EVIDENCE = "evidence"
    RUBRIC = "rubric"
    USER_REVIEW = "user_review"


class SafetyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BudgetProfile(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    priority: RequirementPriority = RequirementPriority.MUST
    verification_method: VerificationMethod = VerificationMethod.RUBRIC
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "priority": self.priority.value,
            "verification_method": self.verification_method.value,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Requirement:
        return cls(
            id=data["id"],
            text=data["text"],
            priority=RequirementPriority(data.get("priority", "must")),
            verification_method=VerificationMethod(data.get("verification_method", "rubric")),
            blocking=data.get("blocking", True),
        )


@dataclass(frozen=True)
class Constraint:
    id: str
    text: str
    category: str = "general"  # safety, style, performance, compatibility, etc.


@dataclass(frozen=True)
class AcceptanceCheck:
    id: str
    description: str
    kind: str  # test, evidence, rubric, manual
    command: str | None = None  # for test checks
    expected: str | None = None
    blocking: bool = True


@dataclass
class TaskContract:
    task_id: str
    original_request: str
    goal: str
    task_type: TaskType
    requirements: list[Requirement] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    context_snapshot_id: str | None = None
    baseline_artifact_id: str | None = None
    acceptance_checks: list[AcceptanceCheck] = field(default_factory=list)
    safety_level: SafetyLevel = SafetyLevel.MEDIUM
    budget_profile: BudgetProfile = BudgetProfile.STANDARD
    ambiguities: list[str] = field(default_factory=list)
    do_not_change: list[str] = field(default_factory=list)
    deliverable_format: str = ""
    created_at: float = field(default_factory=lambda: __import__("time").time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "original_request": self.original_request,
            "goal": self.goal,
            "task_type": self.task_type.value,
            "requirements": [r.to_dict() for r in self.requirements],
            "constraints": [{"id": c.id, "text": c.text, "category": c.category} for c in self.constraints],
            "context_snapshot_id": self.context_snapshot_id,
            "baseline_artifact_id": self.baseline_artifact_id,
            "acceptance_checks": [{"id": a.id, "description": a.description, "kind": a.kind, "command": a.command, "expected": a.expected, "blocking": a.blocking} for a in self.acceptance_checks],
            "safety_level": self.safety_level.value,
            "budget_profile": self.budget_profile.value,
            "ambiguities": self.ambiguities,
            "do_not_change": self.do_not_change,
            "deliverable_format": self.deliverable_format,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContract:
        return cls(
            task_id=data["task_id"],
            original_request=data["original_request"],
            goal=data["goal"],
            task_type=TaskType(data["task_type"]),
            requirements=[Requirement.from_dict(r) for r in data.get("requirements", [])],
            constraints=[Constraint(**c) for c in data.get("constraints", [])],
            context_snapshot_id=data.get("context_snapshot_id"),
            baseline_artifact_id=data.get("baseline_artifact_id"),
            acceptance_checks=[
                AcceptanceCheck(
                    id=a["id"],
                    description=a["description"],
                    kind=a["kind"],
                    command=a.get("command"),
                    expected=a.get("expected"),
                    blocking=a.get("blocking", True),
                )
                for a in data.get("acceptance_checks", [])
            ],
            safety_level=SafetyLevel(data.get("safety_level", "medium")),
            budget_profile=BudgetProfile(data.get("budget_profile", "standard")),
            ambiguities=data.get("ambiguities", []),
            do_not_change=data.get("do_not_change", []),
            deliverable_format=data.get("deliverable_format", ""),
            created_at=data.get("created_at", __import__("time").time()),
        )

    @classmethod
    def create_from_request(
        cls,
        request: str,
        *,
        task_type: TaskType | None = None,
        context_snapshot_id: str | None = None,
    ) -> TaskContract:
        """Create a task contract from a raw user request with minimal normalization."""
        tid = f"task-{uuid.uuid4().hex[:12]}"
        # Default: treat as QA unless specified
        ttype = task_type or TaskType.QA
        return cls(
            task_id=tid,
            original_request=request,
            goal=request[:200],
            task_type=ttype,
            requirements=[Requirement(id="REQ-001", text="Satisfy the user's request as stated", priority=RequirementPriority.MUST)],
            deliverable_format="answer",
        )


__all__ = [
    "TaskType",
    "RequirementPriority",
    "VerificationMethod",
    "SafetyLevel",
    "BudgetProfile",
    "Requirement",
    "Constraint",
    "AcceptanceCheck",
    "TaskContract",
]
