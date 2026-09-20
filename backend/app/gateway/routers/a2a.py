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

_GLOBAL_A2A = A2AProtocolAdapter()

# Register core system capability cards
_GLOBAL_A2A.register_card(
    AgentCapabilityCard(
        agent_id="primary-orchestrator",
        name="Primary Autonomous Orchestrator",
        description="Coordinates cognitive planning, swarms, subagents, and councils.",
        skills=["orchestration", "planning", "delegation"],
        availability="available",
    )
)
_GLOBAL_A2A.register_card(
    AgentCapabilityCard(
        agent_id="agent-researcher",
        name="Lead Research Specialist",
        description="Deep scientific & web research, citation extraction, source verification.",
        skills=["research", "fact_checking", "summarization"],
        availability="available",
    )
)
_GLOBAL_A2A.register_card(
    AgentCapabilityCard(
        agent_id="agent-security-auditor",
        name="Adversarial Security Auditor",
        description="AST code audit, CVE scanning, secret leakage detection, permission checks.",
        skills=["security", "audit", "vulnerability_scan"],
        availability="available",
    )
)


class DelegateTaskRequest(BaseModel):
    sender_agent_id: str = Field(default="gateway-client", description="Requesting agent ID")
    target_agent_id: str = Field(..., description="Target recipient agent ID")
    task_objective: str = Field(..., min_length=2, description="Delegated task prompt or objective")
    context_data: dict[str, Any] = Field(default_factory=dict)
    deadline_seconds: float = Field(default=120.0)


@router.get("/cards")
async def list_capability_cards(skill_filter: str | None = None):
    """Lists all registered Google A2A agent capability cards."""
    cards = _GLOBAL_A2A.list_cards(skill_filter=skill_filter)
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
