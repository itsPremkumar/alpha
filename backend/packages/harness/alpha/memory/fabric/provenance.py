"""Best-effort append-only per-day audit JSONL for memory fabric mutations."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import FabricConfig, fabric_root
from .models import LifecycleStatus, MemoryScope

logger = logging.getLogger(__name__)
_append_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class ProvenanceResult:
    """Disclosed result of one best-effort append."""

    status: str
    path: Path
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the JSONL entry was appended."""

        return self.status == "written"


def _moment(now: float | None) -> float:
    value = time.time() if now is None else float(now)
    if not math.isfinite(value):
        raise ValueError("provenance timestamp must be finite")
    return value


def _day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def provenance_path(
    day: str,
    *,
    config: FabricConfig | None = None,
    storage_path: str | Path | None = None,
) -> Path:
    """Return one UTC-day audit path without creating a directory."""

    selected_path = config.storage_path if config is not None else storage_path
    return fabric_root(selected_path) / "memory_fabric" / "audit" / f"{day}.jsonl"


def _status_value(value: LifecycleStatus | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, LifecycleStatus):
        return value.value
    return str(value)


def append_event(
    *,
    action: str,
    record_id: str,
    record_scope: MemoryScope,
    actor_scope: MemoryScope,
    reason: str,
    status: str,
    timestamp: float,
    from_status: LifecycleStatus | str | None = None,
    to_status: LifecycleStatus | str | None = None,
    forced: bool = False,
    details: Mapping[str, Any] | None = None,
    config: FabricConfig | None = None,
    storage_path: str | Path | None = None,
) -> ProvenanceResult:
    """Append one audit event; logging failure never rolls back canonical data."""

    try:
        moment = _moment(timestamp)
        selected_path = config.storage_path if config is not None else storage_path
        path = provenance_path(_day(moment), config=config, storage_path=selected_path)
        entry = {
            "action": str(action),
            "record_id": str(record_id),
            "record_scope": record_scope.qualified(),
            "actor_scope": actor_scope.qualified(),
            "reason": str(reason or ""),
            "status": str(status),
            "from_status": _status_value(from_status),
            "to_status": _status_value(to_status),
            "forced": bool(forced),
            "timestamp": moment,
            "ts": datetime.fromtimestamp(moment, tz=UTC).isoformat(),
            "details": dict(details or {}),
        }
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
        return ProvenanceResult(status="written", path=path)
    except Exception as exc:  # noqa: BLE001 - provenance is deliberately non-fatal
        try:
            failed_path = provenance_path(
                _day(time.time()),
                config=config,
                storage_path=storage_path,
            )
        except Exception:  # pragma: no cover - defensive path-resolution failure
            failed_path = Path("memory_fabric/audit/unknown.jsonl")
        logger.warning("Memory fabric provenance append failed for %s: %s", failed_path, exc)
        return ProvenanceResult(status="failed", path=failed_path, error=str(exc))


def read_entries(
    day: str,
    *,
    config: FabricConfig | None = None,
    storage_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read valid JSON objects from one day, safely skipping malformed lines."""

    path = provenance_path(day, config=config, storage_path=storage_path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        logger.warning("Memory fabric provenance read failed for %s: %s", path, exc)
        return []
    entries: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Memory fabric provenance skipped malformed JSON line in %s", path)
            continue
        if isinstance(value, Mapping):
            entries.append(dict(value))
    return entries


__all__ = [
    "ProvenanceResult",
    "append_event",
    "provenance_path",
    "read_entries",
]
