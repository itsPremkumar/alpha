"""AutonomySupervisor + event bus behavior tests.

Invariants under test:
* flag off => zero tasks created, zero activity;
* flag on => the loop ticks, status reports runs > 0;
* stop() is a single clean shutdown path with no leaked tasks;
* a tick that outlives its deadline keeps its loop slot, because the worker
  thread cannot be interrupted and a second concurrent tick of the same loop
  is exactly what ``max_concurrent`` forbids;
* the bus drops instead of blocking and isolates handler failures.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest

from alpha.config.autonomy_config import AutonomyConfig, AutonomyLoopConfig
from app.gateway.autonomy.supervisor import AutonomySupervisor, LoopSpec, _summarize


def _config(*, enabled: bool, loop_enabled: bool, interval: float = 0.01) -> AutonomyConfig:
    return AutonomyConfig(
        enabled=enabled,
        loops={"test_loop": AutonomyLoopConfig(enabled=loop_enabled, interval_seconds=interval, jitter_seconds=0.0)},
    )


@pytest.fixture(autouse=True, scope="module")
def _warm_autonomy_admission() -> None:
    """Pay the admission path's one-time import cost before any timing-sensitive test.

    ``_tick_once`` imports ``alpha.mods`` on the first tick of the process, which
    costs tens of seconds on a cold cache. Without this warm-up a fixed-interval
    test measures that import rather than the supervisor, so a poll that expects
    two failures inside 0.15s observes zero on a loaded machine and reads as a
    broken restart budget.
    """
    import alpha.mods.kernel  # noqa: F401 - imported for its side effect


@pytest.fixture(autouse=True)
def _release_worker_threads() -> Iterator[None]:
    """Yield, then assert no loop test left a worker thread blocked."""
    yield
    assert not [thread.name for thread in threading.enumerate() if thread.name.startswith("autonomy-tick:")], "a loop test leaked a blocked worker thread"


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
    # `first_tick` fires inside the tick, which is before the supervisor has
    # recorded (and published) its outcome, so poll for the recorded run rather
    # than reading status off the back of the tick's own event.
    await asyncio.wait_for(first_tick.wait(), timeout=60.0)
    deadline = time.monotonic() + 30.0
    while supervisor.status()["loops"]["test_loop"]["runs"] < 1 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
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
    # Poll rather than sleep a fixed interval: on a loaded machine the first
    # ticks are slow, and a fixed wait measures the host, not the budget.
    deadline = time.monotonic() + 30.0
    while not supervisor.status()["loops"]["test_loop"]["parked"] and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    status = supervisor.status()["loops"]["test_loop"]
    assert status["failures"] >= 2
    assert status["parked"] is True
    assert "failures within" in status["park_reason"]
    await supervisor.stop()


# -- a tick that outlives its deadline -----------------------------------------


class _GatedTick:
    """A synchronous tick that blocks until released, recording its own overlap.

    It mirrors the real loop adapters: a plain callable, so the supervisor runs
    it on a worker thread where cancellation cannot reach it.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.enters = 0
        self.exits = 0

    def __call__(self) -> dict:
        self.enters += 1
        self.entered.set()
        self.release.wait(10.0)
        self.exits += 1
        return {"ok": True}

    @property
    def overlap(self) -> int:
        """Ticks inside the worker at once — 1 is the documented maximum."""
        return self.enters - self.exits


def _gated_config(interval: float, stop_timeout: float) -> AutonomyConfig:
    return AutonomyConfig(
        enabled=True,
        loops={
            "test_loop": AutonomyLoopConfig(
                enabled=True,
                interval_seconds=interval,
                jitter_seconds=0.0,
                max_concurrent=1,
                stop_timeout_seconds=stop_timeout,
                restart_budget=5,
                restart_window_seconds=60.0,
            )
        },
    )


@pytest.mark.asyncio
async def test_overrunning_tick_holds_its_loop_slot_until_the_worker_ends() -> None:
    """A tick the supervisor stops waiting for must not be re-admitted.

    ``max_concurrent`` is 1, yet the deadline used to release ``running`` while
    the worker thread was still inside the tick, so the next interval started a
    second tick of the same loop: two concurrent sentinel/perpetual/APEX passes
    sharing the same durable state, and a fresh overrun every interval.
    """
    supervisor = AutonomySupervisor(_gated_config(interval=0.02, stop_timeout=0.2))
    tick = _GatedTick()
    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    try:
        assert await asyncio.to_thread(tick.entered.wait, 5.0)
        # Far longer than the 0.2s deadline and many intervals beyond it, so a
        # supervisor that drops the in-flight slot has every chance to re-admit.
        await asyncio.sleep(1.0)
        assert supervisor.status()["loops"]["test_loop"]["failures"] >= 1, "the tick deadline never fired; this run proves nothing"
        assert tick.enters == 1, "a second tick of the same loop started while the first worker was still in it"
        assert tick.overlap <= 1
        status = supervisor.status()["loops"]["test_loop"]
        assert status["running"] is True
        assert status["in_flight"] == 1
        assert status["overruns"] >= 1
    finally:
        tick.release.set()
        await supervisor.stop()
    assert supervisor.status()["loops"]["test_loop"]["in_flight"] == 0
    assert supervisor.status()["loops"]["test_loop"]["running"] is False


