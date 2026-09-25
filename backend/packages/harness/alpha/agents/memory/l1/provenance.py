"""L1 provenance: append-only generation log for every pipeline run.

Design provenance: per-day JSONL generation log with one entry per run,
carrying run outcome and per-memory statuses, follows
``tencentdb-agent-memory`` ``MemoryCore/src/core/memory-generation-log/``
(MIT). Implementation is original Python for Alpha.

Honesty contract: a run is recorded for EVERY attempt — ``succeeded``,
``failed`` (with the error text), or ``skipped`` (with the reason). The log
never reports success for a run that did not write anything.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import atomic_write_text, generation_log_path, l1_root

logger = logging.getLogger(__name__)

_append_lock = threading.Lock()


def _day_stamp(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), tz=UTC).strftime("%Y-%m-%d")


def append_run_entry(
    entry: dict[str, Any],
    *,
    user_id: str | None = None,
    storage_path: str | None = None,
    now: float | None = None,
) -> Path:
    """Append one run entry to today's generation log; returns the log path.

    Never raises: provenance must not take a healthy run down. On write
    failure the error is logged and the pipeline continues (the run report
    itself still carries the outcome).
    """
    payload = dict(entry)
    payload.setdefault("ts", datetime.fromtimestamp(now or time.time(), tz=UTC).isoformat())
    day = _day_stamp(now)
    path = generation_log_path(l1_root(storage_path), user_id, day)
    line = json.dumps(payload, ensure_ascii=False)
    try:
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        logger.warning("L1 provenance: could not append to %s (%s)", path, exc)
    return path


def read_entries(
    day: str,
    *,
    user_id: str | None = None,
    storage_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read one day's generation-log entries (best-effort, never raises)."""
    path = generation_log_path(l1_root(storage_path), user_id, day)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
    except OSError as exc:
        logger.warning("L1 provenance: could not read %s (%s)", path, exc)
    return entries


def rewrite_day(
    day: str,
    entries: list[dict[str, Any]],
    *,
    user_id: str | None = None,
    storage_path: str | None = None,
) -> Path:
    """Replace one day's log (tests / compaction helper). Atomic."""
    path = generation_log_path(l1_root(storage_path), user_id, day)
    body = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    atomic_write_text(path, body + ("\n" if body else ""))
    return path


__all__ = ["append_run_entry", "read_entries", "rewrite_day"]
