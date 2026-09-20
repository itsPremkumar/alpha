from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CapabilityGap:
    """A discovered capability weakness."""
    capability: str
    current_score: float  # 0.0 to 1.0
    target_score: float = 0.90  # 0.0 to 1.0
    difficulty: float = 0.5    # 0.1 to 1.0
    evidence: list[str] = field(default_factory=list)

    @property
    def gap_size(self) -> float:
        return round(max(0.0, self.target_score - self.current_score), 4)

    @property
    def priority(self) -> float:
        """Higher = more urgent. Priority = Gap Size / Difficulty."""
        diff = max(0.05, self.difficulty)
        return round(self.gap_size / diff, 4)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gap_size"] = self.gap_size
        d["priority"] = self.priority
        return d


@dataclass
class PracticeTask:
    """A single synthetic practice exercise targeting a capability gap."""
    task_id: str
    capability: str
    description: str
    difficulty: float
    expected_outcome: str = ""
    max_attempts: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Curriculum:
    """Ordered sequence of practice tasks targeting agent capability gaps."""
    curriculum_id: str
    gaps: list[CapabilityGap] = field(default_factory=list)
    tasks: list[PracticeTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "curriculum_id": self.curriculum_id,
            "total_gaps": len(self.gaps),
            "gaps": [g.to_dict() for g in self.gaps],
            "total_tasks": len(self.tasks),
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
        }
