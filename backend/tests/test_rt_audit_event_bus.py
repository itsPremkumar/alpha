"""rt-auditor: `alpha/events/bus.py` subscription teardown.

``EventBus.subscribe`` starts a long-lived ``_pump`` task per subscription and
``unsubscribe`` is the only thing that stops it, by enqueuing a poison pill.
The pill is enqueued with ``put_nowait`` and a ``QueueFull`` is *swallowed*, so
an unsubscribe that lands while a slow handler is holding a full queue drops
the only stop signal the pump can ever see: the task then drains the backlog
and parks on ``queue.get()`` forever.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.events.bus import EventBus


def _pump_tasks(bus: EventBus, subscription_id: int) -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task.get_name() == f"event-bus:autonomy.:{subscription_id}"]


@pytest.mark.asyncio
async def test_the_pump_task_probe_is_not_vacuous():
    """The leak detector must be able to SEE a running pump task.

    Without this, a name mismatch in ``_pump_tasks`` would make the leak
    assertion below pass forever.
    """
    release = asyncio.Event()

    async def slow_handler(event) -> None:
        await release.wait()

    bus = EventBus(queue_maxsize=1, handler_timeout_seconds=30.0)
    subscription = bus.subscribe("autonomy.", slow_handler)
    assert len(_pump_tasks(bus, subscription)) == 1, "the probe must find a live pump task"
    release.set()
    bus.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_unsubscribe_stops_the_pump_task_when_the_queue_is_full():
    """A full queue must not be able to leak the subscription's pump task."""
    release = asyncio.Event()
    handled: list[str] = []

    async def slow_handler(event) -> None:
        handled.append(event.name)
        # Block so the pump is busy and the queue can fill behind it.
        await release.wait()

    bus = EventBus(queue_maxsize=1, handler_timeout_seconds=30.0)
    subscription = bus.subscribe("autonomy.", slow_handler)

    # 1. The pump picks up the first event and blocks inside the handler.
    await bus.publish("autonomy.loop.completed", {"i": 0})
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # 2. One more event fills the queue while the handler is still blocked.
    await bus.publish("autonomy.loop.completed", {"i": 1})
    assert bus.status()["subscribers"][0]["queued"] == 1, "fixture must leave the queue full"

    # 3. Unsubscribe while the queue is full.
    bus.unsubscribe(subscription)
    assert bus.status()["subscribers"] == [], "unsubscribe must drop the subscription"

    # 4. Let the handler finish. The pump must drain and then terminate.
    release.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not _pump_tasks(bus, subscription):
            break

    leaked = _pump_tasks(bus, subscription)
    assert not leaked, f"unsubscribe left {len(leaked)} event-bus pump task(s) running: {[task.get_name() for task in leaked]}; the queue was full so the poison pill was dropped"
    # The in-flight event was still handled; the queued one was the oldest
    # event and made room for the stop signal, which the bus's own drop-oldest
    # accounting now reports instead of swallowing.
    assert handled == ["autonomy.loop.completed"]
    assert bus.status()["dropped_total"] >= 1, "the eviction that made room must be counted"


@pytest.mark.asyncio
async def test_unsubscribe_is_idempotent_and_stops_the_pump_on_an_idle_queue():
    """The ordinary path (empty queue) must keep working after the fix."""
    seen: list[str] = []

    async def handler(event) -> None:
        seen.append(event.name)

    bus = EventBus(queue_maxsize=4, handler_timeout_seconds=5.0)
    subscription = bus.subscribe("autonomy.", handler)
    await bus.publish("autonomy.loop.completed", {"i": 0})
    bus.unsubscribe(subscription)
    bus.unsubscribe(subscription)  # idempotent
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not _pump_tasks(bus, subscription):
            break
    assert not _pump_tasks(bus, subscription)
    assert seen == ["autonomy.loop.completed"]


@pytest.mark.asyncio
async def test_a_full_queue_does_not_swallow_the_unsubscribe_signal():
    """Positive control: the drop-oldest accounting is unrelated to teardown.

    Publishing into a full queue must still count the drop, so a future change
    cannot make the leak disappear by simply not tracking drops.
    """
    release = asyncio.Event()

    async def slow_handler(event) -> None:
        await release.wait()

    bus = EventBus(queue_maxsize=1, handler_timeout_seconds=30.0)
    subscription = bus.subscribe("autonomy.", slow_handler)
    await bus.publish("autonomy.loop.completed", {"i": 0})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    for index in range(1, 4):
        await bus.publish("autonomy.loop.completed", {"i": index})
    assert bus.status()["dropped_total"] >= 1
    bus.unsubscribe(subscription)
    release.set()
