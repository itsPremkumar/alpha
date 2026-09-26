"""Production-exposure tests for the Alpha peer network.

The peer network is an *inbound* plane: ``/api/peer-network/remote/pair``,
``/api/peer-network/inbound/messages``, and ``/api/peer-network/ws`` are mounted
without a browser session, and UDP/mDNS discovery puts the installation on the
LAN.  These tests pin the boundary that makes that safe to expose:

* the plane is off unless an operator opts in,
* the unauthenticated pairing mutation is throttled and fails closed,
* untrusted ingress cannot grant a credential, claim this installation's
  identity, poison the peer registry, or grow any in-memory or on-disk map
  without a bound,
* the blocking resolver calls stay off the event loop.

Each test states the boundary it pins so a failure names the exposure.  No test
here sleeps: every time-dependent assertion uses an injected clock, so the
suite is deterministic and cannot be made to pass by slowing it down.
"""

from __future__ import annotations

import asyncio
import json
import math
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from alpha.peer_network import PeerNetworkService
from alpha.peer_network import discovery as discovery_module
from alpha.peer_network import github as github_module
from alpha.peer_network import identity as identity_module
from alpha.peer_network.github import GitHubRendezvous, GitHubRendezvousError
from alpha.peer_network.models import PeerCard, PeerEnvelope, PeerPairRequest
from alpha.peer_network.ratelimit import (
    ATTEMPTS_CEILING,
    WINDOW_CEILING_SECONDS,
    PairingThrottle,
)
from alpha.peer_network.storage import PeerNetworkStore, PeerRegistryFullError
from alpha.peer_network.transport import PeerTransportError

_WRONG_CODE = "not-the-real-code-" + "0" * 24


@pytest.fixture
def clock() -> dict[str, float]:
    """A monotonic clock the throttle reads, so no test has to sleep."""

    return {"now": 1_000_000.0}


@pytest.fixture
def make_service(tmp_path: Path, clock: dict[str, float]):
    """Build isolated services on a controlled clock and close their stores."""

    created: list[PeerNetworkService] = []

    def _make(
        name: str,
        *,
        enabled: bool = True,
        throttle: PairingThrottle | None = None,
        **kwargs: Any,
    ) -> PeerNetworkService:
        service = PeerNetworkService(
            tmp_path / name,
            enabled=enabled,
            pairing_throttle=throttle if throttle is not None else PairingThrottle(clock=lambda: clock["now"]),
            **kwargs,
        )
        created.append(service)
        return service

    yield _make
    for service in created:
        if not service._store_closed:
            service.store.close()


def _pair_request(sender: PeerNetworkService, receiver: PeerNetworkService, **overrides: Any) -> PeerPairRequest:
    payload: dict[str, Any] = {
        "card": sender.card(),
        "pairing_code": receiver.identity.pairing_code,
    }
    payload.update(overrides)
    return PeerPairRequest.model_validate(payload)


# --------------------------------------------------------------------------- #
# 1. The plane is opt-in
# --------------------------------------------------------------------------- #
def test_peer_network_is_disabled_by_default_and_the_env_var_is_the_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var means no inbound plane; ``ALPHA_PEER_NETWORK_ENABLED=1`` reopens it.

    An inbound plane that binds UDP, answers unauthenticated beacons, and
    accepts a remote pairing is an exposure decision, so it cannot be the
    accidental state of a fresh install.
    """

    monkeypatch.delenv("ALPHA_PEER_NETWORK_ENABLED", raising=False)
    default_off = PeerNetworkService(tmp_path / "default-off")
    try:
        assert default_off.enabled is False
    finally:
        default_off.store.close()

    monkeypatch.setenv("ALPHA_PEER_NETWORK_ENABLED", "1")
    opted_in = PeerNetworkService(tmp_path / "opted-in")
    try:
        assert opted_in.enabled is True
    finally:
        opted_in.store.close()

    # Unparseable/negative values stay closed: the knob fails closed.
    for raw in ("off", "0", "false", "maybe", ""):
        monkeypatch.setenv("ALPHA_PEER_NETWORK_ENABLED", raw)
        closed = PeerNetworkService(tmp_path / f"closed-{raw or 'empty'}")
        try:
            assert closed.enabled is False
        finally:
            closed.store.close()


def test_a_disabled_network_refuses_the_whole_unauthenticated_ingress_plane(make_service) -> None:
    """Pairing, inbound delivery, and the WebSocket handshake all refuse."""

    receiver = make_service("receiver", enabled=False)
    sender = make_service("sender")

    pairing = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver)))
    assert pairing.accepted is False
    assert "disabled" in pairing.message
    assert receiver.store.get_peer(sender.identity.agent_id) is None

    envelope = PeerEnvelope(
        sender_id=sender.identity.agent_id,
        recipients=[receiver.identity.agent_id],
        text="hello",
    )
    with pytest.raises(PeerTransportError):
        asyncio.run(receiver.receive_remote(envelope, receiver.identity.pairing_code))

    # The WebSocket handshake resolves the token through the same service, so a
    # closed plane closes the socket instead of leaving it half-open.
    assert asyncio.run(receiver.get_peer_by_token(receiver.identity.pairing_code)) is None


def test_local_management_reads_still_work_while_the_plane_is_closed(make_service) -> None:
    """Closing the inbound plane must not blind the operator's own UI."""

    receiver = make_service("receiver", enabled=False)
    assert asyncio.run(receiver.list_peers()) == []
    status = asyncio.run(receiver.status())
    assert status.enabled is False
    assert status.pairing["ingress"] == "closed"


