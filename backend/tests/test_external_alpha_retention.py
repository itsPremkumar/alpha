"""Retention and SSE replay tests for the peer plane.

Two guarantees are pinned here.

**Retention is bounded but never destructive.** A cross-installation
conversation is unbounded by nature -- two Alphas can talk indefinitely -- so
history has to be pruned. What must not happen is a misconfigured window
deleting an operator's whole transcript, or the prune eating a message that was
never delivered. The floor and the `delivered`/`read`-only rule exist for that.

**A dropped event is announced.** The event queue is bounded, so a slow client
loses events. Silently losing them would render a continuous timeline the client
never received, which is the same class of dishonesty the whole tab is built to
avoid -- so the loss is published as `stream.overflow`, and a reconnect that
asks for an id the ring has already discarded is told to re-fetch rather than
being handed a silent empty replay.
"""

from __future__ import annotations

import asyncio

from alpha.peer_network.service import (
    _DEFAULT_RETENTION_DAYS,
    _EVENT_BUFFER_SIZE,
    _MIN_RETENTION_DAYS,
    PeerNetworkService,
)
from alpha.peer_network.storage import PeerNetworkStore


def _store(tmp_path) -> PeerNetworkStore:
    return PeerNetworkStore(tmp_path / "network.sqlite3")


def _add(store: PeerNetworkStore, conversation_id: str, *, created_at: str, status: str = "delivered", direction: str = "outbound") -> str:
    """Insert one message directly so retention tests control their timestamps.

    Timestamps are the whole point of the prune, and the store writes
    ``utc_now()`` itself, so these rows bypass the service deliberately.
    """

    store._conn.execute(
        """
        INSERT INTO messages (message_id, owner_id, conversation_id, sender_id, recipients_json, kind, text,
                              payload_json, status, direction, created_at)
        VALUES (?, 'installation', ?, 'alpha_local', '["alpha_remote"]', 'chat', 'body', '{}', ?, ?, ?)
        """,
        (f"msg_{conversation_id}_{created_at}_{status}_{direction}", conversation_id, status, direction, created_at),
    )
    store._conn.commit()
    return f"msg_{conversation_id}_{created_at}_{status}_{direction}"


def _conversation(store: PeerNetworkStore, conversation_id: str = "conv_1") -> None:
    store.create_conversation(conversation_id=conversation_id, mode="direct", title="peer room", participants=["alpha_local", "alpha_remote"])


# --------------------------------------------------------------------------
# Retention: the prune must not be destructive
# --------------------------------------------------------------------------


def test_prune_deletes_only_old_delivered_messages(tmp_path):
    store = _store(tmp_path)
    try:
        _conversation(store)
        _add(store, "conv_1", created_at="2020-01-01T00:00:00+00:00", status="delivered")
        _add(store, "conv_1", created_at="2030-01-01T00:00:00+00:00", status="delivered")
        removed = store.prune_messages("2026-01-01T00:00:00+00:00")
        assert removed == 1
        remaining = store._conn.execute("SELECT created_at FROM messages").fetchall()
        assert [r["created_at"] for r in remaining] == ["2030-01-01T00:00:00+00:00"]
    finally:
        store.close()


def test_prune_keeps_undelivered_messages(tmp_path):
    """A queued message is still awaiting delivery; deleting it would drop a send.

    The retry loop is what eventually resolves a `queued` row, so the prune must
    not remove history the operator still believes is in flight.
    """

    store = _store(tmp_path)
    try:
        _conversation(store)
        _add(store, "conv_1", created_at="2020-01-01T00:00:00+00:00", status="queued")
        _add(store, "conv_1", created_at="2020-01-01T00:00:00+00:00", status="failed", direction="outbound")
        assert store.prune_messages("2026-01-01T00:00:00+00:00") == 0
        assert store._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"] == 2
    finally:
        store.close()


