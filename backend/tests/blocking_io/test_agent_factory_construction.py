"""Regression coverage for off-loop Gateway agent construction (#5172)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from alpha.runtime.runs.manager import RunManager
from alpha.runtime.runs.worker import RunContext, run_agent
from app.gateway import services


class _Agent:
    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        yield {"messages": []}


def _bridge() -> SimpleNamespace:
    return SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())


pytestmark = pytest.mark.asyncio


async def _assert_factory_runs_off_the_event_loop(invoke) -> None:
    """The release waits for an event-loop heartbeat while assembly is blocked."""
    import time as _time

    started_at = _time.perf_counter()
    factory_started = threading.Event()
    release_factory = threading.Event()
    heartbeat_during_factory = threading.Event()
    factory_thread_ids: list[int] = []
    dispatch_at: list[float] = []
    ticker_gaps: list[float] = []
    stop_heartbeat = asyncio.Event()

    # Failure-detection bounds, not performance thresholds. Under the strict
    # Blockbuster gate the pre-dispatch assembly path (lazy imports inside
    # `run_agent`) measures 20-32s, so a 1s budget declared the test failed
    # while the event loop was provably live (max gap <= 0.21s). The waits
    # only need to outlast a run that works; a genuinely blocked loop still
    # fails because the ticker cannot set the heartbeat before the bounded
    # heartbeat wait expires.
    FACTORY_START_BUDGET = 120.0
    HEARTBEAT_BUDGET = 10.0
    RELEASE_BUDGET = 60.0

    def agent_factory(*, config):
        factory_thread_ids.append(threading.get_ident())
        dispatch_at.append(_time.perf_counter() - started_at)
        factory_started.set()
        release_factory.wait(timeout=RELEASE_BUDGET)
        return _Agent()

    def release_after_factory_starts() -> None:
        factory_started.wait(timeout=FACTORY_START_BUDGET)
        heartbeat_during_factory.wait(timeout=HEARTBEAT_BUDGET)
        release_factory.set()

    async def ticker() -> None:
        last = _time.perf_counter()
        while not stop_heartbeat.is_set():
            now = _time.perf_counter()
            ticker_gaps.append(now - last)
            last = now
            if factory_started.is_set() and not release_factory.is_set():
                heartbeat_during_factory.set()
            await asyncio.sleep(0.01)

    releaser = threading.Thread(target=release_after_factory_starts, daemon=True)
    releaser.start()
    ticker_task = asyncio.create_task(ticker())
    try:
        await invoke(agent_factory)
    finally:
        stop_heartbeat.set()
        await ticker_task
        await asyncio.to_thread(releaser.join, 1)

    timing = (f"dispatch_after={dispatch_at[0]:.3f}s" if dispatch_at else "factory never started") + (f", max_loop_gap={max(ticker_gaps):.3f}s" if ticker_gaps else ", no ticker iterations")
    assert len(factory_thread_ids) == 1, timing
    assert factory_thread_ids[0] != threading.get_ident(), timing
    assert heartbeat_during_factory.is_set(), timing


async def test_gateway_agent_factory_runs_off_the_event_loop() -> None:
    """Run execution keeps synchronous MCP/tool assembly off Gateway's loop."""
    run_manager = RunManager()
    record = await run_manager.create("thread-agent-construction")

    async def invoke(agent_factory) -> None:
        await run_agent(
            _bridge(),
            run_manager,
            record,
            ctx=RunContext(checkpointer=None),
            agent_factory=agent_factory,
            graph_input={},
            config={},
        )

    await _assert_factory_runs_off_the_event_loop(invoke)


async def test_fleet_admission_reads_control_state_off_the_event_loop() -> None:
    """Fleet admission resolves paths and reads control state off the loop.

    ``admitted()`` resolves the project root through ``os.getcwd()`` and reads
    the ``.alpha`` control-state file; both are blocking. ``run_agent`` is async
    and runs on Gateway's event loop, so admission must be offloaded to a worker
    thread instead of running inline -- otherwise every run stalls the loop on a
    disk read before any agent work starts (#5172 family).
    """
    run_manager = RunManager()
    record = await run_manager.create("thread-fleet-admission")
    import alpha.runtime.control as control

    loop_thread_id = threading.get_ident()
    observed_thread_ids: list[int] = []
    real_read_state = control.read_state

    def recording_read_state(root_dir=None):
        observed_thread_ids.append(threading.get_ident())
        return real_read_state(root_dir)

    def agent_factory(*, config):
        return _Agent()

    with patch.object(control, "read_state", recording_read_state):
        await run_agent(
            _bridge(),
            run_manager,
            record,
            ctx=RunContext(checkpointer=None),
            agent_factory=agent_factory,
            graph_input={},
            config={},
        )

    assert observed_thread_ids, "fleet admission must read control state"
    assert observed_thread_ids[0] != loop_thread_id, "fleet admission must not read control state on the event loop"


