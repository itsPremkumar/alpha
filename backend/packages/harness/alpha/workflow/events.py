"""Event schemas and event emission for Alpha Dynamic Workflows."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
    workflow_run_id: str
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowEventDispatcher:
    """Manages subscription and emission of workflow lifecycle events."""

    def __init__(self) -> None:
        self._listeners: list[Callable[[WorkflowEvent], Any]] = []
        self._event_log: list[WorkflowEvent] = []

    def subscribe(self, listener: Callable[[WorkflowEvent], Any]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[WorkflowEvent], Any]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, event_type: str, run_id: str, **payload: Any) -> WorkflowEvent:
        event = WorkflowEvent(
            workflow_run_id=run_id,
            event_type=event_type,
            payload=payload,
        )
        self._event_log.append(event)

        # Notify in-memory listeners
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                pass

        # Publish to Alpha system event bus if loaded
        try:
            from alpha.events.bus import get_global_bus

            bus = get_global_bus()
            if bus:
                bus.publish(f"workflow.{event_type}", event.model_dump())
        except Exception:
            pass

        return event

    def get_events(self, run_id: str | None = None) -> list[WorkflowEvent]:
        if run_id:
            return [e for e in self._event_log if e.workflow_run_id == run_id]
        return list(self._event_log)


_GLOBAL_EVENT_DISPATCHER = WorkflowEventDispatcher()


def get_event_dispatcher() -> WorkflowEventDispatcher:
    return _GLOBAL_EVENT_DISPATCHER