def test_prune_keeps_read_messages_only_when_past_the_cutoff(tmp_path):
    store = _store(tmp_path)
    try:
        _conversation(store)
        _add(store, "conv_1", created_at="2020-01-01T00:00:00+00:00", status="read")
        assert store.prune_messages("2026-01-01T00:00:00+00:00") == 1
    finally:
        store.close()


def test_prune_cascades_delivery_receipts(tmp_path):
    """A receipt must never outlive the message it describes."""

    store = _store(tmp_path)
    try:
        _conversation(store)
        message_id = _add(store, "conv_1", created_at="2020-01-01T00:00:00+00:00", status="delivered")
        store._conn.execute(
            "INSERT INTO deliveries (delivery_id, message_id, recipient_id, status) VALUES (?, ?, 'alpha_remote', 'delivered')",
            (f"{message_id}:alpha_remote", message_id),
        )
        store._conn.commit()
        assert store.prune_messages("2026-01-01T00:00:00+00:00") == 1
        assert store._conn.execute("SELECT COUNT(*) AS c FROM deliveries").fetchone()["c"] == 0
    finally:
        store.close()


def test_prune_keeps_the_conversation_record(tmp_path):
    """An empty conversation still records which peers ever talked.

    Deleting conversations would erase that history instead of bounding it.
    """

    store = _store(tmp_path)
    try:
        _conversation(store)
        _add(store, "conv_1", created_at="2020-01-01T00:00:00+00:00", status="delivered")
        store.prune_messages("2026-01-01T00:00:00+00:00")
        assert store.get_conversation("conv_1") is not None
    finally:
        store.close()


def test_retention_window_has_a_floor(tmp_path, monkeypatch):
    """`RETENTION_DAYS=0` must not wipe an operator's whole transcript.

    The floor is the difference between a bounded history and data loss caused by
    a config typo, so it is asserted rather than assumed.
    """

    monkeypatch.setenv("ALPHA_PEER_NETWORK_RETENTION_DAYS", "0")
    service = PeerNetworkService(tmp_path, enabled=False)
    assert service.retention_days == _MIN_RETENTION_DAYS


