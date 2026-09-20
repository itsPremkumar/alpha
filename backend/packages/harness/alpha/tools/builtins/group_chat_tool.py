"""Built-in tool for multi-agent group chat rooms and consensus deliberation."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.groups.room import OrchestrationMode
from alpha.groups.service import get_group_chat_service


@tool("group_chat", parse_docstring=True)
def group_chat_tool(
    action: Literal["send", "create", "list", "history", "propose_vote", "cast_vote", "tally_vote"],
    room_name: str = "general",
    sender: str = "user",
    message: str = "",
    members: str = "",
    mode: OrchestrationMode = "mention",
    proposal_id: str = "",
    question: str = "",
    vote: Literal["agree", "disagree", "amend"] | None = None,
) -> str:
    """Collaborate in multi-agent group chat rooms with adaptive speaker modes and voting.

    Vastly expands on Hermes Bot Mode with 5 speaker selection strategies (mention,
    moderated, quorum, parallel, round_robin) and real-time room auto-provisioning.

    Args:
        action: Operation ('send', 'create', 'list', 'history', 'propose_vote', 'cast_vote', 'tally_vote').
        room_name: Group room name (e.g. 'core-team', 'arch-review'). Auto-created if missing. Defaults to 'general'.
        sender: Sending bot handle or 'user'. Defaults to 'user'.
        message: Message text to post. Supports @bot, @everyone, and (pass). Required for 'send'.
        members: Comma-separated member handles to enroll when creating a room (e.g. 'architect,coder,reviewer').
        mode: Speaker mode ('mention', 'moderated', 'quorum', 'parallel', 'round_robin'). Defaults to 'mention'.
        proposal_id: Proposal identifier (for 'cast_vote' and 'tally_vote').
        question: Question/proposal for consensus voting (required for 'propose_vote').
        vote: Vote choice ('agree', 'disagree', 'amend'). Required for 'cast_vote'.
    """
    service = get_group_chat_service()

    if action == "list":
        rooms = service.list_rooms()
        if not rooms:
            return "No active group chat rooms."
        out = ["=== Active Multi-Agent Group Chat Rooms ==="]
        for r in rooms:
            out.append(f"- **{r.name}** [Mode: `{r.mode}`] Members: {', '.join(r.members)} (Log count: {len(r.log)})")
        return "\n".join(out)

    elif action == "create":
        member_list = [m.strip() for m in members.split(",") if m.strip()] if members else None
        room = service.get_or_create_room(name=room_name, members=member_list, mode=mode)
        return (
            f"Room '{room.name}' active.\n"
            f"- Mode: `{room.mode}`\n"
            f"- Members: {', '.join(room.members)}\n"
            f"- Moderator: @{room.moderator}"
        )

    elif action == "send":
        if not message.strip():
            return "Error: 'message' is required for 'send'."
        msg, next_speakers = service.post_message(
            room_name=room_name,
            sender=sender,
            content=message,
        )
        speaker_str = ", ".join(f"@{s}" for s in next_speakers) if next_speakers else "(none - discussion settled)"
        return (
            f"Message posted to room '{room_name}'.\n"
            f"Sender: {sender}\n"
            f"Next Scheduled Speaker(s): {speaker_str}"
        )

    elif action == "history":
        room = service.get_room(room_name)
        if not room or not room.log:
            return f"No messages in room '{room_name}' yet."
        out = [f"=== Recent Messages in '{room.name}' (Last 10) ==="]
        for m in room.log[-10:]:
            out.append(f"[{m.created_at[:19]}] **{m.sender}**: {m.content}")
        return "\n".join(out)

    elif action == "propose_vote":
        if not question:
            return "Error: 'question' is required for 'propose_vote'."
        room = service.get_or_create_room(room_name)
        proposal = service.quorum.create_proposal(room.room_id, proposer=sender, question=question)
        service.post_message(
            room_name=room_name,
            sender=sender,
            content=f"🗳️ [PROPOSAL SUBMITTED] ID: `{proposal.proposal_id}`\nQuestion: {question}\nMembers, please cast your vote!",
            intent="proposal",
        )
        return f"Proposal created successfully. Proposal ID: {proposal.proposal_id}"

    elif action == "cast_vote":
        if not proposal_id or not vote:
            return "Error: 'proposal_id' and 'vote' are required for 'cast_vote'."
        res = service.quorum.cast_vote(proposal_id, voter=sender, choice=vote)
        if res.get("status") == "error":
            return f"Error: {res.get('error')}"
        return f"Vote recorded for @{sender}: {vote} on proposal {proposal_id}."

    elif action == "tally_vote":
        if not proposal_id:
            return "Error: 'proposal_id' is required for 'tally_vote'."
        room = service.get_room(room_name)
        voters_count = len(room.members) if room else 3
        tally = service.quorum.tally(proposal_id, total_eligible_voters=voters_count)
        return (
            f"=== Proposal Tally: {proposal_id} ===\n"
            f"Status: {tally.get('status')}\n"
            f"Agree: {tally.get('agree')} | Disagree: {tally.get('disagree')} | Amend: {tally.get('amend')}\n"
            f"Total votes cast: {tally.get('total_votes')}/{tally.get('eligible')} (Ratio: {tally.get('ratio'):.1%})"
        )

    return f"Error: Unknown action '{action}'."
