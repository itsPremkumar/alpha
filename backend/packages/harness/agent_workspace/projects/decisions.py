"""Shared project memory: durable architectural decision records (ADRs).

Decisions made in chat or council become searchable project memory so agents
retrieve them instead of rediscovering the same answers.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_workspace.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _projects_root() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "projects"


def decisions_path(project_id: str) -> Path:
    return _projects_root() / project_id / "decisions" / "decisions.json"


@dataclass
class Decision:
    decision_id: str
    title: str
    body: str
    reason: str = ""
    made_by: str = ""
    approved_by: str | None = None
    arch_version: str | None = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class DecisionLog:
    """File-backed ADR log with full-text search."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._path = decisions_path(project_id)
        self._lock = threading.Lock()
        self._rows: list[Decision] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._rows = [Decision.from_dict(d) for d in data.get("decisions", [])]
        except Exception:
            logger.warning("Decision log load failed for %s", self.project_id, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "decisions": [d.to_dict() for d in self._rows]}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            logger.warning("Decision log save failed for %s", self.project_id, exc_info=True)

    def record(self, title: str, body: str, *, reason: str = "", made_by: str = "", approved_by: str | None = None, arch_version: str | None = None) -> Decision:
        with self._lock:
            seq = len(self._rows) + 1
            d = Decision(decision_id=f"ADR-{seq:03d}", title=title, body=body, reason=reason, made_by=made_by, approved_by=approved_by, arch_version=arch_version)
            self._rows.append(d)
            self._save()
        get_event_bus(self.project_id).emit("decision_made", made_by or "system", {"decision_id": d.decision_id, "title": title, "arch_version": arch_version})
        return d

    def list(self) -> list[Decision]:
        with self._lock:
            return list(self._rows)

    def search(self, query: str, *, limit: int = 20) -> list[Decision]:
        q = query.lower()
        with self._lock:
            scored = [(d, sum(1 for part in (d.title, d.body, d.reason) if q in part.lower())) for d in self._rows]
        hits = sorted(((d, s) for d, s in scored if s > 0), key=lambda t: -t[1])
        return [d for d, _ in hits[:limit]]


_logs: dict[str, DecisionLog] = {}
_logs_lock = threading.Lock()


def get_decision_log(project_id: str) -> DecisionLog:
    with _logs_lock:
        try:
            live = str(decisions_path(project_id).resolve())
        except Exception:
            live = None
        log = _logs.get(project_id)
        if log is None or (live and str(log._path.resolve()) != live):
            log = DecisionLog(project_id)
            _logs[project_id] = log
        return log
