"""Best-effort append-only provenance for entity extraction and merges."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import EntityIngestResult
from .paths import entity_root, provenance_log_path

logger = logging.getLogger(__name__)
_append_lock = threading.Lock()


def _append_payload(
    payload: dict[str, Any],
    *,
    user_id: str,
    storage_path: str,
    now: float | None,
) -> Path | None:
    timestamp = time.time() if now is None else float(now)
    try:
        moment = datetime.fromtimestamp(timestamp, tz=UTC)
        data = {
            "ts": moment.isoformat(),
            "user_id": user_id,
            **payload,
        }
        path = provenance_log_path(entity_root(storage_path), user_id, moment.strftime("%Y-%m-%d"))
        line = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path
    except Exception as exc:  # noqa: BLE001 - provenance is explicitly best-effort
        logger.warning("Entity provenance append failed (%s)", exc)
        return None


def append_entity_ingest(
    result: EntityIngestResult,
    *,
    user_id: str,
    storage_path: str,
    now: float | None = None,
) -> Path | None:
    """Append one extraction outcome; never turn provenance failure into success."""
    return _append_payload(
        {
            "action": "entity_ingest",
            "status": result.status.value,
            "reason": result.reason,
            "extractor": result.extractor,
            "extraction_status": result.extraction_status.value,
            "model_status": result.model_status.value if result.model_status is not None else None,
            "records_considered": result.records_considered,
            "candidates": result.candidates,
            "deterministic_candidates": result.deterministic_candidates,
            "model_candidates": result.model_candidates,
            "stored_mentions": result.stored_mentions,
            "new_entities": result.new_entities,
            "resolved_entities": result.resolved_entities,
            "merged_entities": result.merged_entities,
            "evicted_entities": result.evicted_entities,
            "entity_ids": list(result.entity_ids),
            "evicted_entity_ids": list(result.evicted_entity_ids),
            "rejected_items": result.rejected_items,
            "errors": list(result.errors),
        },
        user_id=user_id,
        storage_path=storage_path,
        now=now,
    )


def append_entity_merge(
    *,
    user_id: str,
    storage_path: str,
    target_id: str,
    source_id: str,
    merged_entity_id: str | None,
    mention_count: int,
    first_seen: float,
    last_seen: float,
    now: float | None = None,
) -> Path | None:
    """Record an explicit graph merge with its retained provenance summary."""
    return _append_payload(
        {
            "action": "entity_merge",
            "status": "succeeded" if merged_entity_id else "skipped",
            "target_id": target_id,
            "source_id": source_id,
            "merged_entity_id": merged_entity_id or "",
            "mention_count": mention_count,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "reason": f"merged:{target_id}->{source_id}",
        },
        user_id=user_id,
        storage_path=storage_path,
        now=now,
    )


def read_entries(
    day: str,
    *,
    user_id: str,
    storage_path: str,
) -> list[dict[str, Any]]:
    """Read one provenance day while skipping malformed lines and disclosing I/O failure."""
    try:
        path = provenance_log_path(entity_root(storage_path), user_id, day)
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
        logger.warning("Entity provenance read failed (%s)", exc)
        return []


__all__ = ["append_entity_ingest", "append_entity_merge", "read_entries"]
