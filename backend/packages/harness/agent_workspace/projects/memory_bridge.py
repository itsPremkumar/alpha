"""Bridge the group conversation into Level-2 shared project memory.

The gap this closes: ``ThreeLevelContextRouter.get_project_memory`` folds in
state, decisions, locks and the constitution — but **not** what the agents
actually said to each other. So a crew could hold a meeting and the meeting
evaporated: an agent that joined later, or one whose context rolled over, had no
idea what had been agreed.

This module turns the group room transcript into durable shared memory:

- ``transcript_digest``  — the recent conversation, one line per message
- ``transcript_summary`` — a rolling summary of everything compacted away
- ``member_briefs``      — each agent's most recent contribution
- ``open_questions``     — proposals/votes not yet answered by an action
- ``pending_mentions``   — unanswered @mentions, per agent

``pending_mentions`` is what gives ``@name`` real force: an unanswered mention
stays in the agent's queue and is re-injected on its next turn, so "tag and ask"
becomes a work item instead of decoration.

Compaction is deliberately **non-destructive**. When the log outgrows the
configured budget the overflow is summarised into ``context.json`` and the
``room.log`` is left intact — history is preserved, matching the crew layer's
"park, never delete" rule. The digest then reads from the compaction point
onward, so memory stays bounded without destroying the record.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")
_SNIPPET_CHARS = 160
OPEN_INTENTS = ("proposal", "vote")
RESOLVING_INTENTS = ("action", "card_update")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    return (value or "").lower().strip()


def _snippet(text: str, limit: int = _SNIPPET_CHARS) -> str:
    flat = _WHITESPACE.sub(" ", (text or "")).strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _room_for(project_id: str):
    """Return the project's group room, or None (solo projects have no room)."""
    from agent_workspace.groups.service import get_group_chat_service

    rooms = get_group_chat_service().rooms_for_project(project_id)
    return rooms[0] if rooms else None


def _parse_mentions(text: str, members: list[str]) -> list[str]:
    from agent_workspace.groups.orchestration import GroupOrchestrator

    return GroupOrchestrator.parse_mentions(text or "", members)


def _budget(project_id: str) -> int:
    from agent_workspace.projects.crew import get_crew_service

    return get_crew_service().get_collaboration(project_id).transcript_digest_n


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def build_transcript_digest(project_id: str, limit: int | None = None) -> dict[str, Any]:
    """Recent conversation, reading from the compaction point onward."""
    room = _room_for(project_id)
    if room is None:
        return {"messages": [], "total": 0, "compacted_upto": 0, "summary": None}

    keep = limit if limit is not None else _budget(project_id)
    start = int(_compacted_upto(project_id))
    window = room.log[start:]
    recent = window[-keep:] if keep > 0 else []

    return {
        "messages": [
            {
                "from": m.sender,
                "intent": m.intent,
                "text": _snippet(m.content),
                "at": m.created_at,
            }
            for m in recent
        ],
        "total": len(room.log),
        "compacted_upto": start,
        "summary": _compacted_summary(project_id),
    }


def member_briefs(project_id: str) -> dict[str, str]:
    """Each agent's most recent contribution, one line each."""
    room = _room_for(project_id)
    if room is None:
        return {}
    briefs: dict[str, str] = {}
    for msg in room.log:
        briefs[_slug(msg.sender)] = _snippet(msg.content)
    return briefs


def open_questions(project_id: str, limit: int = 5) -> list[dict[str, str]]:
    """Proposals and votes raised after the most recent resolving action."""
    room = _room_for(project_id)
    if room is None:
        return []

    last_resolved = -1
    for idx, msg in enumerate(room.log):
        if msg.intent in RESOLVING_INTENTS:
            last_resolved = idx

    out: list[dict[str, str]] = []
    for msg in room.log[last_resolved + 1 :]:
        if msg.intent in OPEN_INTENTS:
            out.append({"from": msg.sender, "intent": msg.intent, "text": _snippet(msg.content)})
    return out[-limit:]


