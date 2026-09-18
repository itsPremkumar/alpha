"""Durable missions: long-lived objectives above threads, below the UI.

A mission survives restarts, links the threads/runs working toward it, and
carries an artifact manifest. Lifecycle: draft -> active -> paused ->
active -> completed | cancelled.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

MissionStatus = Literal["draft", "active", "paused", "completed", "cancelled"]

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "cancelled"},
    "active": {"paused", "completed", "cancelled"},
    "paused": {"active", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _default_storage_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "missions" / "missions.json"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "missions" / "missions.json"


@dataclass
class Mission:
    mission_id: str
    owner: str
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    status: MissionStatus = "draft"
    thread_ids: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mission:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class MissionStore:
    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[str, Mission] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in data.get("missions", []):
                m = Mission.from_dict(item)
                self._rows[m.mission_id] = m
        except Exception:
            logger.warning("Mission load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "missions": [m.to_dict() for m in self._rows.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Mission save failed", exc_info=True)

    def create(self, owner: str, objective: str, *, constraints: dict[str, Any] | None = None, budget: dict[str, Any] | None = None) -> Mission:
        m = Mission(mission_id=f"msn-{uuid.uuid4().hex[:10]}", owner=owner, objective=objective, constraints=constraints or {}, budget=budget or {})
        with self._lock:
            self._rows[m.mission_id] = m
            self._save()
        return m

    def get(self, mission_id: str) -> Mission | None:
        with self._lock:
            return self._rows.get(mission_id)

    def list(self, *, owner: str | None = None, status: str | None = None) -> list[Mission]:
        with self._lock:
            rows = list(self._rows.values())
        if owner:
            rows = [m for m in rows if m.owner == owner]
        if status:
            rows = [m for m in rows if m.status == status]
        return sorted(rows, key=lambda m: -m.created_at)

    def transition(self, mission_id: str, to: MissionStatus) -> Mission | None:
        with self._lock:
            m = self._rows.get(mission_id)
            if not m or to not in VALID_TRANSITIONS.get(m.status, set()):
                return None
            m.status = to
            m.updated_at = time.time()
            self._save()
            return m

    def attach_thread(self, mission_id: str, thread_id: str) -> Mission | None:
        with self._lock:
            m = self._rows.get(mission_id)
            if not m:
                return None
            if thread_id not in m.thread_ids:
                m.thread_ids.append(thread_id)
                m.updated_at = time.time()
                self._save()
            return m

    def attach_artifact(self, mission_id: str, artifact: str) -> Mission | None:
        with self._lock:
            m = self._rows.get(mission_id)
            if not m:
                return None
            if artifact not in m.artifacts:
                m.artifacts.append(artifact)
                m.updated_at = time.time()
                self._save()
            return m


_store: MissionStore | None = None
_store_path: str | None = None
_store_lock = threading.Lock()


def get_mission_store() -> MissionStore:
    global _store, _store_path
    with _store_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _store is None or _store_path != live:
            _store = MissionStore()
            try:
                _store_path = str(_store.storage_path.resolve())
            except Exception:
                _store_path = live
        return _store
