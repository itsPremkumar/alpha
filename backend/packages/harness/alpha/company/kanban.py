"""Native Autonomous Kanban Engine & Audit Activity Logging for Alpha Organizations."""

from __future__ import annotations

import logging
import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from alpha.company.models import CompanyProject

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class KanbanTask(BaseModel):
    id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    title: str
    body: str = ""
    status: TaskStatus = TaskStatus.TODO
    assignee: str = "bot-cto"
    priority: TaskPriority = TaskPriority.NORMAL
    department: str = "engineering"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    result: str = ""
    tags: list[str] = Field(default_factory=list)


class KanbanActivityLog(BaseModel):
    event_id: str = Field(default_factory=lambda: f"ev-{uuid.uuid4().hex[:8]}")
    task_id: str
    bot_name: str
    kind: str = "progress_log"  # task_created, status_changed, progress_log, sign_off, blocker
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class CompanyKanbanEngine:
    """Enterprise-grade Native Kanban Engine and Audit Event Logger.

    Enables agents to continuously inspect active workloads, claim tasks, log progress,
    and transition task states without third-party file-locking bottlenecks.
    """

    def __init__(self, org_id: str = "org-default"):
        self.org_id = org_id
        # task_id -> KanbanTask
        self._tasks: dict[str, KanbanTask] = {}
        # Chronological audit activity logs
        self._activity_logs: list[KanbanActivityLog] = []

    def create_task(
        self,
        title: str,
        body: str = "",
        assignee: str = "bot-cto",
        priority: TaskPriority | str = TaskPriority.NORMAL,
        status: TaskStatus | str = TaskStatus.TODO,
        department: str = "engineering",
        tags: list[str] | None = None,
    ) -> KanbanTask:
        """Creates a new Kanban card and appends an audit event."""
        prio = TaskPriority(priority.lower()) if isinstance(priority, str) else priority
        stat = TaskStatus(status.lower()) if isinstance(status, str) else status
        now = time.time()

        task = KanbanTask(
            title=title,
            body=body,
            status=stat,
            assignee=assignee.strip().lower(),
            priority=prio,
            department=department,
            created_at=now,
            updated_at=now,
            tags=tags or [],
        )
        self._tasks[task.id] = task

        self.add_activity_log(
            task_id=task.id,
            bot_name=assignee,
            message=f"Created task '{title}' assigned to @{assignee}",
            kind="task_created",
            payload={"initial_status": stat.value, "priority": prio.value},
        )
        logger.info(f"Created Kanban task '{task.id}': '{title}' (@{assignee})")
        return task

    def get_task(self, task_id: str) -> KanbanTask | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        department: str | None = None,
        limit: int = 100,
    ) -> list[KanbanTask]:
        """Lists tasks matching optional filter criteria."""
        tasks = list(self._tasks.values())

        if status:
            clean_status = status.strip().lower()
            tasks = [t for t in tasks if t.status.value.lower() == clean_status or (clean_status == "ready" and t.status.value in ("todo", "backlog"))]

        if assignee:
            clean_assignee = assignee.strip().lower().replace("bot-", "")
            tasks = [t for t in tasks if t.assignee.replace("bot-", "").lower() == clean_assignee]

        if department:
            clean_dept = department.strip().lower()
            tasks = [t for t in tasks if t.department.lower() == clean_dept]

        # Order by priority descending and updated_at descending (the comment
        # was a lie until now: tasks came back in insertion order, so a full
        # board silently dropped the highest-priority tasks at ``limit``).
        rank = {TaskPriority.LOW: 0, TaskPriority.NORMAL: 1, TaskPriority.HIGH: 2, TaskPriority.CRITICAL: 3}
        tasks.sort(key=lambda t: (rank[t.priority], t.updated_at), reverse=True)
        return tasks[:limit]

    def update_task_status(
        self,
        task_id: str,
        new_status: TaskStatus | str,
        bot_name: str = "system",
        log_message: str = "",
        result: str = "",
    ) -> dict[str, Any]:
        """Updates a task status, records deliverables, and appends an audit event log."""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Kanban task '{task_id}' not found.")

        old_status = task.status.value
        parsed_status = TaskStatus(new_status.lower()) if isinstance(new_status, str) else new_status
        now = time.time()

        task.status = parsed_status
        task.updated_at = now
        if result:
            task.result = result

        comment = log_message or f"Status changed from {old_status} to {parsed_status.value}"
        event = self.add_activity_log(
            task_id=task_id,
            bot_name=bot_name,
            message=comment,
            kind="status_changed",
            payload={"old_status": old_status, "new_status": parsed_status.value, "result": result},
        )

        logger.info(f"Kanban task '{task_id}' transitioned {old_status} -> {parsed_status.value} by @{bot_name}")
        return {
            "task_id": task_id,
            "status": parsed_status.value,
            "old_status": old_status,
            "updated_at": now,
            "event": event.model_dump(),
        }

    def add_activity_log(
        self,
        task_id: str,
        bot_name: str,
        message: str,
        kind: str = "progress_log",
        payload: dict[str, Any] | None = None,
    ) -> KanbanActivityLog:
        """Appends an operational activity log to the timeline."""
        event = KanbanActivityLog(
            task_id=task_id,
            bot_name=bot_name.strip().lower(),
            kind=kind,
            message=message,
            payload=payload or {},
            created_at=time.time(),
        )
        self._activity_logs.append(event)
        return event

    def list_activity_logs(
        self,
        task_id: str | None = None,
        bot_name: str | None = None,
        limit: int = 50,
    ) -> list[KanbanActivityLog]:
        """Retrieves audit timeline events, optionally filtered by task or agent."""
        logs = list(self._activity_logs)

        if task_id:
            logs = [entry for entry in logs if entry.task_id == task_id]

        if bot_name:
            clean_bot = bot_name.strip().lower().replace("bot-", "")
            logs = [entry for entry in logs if entry.bot_name.replace("bot-", "").lower() == clean_bot]

        return list(reversed(logs))[:limit]

    def agent_check_in(
        self,
        bot_name: str,
        current_task_id: str | None = None,
        progress_notes: str = "",
        new_status: str | None = None,
    ) -> dict[str, Any]:
        """Allows an agent to regularly check in on its queue, log progress, claim tasks, and update status."""
        clean_bot = bot_name.strip().lower()
        now = time.time()

        updated_task = None
        if current_task_id:
            if new_status:
                updated_task = self.update_task_status(
                    task_id=current_task_id,
                    new_status=new_status,
                    bot_name=clean_bot,
                    log_message=progress_notes or f"Agent @{clean_bot} check-in update",
                )
            else:
                self.add_activity_log(
                    task_id=current_task_id,
                    bot_name=clean_bot,
                    message=progress_notes or f"Agent @{clean_bot} routine progress check-in",
                    kind="progress_log",
                )

        assigned_tasks = self.list_tasks(assignee=clean_bot)
        ready_tasks = [t for t in assigned_tasks if t.status in (TaskStatus.TODO, TaskStatus.BACKLOG)]
        in_progress = [t for t in assigned_tasks if t.status == TaskStatus.IN_PROGRESS]

        return {
            "bot_name": clean_bot,
            "check_in_timestamp": now,
            "current_task_id": current_task_id,
            "active_tasks_count": len(in_progress),
            "pending_ready_count": len(ready_tasks),
            "assigned_tasks": [t.model_dump() for t in assigned_tasks],
            "updated_task": updated_task,
        }

    def sync_projects_to_kanban(self, projects: list[CompanyProject]) -> dict[str, Any]:
        """Ensures all CompanyProjects are reflected as native Kanban task cards."""
        existing_titles = {t.title: t for t in self._tasks.values()}
        synced_count = 0
        new_count = 0

        for p in projects:
            if p.name in existing_titles:
                synced_count += 1
            else:
                task = self.create_task(
                    title=p.name,
                    body=f"Initiative owned by {p.department} led by @{p.lead_bot_name}",
                    assignee=p.lead_bot_name,
                    priority=TaskPriority.HIGH,
                    status=TaskStatus.TODO,
                    department=p.department,
                )
                # Register immediately so two projects sharing a name in the
                # same batch do not each mint a duplicate card.
                existing_titles[p.name] = task
                new_count += 1

        return {
            "synced_existing": synced_count,
            "created_new": new_count,
            "total_company_projects": len(projects),
            "total_kanban_tasks": len(self._tasks),
        }
