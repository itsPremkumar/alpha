"""Agent <-> project assignments: reusable workers join/leave shared workspaces.

An agent may belong to many projects; a project may host many agents.
Assignment is a temporary relationship, never a permanent binding.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

MembershipStatus = Literal["active", "idle", "offline", "not_joined"]

_DEFAULT_DIR = "projects"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _default_storage_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / _DEFAULT_DIR / "membership.json"
    except Exception:
        return Path.cwd() / ".agent_workspace" / _DEFAULT_DIR / "membership.json"


@dataclass
class Membership:
    """One agent's participation in one project."""

    project_id: str
    bot_name: str
    role_in_project: str = "worker"
    status: MembershipStatus = "active"
    current_task_id: str | None = None
    blocked_reason: str | None = None
    joined_at: str = field(default_factory=_now)
    last_activity: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Membership:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class MembershipStore:
    """Thread-safe, file-backed agent<->project roster."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[tuple[str, str], Membership] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("memberships", []):
                m = Membership.from_dict(item)
                self._rows[(m.project_id, m.bot_name.lower())] = m
        except Exception:
            logger.warning("Project membership load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "memberships": [m.to_dict() for m in self._rows.values()], "updated_at": _now()}, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Project membership save failed", exc_info=True)

    def join(self, project_id: str, bot_name: str, role_in_project: str = "worker") -> Membership:
        key = (project_id, bot_name.lower().strip())
        with self._lock:
            existing = self._rows.get(key)
            if existing:
                existing.status = "active"
                existing.role_in_project = role_in_project or existing.role_in_project
                existing.last_activity = _now()
                self._save()
                return existing
            m = Membership(project_id=project_id, bot_name=key[1], role_in_project=role_in_project)
            self._rows[key] = m
            self._save()
            return m

    def leave(self, project_id: str, bot_name: str) -> bool:
        key = (project_id, bot_name.lower().strip())
        with self._lock:
            if key not in self._rows:
                return False
            del self._rows[key]
            self._save()
            return True

    def heartbeat(
        self,
        project_id: str,
        bot_name: str,
        *,
        status: MembershipStatus | None = None,
        current_task_id: str | None = None,
        blocked_reason: str | None = None,
    ) -> Membership | None:
        key = (project_id, bot_name.lower().strip())
        with self._lock:
            m = self._rows.get(key)
            if not m:
                return None
            if status:
                m.status = status
            if current_task_id is not None:
                m.current_task_id = current_task_id or None
            if blocked_reason is not None:
                m.blocked_reason = blocked_reason or None
            m.last_activity = _now()
            self._save()
            return m

    def presence(self, project_id: str) -> list[Membership]:
        with self._lock:
            return sorted(
                [m for (pid, _), m in self._rows.items() if pid == project_id],
                key=lambda m: m.joined_at,
            )

    def projects_for_bot(self, bot_name: str) -> list[Membership]:
        key = bot_name.lower().strip()
        with self._lock:
            return [m for (_, b), m in self._rows.items() if b == key]

    def member_count(self, project_id: str) -> int:
        return len(self.presence(project_id))


_store: MembershipStore | None = None
_store_path: str | None = None
_store_lock = threading.Lock()


def get_membership_store() -> MembershipStore:
    global _store, _store_path
    with _store_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _store is None or _store_path != live:
            _store = MembershipStore()
            try:
                _store_path = str(_store.storage_path.resolve())
            except Exception:
                _store_path = live
        return _store