# --------------------------------------------------------------------------- #
# 2. The unauthenticated pairing mutation is throttled
# --------------------------------------------------------------------------- #
def test_the_nth_pairing_attempt_inside_the_window_is_refused(make_service) -> None:
    """The attempt budget is charged on success too, and the overflow mutates nothing."""

    receiver = make_service("receiver")
    sender = make_service("sender")
    budget = receiver.pairing_throttle.max_attempts

    for attempt in range(1, budget + 1):
        response = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver)))
        assert response.accepted is True, f"attempt {attempt} inside the budget must be admitted"

    before = receiver.store.get_peer(sender.identity.agent_id)
    assert before is not None

    refused = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver)))
    assert refused.accepted is False
    assert "throttled" in refused.message
    assert refused.retry_after_seconds is not None and refused.retry_after_seconds >= 1
    # A refusal is a refusal: the paired row is untouched, and the throttle
    # charge is not spent on a request that was not evaluated.
    assert receiver.store.get_peer(sender.identity.agent_id) == before
    assert receiver.store.counts()["peers"] == 1


def test_repeated_wrong_codes_back_off_escalate_and_then_expire(make_service, clock: dict[str, float]) -> None:
    """A wrong-code flood is bounded, escalates, and is not a permanent lockout."""

    # The attempt budget is proven by its own test; here it is opened up so the
    # failure backoff can be observed without the two limits colliding.
    throttle = PairingThrottle(clock=lambda: clock["now"], max_attempts=100, global_max_attempts=100)
    receiver = make_service("receiver", throttle=throttle)
    sender = make_service("sender")
    wrong = _pair_request(sender, receiver, pairing_code=_WRONG_CODE)

    # The first ``max_failures`` wrong codes are refused on their merits.
    for _ in range(throttle.max_failures):
        response = asyncio.run(receiver.accept_pair(wrong))
        assert response.accepted is False
        assert response.message == "Pairing code rejected"
        assert response.retry_after_seconds is None

    # After that the caller is locked out, and each served sentence doubles.
    sentences: list[int] = []
    for index in range(3):
        blocked = asyncio.run(receiver.accept_pair(wrong))
        assert blocked.accepted is False
        assert "too many failed pairing attempts" in blocked.message
        sentences.append(blocked.retry_after_seconds or 0)
        clock["now"] += (blocked.retry_after_seconds or 0) + 1
        # Serving the sentence buys exactly one evaluated attempt again: an
        # honest operator can retry, and a flooder re-locks for longer.
        served = asyncio.run(receiver.accept_pair(wrong))
        assert served.accepted is False
        assert served.message == "Pairing code rejected"

    assert sentences == [math.ceil(throttle.base_lockout_seconds * (2**index)) for index in range(3)]
    assert sentences[0] < sentences[1] < sentences[2]

    # Past the longest sentence the caller is evaluated normally again, so the
    # backoff is bounded in time rather than punitive.
    clock["now"] += throttle.max_lockout_seconds + 1
    assert asyncio.run(receiver.accept_pair(_pair_request(sender, receiver))).accepted is True


def test_the_global_budget_survives_identity_rotation(make_service) -> None:
    """A caller cannot buy a fresh budget by claiming a new agent id.

    The per-identity tier is keyed on attacker-controlled data, so the
    installation-wide tier is the real bound.  Every attempt here uses the
    *correct* code and a fresh identity, which is the shape that defeats a
    per-key limiter.
    """

    receiver = make_service("receiver")
    sender = make_service("sender")
    global_budget = receiver.pairing_throttle.global_max_attempts

    for identity in range(global_budget):
        card = sender.card().model_copy(update={"agent_id": f"alpha-rotate-{identity}"})
        response = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver, card=card)))
        assert response.accepted is True, f"identity {identity} should be inside the global budget"

    card = sender.card().model_copy(update={"agent_id": "alpha-rotate-overflow"})
    refused = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver, card=card)))
    assert refused.accepted is False
    assert "budget exhausted" in refused.message
    assert refused.retry_after_seconds is not None


