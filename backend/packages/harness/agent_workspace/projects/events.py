"""Canonical project event bus: every coordination fact is an append-only event.

Agents never maintain private understandings of project state; they read the
event log and the reduced `ProjectState`. Event types are additive — consumers
must ignore unknown types.
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

logger = logging.getLogger(__name__)

EVENT_TYPES = frozenset(
    {
        "agent_joined",
        "agent_left",
        "task_created",
        "task_assigned",
        "task_started",
        "task_blocked",
        "task_completed",
        "task_failed",
        "message_sent",
        "decision_made",
        "file_locked",
        "file_unlocked",
        "file_changed",
        "test_failed",
        "test_passed",
        "conflict_detected",
        "conflict_resolved",
        "handoff_created",
        "handoff_accepted",
        "room_synced",
        "collaboration_updated",
        "memory_compacted",
        "approval_requested",
        "approval_granted",
        "approval_rejected",
        "deployment_started",
        "deployment_completed",
        "constitution_updated",
        "phase_changed",
        "contract_created",
        "evidence_attached",
        "contract_rejected",
        "contract_completed",
        "task_postmortem_recorded",
    }
)


def _projects_root() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "projects"


def event_log_path(project_id: str) -> Path:
    return _projects_root() / project_id / "communication" / "events.jsonl"


@dataclass
class ProjectEvent:
    seq: int
    event_id: str
    project_id: str
    type: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectEvent:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class ProjectEventBus:
    """Append-only per-project event log with sequence numbers."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._path = event_log_path(project_id)
        self._lock = threading.Lock()
        self._seq = self._count_lines()

    def _count_lines(self) -> int:
        if not self._path.exists():
            return 0
        try:
            with open(self._path, encoding="utf-8") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def emit(self, type: str, actor: str, payload: dict[str, Any] | None = None) -> ProjectEvent:
        if type not in EVENT_TYPES:
            raise ValueError(f"Unknown project event type '{type}'")
        with self._lock:
            self._seq += 1
            ev = ProjectEvent(seq=self._seq, event_id=f"ev-{uuid.uuid4().hex[:10]}", project_id=self.project_id, type=type, actor=actor, payload=payload or {})
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev.to_dict()) + "\n")
            except Exception:
                logger.warning("Project event append failed for %s", self.project_id, exc_info=True)
            return ev

    def read(self, *, after_seq: int = 0, limit: int = 200, types: set[str] | None = None) -> list[ProjectEvent]:
        if not self._path.exists():
            return []
        out: list[ProjectEvent] = []
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = ProjectEvent.from_dict(json.loads(line))
                    except Exception:
                        continue
                    if ev.seq <= after_seq:
                        continue
                    if types and ev.type not in types:
                        continue
                    out.append(ev)
                    if len(out) >= limit:
                        break
        except Exception:
            logger.warning("Project event read failed for %s", self.project_id, exc_info=True)
        return out

    def search(self, query: str, *, limit: int = 50) -> list[ProjectEvent]:
        q = query.lower()
        if not self._path.exists():
            return []
        hits: list[ProjectEvent] = []
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or q not in line.lower():
                        continue
                    try:
                        hits.append(ProjectEvent.from_dict(json.loads(line)))
                    except Exception:
                        continue
                    if len(hits) >= limit:
                        break
        except Exception:
            logger.warning("Project event search failed for %s", self.project_id, exc_info=True)
        return hits


_buses: dict[str, ProjectEventBus] = {}
_buses_lock = threading.Lock()


def get_event_bus(project_id: str) -> ProjectEventBus:
    with _buses_lock:
        try:
            live = str(event_log_path(project_id).resolve())
        except Exception:
            live = None
        bus = _buses.get(project_id)
        if bus is None or (live and str(bus._path.resolve()) != live):
            bus = ProjectEventBus(project_id)
            _buses[project_id] = bus
        return bus
