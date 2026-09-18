"""Cron incident tracking: consecutive-failure log with auto-pause.

Every failed occurrence is recorded with its error excerpt. A task that
fails N times in a row auto-pauses (with an incident row explaining why)
instead of burning budget forever. Recovery is explicit: fix, resume.
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

DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
_ERROR_EXCERPT_CHARS = 500


def _default_storage_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "scheduler" / "incidents.json"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "scheduler" / "incidents.json"


@dataclass
class Incident:
    incident_id: str
    task_id: str
    error_excerpt: str
    consecutive_failures: int
    auto_paused: bool = False
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Incident:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class IncidentTracker:
    def __init__(self, storage_path: str | Path | None = None, *, max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self.max_consecutive_failures = max_consecutive_failures
        self._incidents: list[Incident] = []
        self._streaks: dict[str, int] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._incidents = [Incident.from_dict(i) for i in data.get("incidents", [])]
            self._streaks = dict(data.get("streaks", {}))
        except Exception:
            logger.warning("Incident tracker load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "incidents": [i.to_dict() for i in self._incidents], "streaks": self._streaks}, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Incident tracker save failed", exc_info=True)

    def record_success(self, task_id: str) -> None:
        with self._lock:
            self._streaks.pop(task_id, None)
            self._save()

    def record_failure(self, task_id: str, error: str) -> Incident:
        """Log a failure; auto-pause trips at the consecutive limit. Returns the incident."""
        with self._lock:
            streak = self._streaks.get(task_id, 0) + 1
            self._streaks[task_id] = streak
            incident = Incident(
                incident_id=f"inc-{uuid.uuid4().hex[:10]}",
                task_id=task_id,
                error_excerpt=(error or "")[:_ERROR_EXCERPT_CHARS],
                consecutive_failures=streak,
                auto_paused=streak >= self.max_consecutive_failures,
            )
            self._incidents.append(incident)
            self._save()
            return incident

    def should_pause(self, task_id: str) -> bool:
        with self._lock:
            return self._streaks.get(task_id, 0) >= self.max_consecutive_failures

    def resolve(self, incident_id: str) -> Incident | None:
        with self._lock:
            for incident in self._incidents:
                if incident.incident_id == incident_id and incident.resolved_at is None:
                    incident.resolved_at = time.time()
                    self._streaks.pop(incident.task_id, None)
                    self._save()
                    return incident
            return None

    def list(self, *, task_id: str | None = None, unresolved_only: bool = False, limit: int = 100) -> list[Incident]:
        with self._lock:
            rows = list(self._incidents)
        if task_id:
            rows = [r for r in rows if r.task_id == task_id]
        if unresolved_only:
            rows = [r for r in rows if r.resolved_at is None]
        return sorted(rows, key=lambda r: -r.created_at)[:limit]


_tracker: IncidentTracker | None = None
_tracker_path: str | None = None
_tracker_lock = threading.Lock()


def get_incident_tracker() -> IncidentTracker:
    global _tracker, _tracker_path
    with _tracker_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _tracker is None or _tracker_path != live:
            _tracker = IncidentTracker()
            try:
                _tracker_path = str(_tracker.storage_path.resolve())
            except Exception:
                _tracker_path = live
        return _tracker
