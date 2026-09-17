"""Multi-Agent Group Chat Room data models and persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

OrchestrationMode = Literal["mention", "moderated", "quorum", "parallel", "round_robin"]
MessageIntent = Literal["discussion", "proposal", "vote", "action", "pass", "card_update"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class GroupMessage:
    """A single message entry in a group chat room."""

    id: str
    sender: str
    content: str
    intent: MessageIntent = "discussion"
    mentions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupMessage:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class GroupRoom:
    """A multi-agent group conversation room."""

    room_id: str
    name: str
    topic: str = "General Team Collaboration"
    members: list[str] = field(default_factory=list)
    mode: OrchestrationMode = "mention"
    moderator: str | None = None
    kanban_board_id: str | None = None
    project_id: str | None = None
    log: list[GroupMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def append_message(
        self,
        sender: str,
        content: str,
        *,
        intent: MessageIntent = "discussion",
        mentions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GroupMessage:
        msg = GroupMessage(
            id=f"msg_{uuid4().hex[:8]}",
            sender=sender,
            content=content,
            intent=intent,
            mentions=mentions or [],
            metadata=metadata or {},
        )
        self.log.append(msg)
        self.updated_at = _now()
        return msg

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["log"] = [m.to_dict() for m in self.log]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupRoom:
        raw_log = data.pop("log", [])
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        room = cls(**filtered)
        room.log = [GroupMessage.from_dict(m) for m in raw_log]
        return room
