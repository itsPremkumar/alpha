"""Project-scoped handoffs: structured work transfers with durable records.

Wraps the bot handoff protocol so every handoff carries objective, findings,
files, decisions, risks, tests, and the recommended next action — persisted
per project and visible on the event bus.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from alpha.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _projects_root() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".alpha" / "projects"


@dataclass
class HandoffRecord:
    handoff_id: str
    project_id: str
    task_id: str
    from_bot: str
    to_bot: str
    objective: str
    completed_work: str = ""
    findings: str = ""
    files_modified: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    remaining_work: str = ""
    known_risks: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    recommended_next_action: str = ""
    status: str = "open"
    created_at: float = field(default_factory=time.time)
    accepted_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffRecord:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class HandoffStore:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self._path = _projects_root() / project_id / "tasks" / "handoffs.json"
        self._lock = threading.Lock()
        self._rows: dict[str, HandoffRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("handoffs", []):
                r = HandoffRecord.from_dict(item)
                self._rows[r.handoff_id] = r
        except Exception:
            logger.warning("Handoff load failed for %s", self.project_id, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "handoffs": [r.to_dict() for r in self._rows.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            logger.warning("Handoff save failed for %s", self.project_id, exc_info=True)

    def create(self, task_id: str, from_bot: str, to_bot: str, objective: str, **kwargs: Any) -> HandoffRecord:
        from alpha.bots.handoff import execute_handoff

        execute_handoff(task_id, from_bot, to_bot, objective, context_summary=kwargs.get("completed_work", ""), artifacts=kwargs.get("files_modified"))
        rec = HandoffRecord(
            handoff_id=f"ho-{uuid.uuid4().hex[:10]}",
            project_id=self.project_id,
            task_id=task_id,
            from_bot=from_bot.lower().strip(),
            to_bot=to_bot.lower().strip(),
            objective=objective,
            completed_work=str(kwargs.get("completed_work", "")),
            findings=str(kwargs.get("findings", "")),
            files_modified=list(kwargs.get("files_modified", [])),
            decisions=list(kwargs.get("decisions", [])),
            remaining_work=str(kwargs.get("remaining_work", "")),
            known_risks=list(kwargs.get("known_risks", [])),
            tests=list(kwargs.get("tests", [])),
            recommended_next_action=str(kwargs.get("recommended_next_action", "")),
        )
        with self._lock:
            self._rows[rec.handoff_id] = rec
            self._save()
        get_event_bus(self.project_id).emit("handoff_created", rec.from_bot, {"handoff_id": rec.handoff_id, "task_id": task_id, "to_bot": rec.to_bot, "objective": objective})
        return rec

    def accept(self, handoff_id: str, to_bot: str) -> HandoffRecord | None:
        with self._lock:
            rec = self._rows.get(handoff_id)
            if not rec or rec.to_bot != to_bot.lower().strip() or rec.status != "open":
                return None
            rec.status = "accepted"
            rec.accepted_at = time.time()
            self._save()
        get_event_bus(self.project_id).emit("handoff_accepted", rec.to_bot, {"handoff_id": handoff_id, "task_id": rec.task_id})
        return rec

    def list(self, *, status: str | None = None) -> list[HandoffRecord]:
        with self._lock:
            rows = list(self._rows.values())
        if status:
            rows = [r for r in rows if r.status == status]
        return sorted(rows, key=lambda r: r.created_at)


_stores: dict[str, HandoffStore] = {}
_stores_lock = threading.Lock()


def get_handoff_store(project_id: str) -> HandoffStore:
    with _stores_lock:
        store = _stores.get(project_id)
        try:
            live = str((_projects_root() / project_id / "tasks" / "handoffs.json").resolve())
        except Exception:
            live = None
        if store is None or (live and str(store._path.resolve()) != live):
            store = HandoffStore(project_id)
            _stores[project_id] = store
        return store
