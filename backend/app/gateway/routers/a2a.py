"""Gateway REST Router for Google A2A Inter-Agent Protocol."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.protocols.a2a import (
    A2ADelegationRequest,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/protocols/a2a", tags=["a2a-protocol"])

# The shared adapter instance.  Cards are built from real sources via
# _real_cards(), never pre-populated with fictional agents.
_GLOBAL_A2A = A2AProtocolAdapter()


def _real_cards() -> list[AgentCapabilityCard]:
    """Build cards from the real BotRegistry and paired peers."""
    cards: list[AgentCapabilityCard] = []
    try:
        from alpha.bots.registry import BotRegistry

        for bot in BotRegistry().list_bots(include_archived=False):
            cards.append(
                AgentCapabilityCard(
                    agent_id=f"bot:{bot.name}",
                    name=bot.display_name,
                    description=bot.role[:200] if bot.role else bot.display_name,
                    skills=list(bot.skills)[:20],
                    availability="available" if bot.status == "active" else "offline",
                )
            )
    except Exception:
        pass
    try:
        import asyncio

        from alpha.peer_network.service import PeerNetworkService

        peers = asyncio.run(PeerNetworkService().list_peers(trust="paired"))
        for peer in peers:
            agent_id = peer.get("agent_id", "")
            if not agent_id:
                continue
            cards.append(
                AgentCapabilityCard(
                    agent_id=agent_id,
                    name=peer.get("name", agent_id),
                    description=(peer.get("description") or "")[:200],
                    skills=[s.get("name", "") for s in peer.get("skills", []) if s.get("name")][:20],
                    availability="available",
                )
            )
    except Exception:
        pass
    return cards


class DelegateTaskRequest(BaseModel):
    sender_agent_id: str = Field(default="gateway-client", description="Requesting agent ID")
    target_agent_id: str = Field(..., description="Target recipient agent ID")
    task_objective: str = Field(..., min_length=2, description="Delegated task prompt or objective")
    context_data: dict[str, Any] = Field(default_factory=dict)
    deadline_seconds: float = Field(default=120.0)


class A2ATaskRequest(BaseModel):
    """Request to create a real inbox message or run via the A2A plane.

    The target must be a paired peer (for remote delivery) or a local bot
    (for local execution).  The response is always ``accepted`` — never
    ``completed`` — because the result comes back asynchronously.
    """

    sender_agent_id: str = Field(default="gateway-client", description="Requesting agent ID")
    target_agent_id: str = Field(..., description="Target recipient agent ID")
    task_objective: str = Field(..., min_length=2, description="Task prompt or objective")
    context_data: dict[str, Any] = Field(default_factory=dict)
    deadline_seconds: float = Field(default=120.0)


@router.get("/cards")
async def list_capability_cards(skill_filter: str | None = None):
    """Lists all registered Google A2A agent capability cards."""
    cards = _GLOBAL_A2A.list_cards(skill_filter=skill_filter)
    if not cards:
        cards = _real_cards()
    return [c.model_dump() for c in cards]


@router.get("/cards/{agent_id}")
async def get_capability_card(agent_id: str):
    """Retrieves an agent's capability card by agent ID."""
    card = _GLOBAL_A2A.get_card(agent_id)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found in A2A registry.")
    return card.model_dump()


@router.post("/cards")
async def register_capability_card(card: AgentCapabilityCard):
    """Registers a new agent capability card in the A2A federation registry."""
    _GLOBAL_A2A.register_card(card)
    return {"status": "registered", "card": card.model_dump()}


@router.post("/delegate")
async def delegate_task(payload: DelegateTaskRequest):
    """Dispatches a task delegation request to a target agent using the A2A protocol."""
    req = A2ADelegationRequest(
        sender_agent_id=payload.sender_agent_id,
        target_agent_id=payload.target_agent_id,
        task_objective=payload.task_objective,
        context_data=payload.context_data,
        deadline_seconds=payload.deadline_seconds,
    )
    res = _GLOBAL_A2A.delegate(req)
    if res.status == "rejected":
        raise HTTPException(status_code=400, detail=res.error)
    return res.model_dump()


@router.post("/tasks")
async def create_task(payload: A2ATaskRequest):
    """Create a real inbox message or run via the A2A plane.

    This endpoint is the machine-to-machine surface for A2A task creation.
    It delivers a ``task_request`` envelope to a paired peer's inbox (for
    remote targets) or queues a local run (for local bot targets).  The
    response is always ``accepted`` — never ``completed`` — because the
    result comes back asynchronously via the peer network or run system.

    Pairing is required: an unpaired or unknown target is refused with 404.
    """
    from alpha.peer_network.models import MessageCreateRequest
    from alpha.peer_network.service import PeerNetworkService, PeerTransportError

    service = PeerNetworkService()
    try:
        message = await service.send_message(
            MessageCreateRequest(
                recipients=[payload.target_agent_id],
                kind="task_request",
                text=payload.task_objective,
                payload={
                    "sender_agent_id": payload.sender_agent_id,
                    "deadline_seconds": payload.deadline_seconds,
                    "context_data": payload.context_data,
                },
            ),
            trusted_sender_id=payload.sender_agent_id,
        )
    except PeerTransportError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Task delivery failed: {exc}") from exc

    return {
        "status": "accepted",
        "target_agent_id": payload.target_agent_id,
        "message_id": message.get("message_id"),
        "note": "Task delivered to target inbox. Result will arrive asynchronously.",
    }
