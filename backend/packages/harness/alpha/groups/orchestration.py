"""Speaker selection and multi-agent group conversation orchestration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from alpha.channels.mentions import MentionResolution
from alpha.channels.mentions import parse_mentions as _parse_mention_grammar
from alpha.groups.room import GroupMessage, GroupRoom


class GroupOrchestrator:
    """Decides which bot(s) speak next based on room orchestration policy."""

    @staticmethod
    def resolve_mentions(
        text: str,
        available_members: Sequence[str],
        *,
        roles: Mapping[str, Iterable[str]] | None = None,
        sender: str | None = None,
    ) -> MentionResolution:
        """The full routing decision for one body of text, from the ONE grammar.

        This used to be a second, weaker grammar living in this module
        (``@([A-Za-z0-9_-]+)``), and the split was silently wrong in five ways
        that all pointed the same direction — at a message that addressed a bot
        nobody named:

        * ``@bot:alice`` did not resolve to ``alice``. The old charset excluded
          ``:``, so the pattern matched the literal ``@bot`` and dropped it as an
          unknown handle. Every *explicitly qualified* tag — the exact spelling
          the frontend composer inserts — was dead on arrival in a group room.
        * ``@role:reviewer`` had no meaning at all; there was no role selector.
        * An unknown handle was dropped with no record, so the operator had no
          way to learn the tag had addressed nobody.
        * There was no fan-out ceiling, so ``@everyone`` in a 200-member room
          answered with 200 handles.
        * ``@everyone`` included the sender, letting an agent address itself.

        `alpha.channels.mentions` already refused all five; this module simply
        was not calling it. Resolution is now delegated, which is what makes the
        frontend's mirror (`frontend/src/lib/agent-mentions.ts`) a mirror of
        *this* path rather than of a grammar nothing served.
        """
        return _parse_mention_grammar(text, roster=available_members, roles=roles, sender=sender)

    @staticmethod
    def parse_mentions(
        text: str,
        available_members: Sequence[str],
        *,
        roles: Mapping[str, Iterable[str]] | None = None,
        sender: str | None = None,
    ) -> list[str]:
        """Extract mentioned member handles from message text.

        The flat handle list is the shape the speaker strategies consume. Callers
        that need to say *why* a tag resolved to nobody — the composer does —
        use `resolve_mentions` and read `unresolved` instead.
        """
        return list(GroupOrchestrator.resolve_mentions(text, available_members, roles=roles, sender=sender).resolved_handles)

    def resolve_next_speakers(
        self,
        room: GroupRoom,
        last_message: GroupMessage | None = None,
        *,
        roles: Mapping[str, Iterable[str]] | None = None,
    ) -> list[str]:
        """Determine next speakers according to room's active orchestration mode."""
        if not room.members:
            return []

        mode = room.mode
        # The sender is excluded from their own fan-out: an agent addressing
        # `@everyone` must not schedule itself to answer its own post.
        author = last_message.sender if last_message else None

        # 1. Mention-driven mode (default)
        if mode == "mention":
            if not last_message:
                return [room.members[0]]
            mentions = self.parse_mentions(last_message.content, room.members, roles=roles, sender=author)
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
                invited = self.parse_mentions(last_message.content, room.members, roles=roles, sender=author)
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
