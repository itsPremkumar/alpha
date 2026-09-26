"""The closed event taxonomy for a run trace.

An event is one typed, timestamped fact about a run. The vocabulary is
**closed**: :data:`EVENT_NAMES` is the whole set, and :class:`TraceEvent`
rejects anything outside it at construction rather than storing a name no
consumer knows. A trace reader that has to cope with unknown names is a trace
reader that will silently swallow a typo, and a typo in an event name is
indistinguishable from a subsystem that never fired.

Names are dotted ``<domain>.<verb>`` pairs. The domain names the subsystem
(``run``, ``agent``, ``tool``, ``memory``, ``subagent``, ``budget``, ``gate``)
so a reader can filter by prefix, and the verb is past or present tense
consistently: ``requested``/``started`` are lifecycle edges, ``completed``/
``failed`` are terminal edges.

Status is closed too, and deliberately narrower than the span status set:
:class:`EventStatus` describes an *instant*, not a timed interval, so it has
no ``timeout``-adjacent ambiguity -- a tool call that timed out reports
``timeout`` here and ``timeout`` on its span, but a run that was cancelled
mid-flight reports ``cancelled`` once and never again.

Attribute discipline
--------------------
``attributes`` is scrubbed in a ``mode="before"`` validator, so **no value
reaches a sink unredacted** -- not by a call site remembering to redact, and
not by a sink defensively re-checking. There is no code path that constructs a
:class:`TraceEvent` with a raw attribute, because the validator runs before
pydantic validates any other field.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .redaction import Redactor

__all__ = [
    "EVENT_DOMAINS",
    "EVENT_NAMES",
    "EventStatus",
    "EVENT_STATUSES",
    "TraceEvent",
    "TraceEventName",
    "UnknownEventNameError",
    "UnknownEventStatusError",
    "parse_event_name",
    "parse_event_status",
]

#: Every event name in the trace vocabulary. Closed on purpose (see module
#: docstring); ``test_events.py`` asserts the set, the per-domain breakdown
#: and that a name outside it is rejected.
EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        # run lifecycle and context propagation
        "run.started",
        "run.ended",
        "run.context.linked",
        "run.context.detached",
        # the agent's own turn
        "agent.turn",
        # tool call lifecycle, four edges
        "tool.call.requested",
        "tool.call.started",
        "tool.call.completed",
        "tool.call.failed",
        # memory write and read seams
        "memory.capture",
        "memory.recall",
        # subagent delegation
        "subagent.spawned",
        "subagent.completed",
        # resource accounting
        "budget.consumed",
        # an authorization/guardrail/policy refusal
        "gate.refused",
    }
)

#: Name prefix -> the names under it. A reader filters by domain; this table is
#: what makes "which domain can emit this" answerable without string surgery.
EVENT_DOMAINS: Final[dict[str, tuple[str, ...]]] = {
    "run": tuple(sorted(name for name in EVENT_NAMES if name.startswith("run."))),
    "agent": tuple(sorted(name for name in EVENT_NAMES if name.startswith("agent."))),
    "tool": tuple(sorted(name for name in EVENT_NAMES if name.startswith("tool."))),
    "memory": tuple(sorted(name for name in EVENT_NAMES if name.startswith("memory."))),
    "subagent": tuple(sorted(name for name in EVENT_NAMES if name.startswith("subagent."))),
    "budget": tuple(sorted(name for name in EVENT_NAMES if name.startswith("budget."))),
    "gate": tuple(sorted(name for name in EVENT_NAMES if name.startswith("gate."))),
}

#: The type-checker surface for :data:`EVENT_NAMES`. Pydantic validates against
#: it too, but a ``mode="before"`` validator raises the explicit
#: :class:`UnknownEventNameError` first so the failure names the offending value.
TraceEventName = Literal[
    "run.started",
    "run.ended",
    "run.context.linked",
    "run.context.detached",
    "agent.turn",
    "tool.call.requested",
    "tool.call.started",
    "tool.call.completed",
    "tool.call.failed",
    "memory.capture",
    "memory.recall",
    "subagent.spawned",
    "subagent.completed",
    "budget.consumed",
    "gate.refused",
]


class EventStatus(StrEnum):
    """Closed status set for an instantaneous event."""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    REFUSED = "refused"
    #: Recorded but not acted on: a budget cap was reached, a sample was
    #: dropped, a link was only *disclosed* rather than inherited.
    SKIPPED = "skipped"


EVENT_STATUSES: Final[frozenset[str]] = frozenset(member.value for member in EventStatus)


class UnknownEventNameError(ValueError):
    """Raised when an event name is outside :data:`EVENT_NAMES`."""


class UnknownEventStatusError(ValueError):
    """Raised when an event status is outside :data:`EVENT_STATUSES`."""


def parse_event_name(value: object) -> str:
    """Return *value* if it is a known event name, else raise.

    The typed entry point. :class:`TraceEvent` validates through this too, but
    pydantic wraps anything raised inside a validator into a
    ``ValidationError``, so a caller that wants the specific failure -- a
    taxonomy check in a test, or an integration patch asserting its own event
    names -- calls this and gets :class:`UnknownEventNameError` directly. Both
    paths share the one :data:`EVENT_NAMES` set, so they cannot disagree.
    """
    if isinstance(value, str) and value in EVENT_NAMES:
        return value
    raise UnknownEventNameError(f"unknown trace event name {value!r}; expected one of {sorted(EVENT_NAMES)}")


def parse_event_status(value: object) -> EventStatus:
    """Return *value* as an :class:`EventStatus`, else raise.

    The typed counterpart to :func:`parse_event_name`, with the same
    pydantic-wrapping caveat.
    """
    if isinstance(value, EventStatus):
        return value
    if isinstance(value, str):
        try:
            return EventStatus(value)
        except ValueError as exc:
            raise UnknownEventStatusError(f"unknown trace event status {value!r}; expected one of {sorted(EVENT_STATUSES)}") from exc
    raise UnknownEventStatusError(f"trace event status must be a string or EventStatus, got {type(value).__name__}")


#: Redactor used when a caller does not inject one. A module-level *default
#: instance* is not mutable global state -- it holds no counters and cannot be
#: reconfigured -- but callers that need a different policy inject their own.
_DEFAULT_REDACTOR = Redactor()


class TraceEvent(BaseModel):
    """One typed, scrubbed, immutable fact about a run.

    ``trace_id`` and ``run_id`` are required and are not defaulted: an event
    without them is an orphan, and an orphan in a trace file is the exact
    failure this package exists to remove.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: TraceEventName
    status: EventStatus = EventStatus.OK
    trace_id: str
    run_id: str
    timestamp: float
    span_id: str | None = None
    parent_span_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    agent_name: str | None = None
    attributes: dict[str, Any] = {}
    #: ``False`` when this event was produced where the run context did not
    #: propagate (a subprocess, a raw thread). The parent ids are still
    #: carried in ``attributes["parent_trace_id"]`` / ``parent_run_id``, so the
    #: event is *linked* rather than orphaned.
    context_inherited: bool = True
    #: The closed reason a non-inheriting child recorded its link. Empty when
    #: ``context_inherited`` is ``True``.
    link_kind: str = ""

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_name(cls, value: object) -> object:
        if isinstance(value, dict) and "name" in value:
            parse_event_name(value["name"])
        return value

    @field_validator("status", mode="before")
    @classmethod
    def _reject_unknown_status(cls, value: object) -> EventStatus:
        return parse_event_status(value)

    @field_validator("attributes", mode="before")
    @classmethod
    def _scrub_attributes(cls, value: object) -> object:
        """Redact attributes *before* any other field is validated.

        Ordering is the enforcement: there is no path that reaches a sink with
        a raw attribute, because this runs first and replaces the value.
        """
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError(f"trace event attributes must be a mapping, got {type(value).__name__}")
        safe, _outcomes = _DEFAULT_REDACTOR.redact_attributes(value)
        return safe

    @model_validator(mode="after")
    def _require_link_disclosure(self) -> TraceEvent:
        """A non-inheriting child must say so, and must name its parent."""
        if self.context_inherited:
            return self
        if not self.link_kind:
            raise ValueError("context_inherited=False requires a non-empty link_kind naming how the parent link was carried")
        if not (self.attributes.get("parent_trace_id") or self.attributes.get("parent_run_id")):
            raise ValueError("context_inherited=False requires parent_trace_id or parent_run_id in attributes; a disclosed link is never an orphan")
        return self

    def to_record(self) -> dict[str, Any]:
        """Return the stable JSONL record for this event.

        ``v`` is the record-schema version and ``type`` lets one file carry
        events, spans and disclosures without a reader guessing. Attribute
        order inside the mapping is irrelevant to the format; the exporter
        sorts.
        """
        return {
            "v": 1,
            "type": "event",
            "name": self.name,
            "status": self.status.value,
            "ts": self.timestamp,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "context_inherited": self.context_inherited,
            "link_kind": self.link_kind,
            "attributes": dict(self.attributes),
        }
