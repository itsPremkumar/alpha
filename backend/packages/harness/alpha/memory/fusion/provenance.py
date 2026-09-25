"""Append-only, per-day JSONL provenance for each composed memory recall."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from alpha.agents.memory.l1.paths import safe_segment
from alpha.config.runtime_paths import runtime_home

from .config import FusionConfig
from .models import ComposedContext, FusionResult, ProvenanceWriteResult
from .query import ModeSelection

logger = logging.getLogger(__name__)
_append_lock = threading.Lock()


def query_hash(query: str) -> str:
    """Hash exact UTF-8 query bytes so recall logs need not store raw prompts."""
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()


def _root(storage_path: str | None) -> Path:
    if storage_path:
        return Path(storage_path).expanduser().resolve()
    return Path(runtime_home()).resolve()


def recall_provenance_path(day: str, *, scope_id: str, storage_path: str | None = None) -> Path:
    """Return the append-only UTC-day path for one recall scope."""
    return _root(storage_path) / "memory" / "fusion" / "recall-provenance" / safe_segment(scope_id) / f"{day}.jsonl"


def write_recall_provenance(
    query: str,
    *,
    mode: ModeSelection,
    fusion: FusionResult,
    context: ComposedContext,
    config: FusionConfig,
    scope_id: str,
    now: float | None = None,
) -> ProvenanceWriteResult:
    """Append one recall event and disclose success/failure without raw query text."""
    timestamp = time.time() if now is None else float(now)
    moment = datetime.fromtimestamp(timestamp, tz=UTC)
    day = moment.strftime("%Y-%m-%d")
    path = recall_provenance_path(day, scope_id=scope_id, storage_path=config.storage_path)
    payload = {
        "schema_version": 1,
        "timestamp": moment.isoformat(),
        "query_hash": query_hash(query),
        "mode": mode.mode,
        "mode_reason": mode.reason,
        "mode_signals": list(mode.matched_signals),
        "stages_run": list(fusion.stages_run),
        "stages_unavailable": dict(fusion.stages_unavailable),
        "results": [
            {
                "id": candidate.id,
                "memory_type": candidate.memory_type,
                "fused_score": candidate.fused_score,
                "source_stages": list(candidate.source_stages),
                "missing_components": list(candidate.missing_components),
            }
            for candidate in fusion.candidates
        ],
        "tokens_spent": context.total_tokens,
        "token_budget": context.budget,
        "per_type_token_spend": dict(context.per_type_token_spend),
        "dropped_items": [
            {"phase": "fusion", **item.model_dump(mode="json")}
            for item in fusion.dropped_with_reason
        ]
        + [{"phase": "composition", **item.model_dump(mode="json")} for item in context.dropped_with_reason],
        "fusion_latency_ms": fusion.total_latency_ms,
        "latency_budget_ms": fusion.latency_budget_ms,
        "latency_budget_exceeded": fusion.latency_budget_exceeded,
        "latency_disclosure": fusion.latency_disclosure,
    }
    try:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with _append_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 - return an explicit failed write result
        reason = f"provenance_write_failed:{type(exc).__name__}:{' '.join(str(exc).split())[:200]}"
        logger.warning("Memory fusion provenance append failed (%s)", exc)
        return ProvenanceWriteResult(written=False, path=str(path), reason=reason)
    return ProvenanceWriteResult(written=True, path=str(path), reason="appended")


def read_recall_provenance(
    day: str,
    *,
    scope_id: str,
    storage_path: str | None = None,
) -> list[dict[str, object]]:
    """Read valid JSON objects for one day; malformed lines are skipped."""
    path = recall_provenance_path(day, scope_id=scope_id, storage_path=storage_path)
    try:
        if not path.exists():
            return []
        entries: list[dict[str, object]] = []
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
    except Exception as exc:  # noqa: BLE001 - read-side provenance is best effort
        logger.warning("Memory fusion provenance read failed (%s)", exc)
        return []


__all__ = [
    "query_hash",
    "read_recall_provenance",
    "recall_provenance_path",
    "write_recall_provenance",
]
