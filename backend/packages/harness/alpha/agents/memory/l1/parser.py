"""Parsers for L1 extraction / dedup LLM responses.

Design provenance: response-shape handling (``<think>`` block stripping,
fenced-JSON stripping, prose-tolerant array extraction, per-object field
repair, and the fail-open ``store`` fallback) follows the tolerant parsing
strategy of ``tencentdb-agent-memory`` ``MemoryCore/src/core/l1-extractor.ts``
and ``l1-dedup.ts`` (MIT) — see ``docs/THIRD_PARTY_MEMORY_NOTICES.md``.
Implementation is original Python for Alpha.

Closed status sets (no free-form labels leak into the provenance log):

- extraction: ``ok`` ``no_json`` ``parse_fail`` ``not_array`` ``empty_scenes`` ``llm_error``
- conflict:   ``ok`` ``no_json`` ``parse_fail`` ``not_array`` ``empty`` ``llm_error``
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import (
    DEFAULT_PRIORITY,
    MEMORY_ACTIONS,
    MEMORY_TYPES,
    DedupDecision,
    DedupOutcome,
    ExtractedMemory,
    ExtractionOutcome,
    SceneSegment,
)

_THINK_RE = re.compile(r"^[\u3000\s]*<think>.*?([\u3000\s]*)$", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", re.MULTILINE)
_PRIORITY_RE = re.compile(r'"priority"\s*:\s*"?(-?\d+)"?')
_MAX_FIELD_LEN = 4000
_EXTRACTION_STATUSES = frozenset(
    {"ok", "no_json", "parse_fail", "not_array", "empty_scenes", "llm_error"}
)
_CONFLICT_STATUSES = frozenset(
    {"ok", "no_json", "parse_fail", "not_array", "empty", "llm_error"}
)


def _strip_noise(text: str) -> str:
    """Remove think blocks, prose before the JSON payload, and code fences."""
    cleaned = text.strip()
    cleaned = _THINK_RE.sub("", cleaned).strip()
    for opener in ("[", "{"):
        idx = cleaned.find(opener)
        if idx > 0:
            before = cleaned[:idx]
            if not any(c in before for c in "[]{}"):
                cleaned = cleaned[idx:]
    return _FENCE_RE.sub("", cleaned).strip()


def _extract_json_candidates(text: str) -> list[str]:
    """Candidate JSON substrings, outermost-span first."""
    cleaned = _strip_noise(text)
    candidates: list[str] = []
    if cleaned:
        candidates.append(cleaned)
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end > start:
            span = cleaned[start : end + 1]
            if span not in candidates:
                candidates.append(span)
    return candidates


def _loads_lenient(raw: str) -> Any | None:
    """Parse JSON, retrying after repairing unquoted/quoted integer priorities."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = _PRIORITY_RE.sub(r'"priority": \1', raw)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _first_json(text: str) -> Any | None:
    for candidate in _extract_json_candidates(text):
        payload = _loads_lenient(candidate)
        if payload is not None:
            return payload
    return None


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()[:_MAX_FIELD_LEN]
    return str(value)[:_MAX_FIELD_LEN]


