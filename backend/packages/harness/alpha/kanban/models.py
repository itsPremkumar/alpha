"""Data models for Collaborative Kanban Board."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

TaskColumn = Literal["backlog", "todo", "in_progress", "in_review", "done", "blocked"]
TaskPriority = Literal["low", "medium", "high", "critical"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class KanbanTask:
    """A task item on the collaborative Kanban board."""

    task_id: str
    board_id: str
    title: str
    description: str = ""
    column: TaskColumn = "todo"
    priority: TaskPriority = "medium"
    assignee: str | None = None
    reviewer: str | None = None
    dependencies: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    review_verdict: str | None = None
    review_comments: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KanbanTask:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class KanbanBoard:
    """A Kanban board bound to a project or multi-agent group room."""

    board_id: str
    title: str
    room_id: str | None = None
    tasks: dict[str, KanbanTask] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tasks"] = [t.to_dict() for t in self.tasks.values()]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KanbanBoard:
        raw_tasks = data.pop("tasks", [])
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        board = cls(**filtered)
        board.tasks = {t["task_id"]: KanbanTask.from_dict(t) for t in raw_tasks}
        return board
