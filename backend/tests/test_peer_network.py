"""Offline tests for the local-first Alpha peer network."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from alpha.peer_network import GitHubRendezvous, PeerEnvelope, PeerNetworkService, PeerPairRequest
from alpha.peer_network.models import ConversationCreateRequest, MessageCreateRequest
from alpha.peer_network.transport import PeerTransportError, TransportResult


@pytest.fixture
def services(tmp_path: Path):
    # The peer plane is opt-in (ALPHA_PEER_NETWORK_ENABLED), and these tests
    # exercise the live pairing/delivery path, so they enable it explicitly.
    # The disabled behaviour — including a closed ingress plane — is pinned in
    # test_peer_network_security.py.
    first = PeerNetworkService(tmp_path / "alpha-a", enabled=True)
    second = PeerNetworkService(tmp_path / "alpha-b", enabled=True)
    yield first, second
    first.store.close()
    second.store.close()


def test_github_rendezvous_is_explicit_and_never_stores_a_token_in_status(monkeypatch):
    monkeypatch.setenv("ALPHA_PEER_NETWORK_GITHUB_REPO", "owner/public-peers")
    monkeypatch.setenv("ALPHA_PEER_NETWORK_GITHUB_TOKEN", "github-secret")
    adapter = GitHubRendezvous.from_env()
    status = adapter.status()
    assert status["available"] is True
    assert status["writable"] is True
    assert "github-secret" not in str(status)
    adapter.repo = "not-a-repository"
    assert adapter.status()["available"] is False


def test_cards_are_bounded_and_do_not_contain_pairing_code(services):
    first, _ = services
    card = first.card().to_dict()
    assert card["agent_id"].startswith("alpha_")
    assert card["protocol"] == "alpha-a2a"
    assert card["pairing_required"] is True
    assert "pairing_code" not in card
    assert first.identity.pairing_code not in str(card)


def test_all_topology_modes_are_validated(services):
    first, _ = services
    participants = [first.identity.agent_id, "alpha-b", "alpha-c"]
    for mode in ("one_to_many", "many_to_one", "many_to_many", "broadcast"):
        conversation = asyncio.run(first.create_conversation(ConversationCreateRequest(title=mode, mode=mode, participants=participants)))
        assert conversation["mode"] == mode
        assert set(conversation["participants"]) == set(participants)
    direct = asyncio.run(first.create_conversation(ConversationCreateRequest(title="direct", mode="direct", participants=participants[:2])))
    assert len(direct["participants"]) == 2
    with pytest.raises(ValueError):
        asyncio.run(first.create_conversation(ConversationCreateRequest(title="bad", mode="direct", participants=participants)))


def test_pairing_is_explicit_and_untrusted_discovery_cannot_send(services):
    first, second = services
    request = PeerPairRequest(card=first.card(), pairing_code=second.identity.pairing_code)
    accepted = asyncio.run(second.accept_pair(request))
    assert accepted.accepted is True
    assert second.store.get_peer(first.identity.agent_id)["trust"] == "paired"

    # Discovery alone is not a credential.
    discovered = first.observe_discovery(second.card().discovery_dict(), source="test")
    assert discovered is not None
    assert first.store.get_peer(second.identity.agent_id)["trust"] == "discovered"
    queued = asyncio.run(first.send_message(MessageCreateRequest(recipients=[second.identity.agent_id], text="hello")))
    assert queued["status"] == "queued"
    assert queued["deliveries"][0]["transport"] == "offline"


def test_http_delivery_records_receipts_and_retry_queue(services):
    first, second = services
    first.store.upsert_peer(card=second.card().to_dict(), source="test", trust="paired", outbound_token=first.identity.pairing_code, paired=True)
    sent: list[PeerEnvelope] = []

    async def fake_send(peer, envelope, token):
        sent.append(envelope)
        return TransportResult(transport="http", status_code=200)

    first.transport.send = fake_send  # type: ignore[method-assign]
    message = asyncio.run(
        first.send_message(
            MessageCreateRequest(
                recipients=[second.identity.agent_id],
                text="hello peer",
                idempotency_key="test-delivery-1",
            )
        )
    )
    assert message["status"] == "delivered"
    assert message["deliveries"][0]["transport"] == "http"
    assert sent[0].text == "hello peer"

    # Same idempotency key is a replay, not a second network send.
    replay = asyncio.run(
        first.send_message(
            MessageCreateRequest(
                recipients=[second.identity.agent_id],
                text="hello peer",
                idempotency_key="test-delivery-1",
            )
        )
    )
    assert replay["message_id"] == message["message_id"]
    assert len(sent) == 1


def test_many_to_one_incoming_messages_share_a_conversation(services):
    receiver, _ = services
    senders = ["alpha-sender-a", "alpha-sender-b"]
    conversation = asyncio.run(
        receiver.create_conversation(
            ConversationCreateRequest(
                title="many to one",
                mode="many_to_one",
                participants=[receiver.identity.agent_id, *senders],
            )
        )
    )
    for sender in senders:
        receiver.store.upsert_peer(
            card={
                "agent_id": sender,
                "name": sender,
                "description": "",
                "url": f"http://{sender}:8001",
                "capabilities": [],
            },
            source="test",
            trust="paired",
            outbound_token=f"token-{sender}",
            paired=True,
        )
        envelope = PeerEnvelope(
            sender_id=sender,
            recipients=[receiver.identity.agent_id],
            conversation_id=conversation["conversation_id"],
            text=f"from {sender}",
        )
        asyncio.run(receiver.receive_remote(envelope, f"token-{sender}"))
    history = asyncio.run(receiver.get_messages(conversation["conversation_id"]))
    assert [message["sender_id"] for message in history] == senders
    assert all(message["direction"] == "inbound" for message in history)


def test_shared_pairing_code_is_disambiguated_by_sender_id(tmp_path):
    # The ingress plane is opt-in; these senders deliver to the receiver, so the
    # receiver and both senders are created with it enabled.
    receiver = PeerNetworkService(tmp_path / "receiver", enabled=True)
    first_sender = PeerNetworkService(tmp_path / "sender-a", enabled=True)
    second_sender = PeerNetworkService(tmp_path / "sender-b", enabled=True)
    # Deliberately share the receiver's pairing code across two peers.
    for sender in (first_sender, second_sender):
        receiver.store.upsert_peer(
            card=sender.card().to_dict(),
            source="test",
            trust="paired",
            outbound_token=receiver.identity.pairing_code,
            paired=True,
        )
        envelope = PeerEnvelope(sender_id=sender.identity.agent_id, recipients=[receiver.identity.agent_id], text="hello")
        asyncio.run(receiver.receive_remote(envelope, receiver.identity.pairing_code))
    conversations = asyncio.run(receiver.list_conversations())
    assert len(conversations) == 1
    assert conversations[0]["mode"] == "inbox"
    assert set(conversations[0]["participants"]) == {receiver.identity.agent_id, first_sender.identity.agent_id, second_sender.identity.agent_id}
    history = asyncio.run(receiver.get_messages(conversations[0]["conversation_id"]))
    assert all([delivery["recipient_id"] for delivery in message["deliveries"]] == [receiver.identity.agent_id] for message in history)
    receiver.store.close()
    first_sender.store.close()
    second_sender.store.close()


def test_remote_ingress_rejects_wrong_sender_or_replayed_message(services):
    receiver, sender = services
    code = sender.identity.pairing_code
    sender.store.upsert_peer(card=receiver.card().to_dict(), source="test", trust="paired", outbound_token=code, paired=True)
    # Receiver has not paired the sender, so its token map is empty.
    envelope = PeerEnvelope(sender_id=sender.identity.agent_id, recipients=[receiver.identity.agent_id], text="hello")
    with pytest.raises(PeerTransportError):
        asyncio.run(receiver.receive_remote(envelope, code))

    request = PeerPairRequest(card=sender.card(), pairing_code=receiver.identity.pairing_code)
    assert asyncio.run(receiver.accept_pair(request)).accepted is True
    # Use the receiver's shared code for the paired token.
    received = asyncio.run(receiver.receive_remote(envelope, receiver.identity.pairing_code))
    replay = asyncio.run(receiver.receive_remote(envelope, receiver.identity.pairing_code))
    assert replay["message_id"] == received["message_id"]


def test_persistence_survives_service_recreation(tmp_path: Path):
    home = tmp_path / "alpha"
    first = PeerNetworkService(home, enabled=False)
    conversation = asyncio.run(first.create_conversation(ConversationCreateRequest(title="durable", mode="direct", participants=[first.identity.agent_id, "alpha-friend"])))
    asyncio.run(first.send_message(MessageCreateRequest(conversation_id=conversation["conversation_id"], text="durable message")))
    first.store.close()
    second = PeerNetworkService(home, enabled=False)
    try:
        conversations = asyncio.run(second.list_conversations())
        messages = asyncio.run(second.get_messages(conversation["conversation_id"]))
        assert conversations[0]["title"] == "durable"
        assert messages[-1]["text"] == "durable message"
        assert second.identity.agent_id == first.identity.agent_id
    finally:
        second.store.close()


def test_discovery_ignores_self_and_malformed_beacons(services):
    first, _ = services
    assert first.observe_discovery(first.card().discovery_dict(), source="self") is None
    assert first.observe_discovery({"protocol": "other", "agent_id": "x", "url": "http://x"}, source="bad") is None
    assert first.observe_discovery({"agent_id": "bad", "url": "file:///tmp/x"}, source="bad") is None


def test_public_message_sender_is_server_owned(services):
    first, second = services
    with pytest.raises(ValueError, match="sender_id is server-owned"):
        asyncio.run(
            first.send_message(
                MessageCreateRequest(
                    sender_id=second.identity.agent_id,
                    recipients=[second.identity.agent_id],
                    text="spoof",
                )
            )
        )


def test_model_facing_peer_tool_exposes_only_public_capability_operations(tmp_path, monkeypatch):
    from alpha.tools.builtins.peer_network_tool import alpha_peer_network_tool

    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    import alpha.peer_network.service as service_module

    monkeypatch.setattr(service_module, "_service", None)
    monkeypatch.setattr(service_module, "_service_path", None)
    result = alpha_peer_network_tool.func(runtime=object(), action="list")
    assert isinstance(result, str)
    assert "pairing_code" not in result
    assert "alpha_peer_network" in alpha_peer_network_tool.name
    asyncio.run(service_module.shutdown_peer_network_service())


def test_peer_network_public_and_local_route_boundaries():
    from app.gateway.auth_middleware import _is_public

    assert _is_public("/.well-known/agent-card.json")
    assert _is_public("/.well-known/agent.json")
    assert _is_public("/api/peer-network/card")
    assert _is_public("/api/peer-network/remote/pair")
    assert _is_public("/api/peer-network/inbound/messages")
    assert not _is_public("/api/peer-network/status")
    assert not _is_public("/api/peer-network/messages")


def test_gateway_mounts_local_and_public_peer_routes():
    from app.gateway.app import create_app

    paths = {route.path for route in create_app().routes}
    assert "/api/peer-network/status" in paths
    assert "/api/peer-network/conversations" in paths
    assert "/api/peer-network/messages" in paths
    assert "/.well-known/agent-card.json" in paths
    assert "/.well-known/agent.json" in paths
    assert "/api/peer-network/inbound/messages" in paths


def test_status_discloses_optional_global_transports_without_claiming_them(tmp_path: Path):
    service = PeerNetworkService(tmp_path / "status-peer", enabled=False)
    try:
        status = asyncio.run(service.status())
        assert status.discovery["libp2p"]["available"] is False
        assert status.discovery["github"]["available"] is False
        assert status.transports["http"]["enabled"] is True
        assert status.persistence["backend"] == "sqlite"
    finally:
        service.store.close()
