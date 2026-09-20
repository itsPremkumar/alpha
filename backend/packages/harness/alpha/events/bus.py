"""In-process event bus for cross-subsystem signals.

Single bus, single owner (the AutonomySupervisor's host process). Subsystems
publish typed-name events instead of importing each other, which removes the
O(n^2) direct-wiring that caused past integration drift.

Guarantees:
* bounded per-subscriber queues with drop-oldest — a slow consumer can never
  stall a publisher or grow memory without limit;
* per-event handler isolation — one failing handler cannot affect others;
* publish is non-blocking and never raises to the caller;
* ``enabled: false`` makes the bus a no-op (events are dropped, zero overhead).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(frozen=True)
class Event:
    """One cross-subsystem signal."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    ts: float = field(default_factory=time.time)


@dataclass
class _Subscription:
    id: int
    prefix: str
    handler: Handler
    queue: asyncio.Queue[Event | None]
    task: asyncio.Task[None] | None = None
    dropped: int = 0
    failures: int = 0


class EventBus:
    """Bounded, drop-oldest, in-process pub/sub."""

    def __init__(self, *, queue_maxsize: int = 256, handler_timeout_seconds: float = 30.0, enabled: bool = True):
        self._queue_maxsize = max(1, int(queue_maxsize))
        self._handler_timeout = max(1.0, float(handler_timeout_seconds))
        self._enabled = bool(enabled)
        self._subs: dict[int, _Subscription] = {}
        self._next_id = 1
        self._published = 0
        self._dropped_total = 0

    # -- lifecycle ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, *, enabled: bool, queue_maxsize: int, handler_timeout_seconds: float) -> None:
        self._enabled = bool(enabled)
        self._queue_maxsize = max(1, int(queue_maxsize))
        self._handler_timeout = max(1.0, float(handler_timeout_seconds))

    def subscribe(self, prefix: str, handler: Handler) -> int:
        """Register an async handler for events whose name starts with ``prefix``."""
        sub = _Subscription(id=self._next_id, prefix=prefix, handler=handler, queue=asyncio.Queue(maxsize=self._queue_maxsize))
        self._next_id += 1
        self._subs[sub.id] = sub
        sub.task = asyncio.create_task(self._pump(sub), name=f"event-bus:{prefix}:{sub.id}")
        return sub.id

    def unsubscribe(self, subscription_id: int) -> None:
        sub = self._subs.pop(subscription_id, None)
        if sub is None:
            return
        try:
            sub.queue.put_nowait(None)  # poison pill stops the pump
        except asyncio.QueueFull:
            pass

    # -- publish ----------------------------------------------------------------
    async def publish(self, name: str, payload: dict[str, Any] | None = None, *, source: str = "") -> None:
        """Fan an event out to matching subscribers. Never raises, never blocks."""
        self._published += 1
        if not self._enabled or not self._subs:
            return
        event = Event(name=name, payload=dict(payload or {}), source=source)
        for sub in self._subs.values():
            if not name.startswith(sub.prefix):
                continue
            if sub.queue.full():
                # Drop-oldest: keep the freshest signal, count the loss.
                try:
                    sub.queue.get_nowait()
                    sub.dropped += 1
                    self._dropped_total += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.dropped += 1
                self._dropped_total += 1

    # -- internals ----------------------------------------------------------------
    async def _pump(self, sub: _Subscription) -> None:
        while True:
            event = await sub.queue.get()
            if event is None:
                return
            try:
                await asyncio.wait_for(sub.handler(event), timeout=self._handler_timeout)
            except TimeoutError:
                sub.failures += 1
                logger.warning("Event handler %s timed out on %s", sub.prefix, event.name)
            except asyncio.CancelledError:
                raise
            except Exception:
                sub.failures += 1
                logger.debug("Event handler %s failed on %s", sub.prefix, event.name, exc_info=True)

    # -- telemetry --------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "published": self._published,
            "dropped_total": self._dropped_total,
            "subscribers": [
                {
                    "id": sub.id,
                    "prefix": sub.prefix,
                    "queued": sub.queue.qsize(),
                    "dropped": sub.dropped,
                    "failures": sub.failures,
                }
                for sub in self._subs.values()
            ],
        }


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide bus singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def configure_event_bus(*, enabled: bool, queue_maxsize: int, handler_timeout_seconds: float) -> EventBus:
    bus = get_event_bus()
    bus.configure(enabled=enabled, queue_maxsize=queue_maxsize, handler_timeout_seconds=handler_timeout_seconds)
    return bus
