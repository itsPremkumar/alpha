"""A2A interop on the paired-peer network (bot plan 4.1).

Pins the three properties the phase exists to guarantee:

1. **No fake cards.**  The tool and router build cards from real sources
   (BotRegistry + paired peers), never pre-populated fictional agents.
2. **Honest delegation.**  A dispatch to a paired peer returns ``accepted``
   — never ``completed`` — because the result comes back asynchronously.
3. **Real delivery.**  ``POST /api/a2a/tasks`` creates a durable inbox
   message via the peer network service, not a simulated success.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alpha.peer_network.a2a_transport import PeerA2ATransport
from alpha.peer_network.models import MessageCreateRequest
from alpha.peer_network.transport import PeerTransportError
from alpha.protocols.a2a import (
    A2ADelegationRequest,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)


def _card(**over: Any) -> AgentCapabilityCard:
    base = {
        "agent_id": "remote-1",
        "name": "Remote One",
        "description": "A paired remote agent",
        "skills": ["analysis"],
        "availability": "available",
    }
    base.update(over)
    return AgentCapabilityCard(**base)  # type: ignore[arg-type]


def _request(**over: Any) -> A2ADelegationRequest:
    base = {
        "request_id": "req-1",
        "sender_agent_id": "local-1",
        "target_agent_id": "remote-1",
        "task_objective": "Summarise the incident",
    }
    base.update(over)
    return A2ADelegationRequest(**base)  # type: ignore[arg-type]


class _FakeService:
    """Minimal PeerNetworkService stand-in for transport tests."""

    def __init__(self, *, peers: list[dict] | None = None, send_result: dict | None = None, send_error: Exception | None = None) -> None:
        self._peers = peers or []
        self._send_result = send_result or {"message_id": "msg-123"}
        self._send_error = send_error
        self.send_calls: list[MessageCreateRequest] = []

    async def list_peers(self, *, trust: str | None = None) -> list[dict[str, Any]]:
        return [p for p in self._peers if trust is None or p.get("trust") == trust]

    async def get_peer(self, agent_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        for peer in self._peers:
            if peer.get("agent_id") == agent_id:
                return peer
        return None

    async def send_message(self, request: MessageCreateRequest, *, trusted_sender_id: str | None = None) -> dict[str, Any]:
        self.send_calls.append(request)
        if self._send_error:
            raise self._send_error
        return self._send_result


# ---------------------------------------------------------------------------
# PeerA2ATransport
# ---------------------------------------------------------------------------


class TestPeerA2ATransport:
    def test_dispatch_to_paired_peer_returns_accepted_not_completed(self) -> None:
        service = _FakeService(peers=[{"agent_id": "remote-1", "trust": "paired", "url": "https://peer.example"}])
        transport = PeerA2ATransport(service)  # type: ignore[arg-type]
        response = transport.dispatch(_card(), _request())

        assert response.status == "accepted", "a delivered task_request is accepted, never completed"
        assert response.deliverable is None, "no deliverable is invented"
        assert any("msg-123" in ref for ref in response.evidence)

    def test_dispatch_sends_task_request_envelope(self) -> None:
        service = _FakeService(peers=[{"agent_id": "remote-1", "trust": "paired"}])
        transport = PeerA2ATransport(service)  # type: ignore[arg-type]
        transport.dispatch(_card(), _request(task_objective="Analyse logs"))

        assert len(service.send_calls) == 1
        call = service.send_calls[0]
        assert call.kind == "task_request"
        assert call.recipients == ["remote-1"]
        assert "Analyse logs" in call.text
        assert call.payload.get("a2a_request_id") == "req-1"

    def test_dispatch_to_unpaired_peer_is_rejected(self) -> None:
        service = _FakeService(peers=[{"agent_id": "remote-1", "trust": "discovered"}])
        transport = PeerA2ATransport(service)  # type: ignore[arg-type]
        response = transport.dispatch(_card(), _request())

        assert response.status == "rejected"
        assert response.error is not None
        assert "not a paired peer" in response.error.lower() or "not paired" in response.error.lower()

    def test_dispatch_to_unknown_peer_is_rejected(self) -> None:
        service = _FakeService(peers=[])
        transport = PeerA2ATransport(service)  # type: ignore[arg-type]
        response = transport.dispatch(_card(), _request(target_agent_id="nobody"))

        assert response.status == "rejected"

    def test_dispatch_transport_failure_is_failed_not_completed(self) -> None:
        service = _FakeService(peers=[{"agent_id": "remote-1", "trust": "paired"}], send_error=PeerTransportError("connection refused"))
        transport = PeerA2ATransport(service)  # type: ignore[arg-type]
        response = transport.dispatch(_card(), _request())

        assert response.status == "failed"
        assert response.error is not None
        assert "connection refused" in response.error

    def test_dispatch_never_returns_completed(self) -> None:
        """The core honesty invariant: this transport cannot claim completion."""
        service = _FakeService(peers=[{"agent_id": "remote-1", "trust": "paired"}])
        transport = PeerA2ATransport(service)  # type: ignore[arg-type]
        response = transport.dispatch(_card(), _request())
        assert response.status != "completed"


# ---------------------------------------------------------------------------
# Async transport support in the adapter
# ---------------------------------------------------------------------------


class TestAsyncTransportSupport:
    def test_adapter_supports_async_transport(self) -> None:
        """The adapter must await an async transport's response."""

        class _AsyncTransport:
            async def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> Any:
                return A2ADelegationRequest  # placeholder

        # Use a real async transport that returns a proper response
        class _RealAsyncTransport:
            async def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> Any:
                from alpha.protocols.a2a import A2ADelegationResponse

                return A2ADelegationResponse(
                    request_id=request.request_id,
                    status="accepted",
                    evidence=["async transport"],
                )

        adapter = A2AProtocolAdapter(transport=_RealAsyncTransport())  # type: ignore[arg-type]
        adapter.register_card(_card())
        response = adapter.delegate(_request())
        assert response.status == "accepted"
        assert response.evidence == ["async transport"]

    def test_adapter_still_supports_sync_transport(self) -> None:
        """Existing sync transports must continue to work unchanged."""

        class _SyncTransport:
            def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> Any:
                from alpha.protocols.a2a import A2ADelegationResponse

                return A2ADelegationResponse(
                    request_id=request.request_id,
                    status="accepted",
                    evidence=["sync transport"],
                )

        adapter = A2AProtocolAdapter(transport=_SyncTransport())  # type: ignore[arg-type]
        adapter.register_card(_card())
        response = adapter.delegate(_request())
        assert response.status == "accepted"