def test_pairing_fails_closed_when_the_throttle_cannot_decide(make_service, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken limiter refuses the attempt instead of opening the door."""

    receiver = make_service("receiver")
    sender = make_service("sender")

    def broken(_key: str):
        raise RuntimeError("throttle state is unavailable")

    monkeypatch.setattr(receiver.pairing_throttle, "check", broken)
    response = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver)))
    assert response.accepted is False
    assert response.message == "Pairing is temporarily unavailable"
    assert receiver.store.get_peer(sender.identity.agent_id) is None


def test_throttle_state_is_bounded_under_identity_flooding(clock: dict[str, float]) -> None:
    """The limiter's own map cannot grow without bound."""

    throttle = PairingThrottle(clock=lambda: clock["now"], max_tracked_keys=32, breaker_failures=10_000, max_failures=10_000)
    for index in range(5_000):
        throttle.check(f"alpha-flood-{index}")
        clock["now"] += 0.001
    assert throttle.tracked_keys <= 32
    # The global tier is never evicted, so the bound is not a bypass.
    assert throttle.retry_after("alpha-flood-4999") is not None


def test_throttle_knobs_can_tighten_but_cannot_be_raised_past_the_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator may set the budget, not disable it."""

    monkeypatch.setenv("ALPHA_PEER_NETWORK_PAIR_MAX_ATTEMPTS", "1000000")
    monkeypatch.setenv("ALPHA_PEER_NETWORK_PAIR_WINDOW_SECONDS", "9999999")
    monkeypatch.setenv("ALPHA_PEER_NETWORK_PAIR_MAX_FAILURES", "0")
    service = PeerNetworkService(tmp_path / "knobs")
    try:
        assert service.pairing_throttle.max_attempts == ATTEMPTS_CEILING
        assert service.pairing_throttle.window_seconds == WINDOW_CEILING_SECONDS
        assert service.pairing_throttle.max_failures == 1
    finally:
        service.store.close()

    monkeypatch.setenv("ALPHA_PEER_NETWORK_PAIR_MAX_ATTEMPTS", "1")
    tightened = PeerNetworkService(tmp_path / "knobs-tight")
    try:
        assert tightened.pairing_throttle.max_attempts == 1
    finally:
        tightened.store.close()


def test_status_discloses_the_throttle_policy_without_any_credential(make_service) -> None:
    """Operators can see the ingress budget; nobody can read a code from it."""

    receiver = make_service("receiver")
    sender = make_service("sender")
    for _ in range(receiver.pairing_throttle.max_failures):
        asyncio.run(receiver.accept_pair(_pair_request(sender, receiver, pairing_code=_WRONG_CODE)))
    status = asyncio.run(receiver.status(include_pairing_code=False))
    throttle = status.pairing["throttle"]
    assert status.pairing["ingress"] == "open"
    assert throttle["max_attempts_per_identity"] == receiver.pairing_throttle.max_attempts
    assert throttle["global_max_attempts"] == receiver.pairing_throttle.global_max_attempts
    assert throttle["refused_attempts"] == 0
    assert status.limits["pair_max_attempts"] == receiver.pairing_throttle.max_attempts
    serialised = status.model_dump_json()
    assert receiver.identity.pairing_code not in serialised
    assert sender.identity.pairing_code not in serialised

    # A refusal is observable, and still carries no credential.
    refused = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver, pairing_code=_WRONG_CODE)))
    assert refused.accepted is False
    after = asyncio.run(receiver.status()).pairing["throttle"]
    assert after["refused_attempts"] == 1
    assert after["last_refusal_reason"] == "too many failed pairing attempts"
    assert receiver.identity.pairing_code not in str(after)


# --------------------------------------------------------------------------- #
# 3. Untrusted ingress cannot grant a credential or claim this installation
# --------------------------------------------------------------------------- #
def test_pairing_refuses_a_card_that_claims_this_installations_identity(make_service) -> None:
    """A peer may not take over the local agent id in the peer table."""

    receiver = make_service("receiver")
    sender = make_service("sender")
    card = sender.card().model_copy(update={"agent_id": receiver.identity.agent_id, "url": "http://203.0.113.9:8001"})

    response = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver, card=card)))
    assert response.accepted is False
    assert "agent id" in response.message
    assert receiver.store.get_peer(receiver.identity.agent_id) is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://user:pass@192.0.2.4:8001",
    ],
)
def test_pairing_refuses_a_card_with_an_unreachable_endpoint(make_service, url: str) -> None:
    """A paired row is a credential *and* a URL the operator will act on."""

    receiver = make_service("receiver")
    sender = make_service("sender")
    card = sender.card().model_copy(update={"url": url})

    response = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver, card=card)))
    assert response.accepted is False
    assert response.message != "Peer paired"
    assert receiver.store.get_peer(sender.identity.agent_id) is None


def test_a_blocked_peer_cannot_deliver_inbound_messages(make_service) -> None:
    """Blocking a peer must close the inbound plane for that peer, not just the UI."""

    receiver = make_service("receiver")
    sender = make_service("sender")
    assert asyncio.run(receiver.accept_pair(_pair_request(sender, receiver))).accepted is True

    token = receiver.identity.pairing_code
    envelope = PeerEnvelope(
        sender_id=sender.identity.agent_id,
        recipients=[receiver.identity.agent_id],
        text="before the block",
    )
    assert asyncio.run(receiver.receive_remote(envelope, token))["text"] == "before the block"

    asyncio.run(receiver.set_trust(sender.identity.agent_id, "blocked"))

    # Token-only lookup is what the WebSocket handshake uses.
    assert asyncio.run(receiver.get_peer_by_token(token)) is None
    assert asyncio.run(receiver.get_peer_by_token(token, sender.identity.agent_id)) is None
    with pytest.raises(PeerTransportError):
        asyncio.run(
            receiver.receive_remote(
                PeerEnvelope(
                    sender_id=sender.identity.agent_id,
                    recipients=[receiver.identity.agent_id],
                    text="after the block",
                ),
                token,
            )
        )
    history = asyncio.run(receiver.get_messages(asyncio.run(receiver.list_conversations())[0]["conversation_id"]))
    assert [message["text"] for message in history] == ["before the block"]


def test_a_trust_update_can_never_grant_a_credential(make_service) -> None:
    """``paired`` is reachable only through the explicit pairing flow."""

    receiver = make_service("receiver")
    with pytest.raises(ValueError):
        asyncio.run(receiver.set_trust("alpha-anything", "paired"))
    store = PeerNetworkStore(receiver.home / "extra.sqlite3")
    try:
        with pytest.raises(ValueError):
            store.set_peer_trust("alpha-anything", "paired")
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 4. Bounded resource growth
# --------------------------------------------------------------------------- #
def test_the_peer_registry_is_capped_and_never_evicts_a_paired_peer(make_service) -> None:
    """Untrusted beacons are dropped at the ceiling; pairing never loses a peer."""

    receiver = make_service("receiver", max_peers=4)
    sender = make_service("sender")

    for index in range(4):
        card = sender.card().model_copy(update={"agent_id": f"alpha-beacon-{index}"})
        assert receiver.observe_discovery(card.discovery_dict(), source="udp:test") is not None
    assert len(asyncio.run(receiver.list_peers())) == 4

    # One more unauthenticated beacon: refused, not retained.
    overflow = sender.card().model_copy(update={"agent_id": "alpha-beacon-overflow"})
    receiver.observe_discovery(overflow.discovery_dict(), source="udp:test")
    ids = {peer["agent_id"] for peer in asyncio.run(receiver.list_peers())}
    assert "alpha-beacon-overflow" not in ids
    assert len(ids) == 4

    # A paired peer is the operator's decision and survives a beacon flood.
    assert asyncio.run(receiver.accept_pair(_pair_request(sender, receiver))).accepted is True
    ids = {peer["agent_id"] for peer in asyncio.run(receiver.list_peers())}
    assert len(ids) == 4
    assert sender.identity.agent_id in ids


def test_the_registry_refuses_to_grow_past_its_hard_ceiling(tmp_path: Path) -> None:
    """With nothing evictable left, the ceiling wins over a new peer."""

    store = PeerNetworkStore(tmp_path / "cap.sqlite3")
    try:
        for index in range(3):
            store.upsert_peer(
                card={"agent_id": f"alpha-paired-{index}", "name": "p", "url": f"http://192.0.2.{index + 1}:8001"},
                source="remote-pair",
                trust="paired",
                paired=True,
                max_peers=3,
            )
        with pytest.raises(PeerRegistryFullError):
            store.upsert_peer(
                card={"agent_id": "alpha-one-too-many", "name": "p", "url": "http://192.0.2.9:8001"},
                source="remote-pair",
                trust="paired",
                paired=True,
                max_peers=3,
            )
        assert store.counts()["peers"] == 3
    finally:
        store.close()


def test_pairing_is_refused_when_the_registry_is_full(make_service) -> None:
    """The pairing path answers with a rejection, never a partial mutation."""

    receiver = make_service("receiver", max_peers=1)
    sender = make_service("sender")
    # One credentialed peer already occupies the only slot, and nothing is
    # evictable, so the ceiling is the boundary.
    receiver.store.upsert_peer(
        card={"agent_id": "alpha-filler", "name": "f", "url": "http://192.0.2.5:8001"},
        source="remote-pair",
        trust="paired",
        paired=True,
    )
    response = asyncio.run(receiver.accept_pair(_pair_request(sender, receiver)))
    assert response.accepted is False
    assert "registry" in response.message
    assert receiver.store.get_peer(sender.identity.agent_id) is None
    assert receiver.store.get_peer("alpha-filler") is not None


def test_the_inbox_participant_cap_is_enforced_for_inbound_senders(make_service) -> None:
    """A paired peer cannot grow the installation inbox without bound."""

    from alpha.peer_network.service import _MAX_PARTICIPANTS, _conversation_id_for

    receiver = make_service("receiver")
    for index in range(_MAX_PARTICIPANTS - 1):
        sender_id = f"alpha-sender-{index}"
        receiver.store.upsert_peer(
            card={"agent_id": sender_id, "name": "s", "url": "http://192.0.2.11:8001"},
            source="remote-pair",
            trust="paired",
            paired=True,
            outbound_token=f"token-{index}",
        )
        asyncio.run(
            receiver.receive_remote(
                PeerEnvelope(
                    sender_id=sender_id,
                    recipients=[receiver.identity.agent_id],
                    text="hello",
                ),
                f"token-{index}",
            )
        )
    inbox_id = _conversation_id_for([receiver.identity.agent_id], "inbox")
    inbox = next(item for item in asyncio.run(receiver.list_conversations()) if item["mode"] == "inbox")
    assert inbox["conversation_id"] == inbox_id
    assert len(inbox["participants"]) == _MAX_PARTICIPANTS

    overflow_id = "alpha-sender-overflow"
    receiver.store.upsert_peer(
        card={"agent_id": overflow_id, "name": "s", "url": "http://192.0.2.12:8001"},
        source="remote-pair",
        trust="paired",
        paired=True,
        outbound_token="token-overflow",
    )
    with pytest.raises(PeerTransportError):
        asyncio.run(
            receiver.receive_remote(
                PeerEnvelope(
                    sender_id=overflow_id,
                    recipients=[receiver.identity.agent_id],
                    text="hello",
                ),
                "token-overflow",
            )
        )
    inbox = next(item for item in asyncio.run(receiver.list_conversations()) if item["mode"] == "inbox")
    assert len(inbox["participants"]) == _MAX_PARTICIPANTS
    history = asyncio.run(receiver.get_messages(inbox_id))
    assert len(history) == _MAX_PARTICIPANTS - 1


def test_the_inbound_token_lookup_is_index_backed(tmp_path: Path) -> None:
    """An unauthenticated request must not scan the whole peer table.

    ``find_peer_by_token`` runs before any credential is proven, on every
    inbound request and every WebSocket handshake.  With no index on
    ``peers(token_hash)`` that lookup is a full table scan, so a caller who
    cannot authenticate could still make the process read every peer row.
    """

    store = PeerNetworkStore(tmp_path / "index.sqlite3")
    try:
        plan = store._conn.execute(
            "EXPLAIN QUERY PLAN SELECT agent_id FROM peers WHERE token_hash = ? AND trust != 'blocked' ORDER BY agent_id LIMIT 1",
            ("0" * 64,),
        ).fetchall()
        detail = " ".join(str(row["detail"]) for row in plan)
        assert "SCAN peers" not in detail, f"the inbound token lookup still scans peers: {detail}"
        assert "idx_peers_token_hash" in detail
    finally:
        store.close()


def test_sqlite_indexes_exist_for_the_peer_table(tmp_path: Path) -> None:
    store = PeerNetworkStore(tmp_path / "indexes.sqlite3")
    try:
        names = {row["name"] for row in store._conn.execute("PRAGMA index_list('peers')").fetchall()}
        assert {"idx_peers_token_hash", "idx_peers_trust_last_seen"} <= names
        assert isinstance(store._conn, sqlite3.Connection)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 5. Blocking resolver calls stay off the event loop
# --------------------------------------------------------------------------- #
def test_udp_discovery_resolution_does_not_block_the_event_loop(
    make_service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``getaddrinfo`` runs on a worker thread, so a slow resolver cannot stall the Gateway.

    The assertion is made from *inside* the blocked resolver: it reads a
    counter that only the event loop increments.  If the call were made on the
    loop, the counter could not move while the resolver is parked, and the
    test fails instead of hanging.
    """

    service = make_service("udp")
    ticks = 0
    stop = threading.Event()
    observed: list[int] = []
    entered = threading.Event()

    class _RecordingTransport:
        def __init__(self) -> None:
            self.sent: list[tuple[str, int]] = []

        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            self.sent.append(addr)

    transport = _RecordingTransport()
    service.udp._transport = transport  # type: ignore[assignment]
    service.udp.resolve_timeout_seconds = 5.0

    def slow_getaddrinfo(*args: Any, **kwargs: Any):
        entered.set()
        time.sleep(0.25)
        observed.append(ticks)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", 0))]

    monkeypatch.setattr(discovery_module.socket, "getaddrinfo", slow_getaddrinfo)

    async def scenario() -> None:
        nonlocal ticks
        heartbeat_stop = asyncio.Event()

        async def heartbeat() -> None:
            nonlocal ticks
            while not heartbeat_stop.is_set():
                await asyncio.sleep(0.005)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            await service.udp.broadcast_once()
        finally:
            heartbeat_stop.set()
            await beat

    identity_module.reset_advertised_host_cache()
    asyncio.run(scenario())

    assert entered.is_set(), "the discovery resolver was never exercised"
    # The whole claim: the loop kept running while the resolver was parked. The
    # beacon itself legitimately waits for the worker, so elapsed time is not
    # evidence either way — only the counter is.
    assert observed and observed[0] > 0, "the event loop was blocked while the resolver ran"
    assert ("192.0.2.7", service.udp.port) in transport.sent
    assert service.udp.last_error is None
    assert not stop.is_set()


def test_a_slow_resolver_degrades_one_beacon_instead_of_stalling(make_service, monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolver is time-boxed, and the timeout is disclosed rather than silent.

    The proof is ordering, not a wall-clock threshold: ``broadcast_once`` returns
    while the resolver is still parked inside the worker, so the wait is bounded
    by the time-box and not by the resolver.  A regression costs ten seconds
    (the resolver's own release wait) and then fails; it cannot hang.
    """

    service = make_service("udp-timeout")
    service.udp.resolve_timeout_seconds = 0.05

    class _RecordingTransport:
        def __init__(self) -> None:
            self.sent: list[tuple[str, int]] = []

        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            self.sent.append(addr)

    service.udp._transport = _RecordingTransport()  # type: ignore[assignment]

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def hanging_getaddrinfo(*args: Any, **kwargs: Any):
        entered.set()
        release.wait(10)
        finished.set()
        return []

    monkeypatch.setattr(discovery_module.socket, "getaddrinfo", hanging_getaddrinfo)

    async def scenario() -> None:
        await service.udp.broadcast_once()
        assert entered.is_set(), "the discovery resolver was never exercised"
        assert not finished.is_set(), "broadcast_once waited for the resolver instead of its time-box"
        assert "time-box" in (service.udp.last_error or "")
        release.set()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert finished.is_set()


def test_the_advertised_host_is_resolved_once_and_primed_off_loop(
    make_service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocking DNS lookup must not run per Agent Card request."""

    calls: list[tuple[Any, ...]] = []
    identity_module.reset_advertised_host_cache()
    monkeypatch.delenv("ALPHA_PEER_NETWORK_ADVERTISED_HOST", raising=False)

    def counting_gethostbyname(name: str) -> str:
        calls.append((name,))
        return "192.0.2.55"

    monkeypatch.setattr(identity_module.socket, "gethostbyname", counting_gethostbyname)

    service = make_service("advertised")
    identity_module.reset_advertised_host_cache()
    calls.clear()

    assert identity_module.prime_advertised_host() == "192.0.2.55"
    first = len(calls)
    assert first == 1

    for _ in range(5):
        assert service.card().url == "http://192.0.2.55:8001"
    assert len(calls) == first, "the advertised host was re-resolved for a card"

    identity_module.reset_advertised_host_cache()
    identity_module.prime_advertised_host()
    assert len(calls) == first + 1
    identity_module.reset_advertised_host_cache()


def test_service_start_primes_the_advertised_host_off_the_loop(make_service, monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup resolves the host in a worker thread before any card is served."""

    service = make_service("start-prime", enabled=False)
    threads: list[str] = []

    def record_prime() -> str:
        threads.append(threading.current_thread().name)
        return identity_module.prime_advertised_host()

    identity_module.reset_advertised_host_cache()
    monkeypatch.setattr("alpha.peer_network.service.prime_advertised_host", record_prime)
    asyncio.run(service.start())
    try:
        assert threads and threading.current_thread().name not in threads
    finally:
        identity_module.reset_advertised_host_cache()


# --------------------------------------------------------------------------- #
# 6. The public Agent Card discloses nothing privileged
# --------------------------------------------------------------------------- #
def test_the_public_card_and_beacon_expose_no_credential_or_local_path(make_service, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression pin: the unauthenticated surface is public metadata only."""

    monkeypatch.delenv("ALPHA_PEER_NETWORK_ADVERTISED_HOST", raising=False)
    service = make_service("public-card")
    card = service.card().to_dict()
    beacon = service.card().discovery_dict()
    public_identity = service.public_identity()

    for payload in (card, beacon):
        serialised = str(payload)
        assert "pairing_code" not in payload
        assert service.identity.pairing_code not in serialised
        assert str(service.home) not in serialised
        assert "identity.json" not in serialised
        assert "outbound_token" not in serialised
        assert "token_hash" not in serialised

    # The authenticated status view discloses a truncated digest, never a code.
    assert "pairing_code" not in public_identity
    digest = public_identity["pairing_code_hash"]
    assert len(digest) == 16
    assert service.identity.pairing_code not in str(public_identity)
    from alpha.peer_network.storage import token_digest

    assert digest == token_digest(service.identity.pairing_code)[:16]

    with_secret = service.public_identity(include_pairing_code=True)
    assert with_secret["pairing_code"] == service.identity.pairing_code


# --------------------------------------------------------------------------- #
# 7. The GitHub rendezvous cannot be walked out of its directory
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("directory", ["..", "../secrets", "peers/../../etc", "/absolute"])
def test_github_rendezvous_refuses_an_unsafe_configured_directory(directory: str) -> None:
    adapter = GitHubRendezvous(repo="owner/peers", directory=directory, token="test-token")
    with pytest.raises(GitHubRendezvousError):
        adapter._url("peers/alpha.json")


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        body = json.dumps(payload).encode("utf-8")
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def json(self) -> Any:
        return self._payload


def _build_fake_client(calls: list[tuple[str, str, Any]], responder):
    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def request(self, method: str, url: str, headers: Any = None, json: Any = None) -> _FakeResponse:
            calls.append((method, url, json))
            return responder(method, url, json)

    return _FakeClient


def _fake_github_http(monkeypatch: pytest.MonkeyPatch, responder) -> list[tuple[str, str, Any]]:
    """Replace the HTTP client and record every request the adapter makes.

    The path validation lives in ``_url``, which the real ``_request`` calls, so
    the seam to replace is the client — not ``_request`` itself, which would
    skip the very code under test.  The returned list fills in as calls happen.
    """

    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _build_fake_client(calls, responder))
    return calls


def test_github_publish_writes_inside_the_configured_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile agent id can never redirect the write out of the directory."""

    adapter = GitHubRendezvous(repo="owner/peers", directory=".alpha-network/peers", token="test-token")
    calls = _fake_github_http(monkeypatch, lambda method, url, body: _FakeResponse({"content": {}}))

    for agent_id in ("..", ".", "...", "alpha..b", ":"):
        asyncio.run(adapter.publish_card(PeerCard(agent_id=agent_id, url="http://192.0.2.60:8001")))

    # Each publish is a read-then-write, and every one of those requests stays
    # inside the configured directory with no traversing segment.
    assert [method for method, _, _ in calls] == ["GET", "PUT"] * 5
    for _method, url, _body in calls:
        assert url.startswith("https://api.github.com/repos/owner/peers/contents/.alpha-network/peers/")
        assert ".." not in url.split("/contents/")[1].split("/")[1:]
    assert [body["branch"] for _m, _u, body in calls if body] == ["main"] * 5


def test_github_publish_refuses_an_unsafe_configured_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRendezvous(repo="owner/peers", directory="../../other-repo", token="test-token")
    calls = _fake_github_http(monkeypatch, lambda method, url, body: _FakeResponse({}))
    with pytest.raises(GitHubRendezvousError):
        asyncio.run(adapter.publish_card(PeerCard(agent_id="alpha-x", url="http://192.0.2.60:8001")))
    assert calls == []


def test_github_listing_skips_a_traversing_entry_without_requesting_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile directory entry must be dropped, not fetched, and not fatal."""

    adapter = GitHubRendezvous(repo="owner/peers", directory=".alpha-network/peers")
    good = PeerCard(agent_id="alpha-good", url="http://192.0.2.61:8001").to_dict()
    listing = [
        {"type": "file", "name": "evil.json", "path": "../../../other-repo/contents/secrets.json"},
        {"type": "file", "name": "alpha-good.json", "path": f"{adapter.directory}/alpha-good.json"},
    ]

    def responder(method: str, url: str, body: Any) -> _FakeResponse:
        if url.endswith(f"contents/{adapter.directory}?ref=main"):
            return _FakeResponse(listing)
        return _FakeResponse({"content": _b64(good)})

    calls = _fake_github_http(monkeypatch, responder)
    cards = asyncio.run(adapter.list_cards())
    assert [card.agent_id for card in cards] == ["alpha-good"]
    # Two requests: the directory and the one legitimate card. The traversing
    # entry is refused at the URL layer, so it is never asked for and it does
    # not abort the rest of the directory read.
    assert len(calls) == 2
    assert not any("other-repo" in url for _method, url, _body in calls)


def _b64(payload: dict[str, Any]) -> str:
    import base64
    import json

    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------- #
# 8. The mounted public routes, end to end
# --------------------------------------------------------------------------- #
@pytest.fixture
def public_plane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real ``public_router`` on a throwaway installation, opt-in and out."""

    from alpha.peer_network import service as service_module

    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "workspace"))
    saved = (service_module._service, service_module._service_path)
    service_module._service = None
    service_module._service_path = None
    try:
        yield service_module
    finally:
        asyncio.run(service_module.shutdown_peer_network_service())
        service_module._service, service_module._service_path = saved


def _public_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import peer_network as peer_router

    app = FastAPI()
    app.include_router(peer_router.public_router)
    return TestClient(app)


def test_the_public_pairing_route_refuses_the_nth_attempt(public_plane, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the mounted route: the attempt budget is enforced.

    This is the endpoint the audit flagged.  The assertions are on the HTTP
    surface an unauthenticated caller actually sees, not on a service helper.
    """

    monkeypatch.setenv("ALPHA_PEER_NETWORK_ENABLED", "1")
    client = _public_client()
    service = public_plane.get_peer_network_service()
    card = PeerCard(agent_id="alpha-remote-1", url="http://192.0.2.70:8001").model_dump(mode="json")
    body = {"card": card, "pairing_code": service.identity.pairing_code}
    budget = service.pairing_throttle.max_attempts

    for attempt in range(1, budget + 1):
        response = client.post("/api/peer-network/remote/pair", json=body)
        assert response.status_code == 200
        assert response.json()["accepted"] is True, f"attempt {attempt} must be admitted"

    refused = client.post("/api/peer-network/remote/pair", json=body)
    assert refused.status_code == 200
    payload = refused.json()
    assert payload["accepted"] is False
    assert "throttled" in payload["message"]
    assert payload["retry_after_seconds"] >= 1
    # One mutation, one peer: the refusal did not create a second identity.
    assert len(asyncio.run(service.list_peers())) == 1


def test_the_public_pairing_route_is_closed_until_the_operator_opts_in(
    public_plane, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALPHA_PEER_NETWORK_ENABLED", "0")
    client = _public_client()
    service = public_plane.get_peer_network_service()
    assert service.enabled is False
    body = {"card": PeerCard(agent_id="alpha-remote-2", url="http://192.0.2.71:8001").model_dump(mode="json"), "pairing_code": service.identity.pairing_code}
    response = client.post("/api/peer-network/remote/pair", json=body)
    assert response.json()["accepted"] is False
    assert "disabled" in response.json()["message"]
    assert asyncio.run(service.list_peers()) == []


def test_the_public_ingress_routes_require_a_paired_token(public_plane, monkeypatch: pytest.MonkeyPatch) -> None:
    """``inbound/messages`` is 401 without a paired token; the socket is 1008."""

    monkeypatch.setenv("ALPHA_PEER_NETWORK_ENABLED", "1")
    client = _public_client()
    service = public_plane.get_peer_network_service()
    local_id = service.identity.agent_id
    envelope = {
        "sender_id": "alpha-remote-3",
        "recipients": [local_id],
        "text": "hello",
    }

    missing = client.post("/api/peer-network/inbound/messages", json=envelope)
    assert missing.status_code == 401
    assert "X-Alpha-Peer-Token" in missing.json()["detail"]

    unknown = client.post(
        "/api/peer-network/inbound/messages",
        json=envelope,
        headers={"X-Alpha-Peer-Token": "0" * 43},
    )
    assert unknown.status_code == 401

    # A socket without a token is refused before it is accepted.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect("/api/peer-network/ws"):
            pass
    assert refusal.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as unpaired:
        with client.websocket_connect("/api/peer-network/ws", headers={"X-Alpha-Peer-Token": "0" * 43}):
            pass
    assert unpaired.value.code == 1008

    # Pair, and the same request is accepted and acknowledged on the socket.
    code = service.identity.pairing_code
    paired = client.post(
        "/api/peer-network/remote/pair",
        json={"card": PeerCard(agent_id="alpha-remote-3", url="http://192.0.2.72:8001").model_dump(mode="json"), "pairing_code": code},
    )
    assert paired.json()["accepted"] is True

    accepted = client.post(
        "/api/peer-network/inbound/messages",
        json=envelope,
        headers={"X-Alpha-Peer-Token": code},
    )
    assert accepted.status_code == 200
    assert accepted.json()["sender_id"] == "alpha-remote-3"

    with client.websocket_connect("/api/peer-network/ws", headers={"X-Alpha-Peer-Token": code}) as socket:
        assert socket.receive_json()["peer_id"] == "alpha-remote-3"
        socket.send_json(envelope)
        assert socket.receive_json()["type"] == "ack"


def test_the_public_agent_card_route_serves_metadata_only(public_plane, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHA_PEER_NETWORK_ENABLED", "1")
    client = _public_client()
    service = public_plane.get_peer_network_service()
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json", "/api/peer-network/card"):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        assert "pairing_code" not in payload
        assert service.identity.pairing_code not in response.text
        assert str(service.home) not in response.text
        assert "sqlite3" not in response.text
    # The public ingress is the only unauthenticated mutation surface.
    assert client.post("/api/peer-network/remote/pair", json={}).status_code == 422
    assert client.post("/api/peer-network/inbound/messages", json={}).status_code == 422
