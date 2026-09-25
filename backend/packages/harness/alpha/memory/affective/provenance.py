"""Best-effort append-only provenance for affective ingest and mood reads."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AffectEvent, IngestResult, MoodState
from .paths import affective_root, provenance_log_path

logger = logging.getLogger(__name__)

_append_lock = threading.Lock()


def append_entry(
    entry: dict[str, Any],
    *,
    user_id: str | None,
    storage_path: str | None,
    now: float | None = None,
) -> Path | None:
    """Append one JSONL record; return its path, or ``None`` after a disclosed failure."""
    timestamp = time.time() if now is None else float(now)
    try:
        payload = dict(entry)
        moment = datetime.fromtimestamp(timestamp, tz=UTC)
        payload.setdefault("ts", moment.isoformat())
        day = moment.strftime("%Y-%m-%d")
        path = provenance_log_path(affective_root(storage_path), user_id, day)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path
    except Exception as exc:  # noqa: BLE001 - provenance is explicitly best-effort
        logger.warning("Affective provenance append failed (%s)", exc)
        return None


def append_event_ingest(
    event: AffectEvent,
    result: IngestResult,
    *,
    storage_path: str,
    now: float | None = None,
) -> Path | None:
    """Record every explicit/caller/model event ingest attempt."""
    return append_entry(
        {
            "action": "event_ingest",
            "event_id": event.id,
            "event": event.model_dump(mode="json"),
            "user_id": event.user_id,
            "agent_name": event.agent_name or "",
            "subject": event.subject.value,
            "source": event.source.value,
            "status": result.status.value,
            "confidence": event.confidence,
            "intensity": event.intensity,
            "clamped_fields": list(event.clamped_fields),
            "clamp_disclosure": event.clamp_disclosure,
        },
        user_id=event.user_id,
        storage_path=storage_path,
        now=now,
    )


def append_mood_computation(
    mood: MoodState | None,
    *,
    status: str,
    user_id: str | None,
    storage_path: str,
    event_count: int,
    now: float | None = None,
    error: str = "",
) -> Path | None:
    """Record each store-backed mood computation, including unavailable states."""
    entry: dict[str, Any] = {
        "action": "mood_computation",
        "status": status,
        "user_id": user_id or "",
        "event_count": event_count,
        "error": error,
    }
    if mood is not None:
        entry["mood"] = mood.model_dump(mode="json")
    return append_entry(
        entry,
        user_id=user_id,
        storage_path=storage_path,
        now=now,
    )


def read_entries(
    day: str,
    *,
    user_id: str | None = None,
    storage_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read one provenance day without raising on I/O or malformed lines."""
    try:
        path = provenance_log_path(affective_root(storage_path), user_id, day)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and str(item.get("user_id") or "") == (user_id or ""):
                entries.append(item)
        return entries
    except Exception as exc:  # noqa: BLE001 - read-side provenance is best-effort too
        logger.warning("Affective provenance read failed (%s)", exc)
        return []


__all__ = [
    "append_entry",
    "append_event_ingest",
    "append_mood_computation",
    "read_entries",
]
