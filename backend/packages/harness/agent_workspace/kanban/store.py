"""Collaborative Kanban Board Store with ACID state transitions and auto-unblocking."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_workspace.kanban.bridge import KanbanGroupBridge
from agent_workspace.kanban.dependency import DependencyGraph
from agent_workspace.kanban.models import (
    KanbanBoard,
    KanbanTask,
    TaskColumn,
    TaskPriority,
    _now,
)

logger = logging.getLogger(__name__)

_DEFAULT_KANBAN_DIR = ".deerflow/kanban"


class KanbanStore:
    """Thread-safe store for collaborative Kanban boards with auto-provisioning."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else Path.cwd() / _DEFAULT_KANBAN_DIR / "boards.json"
        self._boards: dict[str, KanbanBoard] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("boards", []):
                board = KanbanBoard.from_dict(item)
                self._boards[board.board_id.lower()] = board
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "boards": [b.to_dict() for b in self._boards.values()],
                "updated_at": _now(),
            }
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            pass

    def get_or_create_board(
        self,
        board_id: str = "main",
        title: str = "Primary Project Board",
        room_id: str | None = None,
    ) -> KanbanBoard:
        """Fetch existing board or auto-provision a new one."""
        key = board_id.lower().strip()
        with self._lock:
            if key in self._boards:
                board = self._boards[key]
                if room_id and not board.room_id:
                    board.room_id = room_id
                    self._save()
                return board

            board = KanbanBoard(
                board_id=key,
                title=title,
                room_id=room_id or key,
            )
            self._boards[key] = board
            self._save()
            return board

    def put_board(self, board: KanbanBoard) -> KanbanBoard:
        """Persist a fully built board (e.g. from the autonomous planner).

        Keyed by the board's own id; overwrites any board with the same key.
        Returns the stored board.
        """
        key = board.board_id.lower().strip()
        with self._lock:
            board.board_id = key
            for task in board.tasks.values():
                task.board_id = key
            self._boards[key] = board
            self._save()
            return board

    def create_task(
        self,
        board_id: str,
        title: str,
        description: str = "",
        priority: TaskPriority = "medium",
        dependencies: Sequence[str] | None = None,
        assignee: str | None = None,
    ) -> KanbanTask:
        """Create a new Kanban task with dependency resolution."""
        board = self.get_or_create_board(board_id)
        task_id = f"TASK-{uuid4().hex[:6].upper()}"

        deps = list(dependencies or [])
        column: TaskColumn = "todo"
        blocked_reason = None

        # Check if any dependencies are not yet satisfied
        if deps:
            dummy_task = KanbanTask(task_id=task_id, board_id=board.board_id, title=title, dependencies=deps)
            if not DependencyGraph.are_dependencies_satisfied(dummy_task, board.tasks):
                column = "blocked"
                blocked_reason = f"Waiting on prerequisite tasks: {deps}"

        task = KanbanTask(
            task_id=task_id,
            board_id=board.board_id,
            title=title,
            description=description,
            column=column,
            priority=priority,
            assignee=assignee,
            dependencies=deps,
            blocked_reason=blocked_reason,
        )

        with self._lock:
            board.tasks[task_id] = task
            self._save()

        KanbanGroupBridge.notify_task_event(board.room_id, "created", task)
        return task

    def claim_task(self, board_id: str, task_id: str, bot_name: str) -> dict[str, Any]:
        """An autonomous bot claims an unassigned task."""
        board = self.get_or_create_board(board_id)
        with self._lock:
            task = board.tasks.get(task_id)
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}
            if task.column == "blocked":
                return {"status": "error", "error": f"Task '{task_id}' is BLOCKED: {task.blocked_reason}"}
            if task.column == "done":
                return {"status": "error", "error": f"Task '{task_id}' is already DONE."}

            task.assignee = bot_name
            task.column = "in_progress"
            task.updated_at = _now()
            self._save()

        KanbanGroupBridge.notify_task_event(board.room_id, "claimed", task, actor=bot_name)
        return {"status": "ok", "task_id": task_id, "column": "in_progress", "assignee": bot_name}

    def submit_for_review(
        self,
        board_id: str,
        task_id: str,
        bot_name: str,
        reviewer_name: str = "reviewer",
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit a task deliverable for peer review."""
        board = self.get_or_create_board(board_id)
        with self._lock:
            task = board.tasks.get(task_id)
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}

            task.column = "in_review"
            task.reviewer = reviewer_name
            if artifacts:
                task.artifacts.extend(artifacts)
            task.updated_at = _now()
            self._save()

        KanbanGroupBridge.notify_task_event(board.room_id, "review_requested", task, actor=bot_name)
        return {"status": "ok", "task_id": task_id, "column": "in_review", "reviewer": reviewer_name}

    def approve_task(
        self,
        board_id: str,
        task_id: str,
        reviewer_name: str,
        comment: str = "Deliverables verified and accepted.",
    ) -> dict[str, Any]:
        """Approve task in review -> moves to 'done' and auto-unblocks dependent tasks."""
        board = self.get_or_create_board(board_id)
        unblocked_tasks: list[KanbanTask] = []

        with self._lock:
            task = board.tasks.get(task_id)
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}

            task.column = "done"
            task.review_verdict = "approved"
            task.review_comments.append({"reviewer": reviewer_name, "comment": comment, "at": _now()})
            task.updated_at = _now()

            # Automatic DAG unblocking
            unblockable = DependencyGraph.resolve_unblockable_tasks(board.tasks)
            for ut in unblockable:
                ut.column = "todo"
                ut.blocked_reason = None
                ut.updated_at = _now()
                unblocked_tasks.append(ut)

            self._save()

        KanbanGroupBridge.notify_task_event(board.room_id, "approved", task, actor=reviewer_name, detail=comment)
        for ut in unblocked_tasks:
            KanbanGroupBridge.notify_task_event(board.room_id, "unblocked", ut, detail="All prerequisites finished.")

        return {
            "status": "ok",
            "task_id": task_id,
            "column": "done",
            "unblocked_count": len(unblocked_tasks),
            "unblocked_tasks": [ut.task_id for ut in unblocked_tasks],
        }

    def request_changes(
        self,
        board_id: str,
        task_id: str,
        reviewer_name: str,
        feedback: str,
    ) -> dict[str, Any]:
        """Reject deliverables in review -> moves task back to 'in_progress'."""
        board = self.get_or_create_board(board_id)
        with self._lock:
            task = board.tasks.get(task_id)
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}

            task.column = "in_progress"
            task.review_verdict = "changes_requested"
            task.review_comments.append({"reviewer": reviewer_name, "comment": feedback, "at": _now()})
            task.updated_at = _now()
            self._save()

        KanbanGroupBridge.notify_task_event(board.room_id, "changes_requested", task, actor=reviewer_name, detail=feedback)
        return {"status": "ok", "task_id": task_id, "column": "in_progress", "verdict": "changes_requested"}

    def list_tasks(self, board_id: str = "main", column: TaskColumn | None = None) -> list[KanbanTask]:
        board = self.get_or_create_board(board_id)
        with self._lock:
            tasks = list(board.tasks.values())
        if column:
            return [t for t in tasks if t.column == column]
        return tasks


_global_kanban = KanbanStore()


def get_kanban_store() -> KanbanStore:
    return _global_kanban
