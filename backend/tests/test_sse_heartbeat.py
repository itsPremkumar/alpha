"""SSE liveness: run streams must emit heartbeats during silent phases.

Production evidence (2026-10-02): an SSE run stream delivered zero frames for
877 seconds while the backend kept working — no way for the client to tell
"a slow agent" from "a dead connection", so a proxy idle timeout or a client
stall was indistinguishable from progress. The gateway now guarantees silence
never exceeds ``interval`` seconds: a ``heartbeat`` frame is emitted whenever
the source produces nothing in time.

The wrapper must never cancel or reorder the source: ``asyncio.wait_for``
around ``anext()`` would destroy the underlying generator on its first
timeout, so the source is drained by a pump task into a queue and the outer
loop only ever waits on the queue.
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway.sse import with_heartbeats

pytestmark = pytest.mark.anyio

HB = b": hb-frame\n\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _collect(source, *, count: int, interval: float) -> list[bytes]:
    out: list[bytes] = []
    gen = with_heartbeats(source, interval=interval, heartbeat_frame=HB)
    async for item in gen:
        out.append(item)
        if len(out) >= count:
            break
    await gen.aclose()
    return out


async def test_source_items_pass_through_in_order():
    async def source():
        yield b"one"
        yield b"two"
        yield b"three"

    got: list[bytes] = []
    async for item in with_heartbeats(source(), interval=5.0, heartbeat_frame=HB):
        got.append(item)
    assert got == [b"one", b"two", b"three"]


async def test_silence_produces_heartbeats():
    async def silent_source():
        await asyncio.sleep(60)  # never yields within the test's lifetime
        yield b"never"

    got = await _collect(silent_source(), count=3, interval=0.05)
    assert got == [HB, HB, HB]


async def test_heartbeats_do_not_interrupt_a_slow_but_steady_source():
    """A source that yields within every interval must never see a heartbeat."""

    async def steady():
        for i in range(5):
            await asyncio.sleep(0.01)
            yield f"item{i}".encode()

    got: list[bytes] = []
    async for item in with_heartbeats(steady(), interval=0.5, heartbeat_frame=HB):
        got.append(item)
    assert got == [b"item0", b"item1", b"item2", b"item3", b"item4"]


async def test_the_source_is_never_cancelled_by_a_heartbeat_timeout():
    """The exact bug the pump design exists to avoid: wait_for(anext()) would
    throw CancelledError into the source generator on the first timeout."""
    cancelled = {"value": False}

    async def fragile():
        try:
            await asyncio.sleep(60)
            yield b"done"
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    gen = with_heartbeats(fragile(), interval=0.05, heartbeat_frame=HB)
    first = await gen.__anext__()
    assert first == HB
    second = await gen.__anext__()
    assert second == HB
    await gen.aclose()
    # Closing the outer stream is allowed (expected, even) to tear the source
    # down — that is cleanup, not a heartbeat-side cancellation.
    assert cancelled["value"] is True


async def test_closing_the_outer_generator_stops_the_pump():
    released = asyncio.Event()

    async def blocker():
        try:
            await released.wait()  # would block forever if the pump leaked
            yield b"unreachable"
        finally:
            released.set()

    gen = with_heartbeats(blocker(), interval=0.05, heartbeat_frame=HB)
    await gen.__anext__()
    await gen.aclose()
    # Give the cancelled pump a tick to finish; the finally must have run.
    await asyncio.sleep(0.05)
    assert released.is_set(), "the pump task must be cancelled when the stream closes"


async def test_bridge_comment_heartbeats_pass_through_but_do_not_suppress_the_named_one():
    """The stream bridge already emits ``: heartbeat`` comments: proxies see
    liveness, browser JS cannot. Those must flow through untouched yet never
    count as the client-actionable signal — the named heartbeat has to fire
    during a comment-only (zombie-run) phase."""

    async def commenting():
        for _ in range(50):  # comment every 10 ms, then silence forever
            await asyncio.sleep(0.01)
            yield ": heartbeat\n\n"
        await asyncio.sleep(60)
        yield "never"

    got = await _collect(commenting(), count=60, interval=0.1)
    comments = [i for i in got if i == ": heartbeat\n\n"]
    assert len(comments) >= 40, "bridge comments must pass through"
    assert HB in got, "the named heartbeat must fire once comment traffic stops"


async def test_source_completion_ends_the_stream_without_trailing_heartbeats():
    """Once the source ends, the wrapper must end too — promptly.

    ``interval`` is generous (500 ms) so the pump always wins the startup
    race for the first item: a leading heartbeat here would only mean the
    event loop was starved for longer than the silence budget, which is
    correct behaviour, not what this test is about. The ``wait_for`` bound
    converts a wrapper that failed to notice EOF (waiting for the next
    silence beat forever) into a failure instead of a hang.
    """

    async def quick():
        yield b"last"

    async def drain() -> list[bytes]:
        got: list[bytes] = []
        async for item in with_heartbeats(quick(), interval=0.5, heartbeat_frame=HB):
            got.append(item)
        return got

    got = await asyncio.wait_for(drain(), timeout=3.0)
    assert got == [b"last"]


class TestRouteWiring:
    """Every run-stream route must go through the heartbeat wrapper.

    Same source-assertion style as ``TestGatewayLifespanWiring``: a wrapper
    that is tested but never mounted protects nobody. The archive ZIP
    response and the paginated events list are deliberately not SSE and must
    stay untouched.
    """

    @staticmethod
    def _source(module) -> str:
        import inspect
        from pathlib import Path

        return Path(inspect.getfile(module)).read_text(encoding="utf-8")

    def test_thread_run_streams_are_wrapped(self) -> None:
        import app.gateway.routers.thread_runs as thread_runs

        source = self._source(thread_runs)
        # stream_run, join_run, and _stream_existing_run — the three
        # sse_consumer mounts.
        assert source.count("with_stream_heartbeats(") >= 3, "all three run streams must be wrapped"

    def test_stateless_run_stream_is_wrapped(self) -> None:
        import app.gateway.routers.runs as runs_router

        assert "with_stream_heartbeats(" in self._source(runs_router)

    def test_services_exposes_the_shared_heartbeat_frame(self) -> None:
        import app.gateway.services as services

        source = self._source(services)
        assert "def with_stream_heartbeats(" in source
        assert 'format_sse("heartbeat"' in source
        # The heartbeat must carry no event id: it would move the client's
        # Last-Event-ID rejoin cursor.
        assert "event_id=None" in source or "event_id=" not in source.split('format_sse("heartbeat"')[1][:80]