def test_retention_window_defaults_and_clamps(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_PEER_NETWORK_RETENTION_DAYS", raising=False)
    assert PeerNetworkService(tmp_path, enabled=False).retention_days == _DEFAULT_RETENTION_DAYS

    monkeypatch.setenv("ALPHA_PEER_NETWORK_RETENTION_DAYS", "99999")
    assert PeerNetworkService(tmp_path, enabled=False).retention_days == 3650

    # A malformed value falls back to the default rather than raising at boot.
    monkeypatch.setenv("ALPHA_PEER_NETWORK_RETENTION_DAYS", "not-a-number")
    assert PeerNetworkService(tmp_path, enabled=False).retention_days == _DEFAULT_RETENTION_DAYS


def test_prune_history_deletes_past_the_effective_window(tmp_path):
    """End-to-end through the service, proving the wired-up call actually prunes."""

    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        store = service.store
        store.create_conversation(conversation_id="conv_1", mode="direct", title="r", participants=["alpha_local", "alpha_remote"])
        store._conn.execute(
            "INSERT INTO messages (message_id, owner_id, conversation_id, sender_id, recipients_json, kind, text, payload_json, status, direction, created_at)"
            " VALUES ('m1', 'installation', 'conv_1', 'alpha_local', '[\"alpha_remote\"]', 'chat', 'old', '{}', 'delivered', 'outbound', '2020-01-01T00:00:00+00:00')"
        )
        store._conn.commit()
        removed = asyncio.run(service.prune_history())
        assert removed == 1
        assert store._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"] == 0
    finally:
        service.store.close()


def test_status_reports_the_effective_retention_window(tmp_path, monkeypatch):
    """The UI must show the clamped window, not the configured one."""

    monkeypatch.setenv("ALPHA_PEER_NETWORK_RETENTION_DAYS", "1")
    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        snapshot = service._status_snapshot()
        assert snapshot["retention"]["days"] == _MIN_RETENTION_DAYS
        assert snapshot["retention"]["keeps_undelivered_history"] is True
    finally:
        service.store.close()


# --------------------------------------------------------------------------
# SSE: a dropped event must be announced, not silently swallowed
# --------------------------------------------------------------------------


def test_published_events_carry_a_monotonic_replay_id(tmp_path):
    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        service._publish("message.outbound", {"message_id": "m1"})
        service._publish("message.outbound", {"message_id": "m2"})
        seqs = [service._event_buffer[0].get("seq"), service._event_buffer[1].get("seq")]
        assert seqs == [1, 2]
    finally:
        service.store.close()


def test_replay_since_returns_events_after_the_cursor(tmp_path):
    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        for index in range(3):
            service._publish("message.outbound", {"message_id": f"m{index}"})
        window = service.replay_since(1)
        assert [e["data"]["message_id"] for e in window.events] == ["m1", "m2"]
        assert window.gap is False
        assert window.earliest_retained == 1
    finally:
        service.store.close()


def test_replay_since_none_starts_from_now(tmp_path):
    """A client with no cursor must not be handed the whole retained buffer."""

    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        service._publish("message.outbound", {"message_id": "m1"})
        window = service.replay_since(None)
        assert window.events == []
        assert window.gap is False
    finally:
        service.store.close()


def test_replay_reports_the_gap_when_the_ring_has_moved_past_the_cursor(tmp_path):
    """A cursor older than the retained ring must surface, not vanish.

    The caller turns this into `stream.reset` so the client re-fetches the
    authoritative REST snapshot. Note that a *partial* replay is still returned
    here -- events after the eviction point are genuinely available -- so the
    `gap` flag is the only thing distinguishing that from real continuity.
    """

    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        for index in range(_EVENT_BUFFER_SIZE + 25):
            service._publish("message.outbound", {"message_id": f"m{index}"})
        window = service.replay_since(1)
        assert window.gap is True
        assert window.earliest_retained is not None and window.earliest_retained > 1
        assert window.requested == 1
    finally:
        service.store.close()


def test_a_cursor_exactly_at_the_oldest_retained_event_is_not_a_gap(tmp_path):
    """The boundary is inclusive: the client already has that event."""

    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        for index in range(3):
            service._publish("message.outbound", {"message_id": f"m{index}"})
        assert service.replay_since(1).gap is False
    finally:
        service.store.close()


def test_the_replay_ring_is_bounded(tmp_path):
    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        for index in range(_EVENT_BUFFER_SIZE + 100):
            service._publish("message.outbound", {"message_id": f"m{index}"})
        assert len(service._event_buffer) <= _EVENT_BUFFER_SIZE
    finally:
        service.store.close()


def test_a_slow_subscriber_is_told_it_missed_events(tmp_path):
    """The overflow event is the difference between a gap and a silent loss."""

    service = PeerNetworkService(tmp_path, enabled=False)

    async def _exercise() -> None:
        # Fill one subscriber's bounded queue so the next publish must evict.
        queue = service.subscribe()
        for index in range(300):
            service._publish("message.outbound", {"message_id": f"m{index}"})
        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())
        assert any(event.get("type") == "stream.overflow" for event in drained)
        overflow = next(event for event in drained if event.get("type") == "stream.overflow")
        assert overflow["data"]["dropped_subscribers"] >= 1
        service.unsubscribe(queue)

    try:
        asyncio.run(_exercise())
    finally:
        service.store.close()


def test_unsubscribe_stops_delivery(tmp_path):
    service = PeerNetworkService(tmp_path, enabled=False)
    try:
        queue = service.subscribe()
        service.unsubscribe(queue)
        service._publish("message.outbound", {"message_id": "m1"})
        assert queue.empty()
    finally:
        service.store.close()
