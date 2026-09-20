"""Speaker selection and multi-agent group conversation orchestration."""

from __future__ import annotations

import re
from collections.abc import Sequence

from alpha.groups.room import GroupMessage, GroupRoom

_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_-]+)")


class GroupOrchestrator:
    """Decides which bot(s) speak next based on room orchestration policy."""

    @staticmethod
    def parse_mentions(text: str, available_members: Sequence[str]) -> list[str]:
        """Extract mentioned member handles from message text."""
        member_map = {m.lower(): m for m in available_members}
        found: list[str] = []
        is_everyone = False

        for match in _MENTION_PATTERN.finditer(text):
            raw_handle = match.group(1).lower()
            if raw_handle in ("all", "everyone"):
                is_everyone = True
                continue
            if raw_handle in member_map:
                found.append(member_map[raw_handle])

        if is_everyone:
            return list(available_members)
        return list(dict.fromkeys(found))

    def resolve_next_speakers(
        self,
        room: GroupRoom,
        last_message: GroupMessage | None = None,
    ) -> list[str]:
        """Determine next speakers according to room's active orchestration mode."""
        if not room.members:
            return []

        mode = room.mode

        # 1. Mention-driven mode (default)
        if mode == "mention":
            if not last_message:
                return [room.members[0]]
            mentions = self.parse_mentions(last_message.content, room.members)
            if mentions:
                return mentions
            # If no mentions and message from user, invite first member or moderator
            if last_message.sender == "user":
                return [room.moderator or room.members[0]]
            return []

        # 2. Moderated Lead mode
        elif mode == "moderated":
            moderator = room.moderator or room.members[0]
            if not last_message or last_message.sender != moderator:
                # Specialist spoke, return to moderator for synthesis/next dispatch
                return [moderator]
            else:
                # Moderator spoke: parse whom the moderator invited or default to next member
                invited = self.parse_mentions(last_message.content, room.members)
                specialists = [m for m in invited if m != moderator]
                if specialists:
                    return specialists
                # Default to first non-moderator member
                others = [m for m in room.members if m != moderator]
                return [others[0]] if others else [moderator]

        # 3. Consensus Quorum mode
        elif mode == "quorum":
            # All members except the proposer/speaker deliberate
            exclude = last_message.sender if last_message else ""
            return [m for m in room.members if m != exclude]

        # 4. Parallel Brainstorming mode
        elif mode == "parallel":
            exclude = last_message.sender if last_message else ""
            return [m for m in room.members if m != exclude]

        # 5. Smart Round-Robin mode
        elif mode == "round_robin":
            if not last_message or last_message.sender not in room.members:
                return [room.members[0]]
            idx = room.members.index(last_message.sender)
            next_idx = (idx + 1) % len(room.members)
            return [room.members[next_idx]]

        return []
