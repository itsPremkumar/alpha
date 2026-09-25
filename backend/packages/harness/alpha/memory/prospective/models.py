"""Records and state contracts for prospective (intentional) memory.

Prospective memory is the agent's memory of obligations to the future: a
reminder, a standing commitment, or a recurring obligation.  It is deliberately
separate from factual/episodic memory because these records have a lifecycle
and must not be treated as already-completed knowledge.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProspectiveKind(StrEnum):
    """The kind of future obligation represented by an item."""

    REMINDER = "reminder"
    COMMITMENT = "commitment"
    RECURRING = "recurring"


class ProspectiveStatus(StrEnum):
    """Lifecycle states for a prospective item."""

    PENDING = "pending"
    FIRED = "fired"
    DONE = "done"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TriggerKind(StrEnum):
    """Supported structured trigger kinds."""

    TOOL = "tool"
    EVENT = "event"
    CONDITION = "condition"


#: Legal state transitions.  Terminal states intentionally have no outgoing
#: edges; changing a completed/cancelled/expired item would erase its history.
LEGAL_TRANSITIONS: dict[ProspectiveStatus, frozenset[ProspectiveStatus]] = {
    ProspectiveStatus.PENDING: frozenset(
        {
            ProspectiveStatus.FIRED,
            ProspectiveStatus.DONE,
            ProspectiveStatus.CANCELLED,
            ProspectiveStatus.EXPIRED,
        }
    ),
    ProspectiveStatus.FIRED: frozenset(
        {
            ProspectiveStatus.DONE,
            ProspectiveStatus.CANCELLED,
            ProspectiveStatus.EXPIRED,
        }
    ),
    ProspectiveStatus.DONE: frozenset(),
    ProspectiveStatus.CANCELLED: frozenset(),
    ProspectiveStatus.EXPIRED: frozenset(),
}

# A descriptive alias is useful to callers that think in terms of a state
# machine rather than a legal-transition table.
VALID_TRANSITIONS = LEGAL_TRANSITIONS
TRANSITIONS = LEGAL_TRANSITIONS
TERMINAL_STATUSES = frozenset(
    {
        ProspectiveStatus.DONE,
        ProspectiveStatus.CANCELLED,
        ProspectiveStatus.EXPIRED,
    }
)


def new_item_id() -> str:
    """Return a collision-resistant identifier for a new obligation."""

    return f"prospective_{uuid.uuid4().hex}"


class TriggerSpec(BaseModel):
    """A serializable tool/event/condition trigger specification.

    ``kind`` remains a string rather than a strict enum so an older or
    malformed on-disk record can be loaded and evaluated fail-closed.  This is
    intentional: unknown trigger kinds must not crash recall or accidentally
    match an obligation.
    """

    model_config = ConfigDict(extra="allow")

    kind: str = ""
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @field_validator("params", mode="before")
    @classmethod
    def _normalize_params(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return dict(value)
        return {"value": value}


class TriggerContext(BaseModel):
    """Facts supplied by a runtime seam when evaluating a trigger.

    The model intentionally contains no callbacks, stores, or I/O handles.  A
    middleware or scheduler can therefore build it from a small event payload
    without making trigger evaluation depend on the rest of Alpha.
    """

    model_config = ConfigDict(extra="allow")

    user_id: str | None = None
    agent_name: str | None = None
    tool_name: str | None = None
    event_name: str | None = None
    condition: str | dict[str, Any] | None = None
    tool: str | None = None
    event: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    event_data: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "tool_name" not in data and "tool" in data:
            data["tool_name"] = data["tool"]
        if "event_name" not in data and "event" in data:
            data["event_name"] = data["event"]
        if "tool_args" not in data and "arguments" in data:
            data["tool_args"] = data["arguments"]
        if "event_data" not in data and "payload" in data:
            data["event_data"] = data["payload"]
        if "attributes" not in data and "facts" in data and isinstance(data["facts"], Mapping):
            data["attributes"] = dict(data["facts"])
        return data

    @field_validator("tool_args", "arguments", "event_data", "payload", "values", "metadata", "attributes", "state", mode="before")
    @classmethod
    def _mapping_or_empty(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return dict(value)
        return {"value": value}

    @field_validator("tool_name", "event_name", "tool", "event", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class ProspectiveItem(BaseModel):
    """One future commitment, reminder, or recurring obligation."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    id: str = Field(default_factory=new_item_id, min_length=1)
    user_id: str = Field(default="default", min_length=1)
    agent_name: str | None = None
    kind: ProspectiveKind = ProspectiveKind.REMINDER
    content: str = Field(min_length=1)
    priority: int = Field(default=50, ge=-1, le=100)
    due_at: float | None = None
    trigger: TriggerSpec | None = None
    status: ProspectiveStatus = ProspectiveStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    surfaced_at: float | None = None
    completed_at: float | None = None
    grace_until: float | None = None
    source: str | dict[str, Any] | None = None
    linked_record_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Recurrence is explicit rather than hidden in arbitrary metadata so the
    # bounded expansion policy is inspectable in the persisted record.
    occurrence: int = Field(default=1, ge=1)
    recurrence_interval: float | None = Field(default=None, gt=0.0)
    recurring_max_occurrences: int | None = Field(default=None, ge=1)
    fired_at: float | None = None
    cancelled_at: float | None = None
    last_reason: str = ""
    version: int = Field(default=1, ge=1)

    @field_validator("user_id", mode="before")
    @classmethod
    def _default_user_id(cls, value: Any) -> str:
        text = str(value or "default").strip()
        if not text:
            return "default"
        return text

    @field_validator("content", mode="before")
    @classmethod
    def _nonempty_content(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("prospective content must not be empty")
        return text

    @field_validator("agent_name", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("source", mode="before")
    @classmethod
    def _source_value(cls, value: Any) -> str | dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, Mapping):
            return dict(value)
        text = str(value).strip()
        return text or None

    @field_validator("last_reason", mode="before")
    @classmethod
    def _reason_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("linked_record_ids", mode="before")
    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_mapping(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return dict(value)
        return {"value": value}

    @model_validator(mode="after")
    def _finite_timestamps(self) -> ProspectiveItem:
        numeric_fields = (
            "due_at",
            "created_at",
            "updated_at",
            "surfaced_at",
            "completed_at",
            "grace_until",
            "fired_at",
            "cancelled_at",
        )
        for name in numeric_fields:
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        return self

    @property
    def is_terminal(self) -> bool:
        """Whether the item has reached a terminal lifecycle state."""

        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        """Whether the item still represents future work."""

        return self.status in {ProspectiveStatus.PENDING, ProspectiveStatus.FIRED}

    @property
    def is_due(self) -> bool:
        """Whether the absolute due timestamp has arrived."""

        now = time.time()
        return self.due_at is not None and self.due_at <= now

    def is_due_at(self, now: float) -> bool:
        """Pure due check at an injected wall-clock timestamp."""

        return self.due_at is not None and self.due_at <= now

    def is_expired_at(self, now: float) -> bool:
        """Whether the grace window has elapsed.

        An item without a due date never expires.  When no explicit grace was
        supplied, the due instant itself is the end of the window.
        """

        if self.due_at is None or not self.is_active:
            return False
        if self.grace_until is None:
            return now > self.due_at
        return now >= self.grace_until

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for persistence/logging."""

        return self.model_dump(mode="json")


@dataclass(slots=True)
class LifecycleResult:
    """Disclosed outcome of one pure lifecycle operation.

    ``status`` is deliberately a small string contract rather than an
    exception: callers can safely distinguish ``succeeded``, ``skipped``,
    ``refused``, ``illegal_transition``, and ``failed`` and surface the reason
    to an operator or a status endpoint.
    """

    status: str
    item: ProspectiveItem | None = None
    changed: bool = False
    event: str = ""
    reason: str = ""
    error: str = ""
    next_item: ProspectiveItem | None = None
    provenance_status: str = ""
    provenance_error: str = ""
    current: int | None = None
    limit: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True only when the requested state change was applied."""

        return self.status == "succeeded" and self.changed

    @property
    def applied(self) -> bool:
        """Alias for :attr:`ok` useful at mutation call sites."""

        return self.ok

    def __getattr__(self, name: str) -> Any:
        """Delegate record attributes for ergonomic pure-function callers.

        Mutation code should inspect ``result.item`` explicitly, but allowing
        ``create_item(...).content`` keeps the small pure API pleasant without
        weakening the disclosed ``status``/``reason`` contract.
        """

        item = object.__getattribute__(self, "item")
        if item is not None and hasattr(item, name):
            return getattr(item, name)
        raise AttributeError(name)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Proxy Pydantic serialization when a result wraps an item."""

        if self.item is None:
            return {}
        return self.item.model_dump(*args, **kwargs)

    def model_copy(self, *args: Any, **kwargs: Any) -> ProspectiveItem:
        """Return a copy of the wrapped record, or fail clearly if absent."""

        if self.item is None:
            raise ValueError("lifecycle result has no item")
        return self.item.model_copy(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result without pretending an error was successful."""

        return {
            "status": self.status,
            "item": self.item.to_dict() if self.item is not None else None,
            "changed": self.changed,
            "event": self.event,
            "reason": self.reason,
            "error": self.error,
            "next_item": self.next_item.to_dict() if self.next_item is not None else None,
            "provenance_status": self.provenance_status,
            "provenance_error": self.provenance_error,
            "current": self.current,
            "limit": self.limit,
            "details": dict(self.details),
        }


def can_transition(current: ProspectiveStatus | str, target: ProspectiveStatus | str) -> bool:
    """Return whether a status transition is legal, including string inputs."""

    try:
        current_status = current if isinstance(current, ProspectiveStatus) else ProspectiveStatus(current)
        target_status = target if isinstance(target, ProspectiveStatus) else ProspectiveStatus(target)
    except ValueError:
        return False
    return target_status in LEGAL_TRANSITIONS.get(current_status, frozenset())


__all__ = [
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "VALID_TRANSITIONS",
    "LifecycleResult",
    "ProspectiveItem",
    "ProspectiveKind",
    "ProspectiveStatus",
    "TriggerContext",
    "TriggerKind",
    "TriggerSpec",
    "can_transition",
    "new_item_id",
]
