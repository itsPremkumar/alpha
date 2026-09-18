"""Organizational Event Store and Audit Trail (Master Inventory #53-#58).

Provides an append-only, durable audit log and in-memory query stream for organizational events
(task assignments, handoffs, escalations, reputation updates, and kill switch activations).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from agent_workspace.bots.profile import _now

logger = logging.getLogger(__name__)

_DEFAULT_EVENT_FILE = "bots/events.jsonl"
_MAX_IN_MEMORY_EVENTS = 250


def _resolve_events_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / _DEFAULT_EVENT_FILE
    except Exception:
        return Path.cwd() / ".agent_workspace" / _DEFAULT_EVENT_FILE


class OrgEventStore:
    """Thread-safe, append-only organizational event store."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.log_path = Path(log_path).resolve() if log_path else _resolve_events_path()
        self._lock = threading.Lock()
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=_MAX_IN_MEMORY_EVENTS)
        self._load_recent()

    def _load_recent(self) -> None:
        if not self.log_path.exists():
            return
        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-_MAX_IN_MEMORY_EVENTS:]:
                    clean = line.strip()
                    if clean:
                        try:
                            self._recent_events.append(json.loads(clean))
                        except Exception:
                            continue
        except Exception:
            logger.debug("Failed to load existing org events", exc_info=True)

    def append_event(
        self,
        event_type: str,
        actor: str,
        *,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a new immutable event to the audit log."""
        event = {
            "id": uuid.uuid4().hex[:12],
            "event_type": event_type,
            "actor": actor.lower().strip(),
            "target": target.lower().strip() if target else None,
            "timestamp": _now(),
            "details": details or {},
        }

        with self._lock:
            self._recent_events.append(event)
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            except Exception:
                logger.warning("Failed to persist org event to disk", exc_info=True)

        return event

    def query_events(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        actor: str | None = None,
        target: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query recent organizational events with optional filtering."""
        with self._lock:
            events = list(self._recent_events)

        # Apply filters
        filtered = events
        if event_type:
            et = event_type.lower().strip()
            filtered = [e for e in filtered if e.get("event_type") == et]
        if actor:
            act = actor.lower().strip()
            filtered = [e for e in filtered if e.get("actor") == act]
        if target:
            tgt = target.lower().strip()
            filtered = [e for e in filtered if e.get("target") == tgt]

        # Return latest first
        return list(reversed(filtered))[:limit]


_global_event_store: OrgEventStore | None = None


def get_org_event_store(log_path: Path | str | None = None) -> OrgEventStore:
    global _global_event_store
    if _global_event_store is None:
        _global_event_store = OrgEventStore(log_path)
    return _global_event_store


def log_org_event(
    event_type: str,
    actor: str,
    *,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Global convenience helper to log an organization event."""
    return get_org_event_store().append_event(
        event_type=event_type,
        actor=actor,
        target=target,
        details=details,
    )


def query_org_events(
    *,
    limit: int = 50,
    event_type: str | None = None,
    actor: str | None = None,
    target: str | None = None,
) -> list[dict[str, Any]]:
    """Global convenience helper to query organization events."""
    return get_org_event_store().query_events(
        limit=limit,
        event_type=event_type,
        actor=actor,
        target=target,
    )
