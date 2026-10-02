"""Automatic file-claim acquisition at the write path.

## Why this module exists

`groups/claims.py` shipped the right *schema* — a `WorkClaim` carries a holder,
a subject, an intent, a lease, and an orphaned state — and every external
implementation of this idea agrees on how it must be acquired: **at the write,
automatically, not by asking the agent to remember.**

The pattern is a `PreToolUse` hook on the edit tool. On every write:

    check for a live claim on the same subject -> if one belongs to someone
    else, say so; then claim the subject for this session

The obvious alternative — leaving it to the agent to call `group_chat action=claim`
— is what this repository had, and it does not work: an agent that forgets
collides anyway, and forgetting is precisely what happens when two agents are
running at once. Advisory coordination only prevents the collisions somebody
thinks to avoid.

So the acquisition moves to the one place both write tools already converge.
`ReadBeforeWriteMiddleware` wraps **exactly** `{write_file, str_replace}` and
already holds the path and a per-`(scope, path)` lock, so it is the natural
insertion point — one place, both tools, no new interception layer.

## Three properties this must keep

**Warn, never block.** A claim is *intent*; `projects/locks.py` owns refusal,
where a wrong block already has a reviewed shape (HTTP 423 + holder). Blocking
here would mean the mechanism that draws a status dot could also lose work, and a
bad heartbeat would then read as a lost edit.

**Fail open, silently.** No room binding, no bot identity, or any store error
means exactly today's behaviour. A coordination feature that can fail a write is
worse than no coordination feature.

**Announce nothing to the model that did not happen.** The warning is appended to
the result of the call that actually ran, so a model reading its transcript sees
"someone else is on this file" attached to the write it really performed.

## Identity

The claim is recorded against a room and a bot, both taken from **server-owned
runtime context** — never from tool arguments, which the model controls. A run
with no room binding records nothing, which is the correct answer: it is a real
write with no known crew, and guessing a room would put one project's file row in
an unrelated room.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Runtime context keys consulted, in precedence order. `bot_name` is what the
#: lead runtime stamps for a roster bot; the others are the fallbacks the
#: activity ledger already accepts, so one run cannot be attributed two ways.
_BOT_KEYS = ("bot_name", "agent_name", "member")
_ROOM_KEYS = ("room_name", "group_room", "coordination_room")

#: Bounded so a pathological conflict set cannot bloat a tool result.
_MAX_WARNINGS = 3


@dataclass
class WriteCoordinationContext:
    """Who is writing, and which room they are writing in."""

    bot_name: str
    room_name: str
    project_id: str | None = None
    run_id: str | None = None


@dataclass
class WriteCoordination:
    """What this write should tell the model about.

    `claim_id` is `None` when nothing was recorded, which is the common case for
    an ordinary single-agent run.
    """

    claim_id: str | None = None
    subject: str = ""
    #: Holders of *other* live claims overlapping this subject.
    conflict_holders: list[str] = field(default_factory=list)
    #: True when one of those holders is confirmed dead, so the file is free.
    reclaimable: bool = False
    dead_holder: str | None = None

    @property
    def conflicted(self) -> bool:
        return bool(self.conflict_holders)

    @property
    def warned(self) -> bool:
        return False  # set by warning_text; kept explicit for callers


def _lookup(container: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(container, dict):
        return None
    for key in keys:
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def write_coordination_context(request: Any) -> WriteCoordinationContext | None:
    """Resolve the writing agent's room + identity, or `None`.

    `None` is the answer for an ordinary run and means "behave exactly as you
    did before this module existed". It is not an error and not a fallback to a
    default room.
    """
    try:
        runtime = getattr(request, "runtime", None)
        state = getattr(request, "state", None)

        sources: list[Any] = []
        configurable = state.get("configurable") if isinstance(state, dict) else None
        if isinstance(configurable, dict):
            sources.append(configurable)
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            sources.append(context)
        if isinstance(state, dict):
            sources.append(state)

        bot_name = room_name = None
        project_id = None
        run_id = None
        for source in sources:
            if bot_name is None:
                bot_name = _lookup(source, _BOT_KEYS)
            if room_name is None:
                room_name = _lookup(source, _ROOM_KEYS)
            if project_id is None:
                value = source.get("project_id") if isinstance(source, dict) else None
                project_id = value.strip() if isinstance(value, str) and value.strip() else None
            if run_id is None:
                value = source.get("run_id") if isinstance(source, dict) else None
                run_id = value.strip() if isinstance(value, str) and value.strip() else None

        if not bot_name or not room_name:
            return None
        return WriteCoordinationContext(
            bot_name=bot_name.lower().strip(),
            room_name=room_name,
            project_id=project_id,
            run_id=run_id,
        )
    except Exception:
        # Identity resolution must never be the reason a write fails.
        logger.debug("Write coordination context resolution failed", exc_info=True)
        return None


def auto_claim_write(request: Any, path: str, *, tool_name: str = "") -> WriteCoordination:
    """Record intent for `path`, and report who else holds it.

    Never raises. A coordination store that is unavailable produces an empty
    `WriteCoordination`, which is byte-for-byte today's behaviour.
    """
    outcome = WriteCoordination(subject=str(path or ""))
    context = write_coordination_context(request)
    if context is None or not path:
        return outcome

    try:
        from alpha.groups.activity import get_activity_ledger
        from alpha.groups.claims import detect_soft_conflicts, get_claim_store, normalise_subject
        from alpha.groups.coordination import announce_claim

        store = get_claim_store()
        clean = normalise_subject(path, "file")
        if not clean:
            return outcome
        outcome.subject = clean

        # Detect against the *other* holders first, so this write's own claim
        # never shows up as its own conflict.
        ledger = get_activity_ledger()
        room_members = _room_members(context.room_name)
        states = {a.bot_name: a.activity for a in ledger.room_activity(context.room_name, room_members)}

        claim = store.claim(
            context.room_name,
            context.bot_name,
            "file",
            clean,
            intent="editing",
            detail=f"writing via {tool_name}" if tool_name else "",
            project_id=context.project_id,
            run_id=context.run_id,
        )
        outcome.claim_id = claim.claim_id

        for conflict in detect_soft_conflicts(store.room_claims(context.room_name), holder_states=states):
            others = [h for h in conflict.holders if h != context.bot_name]
            if not others:
                continue
            outcome.conflict_holders.extend(others)
            outcome.reclaimable = conflict.reclaimable
            outcome.dead_holder = conflict.dead_holder
            break

        if outcome.conflicted:
            announce_claim(context.room_name, claim.to_dict())
        return outcome
    except Exception:
        logger.debug("Auto-claim failed for %s; continuing without coordination", path, exc_info=True)
        return outcome


def _room_members(room_name: str) -> list[str]:
    try:
        from alpha.groups.service import get_group_chat_service

        svc = get_group_chat_service()
        if svc.get_room(room_name) is None:
            return []
        return svc.effective_members(room_name)
    except Exception:
        return []


def warning_text(outcome: WriteCoordination, *, holder: str = "") -> str:
    """The model-visible note, or `""` when there is nothing to say.

    Deliberately says what it knows and stops. It does not claim the other agent
    is *writing right now* — a live advisory claim is a declaration of intent
    with a 120s lease, not proof of an in-flight edit, and overstating it would
    be the same class of error this whole feature exists to remove.
    """
    if not outcome.conflicted:
        return ""
    others = outcome.conflict_holders[:_MAX_WARNINGS]
    named = holder or ", ".join(f"@{h}" for h in others)
    if outcome.reclaimable and outcome.dead_holder:
        return (
            f"[coordination] {outcome.subject} was also claimed by @{outcome.dead_holder}, "
            "who is confirmed crashed. That claim is available to take over; "
            "check their unfinished work before assuming it is intact."
        )
    return (
        f"[coordination] {outcome.subject} is also claimed by {named}. "
        "Your write went through, but the two of you may be overwriting each other - "
        "read the file first, and consider telling the other agent what you changed."
    )