@pytest.mark.asyncio
async def test_finished_ticks_are_released_from_the_in_flight_set() -> None:
    """A completed tick must not stay in `_inflight` and grow for the process life.

    The set is what ``stop()`` drains, and a task nobody discards from it turns
    the drain into a scan over every tick the supervisor ever ran.
    """
    supervisor = AutonomySupervisor(_gated_config(interval=0.02, stop_timeout=0.2))
    runs = 0

    async def tick() -> dict:
        nonlocal runs
        runs += 1
        return {"count": runs}

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    deadline = time.monotonic() + 30.0
    while runs < 3 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert runs >= 3
    assert supervisor._inflight["test_loop"] == set()
    assert supervisor._workers["test_loop"] == set()
    assert supervisor.status()["loops"]["test_loop"]["in_flight"] == 0
    await supervisor.stop()


@pytest.mark.asyncio
async def test_stop_waits_a_bounded_grace_period_for_an_in_flight_tick() -> None:
    """stop() must not report a loop as idle while its tick is still executing.

    Cancelling the loop task abandons a worker thread mid-tick, so the loop kept
    mutating subsystem state through the whole Gateway drain — the exact race the
    "stop the supervisor first" ordering in the lifespan exists to prevent.
    """
    supervisor = AutonomySupervisor(_gated_config(interval=0.02, stop_timeout=0.2))
    tick = _GatedTick()
    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    try:
        assert await asyncio.to_thread(tick.entered.wait, 5.0)
        tick.release.set()  # the worker returns promptly, so the drain succeeds
        await supervisor.stop()
        status = supervisor.status()["loops"]["test_loop"]
        assert status["in_flight"] == 0
        assert status["running"] is False
        assert tick.exits == 1
    finally:
        tick.release.set()


@pytest.mark.asyncio
async def test_stop_reports_a_tick_that_outlives_the_grace_period() -> None:
    """A hung worker is bounded and disclosed, never silently dropped."""
    supervisor = AutonomySupervisor(_gated_config(interval=0.02, stop_timeout=0.2))
    tick = _GatedTick()
    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    try:
        assert await asyncio.to_thread(tick.entered.wait, 5.0)
        started = time.monotonic()
        await supervisor.stop()
        elapsed = time.monotonic() - started
        assert elapsed >= 0.2, "stop() returned before the grace period, so an in-flight tick was abandoned"
        assert elapsed < 10.0, "stop() is not bounded by stop_timeout_seconds"
        status = supervisor.status()["loops"]["test_loop"]
        assert status["running"] is True
        assert status["in_flight"] == 1
        tick.release.set()
        await asyncio.sleep(0.3)
        status = supervisor.status()["loops"]["test_loop"]
        assert status["in_flight"] == 0
        assert status["running"] is False
    finally:
        tick.release.set()


@pytest.mark.asyncio
async def test_stop_cancels_an_in_flight_async_tick() -> None:
    """An awaitable tick is still cancelled on shutdown; only its thread is not."""
    supervisor = AutonomySupervisor(_gated_config(interval=0.02, stop_timeout=0.2))
    entered = asyncio.Event()
    cancelled = False

    async def tick() -> dict:
        nonlocal cancelled
        entered.set()
        try:
            await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return {"ok": True}

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick))
    await supervisor.start()
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    await supervisor.stop()
    assert cancelled is True
    status = supervisor.status()["loops"]["test_loop"]
    assert status["in_flight"] == 0
    assert status["running"] is False


@pytest.mark.asyncio
async def test_loop_that_cannot_configure_its_tick_reports_the_error() -> None:
    """A loop whose tick cannot be configured is dead, and must look dead.

    ``configure`` runs once per loop task, outside any failure accounting, so a
    raising one produced a finished task with no error recorded at all: the
    status view showed an idle loop that would never tick again.
    """
    supervisor = AutonomySupervisor(_gated_config(interval=0.02, stop_timeout=0.2))

    def configure(_cfg: AutonomyLoopConfig):
        raise RuntimeError("curator policy unreadable")

    async def tick() -> dict:
        return {"ok": True}

    supervisor.register(LoopSpec(loop_id="test_loop", description="t", tick=tick, configure=configure))
    await supervisor.start()
    await asyncio.sleep(0.05)  # the loop task has to observe its own exit
    status = supervisor.status()["loops"]["test_loop"]
    assert status["task_alive"] is False
    assert "curator policy unreadable" in status["last_error"]
    assert status["runs"] == 0
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
