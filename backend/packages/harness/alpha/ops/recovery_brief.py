"""Privacy-safe recovery briefs for interrupted autonomous work."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from alpha.agents.task_continuity.state import normalize_task_history, normalize_task_notes
from alpha.ops.autonomy_truth import redact_text

MAX_BRIEF_CHARS = 3_000
MAX_NOTE_CHARS = 400
MAX_RECENT_EVENTS = 12


def _message_value(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(key, default)
    return getattr(message, key, default)


def _safe_event(message: Any) -> dict[str, Any] | None:
    message_type = str(_message_value(message, "type", "") or "")
    name = str(_message_value(message, "name", "") or "")
    status = str(_message_value(message, "status", "") or "")
    if message_type == "tool":
        return {
            "type": "tool_result",
            "name": name[:120],
            "status": status[:32] or "unknown",
        }
    calls = _message_value(message, "tool_calls", None)
    if message_type == "ai" and isinstance(calls, list) and calls:
        names = []
        for call in calls[-MAX_RECENT_EVENTS:]:
            if isinstance(call, Mapping):
                value = call.get("name")
            else:
                value = getattr(call, "name", None)
            if isinstance(value, str) and value:
                names.append(value[:120])
        if names:
            return {"type": "tool_calls", "names": list(dict.fromkeys(names))}
    return None


def build_recovery_brief(state: Mapping[str, Any] | None, *, max_chars: int = MAX_BRIEF_CHARS) -> dict[str, Any]:
    """Summarize current-thread continuity without returning raw history."""

    state = state if isinstance(state, Mapping) else {}
    notes = normalize_task_notes(state.get("task_notes"))
    history = normalize_task_history(state.get("task_history"))
    safe_notes = []
    for key, note in list(notes.items())[-8:]:
        content, redacted = redact_text(str(note.get("content", "")), max_chars=MAX_NOTE_CHARS)
        safe_notes.append(
            {
                "key": key,
                "content": content,
                "authority": note.get("authority", "model_report"),
                "source_count": len(note.get("source_ids", [])),
                "redacted": redacted,
            }
        )

    messages = state.get("messages")
    events = []
    if isinstance(messages, list):
        for message in messages[-MAX_RECENT_EVENTS * 2 :]:
            event = _safe_event(message)
            if event is not None:
                events.append(event)
    events = events[-MAX_RECENT_EVENTS:]

    failure_count = sum(1 for event in events if event.get("status") in {"error", "failure", "failed", "denied"})
    approval_events = sum(1 for event in events if "approval" in str(event.get("type", "")))
    next_steps = []
    if history.get("status") == "unavailable":
        next_steps.append("restore the current checkpoint before relying on compacted history")
    if failure_count:
        next_steps.append("classify the recorded failures before retrying")
    if approval_events:
        next_steps.append("verify the existing human approval receipt before any side effect")
    if not next_steps:
        next_steps.append("continue from the last verified checkpoint and re-check changed files")

    canonical = json.dumps(
        {
            "notes": safe_notes,
            "events": events,
            "history_status": history.get("status", "unavailable"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    brief = {
        "schema_version": "alpha.recovery-brief.v1",
        "status": "available" if notes or events or history.get("status") == "available" else "unavailable",
        "brief_id": hashlib.sha256(canonical).hexdigest()[:16],
        "notes": safe_notes,
        "recent_events": events,
        "history": {
            "status": history.get("status", "unavailable"),
            "batch_count": len(history.get("batches", [])),
            "omitted_records": history.get("omitted_records", 0),
        },
        "counts": {
            "notes": len(safe_notes),
            "events": len(events),
            "failures": failure_count,
            "approval_events": approval_events,
        },
        "next_steps": next_steps,
        "contains_raw_messages": False,
        "contains_raw_tool_output": False,
        "secret_redaction_applied": True,
    }
    try:
        limit = max(500, min(MAX_BRIEF_CHARS, int(max_chars)))
    except (TypeError, ValueError):
        limit = MAX_BRIEF_CHARS
    rendered = json.dumps(brief, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) > limit:
        return {
            "schema_version": brief["schema_version"],
            "status": brief["status"],
            "brief_id": brief["brief_id"],
            "history": brief["history"],
            "counts": brief["counts"],
            "next_steps": brief["next_steps"],
            "summary": "Recovery brief truncated; reduce recent event scope.",
            "truncated": True,
            "contains_raw_messages": False,
            "contains_raw_tool_output": False,
            "secret_redaction_applied": True,
        }
    brief["truncated"] = False
    return brief


__all__ = ["MAX_BRIEF_CHARS", "build_recovery_brief"]
