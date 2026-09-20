"""Direct agent-to-agent (A2A) messaging API (thread-scoped).

Exposes the harness-layer AgentRoster mailbox as a real system part so
coordination bots, modes, profiles, and subagents can discover peers and
exchange direct messages without routing everything through the user.

Scope: thread-scoped, owner-checked (same contract as mcp-tasks). Roster is
process-local in-memory (single-worker by design); completed status is
terminal. Delivery modes: auto (deliver if idle else queue), steer (immediate),
follow_up (queued). Broadcast via receiver_name="all".
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from alpha.utils.thread_id import ThreadId

router = APIRouter(prefix="/api/threads/{thread_id}/agent-messages", tags=["agent-messages"])

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VALID_MODES = ("auto", "steer", "follow_up")
_VALID_STATUS = ("idle", "busy", "completed")
_MAX_CONTENT_CHARS = 20000


def _validate_agent_name(value: str, field: str = "agent name") -> str:
    cleaned = value.strip()
    if not _AGENT_NAME_RE.match(cleaned):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field} '{value}': 1-64 chars, letters/digits/'_'/'-'.",
        )
    return cleaned


def _roster(thread_id: str):
    from alpha.subagents.messaging import get_agent_roster

    return get_agent_roster(thread_id)


class RegisterAgentRequest(BaseModel):
    name: str = Field(max_length=64)
    role: str = Field(default="worker", max_length=100)
    status: str = Field(default="idle")
    metadata: dict | None = None


class SendMessageRequest(BaseModel):
    sender_name: str = Field(max_length=64)
    receiver_name: str = Field(max_length=64)
    content: str = Field(min_length=1, max_length=_MAX_CONTENT_CHARS)
    mode: str = Field(default="auto")
    kind: str = Field(default="message", max_length=32)


class UpdateStatusRequest(BaseModel):
    status: str


@router.get("/roster", summary="List agents in thread roster")
@require_permission("threads", "read", owner_check=True)
async def list_roster(thread_id: ThreadId) -> dict:
    roster = _roster(str(thread_id))
    agents = [a.to_dict() for a in roster.list_agents()]
    return {"thread_id": str(thread_id), "agents": agents, "count": len(agents)}


@router.post("/register", status_code=201, summary="Register agent in roster")
@require_permission("threads", "write", owner_check=True)
async def register_agent(thread_id: ThreadId, body: RegisterAgentRequest) -> dict:
    name = _validate_agent_name(body.name)
    status = body.status.strip().lower()
    if status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(_VALID_STATUS)}")
    roster = _roster(str(thread_id))
    desc = roster.register_agent(name=name, role=body.role.strip() or "worker", status=status, metadata=body.metadata or {})
    return desc.to_dict()


@router.post("/messages", status_code=201, summary="Send direct/broadcast agent message")
@require_permission("threads", "write", owner_check=True)
async def send_agent_message(thread_id: ThreadId, body: SendMessageRequest) -> dict:
    sender = _validate_agent_name(body.sender_name, "sender_name")
    receiver = body.receiver_name.strip()
    if receiver != "all":
        receiver = _validate_agent_name(receiver, "receiver_name")
    mode = body.mode.strip().lower()
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {list(_VALID_MODES)}")
    kind = body.kind.strip().lower()
    from alpha.subagents.messaging import MESSAGE_KINDS

    if kind not in MESSAGE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(MESSAGE_KINDS)}")
    roster = _roster(str(thread_id))
    # Auto-register unknown senders as workers so bots/subagents can message
    # without a separate register call; receivers must exist (or "all").
    if sender not in [a.name for a in roster.list_agents()]:
        roster.register_agent(name=sender, role="worker", status="busy")
    result = roster.send_message(sender, receiver, body.content, mode=mode, kind=kind)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("error", "Unknown agent"))
    return {"thread_id": str(thread_id), **result}


@router.get("/inbox", summary="Fetch agent inbox (marks read by default)")
@require_permission("threads", "read", owner_check=True)
async def get_inbox(thread_id: ThreadId, agent_name: str, mark_as_read: bool = True) -> dict:
    name = _validate_agent_name(agent_name)
    roster = _roster(str(thread_id))
    messages = [m.to_dict() for m in roster.get_inbox(name, mark_as_read=mark_as_read)]
    return {"thread_id": str(thread_id), "agent_name": name, "messages": messages, "count": len(messages)}


@router.patch("/{agent_name}/status", summary="Update agent status")
@require_permission("threads", "write", owner_check=True)
async def update_agent_status(thread_id: ThreadId, agent_name: str, body: UpdateStatusRequest) -> dict:
    name = _validate_agent_name(agent_name)
    status = body.status.strip().lower()
    if status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(_VALID_STATUS)}")
    roster = _roster(str(thread_id))
    if name not in [a.name for a in roster.list_agents()]:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in roster.")
    roster.update_status(name, status)  # type: ignore[arg-type]
    return {"thread_id": str(thread_id), "agent_name": name, "status": status}
