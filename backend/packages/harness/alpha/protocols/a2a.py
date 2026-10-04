"""Google A2A (Agent-to-Agent) Interoperability Protocol Adapter.

Implements agent capability discovery, standardized capability cards,
and inter-agent task delegation across organizations and runtime boundaries.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentCapabilityCard(BaseModel):
    agent_id: str
    name: str
    description: str
    version: str = "1.0.0"
    skills: list[str] = Field(default_factory=list)
    supported_protocols: list[str] = Field(default_factory=lambda: ["A2A/1.0", "MCP/2026-07"])
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {"objective": {"type": "string"}}})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {"result": {"type": "string"}}})
    auth_mode: str = "bearer"
    availability: str = "available"  # available, busy, offline
    endpoint_url: str | None = None
    created_at: float = Field(default_factory=time.time)


class A2ADelegationRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"a2a-{uuid.uuid4().hex[:8]}")
    sender_agent_id: str
    target_agent_id: str
    task_objective: str
    context_data: dict[str, Any] = Field(default_factory=dict)
    deadline_seconds: float = Field(default=120.0)
    created_at: float = Field(default_factory=time.time)


class A2ADelegationResponse(BaseModel):
    request_id: str
    # default is "rejected", NOT "completed". A response is a positive claim that
    # work happened, so a default that assumes success is a default that lies
    # whenever a caller forgets to set it.
    status: str = "rejected"  # accepted, completed, rejected, failed, not_dispatched
    deliverable: Any = None
    evidence: list[str] = Field(default_factory=list)
    error: str | None = None
    execution_seconds: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class A2ATransport(Protocol):
    """The only thing permitted to report a delegation as performed.

    Deliberately a Protocol with no implementation shipped here: this module
    contains no HTTP client and therefore cannot dispatch anything on its own.
    A real transport must be injected.
    """

    def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> A2ADelegationResponse: ...


class A2AProtocolAdapter:
    """Registry and dispatcher for A2A cross-agent communication.

    This is a **registry and a wire shape**, not an executor. No transport ships
    with it, so with no transport injected a delegation is reported as
    ``not_dispatched`` — never as completed. See ``delegate``.
    """

    def __init__(self, transport: A2ATransport | None = None):
        # agent_id -> AgentCapabilityCard
        self._registry: dict[str, AgentCapabilityCard] = {}
        # request_id -> A2ADelegationResponse
        self._history: dict[str, A2ADelegationResponse] = {}
        # Optional injected transport. Absent means this adapter cannot dispatch.
        self._transport: A2ATransport | None = transport

    def register_card(self, card: AgentCapabilityCard) -> None:
        self._registry[card.agent_id] = card

    def get_card(self, agent_id: str) -> AgentCapabilityCard | None:
        return self._registry.get(agent_id)

    def list_cards(self, skill_filter: str | None = None) -> list[AgentCapabilityCard]:
        cards = list(self._registry.values())
        if skill_filter:
            cards = [c for c in cards if skill_filter.lower() in [s.lower() for s in c.skills]]
        return cards

    def delegate(self, request: A2ADelegationRequest) -> A2ADelegationResponse:
        """Process a delegation request to a registered or federated agent."""
        start = time.time()
        card = self._registry.get(request.target_agent_id)

        if not card:
            response = A2ADelegationResponse(
                request_id=request.request_id,
                status="rejected",
                error=f"Target agent '{request.target_agent_id}' not found in A2A registry.",
                execution_seconds=time.time() - start,
            )
            self._history[request.request_id] = response
            return response

        if card.availability == "offline":
            response = A2ADelegationResponse(
                request_id=request.request_id,
                status="rejected",
                error=f"Target agent '{request.target_agent_id}' is currently offline.",
                execution_seconds=time.time() - start,
            )
            self._history[request.request_id] = response
            return response

        # Previously this branch read "Simulate or dispatch" and then only
        # simulated: it built a deliverable by interpolating the requested
        # objective back into a sentence, returned status="completed" for work
        # that never ran, and attached evidence reading
        # "Verified by A2A endpoint: local-bus" when the card had no URL. That was
        # reachable from both the registered router and a real agent tool, so a
        # caller could be told work completed with verified evidence when nothing
        # had been dispatched at all.
        #
        # This adapter has never had a transport. It cannot report completion, so
        # it does not: it refuses, and names the missing piece.
        if self._transport is None:
            response = A2ADelegationResponse(
                request_id=request.request_id,
                status="not_dispatched",
                error=(
                    f"No A2A transport is configured, so target '{request.target_agent_id}' "
                    f"(card '{card.name}') was NOT dispatched and no work was performed. "
                    "This adapter is a registry and a wire shape, not an executor. "
                    "A transport must be injected before a delegation can be reported as completed."
                ),
                execution_seconds=round(time.time() - start, 3),
            )
            self._history[request.request_id] = response
            return response

        # A transport exists: it owns the outcome, including the evidence, and it
        # is the only component permitted to assert that work happened.
        response = self._transport.dispatch(card, request)
        if response.request_id != request.request_id:
            response = response.model_copy(update={"request_id": request.request_id})
        self._history[request.request_id] = response
        return response

    def get_response(self, request_id: str) -> A2ADelegationResponse | None:
        return self._history.get(request_id)
