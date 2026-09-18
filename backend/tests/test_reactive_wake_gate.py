"""Unit tests for the Reactive Wake Gate and Event Dispatcher."""

from __future__ import annotations

import asyncio

import pytest

from agent_workspace.scheduler.reactive_wake import ReactiveWakeGateRegistry
from agent_workspace.tools.builtins.wake_gate_tool import await_task_event


def test_wake_gate_all_complete():
    registry = ReactiveWakeGateRegistry()
    gate = registry.register_gate(
        thread_id="t-1",
        task_ids=["task-a", "task-b"],
        condition="all_complete",
    )
    assert gate.is_triggered is False

    # Task A completes
    triggered = registry.notify_task_event("task-a", status="completed", exit_code=0)
    assert len(triggered) == 0
    assert gate.is_triggered is False

    # Task B completes -> both done
    triggered = registry.notify_task_event("task-b", status="completed", exit_code=0)
    assert len(triggered) == 1
    assert gate.is_triggered is True
    assert "All 2 tasks exited" in gate.trigger_reason


def test_wake_gate_any_complete():
    registry = ReactiveWakeGateRegistry()
    gate = registry.register_gate(
        thread_id="t-2",
        task_ids=["subagent-1", "subagent-2"],
        condition="any_complete",
    )
    assert gate.is_triggered is False

    # Subagent 2 finishes first
    triggered = registry.notify_task_event("subagent-2", status="completed", exit_code=0)
    assert len(triggered) == 1
    assert gate.is_triggered is True
    assert "subagent-2" in gate.trigger_reason


def test_wake_gate_on_failure():
    registry = ReactiveWakeGateRegistry()
    gate = registry.register_gate(
        thread_id="t-3",
        task_ids=["worker-1"],
        condition="on_failure",
    )
    assert gate.is_triggered is False

    # Worker 1 succeeds -> does not trigger on_failure
    triggered = registry.notify_task_event("worker-1", status="completed", exit_code=0)
    assert len(triggered) == 0
    assert gate.is_triggered is False

    # Another gate with failure
    gate2 = registry.register_gate(
        thread_id="t-3",
        task_ids=["worker-2"],
        condition="on_failure",
    )
    triggered2 = registry.notify_task_event("worker-2", status="failed", exit_code=1)
    assert len(triggered2) == 1
    assert gate2.is_triggered is True


@pytest.mark.asyncio
async def test_wake_gate_async_wait():
    registry = ReactiveWakeGateRegistry()
    gate = registry.register_gate(
        thread_id="t-async",
        task_ids=["long-job-1"],
        condition="all_complete",
        timeout_seconds=5.0,
    )

    async def _simulate_task():
        await asyncio.sleep(0.1)
        registry.notify_task_event("long-job-1", status="completed", exit_code=0)

    asyncio.create_task(_simulate_task())
    result_gate = await registry.wait_for_gate(gate.gate_id)

    assert result_gate.is_triggered is True
    assert "long-job-1" in result_gate.received_events


def test_await_task_event_tool():
    res = await_task_event.invoke(
        {
            "task_ids_json": '["build-step-1", "test-step-2"]',
            "condition": "all_complete",
            "timeout_seconds": 30.0,
            "thread_id": "thread-main",
        }
    )

    assert "[WAKE_GATE_REGISTERED]" in res
    assert "Gate ID:" in res


def test_wake_gate_empty_task_ids_raises():
    registry = ReactiveWakeGateRegistry()
    with pytest.raises(ValueError, match="task_ids must contain at least one task ID"):
        registry.register_gate(thread_id="t-err", task_ids=[])


@pytest.mark.asyncio
async def test_wake_gate_cross_thread_notification():
    import threading
    import time

    registry = ReactiveWakeGateRegistry()
    gate = registry.register_gate(
        thread_id="t-cross-thread",
        task_ids=["worker-thread-task"],
        condition="all_complete",
        timeout_seconds=5.0,
    )

    def _worker():
        time.sleep(0.1)
        registry.notify_task_event("worker-thread-task", status="completed", exit_code=0)

    t = threading.Thread(target=_worker)
    t.start()

    res = await registry.wait_for_gate(gate.gate_id)
    t.join()
    assert res.is_triggered is True
    assert "worker-thread-task" in res.received_events
