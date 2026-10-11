"""Structured completion events — one canonical history for UI, logs and agents.

Every consumer (the task detail panel, a monitoring agent, a diagnostic tool)
reads *this* list rather than inventing its own status, which is the point:
three components that each derive "what happened" from prose will disagree,
and the disagreement is invisible until something breaks.

Shape per event: ``event_id``, ``scope_id``, optional ``task_id``/``gate_id``,
``kind``, timestamp, producer, schema version and a **redacted** payload.
Redaction happens here, at write time, so an untrusted value cannot reach the
journal in the first place — not at render time, where a new consumer could
forget.

The list is a bounded ring (``completion.max_events``): a scope that runs for
hours must not grow an unbounded journal in memory and on disk. Trimming drops
the *oldest* events and records that it did, so a gap in history is visible
rather than silently implied to be complete.

Event vocabulary is closed (``EventKind``). A typo'd kind is refused rather
than becoming an event nothing filters for.
"""

from __future__ import annotations

import hashlib
import logging
import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

#: Bumped when the payload shape changes. Readers can then distinguish a
#: journal written under old semantics from one written under theirs.
EVENT_SCHEMA_VERSION = 1

#: Producer recorded on every event this package writes.
PRODUCER = "alpha.completion"


class EventKind(StrEnum):
    """Closed vocabulary. Unknown kinds are refused, not stringly-typed."""

    CONTRACT_CREATED = "contract.created"
    CONTRACT_REVISED = "contract.revised"
    CONTRACT_VALIDATION_FAILED = "contract.validation_failed"
    TASK_READY = "task.ready"
    TASK_DISPATCHED = "task.dispatched"
    TASK_STARTED = "task.started"
    TASK_RETURNED = "task.returned"
    TASK_VERIFIED = "task.verified"
    TASK_BLOCKED = "task.blocked"
    DISPATCH_LAUNCH_FAILED = "dispatch.launch_failed"
    DISPATCH_ABANDONED = "dispatch.abandoned"
    GATE_STARTED = "gate.started"
    GATE_PASSED = "gate.passed"
    GATE_FAILED = "gate.failed"
    GATE_STALE = "gate.stale"
    GATE_BLOCKED = "gate.blocked"
    EVIDENCE_CREATED = "evidence.created"
    EVIDENCE_INVALIDATED = "evidence.invalidated"
    OWNERSHIP_CLAIMED = "ownership.claimed"
    OWNERSHIP_CONFLICT = "ownership.conflict"
    OWNERSHIP_RELEASED = "ownership.released"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REVOKED = "approval.revoked"
    SCOPE_CREATED = "scope.created"
    SCOPE_STARTED = "scope.started"
    SCOPE_RECONCILIATION_FAILED = "scope.reconciliation_failed"
    SCOPE_FINALIZED = "scope.finalized"
    SCOPE_HANDOFF_REQUIRED = "scope.handoff_required"
    SCOPE_CANCELLED = "scope.cancelled"
    BUDGET_EXHAUSTED = "budget.exhausted"


class CompletionEvent(BaseModel):
    """One journal entry."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    scope_id: str
    kind: EventKind
    at: float
    producer: str = PRODUCER
    schema_version: int = EVENT_SCHEMA_VERSION
    task_id: str = ""
    gate_id: str = ""
    payload: dict[str, str] = Field(default_factory=dict, description="Redacted, flat, string-valued details.")


#: Keys whose values are replaced by a length marker rather than logged.
_SECRET_HINTS = ("key", "token", "secret", "password", "passwd", "auth", "credential", "cookie", "bearer")

#: Keys dropped entirely: they add nothing and one mistake is a leak.
_DROPPED_KEYS = {"env", "environment", "headers", "body", "output", "stdout", "stderr", "request_snapshot"}

_MAX_STRING = 300
_MAX_PAYLOAD_KEYS = 16


def redact(payload: dict) -> dict[str, str]:
    """Flatten a payload to safe strings: bounded, secret-shaped keys masked.

    Control characters are stripped so a value cannot smuggle terminal
    escapes into a log line or a UI row.
    """
    from .gates import strip_control

    safe: dict[str, str] = {}
    for key, value in list(payload.items())[:_MAX_PAYLOAD_KEYS]:
        name = str(key)
        lowered = name.lower()
        if lowered in _DROPPED_KEYS:
            continue
        if any(hint in lowered for hint in _SECRET_HINTS):
            safe[name] = "[redacted]"
            continue
        text = strip_control(str(value))
        safe[name] = text if len(text) <= _MAX_STRING else text[: _MAX_STRING - 3] + "..."
    return safe


def make_event(
    scope_id: str,
    kind: EventKind,
    *,
    task_id: str = "",
    gate_id: str = "",
    payload: dict | None = None,
    now: float | None = None,
    sequence: int = 0,
) -> CompletionEvent:
    """Build one event. ``sequence`` keeps ids unique within a journal."""
    moment = time.time() if now is None else now
    raw = f"{scope_id}|{kind.value}|{sequence}|{moment}"
    return CompletionEvent(
        event_id="ce-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        scope_id=scope_id,
        kind=kind,
        at=moment,
        task_id=task_id,
        gate_id=gate_id,
        payload=redact(payload or {}),
    )


def append_event(doc: dict, event: CompletionEvent, *, max_events: int) -> CompletionEvent:
    """Append to the bounded ring, trimming the oldest when it overflows."""
    events: list[dict] = doc.setdefault("events", [])
    events.append(event.model_dump(mode="json"))
    overflow = len(events) - max(0, max_events)
    if overflow > 0:
        del events[:overflow]
        logger.debug("completion event ring trimmed %d oldest entries (max_events=%s)", overflow, max_events)
    return event


def emit(
    doc: dict,
    scope_id: str,
    kind: EventKind,
    *,
    task_id: str = "",
    gate_id: str = "",
    payload: dict | None = None,
    now: float | None = None,
    max_events: int = 2000,
) -> CompletionEvent:
    """Allocate the next id, build the event and append it."""
    counters = doc.setdefault("counters", {})
    counters["event_seq"] = int(counters.get("event_seq", 0)) + 1
    event = make_event(scope_id, kind, task_id=task_id, gate_id=gate_id, payload=payload, now=now, sequence=counters["event_seq"])
    return append_event(doc, event, max_events=max_events)


def events_for(doc: dict, scope_id: str, *, kinds: set[EventKind] | None = None, limit: int = 200) -> list[dict]:
    """Newest-last slice of one scope's journal, optionally filtered by kind."""
    rows = [row for row in doc.get("events", []) if row.get("scope_id") == scope_id]
    if kinds is not None:
        wanted = {kind.value for kind in kinds}
        rows = [row for row in rows if row.get("kind") in wanted]
    return rows[-max(1, limit) :]


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "CompletionEvent",
    "EventKind",
    "PRODUCER",
    "append_event",
    "emit",
    "events_for",
    "make_event",
    "redact",
]