# ---------------------------------------------------------------------------
# No fake cards
# ---------------------------------------------------------------------------


class TestNoFakeCards:
    def test_tool_does_not_pre_populate_fake_cards(self) -> None:
        """The fake agent-researcher and agent-security-auditor cards must be gone."""
        import alpha.tools.builtins.a2a_tool as tool_module

        cards = tool_module._GLOBAL_A2A.list_cards()
        agent_ids = [c.agent_id for c in cards]
        assert "agent-researcher" not in agent_ids, "fake card agent-researcher must not be pre-registered"
        assert "agent-security-auditor" not in agent_ids, "fake card agent-security-auditor must not be pre-registered"
        assert "primary-orchestrator" not in agent_ids, "fake card primary-orchestrator must not be pre-registered"

    def test_router_does_not_pre_populate_fake_cards(self) -> None:
        """The router's /cards endpoint must not return fake cards."""
        from app.gateway.routers.a2a import _GLOBAL_A2A

        cards = _GLOBAL_A2A.list_cards()
        agent_ids = [c.agent_id for c in cards]
        assert "agent-researcher" not in agent_ids
        assert "agent-security-auditor" not in agent_ids

    def test_cards_are_built_from_real_bots(self) -> None:
        """Cards from BotRegistry carry real capabilities."""
        from alpha.tools.builtins.a2a_tool import _build_cards_from_bots

        cards = _build_cards_from_bots()
        # With no bots registered, the list is empty — but it must not contain fakes
        for card in cards:
            assert not card.agent_id.startswith("agent-"), f"fake card {card.agent_id} found"


# ---------------------------------------------------------------------------
# POST /api/a2a/tasks
# ---------------------------------------------------------------------------


class TestCreateTaskEndpoint:
    @pytest.mark.asyncio
    async def test_create_task_delivers_real_message(self) -> None:
        """POST /api/a2a/tasks creates a durable inbox message."""
        from app.gateway.routers.a2a import create_task

        fake_message = {"message_id": "msg-abc", "status": "queued"}

        with patch("alpha.peer_network.service.PeerNetworkService") as mock_service_cls:
            mock_service = MagicMock()
            mock_service.send_message = AsyncMock(return_value=fake_message)
            mock_service_cls.return_value = mock_service

            payload = MagicMock()
            payload.sender_agent_id = "local-1"
            payload.target_agent_id = "remote-1"
            payload.task_objective = "Analyse the incident"
            payload.context_data = {}
            payload.deadline_seconds = 120.0

            result = await create_task(payload)

        assert result["status"] == "accepted"
        assert result["message_id"] == "msg-abc"
        assert result["target_agent_id"] == "remote-1"
        mock_service.send_message.assert_called_once()
        call_args = mock_service.send_message.call_args
        assert call_args.kwargs.get("trusted_sender_id") == "local-1"

    @pytest.mark.asyncio
    async def test_create_task_returns_accepted_never_completed(self) -> None:
        """The endpoint must never claim completion."""
        from app.gateway.routers.a2a import create_task

        fake_message = {"message_id": "msg-xyz"}

        with patch("alpha.peer_network.service.PeerNetworkService") as mock_service_cls:
            mock_service = MagicMock()
            mock_service.send_message = AsyncMock(return_value=fake_message)
            mock_service_cls.return_value = mock_service

            payload = MagicMock()
            payload.sender_agent_id = "local-1"
            payload.target_agent_id = "remote-1"
            payload.task_objective = "Do something"
            payload.context_data = {}
            payload.deadline_seconds = 120.0

            result = await create_task(payload)

        assert result["status"] == "accepted"
        assert result["status"] != "completed"

    @pytest.mark.asyncio
    async def test_create_task_unpaired_target_is_404(self) -> None:
        """An unpaired or unknown target is refused, not silently queued."""
        from alpha.peer_network.transport import PeerTransportError
        from app.gateway.routers.a2a import create_task

        with patch("alpha.peer_network.service.PeerNetworkService") as mock_service_cls:
            mock_service = MagicMock()
            mock_service.send_message = AsyncMock(side_effect=PeerTransportError("Peer is not paired"))
            mock_service_cls.return_value = mock_service

            payload = MagicMock()
            payload.sender_agent_id = "local-1"
            payload.target_agent_id = "unknown-agent"
            payload.task_objective = "Do something"
            payload.context_data = {}
            payload.deadline_seconds = 120.0

            with pytest.raises(Exception) as exc_info:
                await create_task(payload)

            assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


class TestConfigFlag:
    def test_a2a_enabled_defaults_to_false(self) -> None:
        """The A2A plane is off by default."""
        from pathlib import Path

        config_path = Path(__file__).parent.parent.parent / "config.example.yaml"
        with open(config_path, encoding="utf-8") as handle:
            content = handle.read()

        assert "a2a:" in content
        assert "enabled: false" in content


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
