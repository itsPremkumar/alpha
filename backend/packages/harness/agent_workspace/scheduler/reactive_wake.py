"""Reactive Wake Gate and Event-Driven Dispatcher.

Enables asynchronous tasks, subagent delegations, and background processes
to register event wake gates (`notify_on_exit`), waking paused agent runs
immediately upon completion without token-burning polling loops.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

WakeCondition = Literal["all_complete", "any_complete", "on_failure", "on_exit"]


@dataclass
class TaskEvent:
    """Event emitted when a monitored background task changes state."""

    task_id: str
    status: str  # "completed", "failed", "timeout", "cancelled"
    exit_code: int = 0
    output_summary: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WakeGate:
    """Represents a registered condition waiting for one or more task events."""

    gate_id: str
    thread_id: str
    target_task_ids: set[str]
    condition: WakeCondition
    timeout_seconds: float
    created_at: float = field(default_factory=time.time)
    received_events: dict[str, TaskEvent] = field(default_factory=dict)
    is_triggered: bool = False
    trigger_reason: str = ""

    def check_condition(self) -> bool:
        """Evaluate whether the condition has been met."""
        if self.is_triggered:
            return True

        if self.condition == "any_complete":
            for ev in self.received_events.values():
                if ev.status in ("completed", "failed", "cancelled"):
                    self.is_triggered = True
                    self.trigger_reason = f"Task {ev.task_id} exited with status {ev.status}"
                    return True

        elif self.condition == "on_failure":
            for ev in self.received_events.values():
                if ev.status == "failed" or ev.exit_code != 0:
                    self.is_triggered = True
                    self.trigger_reason = f"Task {ev.task_id} failed with exit code {ev.exit_code}"
                    return True

        elif self.condition in ("all_complete", "on_exit"):
            # All target task IDs must have an event
            if self.target_task_ids.issubset(set(self.received_events.keys())):
                self.is_triggered = True
                self.trigger_reason = f"All {len(self.target_task_ids)} tasks exited"
                return True

        return False


class ReactiveWakeGateRegistry:
    """Thread-safe registry for reactive task monitoring and wake gates."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gates: dict[str, WakeGate] = {}
        # Asyncio event mapping for async waiters: gate_id -> (Event, EventLoop)
        self._async_waiters: dict[str, tuple[asyncio.Event, asyncio.AbstractEventLoop]] = {}

    def register_gate(
        self,
        thread_id: str,
        task_ids: list[str],
        condition: WakeCondition = "all_complete",
        timeout_seconds: float = 300.0,
    ) -> WakeGate:
        """Register a new wake gate waiting for task events."""
        if not task_ids:
            raise ValueError("task_ids must contain at least one task ID to monitor.")
        with self._lock:
            gate_id = f"gate_{uuid.uuid4().hex[:8]}"
            gate = WakeGate(
                gate_id=gate_id,
                thread_id=thread_id,
                target_task_ids=set(task_ids),
                condition=condition,
                timeout_seconds=timeout_seconds,
            )
            self._gates[gate_id] = gate
            return gate

    def notify_task_event(
        self,
        task_id: str,
        status: str,
        exit_code: int = 0,
        output_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> list[WakeGate]:
        """Record a task state change and trigger any satisfied wake gates."""
        event = TaskEvent(
            task_id=task_id,
            status=status,
            exit_code=exit_code,
            output_summary=output_summary,
            metadata=metadata or {},
        )

        triggered_gates: list[WakeGate] = []
        with self._lock:
            for gate in self._gates.values():
                if task_id in gate.target_task_ids:
                    gate.received_events[task_id] = event
                    if gate.check_condition():
                        triggered_gates.append(gate)
                        waiter = self._async_waiters.get(gate.gate_id)
                        if waiter:
                            ev, ev_loop = waiter
                            try:
                                if ev_loop and ev_loop.is_running():
                                    ev_loop.call_soon_threadsafe(ev.set)
                                else:
                                    ev.set()
                            except Exception:
                                pass

        return triggered_gates

    async def wait_for_gate(self, gate_id: str, loop: asyncio.AbstractEventLoop | None = None) -> WakeGate:
        """Asynchronously await fulfillment of a wake gate with timeout."""
        with self._lock:
            gate = self._gates.get(gate_id)
            if not gate:
                raise KeyError(f"Wake gate '{gate_id}' not found.")
            if gate.check_condition():
                return gate
            current_loop = loop or asyncio.get_running_loop()
            async_ev = asyncio.Event()
            self._async_waiters[gate_id] = (async_ev, current_loop)

        try:
            await asyncio.wait_for(async_ev.wait(), timeout=gate.timeout_seconds)
        except TimeoutError:
            with self._lock:
                gate.is_triggered = True
                gate.trigger_reason = f"Timed out after {gate.timeout_seconds}s"
        finally:
            with self._lock:
                self._async_waiters.pop(gate_id, None)

        return gate

    def get_gate(self, gate_id: str) -> WakeGate | None:
        """Get status of a wake gate."""
        with self._lock:
            return self._gates.get(gate_id)

    def cleanup_thread(self, thread_id: str) -> None:
        """Remove all gates associated with a thread."""
        with self._lock:
            to_del = [gid for gid, g in self._gates.items() if g.thread_id == thread_id]
            for gid in to_del:
                self._gates.pop(gid, None)
                self._async_waiters.pop(gid, None)


# Global singleton
_GLOBAL_WAKE_REGISTRY = ReactiveWakeGateRegistry()


def get_reactive_wake_registry() -> ReactiveWakeGateRegistry:
    """Return the global reactive wake gate registry instance."""
    return _GLOBAL_WAKE_REGISTRY
