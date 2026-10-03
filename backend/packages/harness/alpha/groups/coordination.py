"""Room-level coordination: one read that answers "who is on what right now".

`activity.py` owns per-agent state and `claims.py` owns per-subject intent.
Neither knows about the other, and neither reads the run store — `RunManager`
belongs to `app.gateway.deps`, and `packages/harness/` may never import `app.*`
(`tests/test_harness_boundary.py`). This module is the composition point: a
caller supplies live run facts, and gets back one coherent picture of a room.

It also owns the two behaviours that need both halves:

- **orphaning**, which a hard crash verdict triggers and which turns a dead
  agent's claims from *held* into *available*;
- **transcript signalling**, which posts the conflict and crash news into the
  room using the `MessageIntent` values that already exist (`status`,
  `warning`, `blocker`, `handoff`) rather than adding a sixth inter-agent
  messaging mechanism to the five this repository already has.

Nothing here can refuse work. Visibility and refusal are separate guarantees on
purpose: if the mechanism that draws a status dot could also block a write, a
bad heartbeat would become lost work, operators would switch it off, and then
they would have no visibility either.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Every message this module posts carries one of these intents, all of which
#: already exist in `groups/room.py::MessageIntent`. Reusing the room's own
#: vocabulary is what keeps a coordination notice indistinguishable from any
#: other room message to a reader, a relay, and `groups/taint.py`.
INTENT_CLAIM = "status"
INTENT_CONFLICT = "warning"
INTENT_CRASH = "blocker"
INTENT_ORPHAN = "handoff"


def _service():
    from alpha.groups.service import get_group_chat_service

    return get_group_chat_service()


def room_snapshot(
    room_name: str,
    *,
    run_facts: dict[str, Any] | None = None,
    include_claims: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    """Everything a room's status display needs, in one pass.

    Resolves membership through the service's `effective_members()` — never
    `room.members` — so a nested room reports the bots it inherits and the bots
    its rules match. That is also what makes attribution correct for an
    inherited member without a second code path.
    """
    from alpha.groups.activity import RunEvidence, get_activity_ledger
    from alpha.groups.claims import detect_soft_conflicts, get_claim_store

    svc = _service()
    room = svc.get_room(room_name)
    if room is None:
        raise KeyError(room_name)
    members = svc.effective_members(room_name)

    facts: dict[str, RunEvidence] = {}
    for key, raw in (run_facts or {}).items():
        if isinstance(raw, RunEvidence):
            facts[key] = raw
        elif isinstance(raw, dict):
            facts[key] = RunEvidence.from_dict(raw)

    claims_store = get_claim_store() if include_claims else None
    claims: list = []
    claims_by_bot: dict[str, list[str]] = {}
    held_by_bot: dict[str, list[str]] = {}
    if claims_store is not None:
        claims_store.sweep(now=now)
        claims = claims_store.room_claims(room_name, now=now)
        claims_by_bot = claims_store.claims_by_bot(room_name, now=now)
        held_by_bot = claims_store.held_by_bot(room_name, now=now)

    ledger = get_activity_ledger()
    activities = ledger.room_activity(
        room_name,
        members,
        run_facts=facts,
        claims_by_bot=claims_by_bot,
        held_by_bot=held_by_bot,
        now=now,
    )

    states = {a.bot_name: a.activity for a in activities}
    conflicts = detect_soft_conflicts(claims, holder_states=states) if claims_store is not None else []

    by_activity: dict[str, int] = {}
    by_tone: dict[str, int] = {}
    for entry in activities:
        by_activity[entry.activity] = by_activity.get(entry.activity, 0) + 1
        by_tone[entry.tone] = by_tone.get(entry.tone, 0) + 1

    live_claims = [c for c in claims if c.live]
    return {
        "room": room_name,
        "project_id": getattr(room, "project_id", None),
        "agents": [a.to_dict() for a in activities],
        "count": len(activities),
        # A breakdown by state, kept separate from the two membership counts
        # for the same reason `by_state` is: mixing them would let a client
        # render "3 members" over six visible bots.
        "by_activity": by_activity,
        "by_tone": by_tone,
        "claims": [c.to_dict() for c in claims],
        "live_claim_count": len(live_claims),
        "conflicts": [c.to_dict() for c in conflicts],
        "orphaned": [c.to_dict() for c in claims if c.state == "orphaned"],
    }


def crash_orphans(
    room_name: str,
    *,
    actor: str = "supervisor",
    now: float | None = None,
) -> dict[str, Any]:
    """Move every crashed agent's claims from held to available, and say so.

    Returns a receipt naming what changed and what was announced, because
    "reconciled" as an unverifiable claim is exactly what this layer exists to
    avoid. A room with nothing to do returns empty lists rather than a silent
    success.

    Only a **hard** crash verdict triggers this — the rows that carry a named
    terminal reason. An `unresponsive` agent may be mid-tool-call, and taking
    its claims away would hand live work to a second agent.
    """
    from alpha.groups.activity import get_activity_ledger
    from alpha.groups.claims import get_claim_store

    svc = _service()
    if svc.get_room(room_name) is None:
        raise KeyError(room_name)

    claims_store = get_claim_store()
    ledger = get_activity_ledger()
    members = svc.effective_members(room_name)
    activities = {a.bot_name: a for a in ledger.room_activity(room_name, members, now=now)}

    orphaned: list[dict[str, Any]] = []
    crashed: list[str] = []
    gone: list[str] = []
    for name, entry in activities.items():
        # `crashed` needs a hard run fact. `unresponsive` is *not* enough on its
        # own — an agent inside a long tool call is silent too — but when the
        # silence is corroborated by a process that no longer exists, the work
        # is genuinely abandoned and holding it helps nobody.
        if entry.activity == "crashed":
            crashed.append(name)
        elif entry.activity == "unresponsive" and entry.evidence.reason == "process_gone":
            gone.append(name)
        else:
            continue
        evidence = {
            "reason": entry.evidence.reason,
            "detail": entry.evidence.detail,
            "run_id": entry.run_id,
            "stop_reason": (entry.evidence.run.stop_reason if entry.evidence.run else None),
        }
        moved = claims_store.orphan_for_bot(room_name, name, evidence=evidence)
        orphaned.extend(c.to_dict() for c in moved)

    announced = 0
    if orphaned:
        try:
            _announce(room_name, actor, orphaned, crashed + gone)
            announced = len(orphaned)
        except Exception:
            logger.warning("Crash announcement failed for room %s", room_name, exc_info=True)

    return {
        "room": room_name,
        "crashed": crashed,
        "process_gone": gone,
        "orphaned": orphaned,
        "announced": announced,
    }


def _announce(room_name: str, actor: str, orphaned: list[dict[str, Any]], crashed: list[str]) -> None:
    """Post the crash and the newly-available work into the room transcript.

    Two messages, not one: `blocker` for the crash itself and `handoff` for the
    claims that are now available. An agent that reads only one of them still
    gets the half that matters to it, and the intents mean different things to
    `resolve_next_speakers`.
    """
    svc = _service()
    if crashed:
        svc.post_message(
            room_name=room_name,
            sender=actor,
            content=(f"{len(crashed)} agent(s) stopped unexpectedly: {', '.join(sorted(crashed))}. Their unfinished work is listed below and is available to pick up."),
            intent=INTENT_CRASH,
            metadata={"crashed": sorted(crashed), "orchestration": "activity"},
        )
    subjects = sorted({str(c.get("subject")) for c in orphaned})
    if subjects:
        holders = sorted({str(c.get("holder")) for c in orphaned})
        svc.post_message(
            room_name=room_name,
            sender=actor,
            content=(f"{len(orphaned)} claim(s) released by {', '.join(holders)} are now unclaimed: {', '.join(subjects)}. Claim one before editing it."),
            intent=INTENT_ORPHAN,
            metadata={"subjects": subjects, "orchestration": "activity"},
        )


def announce_conflict(room_name: str, conflict: dict[str, Any], *, actor: str = "supervisor") -> int | None:
    """Post one soft conflict. Returns the message id, or `None` on failure.

    A caller that cannot post gets `None` rather than an exception, because a
    notification failure must never be the thing that breaks the agent that
    triggered it — but it is never reported as delivered either.
    """
    try:
        svc = _service()
        message, _ = svc.post_message(
            room_name=room_name,
            sender=actor,
            content=conflict.get("detail") or "Two agents claim the same work.",
            intent=INTENT_CONFLICT,
            metadata={"orchestration": "claims", **conflict},
        )
        return message.id
    except Exception:
        logger.warning("Conflict announcement failed for room %s", room_name, exc_info=True)
        return None


def announce_claim(room_name: str, claim: dict[str, Any], *, actor: str = "supervisor") -> int | None:
    """Post a newly taken claim, so peers can route around it."""
    try:
        svc = _service()
        message, _ = svc.post_message(
            room_name=room_name,
            sender=actor,
            content=f"{claim.get('holder')} is working on {claim.get('subject')} ({claim.get('intent')}).",
            intent=INTENT_CLAIM,
            metadata={"orchestration": "claims", **claim},
        )
        return message.id
    except Exception:
        logger.warning("Claim announcement failed for room %s", room_name, exc_info=True)
        return None
