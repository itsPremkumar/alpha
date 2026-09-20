"""Group communication mesh, dynamic sub-groups, and @mention routing for autonomous organizations."""

from __future__ import annotations

import logging
import re
import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Matches @bot-name or @bot_name or @username
MENTION_REGEX = re.compile(r"@([a-zA-Z0-9_\-]+)")


class GroupMessageType(StrEnum):
    CHAT = "chat"
    BROADCAST = "broadcast"
    SYSTEM_NOTICE = "system_notice"
    STANDUP_DIGEST = "standup_digest"
    MEDIC_ALERT = "medic_alert"


class GroupMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    channel_id: str
    sender_bot: str
    content: str
    mentions: list[str] = Field(default_factory=list)
    message_type: GroupMessageType = GroupMessageType.CHAT
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class GroupChannel(BaseModel):
    channel_id: str
    name: str
    description: str = ""
    member_bot_names: list[str] = Field(default_factory=list)
    is_default: bool = False
    created_by: str = "system"
    created_at: float = Field(default_factory=time.time)


class GroupChatEngine:
    """Manages company group channels, dynamic sub-groups, and @mention routing without bot-count limits."""

    def __init__(self, org_id: str = "org-default"):
        self.org_id = org_id
        # channel_id -> GroupChannel
        self._channels: dict[str, GroupChannel] = {}
        # channel_id -> list[GroupMessage]
        self._messages: dict[str, list[GroupMessage]] = {}
        # Message count caps per channel to prevent runaway loops
        self._max_channel_history = 500

    def create_channel(
        self,
        channel_id: str,
        name: str,
        member_bot_names: list[str] | None = None,
        description: str = "",
        is_default: bool = False,
        created_by: str = "system",
    ) -> GroupChannel:
        """Creates a new channel or sub-group."""
        clean_id = channel_id.strip().lower().replace(" ", "-")
        members = list(set(member_bot_names or []))
        channel = GroupChannel(
            channel_id=clean_id,
            name=name,
            description=description,
            member_bot_names=members,
            is_default=is_default,
            created_by=created_by,
        )
        self._channels[clean_id] = channel
        if clean_id not in self._messages:
            self._messages[clean_id] = []
        logger.info(f"Created group channel '{clean_id}' ('{name}') with {len(members)} bots (default={is_default})")
        return channel

    def get_channel(self, channel_id: str) -> GroupChannel | None:
        return self._channels.get(channel_id.strip().lower())

    def list_channels(self) -> list[GroupChannel]:
        return list(self._channels.values())

    def add_member(self, channel_id: str, bot_name: str) -> bool:
        channel = self.get_channel(channel_id)
        if not channel:
            return False
        if bot_name not in channel.member_bot_names:
            channel.member_bot_names.append(bot_name)
        return True

    def remove_member(self, channel_id: str, bot_name: str) -> bool:
        channel = self.get_channel(channel_id)
        if not channel:
            return False
        if bot_name in channel.member_bot_names:
            channel.member_bot_names.remove(bot_name)
            return True
        return False

    def post_message(
        self,
        channel_id: str,
        sender_bot: str,
        content: str,
        message_type: GroupMessageType = GroupMessageType.CHAT,
        metadata: dict[str, Any] | None = None,
    ) -> GroupMessage:
        """Posts a message to a channel, automatically extracting @mentions."""
        clean_id = channel_id.strip().lower()
        channel = self.get_channel(clean_id)
        if not channel:
            raise KeyError(f"Channel '{clean_id}' does not exist.")

        # Ensure sender is a recognized member (or system)
        if sender_bot != "system" and sender_bot not in channel.member_bot_names:
            channel.member_bot_names.append(sender_bot)

        # Extract @mentions
        extracted_mentions = list(set(MENTION_REGEX.findall(content)))

        msg = GroupMessage(
            channel_id=clean_id,
            sender_bot=sender_bot,
            content=content,
            mentions=extracted_mentions,
            message_type=message_type,
            metadata=metadata or {},
        )

        history = self._messages.setdefault(clean_id, [])
        history.append(msg)
        if len(history) > self._max_channel_history:
            self._messages[clean_id] = history[-self._max_channel_history :]

        logger.debug(f"[{clean_id}] @{sender_bot}: {content[:60]} (Mentions: {extracted_mentions})")
        return msg

    def broadcast(
        self,
        sender_bot: str,
        content: str,
        target_channel_id: str = "all-hands",
    ) -> GroupMessage:
        """Company-wide broadcast notice to all members."""
        channel = self.get_channel(target_channel_id)
        if not channel:
            # Fallback to first default channel or create all-hands
            defaults = [c for c in self._channels.values() if c.is_default]
            if defaults:
                target_channel_id = defaults[0].channel_id
            else:
                self.create_channel(
                    channel_id="all-hands",
                    name="#Company-All-Hands",
                    is_default=True,
                )
                target_channel_id = "all-hands"

        return self.post_message(
            channel_id=target_channel_id,
            sender_bot=sender_bot,
            content=content,
            message_type=GroupMessageType.BROADCAST,
            metadata={"is_broadcast": True},
        )

    def get_channel_history(self, channel_id: str, limit: int = 50) -> list[GroupMessage]:
        clean_id = channel_id.strip().lower()
        history = self._messages.get(clean_id, [])
        return history[-limit:]

    def get_mentions_for_bot(self, bot_name: str, limit: int = 20) -> list[GroupMessage]:
        """Returns recent messages where this bot was explicitly tagged."""
        clean_name = bot_name.lstrip("@").lower()
        results: list[GroupMessage] = []
        for channel_msgs in self._messages.values():
            for msg in reversed(channel_msgs):
                if any(m.lower() == clean_name or m.lower() == "all" for m in msg.mentions):
                    results.append(msg)
                    if len(results) >= limit:
                        return results
        return results
