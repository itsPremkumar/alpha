"""Core type contracts, event schemas, and priority tiers for the Alpha Mod Kernel (AMK)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from alpha.mods.context import CapabilityContext


class ModPriority(IntEnum):
    """Deterministic priority tiers for the Alpha Mod Kernel middleware chain.

    Execution proceeds in ascending priority order (lowest integer first):
    KERNEL (0) -> EMERGENCY (100) -> SECURITY (200) -> BUDGET (300) ->
    AUTONOMY (500) -> EXECUTION (700) -> VERIFICATION (800) ->
    RECOVERY (900) -> OBSERVABILITY (1000) -> USER_EXTENSIONS (2000).
    """

    KERNEL = 0  # Invariants, correlation tagging, audit logging
    EMERGENCY = 100  # Fleet-wide ESTOP, immediate execution halting
    SECURITY = 200  # Blast radius, secret leakage, permission gating
    BUDGET = 300  # Token/spend quotas, admission limits
    AUTONOMY = 500  # APEX autopilot, task routing, dynamic workflow
    EXECUTION = 700  # Instrumentation, tracing, prompt compilation
    VERIFICATION = 800  # Acceptance criteria gate, evidence validation
    RECOVERY = 900  # Sentinels, self-repair loops, fallback paths
    OBSERVABILITY = 1000  # Flight recorder, metrics, telemetry projections
    USER_EXTENSIONS = 2000  # Operator-installed custom third-party mods


class EventOutcome(StrEnum):
    """Explicit lifecycle outcomes yielded by a mod when intercepting an event."""

    OBSERVE = "observe"  # Observe event without mutating; pass control to next(event)
    CONTINUE = "continue"  # Pass control to next(event)
    REWRITE = "rewrite"  # Mutate event and pass next(mutated_event)
    ANSWER = "answer"  # Short-circuit and return immediately without downstream execution
    DENY = "deny"  # Refuse the operation due to security/policy failure
    DEFER = "defer"  # Park event for asynchronous human approval or resolution
    RETRY = "retry"  # Request automated retry with backoff or adjusted parameters
    ESCALATE = "escalate"  # Escalate event to supervisor or specialist repair agent


@dataclass(frozen=True)
class CorrelationContext:
    """Immutable trace and lineage context propagated across all mod events."""

    trace_id: str
    run_id: str
    mission_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    parent_id: str | None = None
    causal_parent_id: str | None = None
    attempt: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        mission_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        parent_id: str | None = None,
        causal_parent_id: str | None = None,
        attempt: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> CorrelationContext:
        """Create a new correlation context, generating missing trace/run IDs."""
        return cls(
            trace_id=trace_id or uuid.uuid4().hex,
            run_id=run_id or uuid.uuid4().hex,
            mission_id=mission_id,
            task_id=task_id,
            agent_id=agent_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            parent_id=parent_id,
            causal_parent_id=causal_parent_id,
            attempt=attempt,
            metadata=dict(metadata or {}),
        )

    def child(
        self,
        *,
        tool_call_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        parent_id: str | None = None,
        causal_parent_id: str | None = None,
        attempt: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CorrelationContext:
        """Derive a descendant correlation context retaining root lineage."""
        meta = dict(self.metadata)
        if metadata:
            meta.update(metadata)
        fallback_parent = self.tool_call_id or self.turn_id or self.task_id or self.run_id
        return CorrelationContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            mission_id=self.mission_id,
            task_id=task_id or self.task_id,
            agent_id=agent_id or self.agent_id,
            turn_id=turn_id or self.turn_id,
            tool_call_id=tool_call_id or self.tool_call_id,
            parent_id=parent_id or fallback_parent,
            causal_parent_id=causal_parent_id or fallback_parent,
            attempt=attempt if attempt is not None else self.attempt,
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "parent_id": self.parent_id,
            "causal_parent_id": self.causal_parent_id,
            "attempt": self.attempt,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrelationContext:
        return cls(
            trace_id=str(data.get("trace_id") or uuid.uuid4().hex),
            run_id=str(data.get("run_id") or uuid.uuid4().hex),
            mission_id=data.get("mission_id"),
            task_id=data.get("task_id"),
            agent_id=data.get("agent_id"),
            turn_id=data.get("turn_id"),
            tool_call_id=data.get("tool_call_id"),
            parent_id=data.get("parent_id"),
            causal_parent_id=data.get("causal_parent_id"),
            attempt=int(data.get("attempt", 1)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class AlphaEvent:
    """A strongly typed runtime event delivered through the Alpha Mod Kernel."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation: CorrelationContext = field(default_factory=lambda: CorrelationContext.create())
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source: str = "runtime"

    def copy(
        self,
        *,
        name: str | None = None,
        payload: dict[str, Any] | None = None,
        correlation: CorrelationContext | None = None,
    ) -> AlphaEvent:
        """Create a clone of the event with optional overrides."""
        return AlphaEvent(
            name=name if name is not None else self.name,
            payload=dict(payload if payload is not None else self.payload),
            correlation=correlation or self.correlation,
            timestamp=self.timestamp,
            event_id=self.event_id,
            source=self.source,
        )

    def with_payload(self, **kwargs: Any) -> AlphaEvent:
        """Return a copy with updated payload fields."""
        new_payload = dict(self.payload)
        new_payload.update(kwargs)
        return self.copy(payload=new_payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload": dict(self.payload),
            "correlation": self.correlation.to_dict(),
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlphaEvent:
        corr_data = data.get("correlation")
        corr = CorrelationContext.from_dict(corr_data) if isinstance(corr_data, dict) else CorrelationContext.create()
        return cls(
            name=str(data.get("name", "unknown")),
            payload=dict(data.get("payload") or {}),
            correlation=corr,
            timestamp=float(data.get("timestamp", time.time())),
            event_id=str(data.get("event_id") or uuid.uuid4().hex),
            source=str(data.get("source", "runtime")),
        )


@dataclass
class EventResult:
    """The terminal or continuing verdict returned by a mod handler."""

    outcome: EventOutcome
    event: AlphaEvent
    response_payload: dict[str, Any] | None = None
    reason: str = ""
    error: Exception | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def continue_(
        cls,
        event: AlphaEvent,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.CONTINUE,
            event=event,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def observe(
        cls,
        event: AlphaEvent,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.OBSERVE,
            event=event,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def rewrite(
        cls,
        mutated_event: AlphaEvent,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.REWRITE,
            event=mutated_event,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def answer(
        cls,
        event: AlphaEvent,
        response_payload: dict[str, Any] | None = None,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.ANSWER,
            event=event,
            response_payload=response_payload,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def deny(
        cls,
        event: AlphaEvent,
        reason: str,
        *,
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.DENY,
            event=event,
            reason=reason,
            error=error,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def defer(
        cls,
        event: AlphaEvent,
        reason: str,
        *,
        response_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.DEFER,
            event=event,
            reason=reason,
            response_payload=response_payload,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def retry(
        cls,
        event: AlphaEvent,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.RETRY,
            event=event,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def escalate(
        cls,
        event: AlphaEvent,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> EventResult:
        return cls(
            outcome=EventOutcome.ESCALATE,
            event=event,
            reason=reason,
            metadata=dict(metadata or {}),
        )


NextHandler = Callable[[AlphaEvent], Awaitable[EventResult]]


@runtime_checkable
class AlphaMod(Protocol):
    """Protocol that every Alpha Mod must implement."""

    name: str
    version: str
    priority: int
    required_capabilities: set[str]
    subscribed_events: set[str] | list[str] | tuple[str] | None  # None matches all events

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        """Intercept, mutate, observe, or short-circuit an event."""
        ...
