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


#: How long the releaser waits for the run to *reach* agent assembly. This is a
#: gate, not the discriminator, so it must cover the whole pre-assembly phase
#: plus thread scheduling. Under the strict blocking-IO detector on a loaded box
#: that is seconds, not milliseconds -- ``threading.Thread.start()`` alone has
#: been observed to block for >10s there. A too-tight bound makes the releaser
#: give up before the factory is ever called, sets ``release_factory`` early, and
#: the heartbeat below becomes unobservable: a spurious failure that has nothing
#: to do with where assembly actually ran.
_FACTORY_START_TIMEOUT_SECONDS = 60.0

#: How long the releaser waits to observe a loop heartbeat once assembly has
#: started. This IS the discriminator: off-loop assembly leaves the loop free to
#: tick, on-loop assembly starves it.
_HEARTBEAT_TIMEOUT_SECONDS = 10.0

#: How long the factory blocks before giving up. It must outlast
#: ``_HEARTBEAT_TIMEOUT_SECONDS``: if assembly ran on the loop, this block is
#: what starves the ticker, so releasing it early would let the loop recover and
#: mask the very bug this test exists to catch.
_FACTORY_BLOCK_TIMEOUT_SECONDS = 30.0


async def _assert_factory_runs_off_the_event_loop(invoke) -> None:
    """The release waits for an event-loop heartbeat while assembly is blocked."""
    factory_started = threading.Event()
    release_factory = threading.Event()
    heartbeat_during_factory = threading.Event()
    factory_thread_ids: list[int] = []
    stop_heartbeat = asyncio.Event()

    def agent_factory(*, config):
        factory_thread_ids.append(threading.get_ident())
        factory_started.set()
        release_factory.wait(timeout=_FACTORY_BLOCK_TIMEOUT_SECONDS)
        return _Agent()

    def release_after_factory_starts() -> None:
        if not factory_started.wait(timeout=_FACTORY_START_TIMEOUT_SECONDS):
            return
        heartbeat_during_factory.wait(timeout=_HEARTBEAT_TIMEOUT_SECONDS)
        release_factory.set()

    async def ticker() -> None:
        while not stop_heartbeat.is_set():
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

    assert len(factory_thread_ids) == 1
    assert factory_thread_ids[0] != threading.get_ident()
    assert heartbeat_during_factory.is_set()


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
