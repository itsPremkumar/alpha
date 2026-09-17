"""Direct Agent-to-Agent Messaging and Family Roster (inspired by Prime Agent).

Enables running subagents to discover peers, observe their status, and exchange
direct messages without routing every interaction through the user.
Supports delivery modes: 'auto', 'steer', and 'follow_up'.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

DeliveryMode = Literal["auto", "steer", "follow_up"]
AgentStatus = Literal["idle", "busy", "completed"]
MessageStatus = Literal["queued", "delivered", "read"]

#: Structured message kinds (inventory #12). Machine-operable types so agents
#: can route on intent instead of parsing prose. ``message`` is the default
#: free-form kind; the rest label the envelope, never the content schema.
MESSAGE_KINDS: tuple[str, ...] = (
    "message",
    "request",
    "task_assignment",
    "task_handoff",
    "question",
    "answer",
    "status",
    "progress",
    "warning",
    "incident",
    "recovery",
    "decision",
    "approval",
    "escalation",
    "result",
    "review",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AgentDescriptor:
    """Description of an active agent or subagent in the family roster."""

    agent_id: str
    name: str
    role: str = "worker"
    status: AgentStatus = "idle"
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InterAgentMessage:
    """A direct communication message between agents."""

    id: str
    sender_name: str
    receiver_name: str
    content: str
    mode: DeliveryMode = "auto"
    status: MessageStatus = "queued"
    kind: str = "message"
    created_at: str = field(default_factory=_now)
    delivered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentRoster:
    """Thread-scoped family roster and inter-agent mailbox."""

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self._agents: dict[str, AgentDescriptor] = {}
        self._mailboxes: dict[str, list[InterAgentMessage]] = {}

    def register_agent(
        self,
        name: str,
        role: str = "worker",
        agent_id: str | None = None,
        status: AgentStatus = "idle",
        metadata: dict[str, Any] | None = None,
    ) -> AgentDescriptor:
        aid = agent_id or f"{name}_{uuid4().hex[:6]}"
        desc = AgentDescriptor(
            agent_id=aid,
            name=name,
            role=role,
            status=status,
            metadata=metadata or {},
        )
        self._agents[name] = desc
        if name not in self._mailboxes:
            self._mailboxes[name] = []
        return desc

    def update_status(self, name: str, status: AgentStatus) -> None:
        if name in self._agents:
            self._agents[name].status = status

    def deregister_agent(self, name: str) -> None:
        if name in self._agents:
            self._agents[name].status = "completed"

    def list_agents(self) -> list[AgentDescriptor]:
        return list(self._agents.values())

    def send_message(
        self,
        sender_name: str,
        receiver_name: str,
        content: str,
        mode: DeliveryMode = "auto",
        kind: str = "message",
    ) -> dict[str, Any]:
        """Send a direct message to a peer agent or broadcast to 'all'."""
        if kind not in MESSAGE_KINDS:
            return {
                "status": "error",
                "error": f"Unknown message kind '{kind}'. Expected one of {list(MESSAGE_KINDS)}.",
                "available_kinds": list(MESSAGE_KINDS),
            }
        msg_id = f"msg_{uuid4().hex[:8]}"

        targets: list[str] = []
        if receiver_name == "all":
            targets = [n for n in self._agents.keys() if n != sender_name]
        else:
            if receiver_name not in self._agents:
                return {
                    "status": "error",
                    "error": f"Agent '{receiver_name}' not found in current roster.",
                    "available_agents": [a.name for a in self.list_agents()],
                }
            targets = [receiver_name]

        receipts: list[dict[str, Any]] = []
        for target in targets:
            target_agent = self._agents[target]
            # Determine delivery status based on target state and mode
            # auto: steer if busy, deliver immediately if idle
            if mode == "auto":
                delivery_status: MessageStatus = "delivered" if target_agent.status == "idle" else "queued"
            elif mode == "steer":
                delivery_status = "delivered"
            else:  # follow_up
                delivery_status = "queued"

            msg = InterAgentMessage(
                id=msg_id,
                sender_name=sender_name,
                receiver_name=target,
                content=content,
                mode=mode,
                status=delivery_status,
                kind=kind,
                delivered_at=_now() if delivery_status == "delivered" else None,
            )

            if target not in self._mailboxes:
                self._mailboxes[target] = []
            self._mailboxes[target].append(msg)

            receipts.append(
                {
                    "message_id": msg.id,
                    "receiver": target,
                    "delivery_status": delivery_status,
                    "mode": mode,
                }
            )

        return {
            "status": "ok",
            "receipts": receipts,
        }

    def get_inbox(self, agent_name: str, mark_as_read: bool = True) -> list[InterAgentMessage]:
        """Fetch all messages for an agent."""
        box = self._mailboxes.get(agent_name, [])
        unread = [m for m in box if m.status != "read"]
        if mark_as_read:
            for m in unread:
                m.status = "read"
        return unread


_rosters: dict[str, AgentRoster] = {}


def get_agent_roster(thread_id: str | None = None) -> AgentRoster:
    """Retrieve or construct the thread-scoped AgentRoster."""
    tid = thread_id or "default_thread"
    if tid not in _rosters:
        roster = AgentRoster(tid)
        # Register the lead agent by default
        roster.register_agent(name="lead_agent", role="orchestrator", status="busy")
        _rosters[tid] = roster
    return _rosters[tid]
