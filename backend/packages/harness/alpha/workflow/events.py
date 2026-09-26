"""Event schemas and event emission for Alpha Dynamic Workflows."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y%m%d%H%M%S%f"))
    workflow_run_id: str
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


logger = logging.getLogger(__name__)
# Redact credential-bearing keys, not measured accounting fields such as
# ``tokens_used``/``token_budget``.  A broad ``token`` substring match would
# silently destroy the very usage telemetry the workflow event log preserves.
_SECRET_KEY_RE = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|authorization|credential|access[_-]?token|refresh[_-]?token|bearer)",
    re.IGNORECASE,
)
_SECRET_EXACT_KEYS = frozenset({"token", "auth_token", "session_token"})


def _redact_event_value(value: Any, key: str | None = None) -> Any:
    if key and (_SECRET_KEY_RE.search(key) or key.lower() in _SECRET_EXACT_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_event_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_event_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_event_value(item) for item in value)
    return copy.deepcopy(value)


def redact_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a detached, credential-redacted payload for any persistence seam."""
    redacted = _redact_event_value(payload)
    return redacted if isinstance(redacted, dict) else {}


class WorkflowEventDispatcher:
    """Subscription + in-memory history + optional DURABLE append (W-N1).

    A ``durable_sink`` (normally ``DurableEventLog.append``) makes every
    emitted event survive a process restart, so a fresh process can
    hydrate the run instead of losing its history. Sink failures are never
    silent: they are counted, the last real reason is retained for the
    durability status endpoint, and an ERROR log carries the traceback.
    Listener and event-bus failures are disclosed the same way (both used
    to be swallowed by a bare ``except: pass``, which made a broken
    listener indistinguishable from a healthy one).
    """

    def __init__(
        self,
        durable_sink: Callable[[WorkflowEvent], Any] | None = None,
        *,
        max_events: int = 10_000,
    ) -> None:
        self._listeners: list[Callable[[WorkflowEvent], Any]] = []
        self._event_log: list[WorkflowEvent] = []
        self.max_events = max(100, int(max_events))
        self._durable_sink: Callable[[WorkflowEvent], Any] | None = durable_sink
        self.durable_write_failures = 0
        self.last_durable_error: str | None = None
        # Generic library callers retain best-effort behavior; the Gateway
        # workflow router opts into fail-closed admission so a run cannot
        # execute without an audit trail.
        self.fail_closed_on_durable_error = False

    def subscribe(self, listener: Callable[[WorkflowEvent], Any]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[WorkflowEvent], Any]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def attach_durable_sink(self, sink: Callable[[WorkflowEvent], Any]) -> None:
        """Attach (or replace) the durable append seam."""
        self._durable_sink = sink

    def durable_status(self) -> dict[str, Any]:
        """Honest durability status: attached? how many appends failed? why?"""
        return {
            "attached": self._durable_sink is not None,
            "write_failures": self.durable_write_failures,
            "last_error": self.last_durable_error,
        }

    @staticmethod
    def _publish_to_bus(name: str, payload: dict[str, Any]) -> None:
        """Publish to the process event bus from a sync OR async caller.

        ``EventBus.publish`` is a coroutine. From an async caller we schedule
        it on the running loop and log any late failure; from a sync caller
        (the engine/loop emit sites) we run it on a short-lived daemon thread
        with its own loop. Failures are logged, never swallowed. (The previous
        code imported a non-existent ``get_global_bus`` and hid the ImportError
        behind a bare ``except: pass`` — so workflow events never reached the
        bus at all.)
        """
        from alpha.events.bus import get_event_bus

        bus = get_event_bus()
        if not bus:
            return

        def _log_failure(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.warning("Workflow event bus publish task failed: %r", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(bus.publish(name, payload)).add_done_callback(_log_failure)
            return

        def _run() -> None:
            try:
                asyncio.run(bus.publish(name, payload))
            except Exception as exc:  # disclosed, never silent
                logger.warning("Workflow event bus publish failed for %s: %r", name, exc, exc_info=True)

        threading.Thread(target=_run, name="workflow-event-bus", daemon=True).start()

    def emit(
        self,
        event_type: str,
        run_id: str,
        *,
        idempotency_key: str | None = None,
        **payload: Any,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            workflow_run_id=run_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=_redact_event_value(payload),
        )
        self._event_log.append(event)
        if len(self._event_log) > self.max_events:
            del self._event_log[: len(self._event_log) - self.max_events]

        # Durable append first: the in-memory list is the fast path, the
        # JSONL log is the record that survives a restart.
        if self._durable_sink is not None:
            try:
                self._durable_sink(event)
            except Exception as exc:
                self.durable_write_failures += 1
                self.last_durable_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "Durable event append FAILED (%s, run %s): %s",
                    event_type,
                    run_id,
                    self.last_durable_error,
                    exc_info=True,
                )
                if self.fail_closed_on_durable_error:
                    raise

        # Notify in-memory listeners
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as exc:
                logger.warning("Workflow event listener failed for %s: %r", event_type, exc, exc_info=True)

        # Publish to the Alpha system event bus (real symbol; sync/async safe)
        try:
            self._publish_to_bus(f"workflow.{event_type}", event.model_dump())
        except Exception as exc:
            logger.warning("Workflow event bus publish failed for %s: %r", event_type, exc, exc_info=True)

        return event

    def get_events(self, run_id: str | None = None) -> list[WorkflowEvent]:
        if run_id:
            return [e for e in self._event_log if e.workflow_run_id == run_id]
        return list(self._event_log)


_GLOBAL_EVENT_DISPATCHER = WorkflowEventDispatcher()


def get_event_dispatcher() -> WorkflowEventDispatcher:
    return _GLOBAL_EVENT_DISPATCHER
