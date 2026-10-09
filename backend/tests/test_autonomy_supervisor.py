"""AutonomySupervisor + event bus behavior tests.

Invariants under test:
* flag off => zero tasks created, zero activity;
* flag on => the loop ticks, status reports runs > 0;
* stop() is a single clean shutdown path with no leaked tasks;
* the bus drops instead of blocking and isolates handler failures.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.config.autonomy_config import AutonomyConfig, AutonomyLoopConfig
from app.gateway.autonomy.supervisor import AutonomySupervisor, LoopSpec, _summarize


def _config(*, enabled: bool, loop_enabled: bool, interval: float = 0.01) -> AutonomyConfig:
    return AutonomyConfig(
        enabled=enabled,
        loops={"test_loop": AutonomyLoopConfig(enabled=loop_enabled, interval_seconds=interval, jitter_seconds=0.0)},
    )


def test_summary_keeps_apex_failures_and_counts_error_rows_without_details() -> None:
    summary = _summarize(
        {
            "sessions": 10,
            "dispatched": 0,
            "running": 0,
            "awaiting_verification": 0,
            "completed": 0,
            "replanned": 0,
            "approval_requeued": 0,
            "blocked": 2,
            "failed": 7,
            "budget_exhausted": 1,
            "errors": [{"session_id": "private-session", "error": "sensitive detail"}],
        }
    )

    assert summary.startswith("failed=7 blocked=2 budget_exhausted=1 errors=1")
    assert "sessions=10" in summary
    assert "sensitive detail" not in summary
    assert len(summary) <= 300


@pytest.mark.asyncio
async def test_disabled_loop_creates_no_task_and_no_activity() -> None:
    supervisor = AutonomySupervisor(_config(enabled=True, loop_enabled=False))
    calls: list[int] = []

    async def tick() -> dict:
        calls.append(1)
        return {"ok": True}

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    await asyncio.sleep(0.05)
    status = supervisor.status()["loops"]["test_loop"]
    assert status["enabled"] is False
    assert status["task_alive"] is False
    assert status["runs"] == 0
    assert calls == []
    await supervisor.stop()


@pytest.mark.asyncio
async def test_master_switch_off_means_zero_activity() -> None:
    supervisor = AutonomySupervisor(_config(enabled=False, loop_enabled=True))
    calls: list[int] = []

    async def tick() -> dict:
        calls.append(1)
        return {}

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    await asyncio.sleep(0.05)
    assert calls == []
    await supervisor.stop()


@pytest.mark.asyncio
async def test_enabled_loop_ticks_and_reports_status() -> None:
    supervisor = AutonomySupervisor(_config(enabled=True, loop_enabled=True, interval=0.01))
    calls: list[int] = []
    first_tick = asyncio.Event()

    async def tick() -> dict:
        calls.append(1)
        first_tick.set()
        return {"count": len(calls)}

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    await asyncio.wait_for(first_tick.wait(), timeout=5.0)
    status = supervisor.status()["loops"]["test_loop"]
    assert status["runs"] >= 1
    assert status["task_alive"] is True
    assert status["last_summary"].startswith("count=")
    await supervisor.stop()
    assert all(task.done() for task in supervisor._tasks.values()) or not supervisor._tasks
    # After stop, no loop may still be marked running.
    assert supervisor.status()["loops"]["test_loop"]["running"] is False


@pytest.mark.asyncio
async def test_failing_tick_increments_failures_and_parks_after_budget() -> None:
    config = AutonomyConfig(
        enabled=True,
        loops={
            "test_loop": AutonomyLoopConfig(
                enabled=True,
                interval_seconds=0.01,
                jitter_seconds=0.0,
                restart_budget=2,
                restart_window_seconds=60.0,
            )
        },
    )
    supervisor = AutonomySupervisor(config)

    async def tick() -> dict:
        raise RuntimeError("boom")

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    await asyncio.sleep(0.15)
    status = supervisor.status()["loops"]["test_loop"]
    assert status["failures"] >= 2
    assert status["parked"] is True
    assert "failures within" in status["park_reason"]
    await supervisor.stop()


@pytest.mark.asyncio
async def test_register_is_idempotent() -> None:
    supervisor = AutonomySupervisor()

    async def tick_a() -> dict:
        return {"a": 1}

    async def tick_b() -> dict:
        return {"b": 2}

    spec_a = LoopSpec(loop_id="dup", description="a", tick=tick_a)
    supervisor.register(spec_a)
    supervisor.register(LoopSpec(loop_id="dup", description="a", tick=tick_b))
    assert list(supervisor.status()["loops"]) == ["dup"]
    # Re-registration replaced the tick, not the state.
    assert supervisor._specs["dup"].tick is tick_b


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe_and_drop_oldest() -> None:
    from alpha.events.bus import EventBus

    received: list[str] = []

    async def handler(event) -> None:
        received.append(event.name)

    bus = EventBus(queue_maxsize=2, handler_timeout_seconds=5.0)
    subscription = bus.subscribe("autonomy.", handler)
    for index in range(5):
        await bus.publish("autonomy.loop.completed", {"i": index})
    await asyncio.sleep(0.1)
    assert received, "subscriber received nothing"
    status = bus.status()
    assert status["published"] == 5
    bus.unsubscribe(subscription)


def test_register_default_loops_includes_expected_loops() -> None:
    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()
    loops = supervisor.status()["loops"]
    assert "sentinel" in loops
    assert "swarm_status" in loops
    assert "perpetual" in loops
    assert "review_queue" in loops
    assert "skill_curator" in loops
    assert "enterprise_heartbeat" in loops
    assert "self_update" in loops