def _coerce_priority(value: Any) -> int:
    """0-100 band, or the ``-1`` strict-instruction sentinel; default mid-band."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    if number == -1:
        return -1
    return max(0, min(100, number))


def _coerce_str_list(value: Any, limit: int = 64) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _coerce_str(item, "")
        if text:
            out.append(text[:128])
        if len(out) >= limit:
            break
    return out


def _coerce_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k)[:64]: v for k, v in list(value.items())[:32]}
    return {}


def _memory_from_obj(obj: dict[str, Any]) -> ExtractedMemory:
    memory_type = _coerce_str(obj.get("type") or obj.get("memory_type"), "persona").lower()
    if memory_type not in MEMORY_TYPES:
        memory_type = "persona"
    return ExtractedMemory(
        content=_coerce_str(obj.get("content") or obj.get("text")),
        memory_type=memory_type,
        priority=_coerce_priority(obj.get("priority")),
        source_message_ids=_coerce_str_list(obj.get("source_message_ids")),
        metadata=_coerce_metadata(obj.get("metadata")),
    )


def _scene_from_obj(obj: dict[str, Any]) -> SceneSegment:
    memories_raw = obj.get("memories")
    memories: list[ExtractedMemory] = []
    if isinstance(memories_raw, list):
        for item in memories_raw:
            if not isinstance(item, dict):
                continue
            memory = _memory_from_obj(item)
            if memory.content:
                memories.append(memory)
    return SceneSegment(
        scene_name=_coerce_str(obj.get("scene_name"))[:200],
        message_ids=_coerce_str_list(obj.get("message_ids")),
        memories=memories,
    )


def parse_extraction_response(text: str | None) -> ExtractionOutcome:
    """Parse an extraction LLM response into scene segments + memories.

    The contract (``l1-extractor.ts``) is a JSON array of scene objects. A
    flat array of memory objects is tolerated (wrapped into one unnamed
    scene). Fail-open: unparseable output yields a non-``ok`` status and no
    memories, so the pipeline never fabricates content from garbage.
    """
    if text is None or not str(text).strip():
        return ExtractionOutcome(status="no_json", raw="")
    raw = str(text)
    payload = _first_json(raw)
    if payload is None:
        return ExtractionOutcome(status="parse_fail", raw=raw)
    if isinstance(payload, dict):
        # Single scene object (or {"scenes": [...]} / {"memories": [...]}).
        if isinstance(payload.get("scenes"), list):
            payload = payload["scenes"]
        elif isinstance(payload.get("memories"), list):
            payload = [payload]
        else:
            return ExtractionOutcome(status="not_array", raw=raw)
    if not isinstance(payload, list):
        return ExtractionOutcome(status="not_array", raw=raw)
    if not payload:
        return ExtractionOutcome(status="empty_scenes", raw=raw)

    scenes: list[SceneSegment] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if "memories" in item or "scene_name" in item or "message_ids" in item:
            scene = _scene_from_obj(item)
            if scene.scene_name or scene.memories or scene.message_ids:
                scenes.append(scene)
        elif "content" in item:
            # Lenient: flat memory object -> one unnamed scene.
            memory = _memory_from_obj(item)
            if memory.content:
                scenes.append(SceneSegment(memories=[memory]))
    if not scenes:
        return ExtractionOutcome(status="empty_scenes", raw=raw)
    return ExtractionOutcome(status="ok", scenes=scenes, raw=raw)


def _decision_from_obj(obj: dict[str, Any]) -> DedupDecision:
    action = _coerce_str(obj.get("action"), "store").lower()
    if action not in MEMORY_ACTIONS:
        action = "store"
    target_ids = _coerce_str_list(obj.get("target_ids") or obj.get("target_id"))
    merged_content = _coerce_str(obj.get("merged_content"))
    merged_priority_raw = obj.get("merged_priority")
    merged_priority: int | None
    if merged_priority_raw in (None, ""):
        merged_priority = None
    else:
        merged_priority = _coerce_priority(merged_priority_raw)
    decision = DedupDecision(
        record_id=_coerce_str(obj.get("record_id"))[:128],
        action=action,  # type: ignore[arg-type]
        target_ids=target_ids,
        merged_content=merged_content,
        merged_type=_coerce_str(obj.get("merged_type")).lower(),
        merged_priority=merged_priority,
        merged_timestamps=_coerce_str_list(obj.get("merged_timestamps")),
        reason=_coerce_str(obj.get("reason"))[:500],
    )
    # Field-requirement repair (prompt: merged_content/merged_type required
    # for merge/update). A malformed merge/update degrades to ``store`` —
    # never to a silent drop.
    if decision.action in ("merge", "update") and not (
        decision.merged_content and decision.merged_type in MEMORY_TYPES
    ):
        return DedupDecision(
            record_id=decision.record_id,
            action="store",
            reason=decision.reason or "malformed_merge_fallback_store",
            merged_timestamps=decision.merged_timestamps,
        )
    if decision.action == "update" and not decision.target_ids:
        return DedupDecision(
            record_id=decision.record_id,
            action="store",
            reason=decision.reason or "update_without_target_fallback_store",
            merged_timestamps=decision.merged_timestamps,
        )
    return decision


def parse_dedup_response(text: str | None) -> DedupOutcome:
    """Parse a conflict-detection LLM response into decisions.

    Fail-open: unparseable output yields a non-``ok`` status and no
    decisions; the pipeline then stores candidates normally (a broken LLM
    answer must never silently swallow a memory).
    """
    if text is None or not str(text).strip():
        return DedupOutcome(status="no_json", raw="")
    raw = str(text)
    payload = _first_json(raw)
    if payload is None:
        return DedupOutcome(status="parse_fail", raw=raw)
    if isinstance(payload, dict) and isinstance(payload.get("decisions"), list):
        payload = payload["decisions"]
    if not isinstance(payload, list):
        return DedupOutcome(status="not_array", raw=raw)
    if not payload:
        return DedupOutcome(status="empty", raw=raw)
    decisions: list[DedupDecision] = []
    for item in payload:
        if isinstance(item, dict):
            decisions.append(_decision_from_obj(item))
    if not decisions:
        return DedupOutcome(status="empty", raw=raw)
    return DedupOutcome(status="ok", decisions=decisions, raw=raw)


def extraction_status_known(status: str) -> bool:
    """Whether ``status`` belongs to the closed extraction status set."""
    return status in _EXTRACTION_STATUSES


def conflict_status_known(status: str) -> bool:
    """Whether ``status`` belongs to the closed conflict status set."""
    return status in _CONFLICT_STATUSES


__all__ = [
    "conflict_status_known",
    "extraction_status_known",
    "parse_dedup_response",
    "parse_extraction_response",
]
