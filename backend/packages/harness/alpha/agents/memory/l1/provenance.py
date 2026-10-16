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
from datetime import UTC, datetime, timedelta
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


def read_recent_capture_entry(
    *,
    user_id: str | None = None,
    storage_path: str | None = None,
    now: float | None = None,
    days: int = 7,
    max_bytes_per_day: int = 128 * 1024,
    max_lines_per_day: int = 500,
) -> dict[str, Any]:
    """Return the latest bounded capture-provenance entry for one owner.

    The response intentionally distinguishes no recent run from a damaged or
    unreadable log. Reads are bounded by both a short day window and a tail
    byte/line ceiling, so a status panel cannot scan an unbounded history file.
    If the last readable run predates capture reporting, ``not_reported`` is
    returned rather than inferring that capture was disabled or empty.
    """
    if not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")
    if max_bytes_per_day < 1024 or max_lines_per_day < 1:
        raise ValueError("capture provenance read bounds are invalid")

    anchor = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC).date()
    saw_file = False
    had_read_error = False
    had_corrupt_line = False

    for offset in range(days):
        day = (anchor - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = generation_log_path(l1_root(storage_path), user_id, day)
        try:
            if not path.exists():
                continue
            saw_file = True
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                start = max(0, size - max_bytes_per_day)
                handle.seek(start)
                raw = handle.read(max_bytes_per_day)
        except OSError as exc:
            logger.warning("L1 provenance: could not inspect/read %s (%s)", path, exc)
            had_read_error = True
            continue

        lines = raw.splitlines()
        # When reading a tail, the first line may be only the end of a JSON
        # record. Drop it; reporting a corrupt file because of our own bound
        # would be misleading.
        if start > 0 and lines:
            lines = lines[1:]
        if len(lines) > max_lines_per_day:
            lines = lines[-max_lines_per_day:]

        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("L1 provenance: skipping corrupt entry in %s (%s)", path, exc)
                had_corrupt_line = True
                continue
            if not isinstance(entry, dict):
                had_corrupt_line = True
                continue
            capture = entry.get("capture_outcome")
            if not isinstance(capture, dict):
                return {
                    "status": "degraded" if had_read_error or had_corrupt_line else "not_reported",
                    "entry": None,
                    "reason": "log_partly_unreadable" if had_read_error or had_corrupt_line else "capture_not_recorded",
                }
            return {
                "status": "degraded" if had_read_error or had_corrupt_line else "available",
                "entry": entry,
                "reason": "log_partly_unreadable" if had_read_error or had_corrupt_line else None,
            }

    if had_read_error or had_corrupt_line:
        return {"status": "unreadable", "entry": None, "reason": "generation_log_unreadable"}
    return {
        "status": "no_recent_runs" if not saw_file else "no_readable_entries",
        "entry": None,
        "reason": None if not saw_file else "generation_log_has_no_entries",
    }


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


__all__ = ["append_run_entry", "read_entries", "read_recent_capture_entry", "rewrite_day"]