def pending_mentions(project_id: str) -> dict[str, list[dict[str, str]]]:
    """Unanswered @mentions, per agent.

    A mention is answered when the mentioned agent speaks again; posting clears
    that agent's queue. So the value here is the live "someone is waiting on
    you" set, not the full history of mentions.
    """
    room = _room_for(project_id)
    if room is None:
        return {}

    pending: dict[str, list[dict[str, str]]] = {}
    for msg in room.log:
        sender = _slug(msg.sender)
        if sender in pending:
            del pending[sender]

        for target in _parse_mentions(msg.content, room.members):
            target_key = _slug(target)
            if not target_key or target_key == sender:
                continue
            pending.setdefault(target_key, []).append(
                {
                    "from": msg.sender,
                    "text": _snippet(msg.content),
                    "at": msg.created_at,
                }
            )
    return pending


def build_memory_extras(project_id: str) -> dict[str, Any]:
    """All transcript-derived keys, in the shape Level-2 memory expects."""
    return {
        "transcript_digest": build_transcript_digest(project_id),
        "member_briefs": member_briefs(project_id),
        "open_questions": open_questions(project_id),
        "pending_mentions": pending_mentions(project_id),
    }


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


def _context_json(project_id: str) -> dict[str, Any]:
    """Read `context.json` directly.

    Deliberately *not* ``get_project_memory()``: that now folds in these very
    digests, so routing bookkeeping through it would recurse infinitely.
    """
    from agent_workspace.config.runtime_paths import runtime_home

    path = runtime_home() / "projects" / _slug(project_id) / "context.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.debug("Compaction state unreadable for %s", project_id, exc_info=True)
        return {}


def _compacted_upto(project_id: str) -> int:
    try:
        return int(_context_json(project_id).get("compacted_upto", 0))
    except (TypeError, ValueError):
        return 0


def _compacted_summary(project_id: str) -> str | None:
    value = _context_json(project_id).get("transcript_summary")
    return str(value) if value else None


def _summarise(messages: list[Any]) -> str:
    """Collapse overflow messages into a compact per-sender roll-up."""
    by_sender: dict[str, list[str]] = {}
    for msg in messages:
        by_sender.setdefault(msg.sender, []).append(_snippet(msg.content, 80))

    parts = []
    for sender, lines in by_sender.items():
        parts.append(f"{sender} ({len(lines)}): " + " | ".join(lines[-2:]))
    return " ;; ".join(parts)


def maybe_compact(project_id: str, *, keep: int | None = None) -> dict[str, Any] | None:
    """Summarise transcript overflow into Level-2 memory. Non-destructive.

    Returns a report dict when compaction happened, None when the log was
    already inside budget (or there is no room).
    """
    room = _room_for(project_id)
    if room is None:
        return None

    budget = keep if keep is not None else _budget(project_id)
    start = _compacted_upto(project_id)
    outstanding = len(room.log) - start
    if outstanding <= budget:
        return None

    overflow = room.log[start : len(room.log) - budget]
    if not overflow:
        return None

    from agent_workspace.projects.context_router import get_three_level_router
    from agent_workspace.projects.events import get_event_bus

    previous = _compacted_summary(project_id)
    summary = _summarise(overflow)
    if previous:
        summary = f"{previous} ;; {summary}"

    get_three_level_router().update_project_memory(
        project_id,
        {
            "transcript_summary": summary,
            "compacted_upto": start + len(overflow),
            "compacted_at": _now(),
        },
    )
    get_event_bus(project_id).emit(
        "memory_compacted",
        "supervisor",
        {"messages": len(overflow), "kept": budget},
    )

    return {
        "compacted": len(overflow),
        "kept": budget,
        "compacted_upto": start + len(overflow),
        "summary": summary,
    }
