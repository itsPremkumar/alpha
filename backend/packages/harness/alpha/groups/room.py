"""Multi-Agent Group Chat Room data models and persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args
from uuid import uuid4

OrchestrationMode = Literal["mention", "moderated", "quorum", "parallel", "round_robin"]

#: Lifecycle. `draft` is created-but-never-posted-to and stays out of the
#: sidebar; `parked` means below the runnable membership threshold with history
#: kept; `dissolved` is squad-only. See `alpha.groups.scope` for the legal
#: transitions — a room never moves between these freely.
RoomLifecycle = Literal["draft", "active", "parked", "archived", "dissolved"]

#: The canonical intent vocabulary.
#:
#: The first six are the harness-native intents (``alpha.kanban.bridge`` writes
#: ``card_update``, ``alpha.projects.memory_bridge`` folds ``action`` and
#: ``card_update`` into shared memory). The rest are the *typed A2A layer* the
#: operator composer offers.
#:
#: This used to be the six-value set only, while the composer's dropdown was
#: driven by a separate, larger client list. The two never agreed, so seven of
#: the thirteen kinds the UI offered were rejected by
#: ``POST /api/groups/{name}/messages`` with a 422 — including ``decision``,
#: which is what "Post as group decision" sends. The vocabulary is now one
#: set; a *new* kind must be added here and picked up from the wire, never
#: declared a second time in a client.
MessageIntent = Literal[
    # harness-native
    "discussion",
    "proposal",
    "vote",
    "action",
    "pass",
    "card_update",
    # typed A2A layer
    "question",
    "answer",
    "request",
    "status",
    "handoff",
    "decision",
    "blocker",
    "warning",
    "approval_request",
    "escalation",
    "task_assignment",
    "task_completion",
]

#: Every intent the room accepts, in the order a picker should offer them.
VALID_INTENTS: tuple[str, ...] = get_args(MessageIntent)

#: Reaction emoji the room stores. Bounded and small on purpose: this is a
#: fixed vocabulary a client may render, not free text.
REACTION_EMOJI: tuple[str, ...] = ("👍", "👎", "🎉", "🚀", "👀", "❤️", "🔥", "🤔")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class GroupMessage:
    """A single message entry in a group chat room.

    Every field added after ``created_at`` defaults, so a room persisted by an
    older build round-trips through ``from_dict`` unchanged.
    """

    id: str
    sender: str
    content: str
    intent: MessageIntent = "discussion"
    mentions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    # ── Message features ────────────────────────────────────────────────
    #: Set when the content was edited in place; ``None`` on an original.
    edited_at: str | None = None
    #: Soft delete. The row stays so replies and reactions keep a target, but
    #: ``content`` is what a client must render as withheld.
    deleted: bool = False
    #: emoji -> the actors who reacted, in click order.
    reactions: dict[str, list[str]] = field(default_factory=dict)
    #: id of the message this one replies to, or ``None``.
    reply_to: str | None = None
    #: ``{"room": ..., "sender": ...}`` when this message was forwarded here.
    forwarded_from: dict[str, str] | None = None

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
    # ── Nesting ──────────────────────────────────────────────────────────
    #: Room ids this room is nested under. May be several: *visibility* fans out.
    #: The single policy owner is `authority_parent`, which lives on the room's
    #: `GroupScope` — see `alpha.groups.scope` for why authority is at most one.
    parent_ids: list[str] = field(default_factory=list)
    #: One-line free-text description of the whole group, shown when a subgroup
    #: is collapsed. Distinct from `topic`, which is the room's working subject.
    summary: str = ""
    #: `draft` until the room is used, then `active`. Mirrors `GroupScope.state`
    #: so a plain room read can filter drafts without loading every scope.
    lifecycle: RoomLifecycle = "active"

    def append_message(
        self,
        sender: str,
        content: str,
        *,
        intent: MessageIntent = "discussion",
        mentions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        reply_to: str | None = None,
        forwarded_from: dict[str, str] | None = None,
    ) -> GroupMessage:
        msg = GroupMessage(
            id=f"msg_{uuid4().hex[:8]}",
            sender=sender,
            content=content,
            intent=intent,
            mentions=mentions or [],
            metadata=metadata or {},
            reply_to=reply_to,
            forwarded_from=forwarded_from,
        )
        self.log.append(msg)
        self.updated_at = _now()
        return msg

    def find_message(self, message_id: str) -> GroupMessage | None:
        """The message with this id, or ``None``.

        Ids are compared whole and case-folded. A substring match would let
        ``msg_ab`` edit ``msg_abcdef`` — the same class of bug the presence
        dot had, where a partial match stands in for a real identity.
        """
        key = (message_id or "").strip().lower()
        if not key:
            return None
        for msg in self.log:
            if msg.id.strip().lower() == key:
                return msg
        return None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["log"] = [m.to_dict() for m in self.log]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupRoom:
        # Copy before popping: `_load` hands this the dict it just parsed, and
        # mutating the caller's mapping means a second reader of that same
        # object silently sees a room with no log.
        raw_log = data.get("log", [])
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        room = cls(**filtered)
        room.log = [GroupMessage.from_dict(dict(m)) for m in raw_log]
        return room
