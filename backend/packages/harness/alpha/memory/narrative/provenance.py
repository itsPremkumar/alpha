"""Append-only per-day provenance for narrative ingests and regenerations."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import atomic_write_text, narrative_root, provenance_log_path

logger = logging.getLogger(__name__)
_append_lock = threading.Lock()


def _day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def append_entry(
    entry: Mapping[str, Any],
    *,
    scope: Any = "user",
    scope_id: str | None = None,
    storage_path: str | None = None,
    now: float | None = None,
) -> Path | None:
    """Append one JSONL record without allowing provenance to break a run."""

    timestamp = time.time() if now is None else float(now)
    try:
        payload = dict(entry)
        payload.setdefault("ts", datetime.fromtimestamp(timestamp, tz=UTC).isoformat())
        path = provenance_log_path(narrative_root(storage_path), scope, _day(timestamp), scope_id)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path
    except Exception as exc:  # noqa: BLE001 - provenance is explicitly best-effort
        logger.warning("Narrative provenance append failed (%s)", exc)
        return None


def append_ingest(
    *,
    scope: Any = "user",
    scope_id: str | None = None,
    event_count: int,
    stored_count: int,
    status: str,
    storage_path: str | None = None,
    now: float | None = None,
    event_ids: list[str] | None = None,
    evicted_event_ids: list[str] | None = None,
    error: str = "",
) -> Path | None:
    """Record every event-ingest attempt, including disabled/store failures."""

    return append_entry(
        {
            "action": "event_ingest",
            "scope": str(scope),
            "scope_id": scope_id or "default",
            "event_count": int(event_count),
            "stored_count": int(stored_count),
            "event_ids": list(event_ids or []),
            "evicted_event_ids": list(evicted_event_ids or []),
            "status": str(status),
            "error": error[:500],
        },
        scope=scope,
        scope_id=scope_id,
        storage_path=storage_path,
        now=now,
    )


def append_regeneration(
    *,
    scope: Any = "user",
    scope_id: str | None = None,
    event_count: int,
    chapters_changed: list[str] | None,
    chars: int,
    status: str,
    synthesis_status: str,
    synthesis: str = "",
    storage_path: str | None = None,
    now: float | None = None,
    error: str = "",
) -> Path | None:
    """Record chapter-level regeneration changes and model disposition."""

    changed = list(chapters_changed or [])
    return append_entry(
        {
            "action": "story_regeneration",
            "scope": str(scope),
            "scope_id": scope_id or "default",
            "event_count": int(event_count),
            "chapters_changed": changed,
            "chapters_changed_count": len(changed),
            "chars": int(chars),
            "status": str(status),
            "synthesis_status": str(synthesis_status),
            "synthesis": str(synthesis),
            "error": error[:500],
        },
        scope=scope,
        scope_id=scope_id,
        storage_path=storage_path,
        now=now,
    )


def append_event_ingest(*args: Any, **kwargs: Any) -> Path | None:
    """Descriptive alias for :func:`append_ingest`."""

    return append_ingest(*args, **kwargs)


def append_story_regeneration(*args: Any, **kwargs: Any) -> Path | None:
    """Descriptive alias for :func:`append_regeneration`."""

    return append_regeneration(*args, **kwargs)


def read_entries(
    day: str,
    *,
    scope: Any = "user",
    scope_id: str | None = None,
    storage_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read one provenance day, skipping malformed lines and I/O failures."""

    try:
        path = provenance_log_path(narrative_root(storage_path), scope, day, scope_id)
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
            if isinstance(item, dict):
                entries.append(item)
        return entries
    except Exception as exc:  # noqa: BLE001 - read-side provenance is best-effort
        logger.warning("Narrative provenance read failed (%s)", exc)
        return []


def rewrite_day(
    day: str,
    entries: list[Mapping[str, Any]],
    *,
    scope: Any = "user",
    scope_id: str | None = None,
    storage_path: str | None = None,
) -> Path:
    """Atomically replace one day for tests/forensic compaction."""

    path = provenance_log_path(narrative_root(storage_path), scope, day, scope_id)
    body = "\n".join(json.dumps(dict(entry), ensure_ascii=False, default=str) for entry in entries)
    atomic_write_text(path, body + ("\n" if body else ""))
    return path


def provenance_path(
    day: str,
    *,
    scope: Any = "user",
    scope_id: str | None = None,
    storage_path: str | None = None,
) -> Path:
    """Return the JSONL path used for a scope/day."""

    return provenance_log_path(narrative_root(storage_path), scope, day, scope_id)


__all__ = [
    "append_entry",
    "append_event_ingest",
    "append_ingest",
    "append_regeneration",
    "append_story_regeneration",
    "provenance_path",
    "read_entries",
    "rewrite_day",
]
