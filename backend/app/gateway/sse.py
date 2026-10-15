"""SSE liveness wrapper: bound how long a run stream may stay silent.

See ``tests/test_sse_heartbeat.py`` for the incident this encodes (an SSE
stream that delivered zero frames for 877 seconds while the backend kept
working — a slow agent and a dead connection became indistinguishable).

Why a pump task and a queue rather than ``asyncio.wait_for(source.__anext__())``
on each turn: ``wait_for`` *cancels* its awaitable on timeout, and awaiting
``__anext__`` on an async generator propagates that cancellation **into the
generator**, closing it on the first heartbeat. The pump drains the source at
the source's own pace into a bounded queue. If a client is slow, backpressure
reaches the stream bridge; its existing retention and replay-gap behavior then
decides whether the subscriber can catch up. The outer loop only waits on
``queue.get()``, which is safe to abandon. Closing the outer generator cancels
the pump, which tears the source down through its normal ``CancelledError``
path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

__all__ = ["with_heartbeats"]

#: Marks source exhaustion inside the queue. A private object, not a sentinel
#: bytes value, so a legitimate source item can never be mistaken for it.
_EOF = object()

# A slow HTTP client must not let one run's SSE frames accumulate without
# bound. When this queue fills, the pump naturally backpressures the source;
# run-stream sources in turn rely on StreamBridge's bounded replay retention.
_HEARTBEAT_QUEUE_MAXSIZE = 32


async def with_heartbeats(
    source: AsyncIterator[str | bytes],
    *,
    interval: float,
    heartbeat_frame: str | bytes,
) -> AsyncIterator[str | bytes]:
    """Yield ``source`` items in order, substituting ``heartbeat_frame`` whenever
    no *real* event arrives within ``interval`` seconds.

    Semantics:

    * SSE comment frames (lines starting ``:``) are **passed through but do
      not reset the silence timer**: the stream bridge already emits
      ``: heartbeat`` comments, and they prove liveness to proxies while
      remaining invisible to browser JS. The named heartbeat here is what a
      client can actually observe and act on, so it fires on the absence of
      real events — bounded by roughly ``interval`` plus one comment period.
    * Source completion ends the stream immediately (no trailing heartbeats).
    * Closing the returned generator cancels the pump, which tears the source
      down through its normal cancellation path, but a heartbeat timeout
      never touches the source.
    """
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=_HEARTBEAT_QUEUE_MAXSIZE)

    async def _pump() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except asyncio.CancelledError:
            # Do not enqueue EOF during cancellation: a full bounded queue
            # would make cleanup wait forever for a consumer that has closed.
            close_source = getattr(source, "aclose", None)
            if close_source is not None:
                await close_source()
            raise
        except Exception:
            # Match source completion semantics: a failed source still ends
            # the client stream instead of leaving it emitting heartbeats.
            await queue.put(_EOF)
            raise
        else:
            await queue.put(_EOF)

    pump = asyncio.create_task(_pump(), name="sse-heartbeat-pump")
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield heartbeat_frame
                continue
            if item is _EOF:
                return
            if _is_sse_comment(item):
                # Liveness for proxies, not signal for JS: forward it, keep
                # the client-facing silence clock running.
                yield item  # type: ignore[misc]
                continue
            yield item  # type: ignore[misc]
    finally:
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - source cleanup must not mask stream errors
            pass


def _is_sse_comment(item: object) -> bool:
    """An SSE comment: a frame line whose first byte is ``:`` (spec: ignored
    by client parsers, used for keep-alive)."""
    if isinstance(item, (bytes, bytearray)):
        return item[:1] == b":"
    if isinstance(item, str):
        return item[:1] == ":"
    return False
