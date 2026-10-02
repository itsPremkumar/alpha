"""Built-in tool for multi-agent group chat rooms and consensus deliberation."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.groups.room import OrchestrationMode
from alpha.groups.service import get_group_chat_service


@tool("group_chat", parse_docstring=True)
def group_chat_tool(
    action: Literal["send", "create", "list", "history", "propose_vote", "cast_vote", "tally_vote", "status", "claim", "release"],
    room_name: str = "general",
    sender: str = "user",
    message: str = "",
    members: str = "",
    mode: OrchestrationMode = "mention",
    proposal_id: str = "",
    question: str = "",
    vote: Literal["agree", "disagree", "amend"] | None = None,
    subject: str = "",
    kind: Literal["file", "dir", "symbol", "task", "artifact", "requirement"] = "file",
    claim_id: str = "",
) -> str:
    """Collaborate in multi-agent group chat rooms with adaptive speaker modes and voting.

    Vastly expands on bot mode with 5 speaker selection strategies (mention,
    moderated, quorum, parallel, round_robin) and real-time room auto-provisioning.

    Use 'status' BEFORE you start editing shared code. It reports which agents
    are working, on which files, and which of them stopped. A crashed agent is
    reported as crashed, never as idle, so its unfinished work shows up as
    available to pick up rather than silently held. Use 'claim' to announce the
    file you are about to touch so peers can route around it. Both are advisory:
    'claim' never refuses, it only tells the room.

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
        subject: File path, directory, symbol, or task id being claimed. Required for 'claim'.
        kind: What the subject is ('file', 'dir', 'symbol', 'task', 'artifact', 'requirement'). A 'dir' claim covers everything beneath it.
        claim_id: Which claim to release. Required for 'release'.
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
        return f"Room '{room.name}' active.\n- Mode: `{room.mode}`\n- Members: {', '.join(room.members)}\n- Moderator: @{room.moderator}"

    elif action == "send":
        if not message.strip():
            return "Error: 'message' is required for 'send'."
        msg, next_speakers = service.post_message(
            room_name=room_name,
            sender=sender,
            content=message,
        )
        speaker_str = ", ".join(f"@{s}" for s in next_speakers) if next_speakers else "(none - discussion settled)"
        return f"Message posted to room '{room_name}'.\nSender: {sender}\nNext Scheduled Speaker(s): {speaker_str}"

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

    elif action == "status":
        from alpha.groups.coordination import room_snapshot

        if service.get_room(room_name) is None:
            return f"Room '{room_name}' does not exist. Create it first with action='create'."
        snapshot = room_snapshot(room_name)
        lines = [f"=== Live Activity: {room_name} ==="]
        for agent in snapshot["agents"]:
            held = ", ".join(agent["held_paths"]) if agent["held_paths"] else "-"
            lines.append(f"- @{agent['bot_name']}: {agent['activity']} [{held}] - {agent['detail']}")
        crashed = [a["bot_name"] for a in snapshot["agents"] if a["activity"] == "crashed"]
        if crashed:
            lines.append(f"\nCRASHED: {', '.join(sorted(crashed))}. Their claims are orphaned and available to take over.")
        available = sorted({str(c["subject"]) for c in snapshot["orphaned"]})
        if available:
            lines.append(f"Available to claim: {', '.join(available)}")
        if snapshot["conflicts"]:
            lines.append("\nOverlapping claims:")
            lines.extend(f"- {c['detail']}" for c in snapshot["conflicts"])
        if not snapshot["agents"]:
            lines.append("No members have any recorded work yet.")
        return "\n".join(lines)

    elif action == "claim":
        if not subject.strip():
            return "Error: 'subject' is required for 'claim'."
        from alpha.groups.claims import get_claim_store
        from alpha.groups.coordination import announce_claim

        room = service.get_room(room_name)
        if room is None:
            return f"Room '{room_name}' does not exist."
        try:
            claim = get_claim_store().claim(room_name, sender, kind, subject, project_id=getattr(room, "project_id", None))
        except ValueError as exc:
            return f"Error: {exc}"
        announce_claim(room_name, claim.to_dict())
        return f"@{sender} claimed {claim.subject} ({claim.intent}). Peers are notified. Claims expire in ~120s unless renewed."

    elif action == "release":
        if not claim_id:
            return "Error: 'claim_id' is required for 'release'."
        from alpha.groups.claims import get_claim_store

        if not get_claim_store().release(claim_id, sender):
            return f"Error: claim '{claim_id}' was not found, is already released, or is not held by @{sender}."
        return f"Released claim '{claim_id}'."

    return f"Error: Unknown action '{action}'."
