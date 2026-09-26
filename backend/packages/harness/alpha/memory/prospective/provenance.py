"""Best-effort append-only provenance for prospective-memory transitions."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import atomic_write_text, safe_segment

from .config import ProspectiveConfig, prospective_root
from .models import ProspectiveItem, ProspectiveStatus

logger = logging.getLogger(__name__)
_append_lock = threading.Lock()


@dataclass(slots=True)
class ProvenanceResult:
    """Result of one best-effort JSONL append."""

    status: str
    path: Path
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the entry was durably appended."""

        return self.status == "written"


def _day_stamp(now: float) -> str:
    return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")


def _provenance_path(root: Path, user_id: str | None, day: str) -> Path:
    return root / "users" / safe_segment(user_id or "default") / "prospective" / "provenance" / f"{day}.jsonl"


def provenance_path(
    user_id: str | None,
    day: str,
    *,
    storage_path: str | Path | None = None,
    config: ProspectiveConfig | None = None,
) -> Path:
    """Return the per-user/day provenance path without creating it."""

    root = prospective_root(config.storage_path if config is not None else storage_path)
    return _provenance_path(root, user_id, day)


def _status_value(value: ProspectiveStatus | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, ProspectiveStatus):
        return value.value
    return str(value)


def _entry(
    item: ProspectiveItem,
    event: str,
    *,
    reason: str,
    from_status: ProspectiveStatus | str | None,
    to_status: ProspectiveStatus | str | None,
    now: float,
) -> dict[str, Any]:
    timestamp = now
    return {
        "event": str(event),
        "item_id": item.id,
        "user_id": item.user_id,
        "agent_name": item.agent_name,
        "from_status": _status_value(from_status),
        "to_status": _status_value(to_status),
        "timestamp": timestamp,
        "ts": datetime.fromtimestamp(timestamp, tz=UTC).isoformat(),
        "reason": str(reason or ""),
        "source": item.source,
        "occurrence": item.occurrence,
    }


def append_event(
    item: ProspectiveItem,
    event: str,
    *,
    reason: str = "",
    from_status: ProspectiveStatus | str | None = None,
    to_status: ProspectiveStatus | str | None = None,
    storage_path: str | Path | None = None,
    config: ProspectiveConfig | None = None,
    now: float | None = None,
) -> ProvenanceResult:
    """Append one transition entry; never make a healthy state change fail.

    Provenance is an audit side effect.  A failure is returned and logged so a
    caller can disclose it, but the already-committed item state remains
    intact.
    """

    timestamp = time.time() if now is None else float(now)
    root = prospective_root(config.storage_path if config is not None else storage_path)
    path = _provenance_path(root, item.user_id, _day_stamp(timestamp))
    payload = _entry(
        item,
        event,
        reason=reason,
        from_status=from_status,
        to_status=to_status,
        now=timestamp,
    )
    try:
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
    except Exception as exc:  # noqa: BLE001 - audit logging must not break state changes
        logger.warning("Prospective provenance append failed for %s: %s", path, exc)
        return ProvenanceResult(status="failed", path=path, error=str(exc))
    return ProvenanceResult(status="written", path=path)


def append_transition(
    item: ProspectiveItem,
    event: str,
    *,
    reason: str = "",
    from_status: ProspectiveStatus | str | None = None,
    to_status: ProspectiveStatus | str | None = None,
    storage_path: str | Path | None = None,
    config: ProspectiveConfig | None = None,
    now: float | None = None,
) -> ProvenanceResult:
    """Descriptive alias for :func:`append_event`."""

    return append_event(
        item,
        event,
        reason=reason,
        from_status=from_status,
        to_status=to_status,
        storage_path=storage_path,
        config=config,
        now=now,
    )


def read_entries(
    day: str,
    *,
    user_id: str | None = None,
    storage_path: str | Path | None = None,
    config: ProspectiveConfig | None = None,
) -> list[dict[str, Any]]:
    """Read valid JSON objects from one day's log, skipping bad lines safely."""

    path = provenance_path(user_id, day, storage_path=storage_path, config=config)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        logger.warning("Prospective provenance read failed for %s: %s", path, exc)
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Prospective provenance skipped malformed line in %s", path)
            continue
        if isinstance(value, Mapping):
            entries.append(dict(value))
    return entries


def rewrite_day(
    day: str,
    entries: list[Mapping[str, Any]],
    *,
    user_id: str | None = None,
    storage_path: str | Path | None = None,
    config: ProspectiveConfig | None = None,
) -> ProvenanceResult:
    """Atomically replace a day for tests or an explicit repair workflow."""

    root = prospective_root(config.storage_path if config is not None else storage_path)
    path = _provenance_path(root, user_id, day)
    try:
        body = "\n".join(json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) for entry in entries)
        atomic_write_text(path, body + ("\n" if body else ""))
    except Exception as exc:  # noqa: BLE001 - report repair failure to caller
        return ProvenanceResult(status="failed", path=path, error=str(exc))
    return ProvenanceResult(status="written", path=path)


__all__ = [
    "ProvenanceResult",
    "append_event",
    "append_transition",
    "provenance_path",
    "read_entries",
    "rewrite_day",
]