async def test_gateway_checkpoint_state_factory_runs_off_the_event_loop() -> None:
    """State/history reads must not rebuild MCP tools on Gateway's loop."""

    request = SimpleNamespace(state=SimpleNamespace(checkpoint_channel_mode="full"))
    ctx = SimpleNamespace(checkpointer=object(), store=None, checkpoint_channel_mode="full", app_config=None)

    async def invoke(agent_factory) -> None:
        with (
            patch.object(services, "get_run_context", return_value=ctx),
            patch.object(services, "resolve_agent_factory", return_value=agent_factory),
        ):
            await services.abuild_checkpoint_state_accessor(request, thread_id="thread-checkpoint-state")

    try:
        await _assert_factory_runs_off_the_event_loop(invoke)
    finally:
        services._state_accessor_graph_cache.clear()


async def test_gateway_checkpoint_state_factory_is_single_flight() -> None:
    """Concurrent cold-cache reads build one graph without occupying extra workers."""
    request = SimpleNamespace(state=SimpleNamespace(checkpoint_channel_mode="full"))
    ctx = SimpleNamespace(checkpointer=object(), store=None, checkpoint_channel_mode="full", app_config=None)
    factory_started = threading.Event()
    release_factory = threading.Event()
    factory_calls: list[int] = []

    def agent_factory(*, config):
        factory_calls.append(threading.get_ident())
        factory_started.set()
        release_factory.wait(timeout=1)
        return _Agent()

    with (
        patch.object(services, "get_run_context", return_value=ctx),
        patch.object(services, "resolve_agent_factory", return_value=agent_factory),
    ):
        try:
            first = asyncio.create_task(services.abuild_checkpoint_state_accessor(request, thread_id="thread-single-flight"))
            assert await asyncio.to_thread(factory_started.wait, 1)
            second = asyncio.create_task(services.abuild_checkpoint_state_accessor(request, thread_id="thread-single-flight"))
            await asyncio.sleep(0)
            release_factory.set()
            first_accessor, second_accessor = await asyncio.gather(first, second)
        finally:
            release_factory.set()
            services._state_accessor_graph_cache.clear()

    assert len(factory_calls) == 1
    assert first_accessor[0].graph is second_accessor[0].graph


async def test_gateway_checkpoint_state_factory_survives_waiter_cancellation() -> None:
    """Cancelling one reader cannot make a same-key reader rebuild the graph."""
    request = SimpleNamespace(state=SimpleNamespace(checkpoint_channel_mode="full"))
    ctx = SimpleNamespace(checkpointer=object(), store=None, checkpoint_channel_mode="full", app_config=None)
    factory_started = threading.Event()
    release_factory = threading.Event()
    factory_calls: list[int] = []

    def agent_factory(*, config):
        factory_calls.append(threading.get_ident())
        factory_started.set()
        release_factory.wait(timeout=1)
        return _Agent()

    with (
        patch.object(services, "get_run_context", return_value=ctx),
        patch.object(services, "resolve_agent_factory", return_value=agent_factory),
    ):
        try:
            first = asyncio.create_task(services.abuild_checkpoint_state_accessor(request, thread_id="thread-cancelled-single-flight"))
            assert await asyncio.to_thread(factory_started.wait, 1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            second = asyncio.create_task(services.abuild_checkpoint_state_accessor(request, thread_id="thread-cancelled-single-flight"))
            await asyncio.sleep(0)
            assert len(factory_calls) == 1
            release_factory.set()
            second_accessor = await second
        finally:
            release_factory.set()
            services._state_accessor_graph_cache.clear()

    assert len(factory_calls) == 1
    assert second_accessor[0].graph is not None
