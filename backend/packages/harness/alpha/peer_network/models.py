"""Typed, bounded contracts for the local-first Alpha peer network.

The peer network deliberately keeps its wire format small and boring: JSON over
HTTP or WebSocket, with an optional UDP discovery beacon.  The envelope is
aligned with the A2A concepts (Agent Card, Message, Task-like request/result
kinds) without pretending to implement every A2A SDK feature.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

PROTOCOL = "alpha-a2a"
PROTOCOL_VERSION = "1.0"
CARD_TYPE = "application/alpha-peer-card+json"
ENVELOPE_MEDIA_TYPE = "application/alpha-a2a+json"

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MESSAGE_KIND_VALUES = {
    "chat",
    "hello",
    "capabilities",
    "ping",
    "pong",
    "task_request",
    "task_accept",
    "task_reject",
    "task_progress",
    "task_result",
    "status",
    "question",
    "answer",
    "review_request",
    "review_result",
    "file_offer",
    "file_request",
    "receipt",
    "goodbye",
}

ConversationMode = Literal[
    "direct",
    "one_to_many",
    "many_to_one",
    "many_to_many",
    "broadcast",
    "inbox",
]
MessageStatus = Literal["queued", "delivered", "read", "failed"]
PeerTrust = Literal["discovered", "paired", "blocked"]


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp used by every network record."""

    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    """Create a short, URL-safe record id."""

    return f"{prefix}_{uuid4().hex}"


def validate_agent_id(value: str) -> str:
    cleaned = value.strip()
    if not _AGENT_ID_RE.fullmatch(cleaned):
        raise ValueError("agent_id must be 1-128 characters using letters, digits, '_', '.', ':', or '-'")
    return cleaned


class PeerCard(BaseModel):
    """Self-description exchanged during discovery and pairing.

    ``url`` and the transport fields make the card useful to ordinary A2A
    clients, while the ``alpha_*`` fields preserve Alpha's discovery metadata.
    No credential or private file path is allowed in this model.
    """

    model_config = {"extra": "ignore"}

    protocol: str = PROTOCOL
    protocol_version: str = PROTOCOL_VERSION
    card_type: str = CARD_TYPE
    agent_id: str
    name: str = Field(default="Alpha", min_length=1, max_length=120)
    description: str = Field(default="Alpha autonomous agent", max_length=1000)
    version: str = Field(default="unknown", max_length=64)
    url: str = Field(..., max_length=2048)
    websocket_url: str | None = Field(default=None, max_length=2048)
    preferred_transport: str = Field(default="HTTP", max_length=32)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    skills: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    supports: list[str] = Field(default_factory=lambda: ["http", "websocket", "udp-discovery"], max_length=32)
    pairing_required: bool = True
    issued_at: str = Field(default_factory=utc_now)
    alpha_instance: str = Field(default="alpha", max_length=64)

    @field_validator("agent_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return validate_agent_id(value)

    @field_validator("url", "websocket_url")
    @classmethod
    def _validate_url_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or not value.startswith(("http://", "https://", "ws://", "wss://")):
            raise ValueError("peer endpoints must use http(s) or ws(s)")
        if any(ch in value for ch in ("\n", "\r", "\t")):
            raise ValueError("peer endpoint contains control characters")
        return value

    def discovery_dict(self) -> dict[str, Any]:
        """Return the bounded beacon representation (never secrets)."""

        return {
            "type": "alpha-discovery",
            "protocol": self.protocol,
            "protocol_version": self.protocol_version,
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "url": self.url,
            "websocket_url": self.websocket_url,
            "preferred_transport": self.preferred_transport,
            "capabilities": self.capabilities[:64],
            "supports": self.supports[:32],
            "pairing_required": self.pairing_required,
            "issued_at": self.issued_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PeerEnvelope(BaseModel):
    """A single bounded message exchanged between Alpha instances."""

    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=lambda: new_id("msg"), min_length=1, max_length=128)
    protocol: str = PROTOCOL
    protocol_version: str = PROTOCOL_VERSION
    kind: str = Field(default="chat", min_length=1, max_length=32)
    sender_id: str
    recipients: list[str] = Field(default_factory=list, max_length=50)
    conversation_id: str | None = Field(default=None, max_length=128)
    text: str = Field(default="", max_length=20000)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    reply_to: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=256)

    @field_validator("sender_id")
    @classmethod
    def _validate_sender(cls, value: str) -> str:
        return validate_agent_id(value)

    @field_validator("recipients")
    @classmethod
    def _validate_recipients(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for recipient in value:
            normalized = validate_agent_id(recipient)
            if normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in MESSAGE_KIND_VALUES:
            raise ValueError(f"kind must be one of {sorted(MESSAGE_KIND_VALUES)}")
        return normalized

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PeerPairRequest(BaseModel):
    """Pairing request sent to a remote Alpha peer.

    The shared pairing code is deliberately not placed in an Agent Card.  It
    is supplied out of band by the operator, and the remote side validates it
    before registering the caller.
    """

    model_config = {"extra": "forbid"}

    card: PeerCard
    pairing_code: str = Field(min_length=16, max_length=256)
    requested_at: str = Field(default_factory=utc_now)


class PeerPairResponse(BaseModel):
    accepted: bool
    peer: PeerCard | None = None
    message: str
    paired_at: str | None = None


class PeerStatus(BaseModel):
    """Serializable status used by the Gateway and the network UI."""

    enabled: bool
    identity: dict[str, Any]
    discovery: dict[str, Any]
    transports: dict[str, Any]
    persistence: dict[str, Any]
    pairing_code: str | None = None
    limits: dict[str, int]


class ConversationCreateRequest(BaseModel):
    """Validated local conversation topology request."""

    model_config = {"extra": "forbid"}

    title: str = Field(default="Alpha peer conversation", max_length=200)
    mode: ConversationMode = "direct"
    participants: list[str] = Field(..., min_length=1, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageCreateRequest(BaseModel):
    """Validated local outbound message request."""

    model_config = {"extra": "forbid"}

    conversation_id: str | None = Field(default=None, max_length=128)
    sender_id: str | None = Field(default=None, max_length=128)
    recipients: list[str] = Field(default_factory=list, max_length=50)
    kind: str = Field(default="chat", min_length=1, max_length=32)
    text: str = Field(default="", max_length=20000)
    payload: dict[str, Any] = Field(default_factory=dict)
    mode: ConversationMode | None = None
    title: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, max_length=256)


class PairRequest(BaseModel):
    """Local operator request to pair with a manually supplied endpoint."""

    model_config = {"extra": "forbid"}

    endpoint: str = Field(..., min_length=8, max_length=2048)
    pairing_code: str = Field(..., min_length=16, max_length=256)
    expected_agent_id: str | None = Field(default=None, max_length=128)


class TrustRequest(BaseModel):
    trust: PeerTrust


__all__ = [
    "CARD_TYPE",
    "ConversationCreateRequest",
    "ConversationMode",
    "ENVELOPE_MEDIA_TYPE",
    "MESSAGE_KIND_VALUES",
    "MessageCreateRequest",
    "MessageStatus",
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "PairRequest",
    "PeerCard",
    "PeerEnvelope",
    "PeerPairRequest",
    "PeerPairResponse",
    "PeerStatus",
    "PeerTrust",
    "TrustRequest",
    "new_id",
    "utc_now",
    "validate_agent_id",
]
