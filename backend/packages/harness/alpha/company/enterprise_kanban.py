"""Hermes Kanban Adapter: Synchronizes tasks and activity audit logs with Hermes SQLite Kanban boards."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from alpha.company.models import CompanyProject

logger = logging.getLogger(__name__)

DEFAULT_HERMES_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DEFAULT_BOARD_DB = DEFAULT_HERMES_DIR / "kanban" / "boards" / "it-company-ops" / "kanban.db"


class EnterpriseKanbanAdapter:
    """Reads, writes, syncs tasks, and logs agent activity events with local SQLite kanban database."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path:
            self._db_path = Path(db_path)
        else:
            self._db_path = DEFAULT_BOARD_DB

        # In-memory storage when offline or running in mock container
        self._synthetic_tasks: dict[str, dict[str, Any]] = {}
        self._synthetic_events: list[dict[str, Any]] = []

    @property
    def is_available(self) -> bool:
        return self._db_path.exists()

    def list_tasks(self, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        """Reads recent tasks from local Hermes SQLite kanban board."""
        if not self.is_available:
            tasks = list(self._synthetic_tasks.values())
            if status:
                tasks = [t for t in tasks if t.get("status", "").lower() == status.lower()]
            return tasks[:limit]

        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if status:
                rows = cursor.execute(
                    "SELECT * FROM tasks WHERE LOWER(status) = LOWER(?) ORDER BY rowid DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = cursor.execute(
                    "SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            tasks = [dict(r) for r in rows]
            conn.close()
            return tasks
        except Exception as exc:
            logger.warning(f"Error reading Hermes kanban tasks: {exc}")
            return list(self._synthetic_tasks.values())[:limit]

    def create_task(
        self,
        title: str,
        body: str = "",
        assignee: str = "cto",
        priority: int | str = 0,
        status: str = "ready",
    ) -> str:
        """Creates a card in the local Hermes SQLite kanban board."""
        task_id = f"t-{uuid.uuid4().hex[:6]}"
        now_ts = time.time()
        prio_val = int(priority) if isinstance(priority, int) or (isinstance(priority, str) and priority.isdigit()) else 1

        if not self.is_available:
            self._synthetic_tasks[task_id] = {
                "id": task_id,
                "title": title,
                "body": body,
                "status": status,
                "assignee": assignee,
                "priority": prio_val,
                "created_at": now_ts,
                "updated_at": now_ts,
                "result": "",
            }
            return task_id

        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO tasks (id, title, body, status, assignee, priority, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, title, body, status, assignee, prio_val, now_ts, now_ts),
            )
            conn.commit()
            conn.close()
            logger.info(f"Created task '{task_id}' in Hermes kanban for @{assignee}")
            return task_id
        except Exception as exc:
            logger.warning(f"Error writing to Hermes kanban.db: {exc}")
            self._synthetic_tasks[task_id] = {
                "id": task_id,
                "title": title,
                "body": body,
                "status": status,
                "assignee": assignee,
                "priority": prio_val,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
            return task_id

    def update_task_status(
        self,
        task_id: str,
        new_status: str,
        bot_name: str = "system",
        log_message: str = "",
        result: str = "",
    ) -> dict[str, Any]:
        """Updates task status and writes an audit event log."""
        now_ts = time.time()
        old_status = "unknown"

        if not self.is_available:
            t = self._synthetic_tasks.get(task_id)
            if t:
                old_status = t.get("status", "unknown")
                t["status"] = new_status
                t["updated_at"] = now_ts
                if result:
                    t["result"] = result
            event = self.add_task_event(
                task_id=task_id,
                kind="status_changed",
                bot_name=bot_name,
                message=log_message or f"Status changed: {old_status} -> {new_status}",
                payload_data={"old_status": old_status, "new_status": new_status, "result": result},
            )
            return {"task_id": task_id, "status": new_status, "event": event}

        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            row = cursor.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row:
                old_status = row["status"]

            cursor.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, result = COALESCE(NULLIF(?, ''), result) WHERE id = ?",
                (new_status, now_ts, result, task_id),
            )
            conn.commit()
            conn.close()

            event = self.add_task_event(
                task_id=task_id,
                kind="status_changed",
                bot_name=bot_name,
                message=log_message or f"Status changed: {old_status} -> {new_status}",
                payload_data={"old_status": old_status, "new_status": new_status, "result": result},
            )
            logger.info(f"Updated task '{task_id}' -> {new_status} by @{bot_name}")
            return {"task_id": task_id, "status": new_status, "old_status": old_status, "event": event}
        except Exception as exc:
            logger.warning(f"Error updating task {task_id}: {exc}")
            return {"task_id": task_id, "status": new_status, "error": str(exc)}

    def add_task_event(
        self,
        task_id: str,
        kind: str,
        bot_name: str,
        message: str,
        payload_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Appends an operational activity log to task_events in SQLite or synthetic storage."""
        now_ts = time.time()
        full_payload = {
            "bot_name": bot_name,
            "message": message,
            **(payload_data or {}),
        }
        payload_str = json.dumps(full_payload)

        event_record = {
            "task_id": task_id,
            "kind": kind,
            "bot_name": bot_name,
            "message": message,
            "payload": full_payload,
            "created_at": now_ts,
        }

        if not self.is_available:
            event_record["id"] = len(self._synthetic_events) + 1
            self._synthetic_events.append(event_record)
            return event_record

        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (task_id, kind, payload_str, now_ts),
            )
            inserted_id = cursor.lastrowid
            conn.commit()
            conn.close()
            event_record["id"] = inserted_id
            return event_record
        except Exception as exc:
            logger.warning(f"Error logging task event to Hermes kanban: {exc}")
            event_record["id"] = len(self._synthetic_events) + 1
            self._synthetic_events.append(event_record)
            return event_record

    def list_task_events(self, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieves recent task activity and audit events."""
        if not self.is_available:
            events = list(self._synthetic_events)
            if task_id:
                events = [e for e in events if e.get("task_id") == task_id]
            return list(reversed(events))[:limit]

        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if task_id:
                rows = cursor.execute(
                    "SELECT id, task_id, kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = cursor.execute(
                    "SELECT id, task_id, kind, payload, created_at FROM task_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            results = []
            for r in rows:
                item = dict(r)
                try:
                    parsed = json.loads(item["payload"])
                    item["parsed_payload"] = parsed
                    item["bot_name"] = parsed.get("bot_name", "unknown")
                    item["message"] = parsed.get("message", "")
                except Exception:
                    item["parsed_payload"] = {}
                    item["bot_name"] = "unknown"
                    item["message"] = item["payload"]
                results.append(item)
            conn.close()
            return results
        except Exception as exc:
            logger.warning(f"Error reading Hermes task_events: {exc}")
            return []

    def get_agent_tasks(self, bot_name: str, status: str | None = None) -> list[dict[str, Any]]:
        """Retrieves all tasks assigned to a specific bot profile."""
        clean_bot = bot_name.strip().lower().replace("bot-", "")
        all_tasks = self.list_tasks(limit=100, status=status)
        matching = []
        for t in all_tasks:
            assignee = (t.get("assignee") or "").lower().replace("bot-", "")
            if assignee == clean_bot:
                matching.append(t)
        return matching

    def agent_check_in(
        self,
        bot_name: str,
        current_task_id: str | None = None,
        progress_notes: str = "",
        new_status: str | None = None,
    ) -> dict[str, Any]:
        """Allows an agent to regularly check in on its queue, log progress, and claim work."""
        clean_bot = bot_name.strip().lower()
        now_ts = time.time()

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
                self.add_task_event(
                    task_id=current_task_id,
                    kind="progress_log",
                    bot_name=clean_bot,
                    message=progress_notes or f"Agent @{clean_bot} check-in: active progress",
                )

        assigned_tasks = self.get_agent_tasks(bot_name=clean_bot)
        ready_tasks = [t for t in assigned_tasks if t.get("status") in ("ready", "todo")]
        in_progress = [t for t in assigned_tasks if t.get("status") == "in_progress"]

        return {
            "bot_name": clean_bot,
            "check_in_timestamp": now_ts,
            "current_task_id": current_task_id,
            "active_tasks_count": len(in_progress),
            "pending_ready_count": len(ready_tasks),
            "assigned_tasks": assigned_tasks,
            "updated_task": updated_task,
        }

    def sync_projects_to_kanban(self, projects: list[CompanyProject]) -> dict[str, Any]:
        """Ensures all active CompanyProjects have corresponding tasks in the Hermes kanban board."""
        existing_tasks = {t["title"]: t for t in self.list_tasks(limit=100)}
        synced_count = 0
        new_count = 0

        for p in projects:
            if p.name in existing_tasks:
                synced_count += 1
            else:
                tid = self.create_task(
                    title=p.name,
                    body=f"Initiative owned by {p.department} led by @{p.lead_bot_name}",
                    assignee=p.lead_bot_name.replace("bot-", ""),
                    priority=1,
                    status="ready",
                )
                self.add_task_event(
                    task_id=tid,
                    kind="project_initialized",
                    bot_name=p.lead_bot_name,
                    message=f"Project '{p.name}' auto-synced to Kanban board for @{p.lead_bot_name}",
                )
                new_count += 1

        return {
            "synced_existing": synced_count,
            "created_new": new_count,
            "total_company_projects": len(projects),
            "kanban_available": self.is_available,
        }


# Transparent alias for backwards compatibility
HermesKanbanAdapter = EnterpriseKanbanAdapter
