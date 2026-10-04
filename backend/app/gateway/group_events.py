"""Real-time group event stream (SSE).

The stream is a *projection* of room state, not a second
write path. When a room mutates, the service publishes an
event onto an in-process bus; this module fans it out to
subscribed SSE clients. A client that misses an event
re-reads the room and converges — the room's own JSON is
the source of truth, the stream is a convenience.

Single-process by design, like every other store in this
layer: the bus is process-local and the events die with the
process. A reconnecting client re-reads the room, so a lost
event is a re-read, not a lost fact.

Heartbeats keep the connection alive through proxies that
would otherwise idle-timeout a quiet room. The heartbeat
interval is deliberately longer than the run-stream
heartbeat because a group room is quieter than a chat turn.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from starlette.responses import StreamingResponse

#: How often a no-op frame is sent to keep the connection open.
#: A quiet room must not look like a hung one to a proxy.
HEARTBEAT_INTERVAL_SECONDS = 15.0

#: How long an event is retained for a late subscriber. The
#: retention is a convenience for a client that connects while
#: events are in flight; the room read is the real catch-up.
RETAINED_EVENTS = 100


class GroupEventBus:
    """Process-local pub/sub for room events.

    Subscribers are asyncio queues. A slow consumer's queue is
    bounded so a client that stops reading cannot grow the
    process's memory without limit — when the bound is hit,
    the oldest event is dropped and the client re-reads.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._history: list[dict[str, Any]] = []

    def subscribe(self, room_id: str) -> asyncio.Queue:
        """Subscribe to a room's events, replaying recent history.

        The replay lets a freshly-connected client catch up on
        what it missed while disconnected, bounded by
        ``RETAINED_EVENTS``.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(room_id, set()).add(queue)
        for event in self._history[-RETAINED_EVENTS:]:
            if event.get("room_id") == room_id:
                queue.put_nowait(event)
        return queue

    def unsubscribe(self, room_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(room_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(room_id, None)

    def publish(self, room_id: str, event: str, data: dict[str, Any]) -> None:
        """Publish an event to every subscriber of a room.

        The publisher never blocks on a slow consumer: each
        subscriber's queue has a bounded size and drops the
        oldest event when full.
        """
        payload = {
            "event": event,
            "room_id": room_id,
            "data": data,
            "published_at": time.time(),
        }
        self._history.append(payload)
        if len(self._history) > RETAINED_EVENTS * 4:
            del self._history[: -RETAINED_EVENTS * 4]
        for queue in list(self._subscribers.get(room_id, set())):
            if queue.qsize() >= RETAINED_EVENTS:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)


_bus: GroupEventBus | None = None


def get_group_event_bus() -> GroupEventBus:
    """The process-wide group event bus."""
    global _bus
    if _bus is None:
        _bus = GroupEventBus()
    return _bus


def publish_room_event(room_id: str, event: str, data: dict[str, Any]) -> None:
    """Publish a room event onto the bus.

    Called by the service layer after a successful mutation
    so the SSE stream and the durable state agree.
    """
    get_group_event_bus().publish(room_id, event, data)


async def group_event_stream(room_id: str) -> StreamingResponse:
    """An SSE stream of one room's events.

    Yields every event the room publishes, plus a comment
    heartbeat on the interval so an idle room does not look
    like a dead connection.
    """
    bus = get_group_event_bus()
    queue = bus.subscribe(room_id)

    async def generate():
        last_heartbeat = time.monotonic()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    event = None
                if event is not None:
                    yield _format_event(event["event"], event["data"])
                    last_heartbeat = time.monotonic()
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    # A comment frame: keeps the connection alive
                    # without asserting any state about the room.
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
        finally:
            bus.unsubscribe(room_id, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_event(event: str, data: dict[str, Any]) -> str:
    """Frame an event for SSE.

    The payload is JSON-encoded once so a multi-line value
    cannot break the frame. The id is the publish timestamp,
    which is what a reconnecting ``Last-Event-ID`` carries.
    """
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
