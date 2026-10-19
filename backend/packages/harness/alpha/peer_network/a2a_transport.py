"""A2A transport over the paired-peer network (bot plan 4.1).

This is the only A2A transport that can honestly report ``accepted``: it
delivers a real ``task_request`` envelope to a paired peer through the
existing peer transport, and the peer's inbox is the durable record that the
task was received.  It never reports ``completed`` — a result comes back
asynchronously via the peer network, and no synchronous dispatch can know
one exists.

The transport is deliberately narrow:

* **Paired peers only.**  A discovered-but-unpaired agent is refused, not
  silently downgraded to a local queue.
* **Real envelopes only.**  Every dispatch is a ``PeerEnvelope`` with
  ``kind="task_request"`` carrying the A2A request id in its payload, so a
  result that comes back can be correlated to the delegation that asked.
* **Honest statuses.**  ``accepted`` means "delivered to the peer's inbox";
  ``rejected`` means the target is not a paired peer; ``failed`` means the
  transport raised.  ``completed`` is never returned by this transport.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from alpha.peer_network.service import PeerNetworkService
from alpha.peer_network.transport import PeerTransportError
from alpha.protocols.a2a import A2ADelegationRequest, A2ADelegationResponse, AgentCapabilityCard

logger = logging.getLogger(__name__)


class PeerA2ATransport:
    """Dispatch A2A delegations to paired peers via the peer-network transport.

    The transport is synchronous to match the ``A2ATransport`` protocol.
    It uses ``asyncio.run()`` internally because ``PeerNetworkService`` is
    async; this is safe because the transport is only called from contexts
    that do not hold a running event loop (the A2A tool and the delegation
    router both call it synchronously).
    """

    def __init__(self, service: PeerNetworkService) -> None:
        self.service = service

    def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> A2ADelegationResponse:
        """Deliver a task_request envelope to the target peer.

        Returns ``accepted`` when the envelope is durably queued in the
        peer's inbox, ``rejected`` when the target is not a paired peer,
        and ``failed`` when the transport raises.  Never ``completed``.
        """
        try:
            asyncio.run(self._check_pairing(request))
        except PeerTransportError as exc:
            return A2ADelegationResponse(
                request_id=request.request_id,
                status="rejected",
                error=str(exc),
            )

        try:
            message = asyncio.run(self._send(request))
        except PeerTransportError as exc:
            return A2ADelegationResponse(
                request_id=request.request_id,
                status="failed",
                error=str(exc),
            )
        except Exception as exc:
            logger.exception("A2A dispatch to %s failed", request.target_agent_id)
            return A2ADelegationResponse(
                request_id=request.request_id,
                status="failed",
                error=f"A2A dispatch failed: {exc}",
            )

        return A2ADelegationResponse(
            request_id=request.request_id,
            status="accepted",
            evidence=[f"task_request queued as message {message.get('message_id', '?')}"],
        )

    async def _check_pairing(self, request: A2ADelegationRequest) -> dict[str, Any]:
        """Verify the target is a paired peer before any bytes move."""
        peer = await self.service.get_peer(request.target_agent_id, include_secret=True)
        if peer is None:
            raise PeerTransportError(f"Target agent '{request.target_agent_id}' is not a known peer")
        if peer.get("trust") != "paired":
            raise PeerTransportError(f"Target agent '{request.target_agent_id}' is not paired (trust={peer.get('trust', 'unknown')})")
        return peer

    async def _send(self, request: A2ADelegationRequest) -> dict[str, Any]:
        """Send the task envelope through the peer network service."""
        from alpha.peer_network.models import MessageCreateRequest

        envelope = MessageCreateRequest(
            recipients=[request.target_agent_id],
            kind="task_request",
            text=request.task_objective,
            payload={
                "a2a_request_id": request.request_id,
                "deadline_seconds": request.deadline_seconds,
                "sender_agent_id": request.sender_agent_id,
            },
        )
        return await self.service.send_message(envelope, trusted_sender_id=request.sender_agent_id)


__all__ = ["PeerA2ATransport"]
