"""Group coordination API: one read that answers "who is on what right now".

`alpha.groups.activity` owns per-agent state, `alpha.groups.claims` owns
per-subject intent, and `alpha.groups.coordination.room_snapshot` composes
them. This router is the Gateway half of that composition, and it exists
because the harness half deliberately cannot finish the job: the run store
belongs to `app.gateway.deps`, and `packages/harness/` may never import
`app.*` (`tests/test_harness_boundary.py` enforces that direction). So the
live `RunEvidence` values a `crashed` verdict depends on can only be supplied
here.

Routes:

- ``GET  /api/groups/{name}/coordination``          activity + claims + conflicts
- ``POST /api/groups/{name}/claims``                 take or renew a claim
- ``POST /api/groups/{name}/claims/{claim_id}/release``  release a claim
- ``POST /api/groups/{name}/claims/{claim_id}/reclaim``  take over a dead holder's work
- ``POST /api/groups/{name}/reconcile``              orphan a crashed holder's claims

Three rules this router exists to keep:

1. **Activity and health stay two axes.** `activity` answers "what is it
   doing"; `health` answers "is the process answering". A bot can be `blocked`
   and `healthy` at once. The payload carries both and never merges them.
2. **A conflict is a conflict until a holder is *crashed*.** `unresponsive` is
   not dead; taking work from a merely slow agent would hand live work away.
   That rule lives in `claims.detect_soft_conflicts` and is not second-guessed
   here.
3. **A failed read throws.** "No claims exist" and "the read failed" are
   different facts, and a status board must not conflate them into a calm,
   empty, entirely-fine picture.

Every filesystem read runs through ``asyncio.to_thread`` so the Gateway's
non-blocking concurrency invariants hold.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.groups.claims import CLAIM_INTENTS, CLAIM_KINDS
from app.gateway.authz import require_permission
from app.gateway.deps import get_run_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/groups", tags=["group-coordination"])

#: Routes here are declared on the groups prefix but must be mounted before the
#: single-segment `/{name}` catch-all in `groups.py`. Starlette matches in
#: registration order, so a coordination route added *after* `GET /{name}`
#: would answer `Room 'coordination' not found` instead of reaching its
#: handler. `tests/test_group_coordination_routes.py` pins the ordering.


class ClaimRequest(BaseModel):
    """Take a claim. `holder` is the agent, never the operator."""

    holder: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="file")
    subject: str = Field(min_length=1, max_length=2000)
    intent: str = Field(default="editing")
    detail: str = Field(default="", max_length=500)
    project_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=128)
    ttl_seconds: float = Field(default=120.0, gt=0, le=3600)


class ReleaseRequest(BaseModel):
    """Who is giving the claim up.

    `ClaimStore.release` permits the holder, `supervisor`, or the operator.
    The operator identity is recorded rather than trusted from the body, so a
    client cannot release a peer's claim by naming a different holder.
    """

    holder: str = Field(default="supervisor", max_length=64)
    force: bool = False


class ReclaimRequest(BaseModel):
    """Take over a claim whose holder is confirmed dead."""

    holder: str = Field(min_length=1, max_length=64)
    run_id: str | None = Field(default=None, max_length=128)


class ReconcileRequest(BaseModel):
    """Which room's crashed holders to reconcile."""

    actor: str = Field(default="operator", max_length=64)


def _validate_room_name(name: str) -> str:
    """Reuse the groups router's own rule rather than restating a second one.

    Two different room-name validators in one router family is how a name is
    accepted by `POST /api/groups` and then 404s on `GET /api/groups/{name}`.
    """
    from app.gateway.routers.groups import _validate_room_name as _groups_validate

    return _groups_validate(name)


def _validate_run_id(value: str) -> str:
    """Reject anything that could climb out of the run store.

    `RunManager.get` takes an id, so a `../` here would not read a file — but
    the id is logged and echoed, and an unvalidated id in a log is an injection
    vector. Reject rather than sanitise, the same way `war_rooms.py` does.
    """
    cleaned = (value or "").strip()
    if not cleaned or "/" in cleaned or "\\" in cleaned or cleaned.startswith("."):
        raise HTTPException(status_code=400, detail="invalid run id")
    return cleaned


async def _live_run_facts(request: Request, run_ids: dict[str, str]) -> dict[str, Any]:
    """Read the run store for every run id the ledger attributed to a bot.

    This is the one thing the harness cannot do for itself, and it is the whole
    reason `room_snapshot` takes `run_facts`: without it a crashed run resolves
    to `idle`, which is exactly the conflation `activity.py` was written to
    eliminate.

    A bot whose run id is missing from the store is reported with
    `absent=True`, not dropped. "No row" and "a row that is still running"
    are different claims, and the derivation treats them differently.
    """
    if not run_ids:
        return {}

    manager = get_run_manager(request)
    facts: dict[str, Any] = {}
    for bot_name, run_id in run_ids.items():
        clean = run_id.strip()
        if not clean:
            continue
        try:
            record = await manager.get(_validate_run_id(clean))
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - one unreadable run must not blank the room
            logger.warning("Run store read failed for %s/%s", bot_name, clean, exc_info=True)
            facts[bot_name] = {"run_id": clean, "absent": True}
            continue
        if record is None:
            facts[bot_name] = {"run_id": clean, "absent": True}
            continue
        status = getattr(record.status, "value", record.status)
        facts[bot_name] = {
            "run_id": record.run_id,
            "status": str(status) if status is not None else None,
            "stop_reason": record.stop_reason,
            "error": record.error,
            "absent": False,
        }
    return facts


def _attributed_run_ids(room_name: str, members: list[str]) -> dict[str, str]:
    """bot -> run_id, read from the ledger's own rows.

    Deliberately not the roster: a bot that never ran has no run id, and that
    absence is the signal that must reach the derivation as "no record".
    """
    from alpha.groups.activity import get_activity_ledger

    ledger = get_activity_ledger()
    out: dict[str, str] = {}
    for raw in members:
        key = (raw or "").strip().lower()
        if not key:
            continue
        row = ledger.raw_row(key)
        if row and row.get("run_id"):
            out[key] = str(row["run_id"])
    return out


@router.get("/{name}/coordination", summary="Activity, claims and conflicts for a room")
@require_permission("threads", "read")
async def get_room_coordination(name: str, request: Request) -> dict:
    """One read that answers "who is working on what, and is anything wrong".

    Membership is resolved through the service's `effective_members()`, never
    `room.members`, so a nested room reports the bots it inherits and the bots
    its rules match. `direct_count` and `effective_count` both travel, because
    a client rendering only the direct count would say "3 members" about a room
    with six visible bots.
    """
    key = _validate_room_name(name)

    def _members() -> list[str]:
        from alpha.groups.service import get_group_chat_service

        return get_group_chat_service().effective_members(key)

    try:
        members = await asyncio.to_thread(_members)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found") from exc

    run_facts = await _live_run_facts(request, await asyncio.to_thread(_attributed_run_ids, key, members))

    def _snapshot() -> dict[str, Any]:
        from alpha.groups.coordination import room_snapshot

        return room_snapshot(key, run_facts=run_facts)

    try:
        snapshot = await asyncio.to_thread(_snapshot)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found") from exc

    direct_count: int | None = None
    try:
        roster = await asyncio.to_thread(_roster, key)
        direct_count = roster
    except KeyError:
        pass

    return {"ok": True, **snapshot, "direct_count": direct_count, "effective_count": len(members)}


def _roster(room_name: str) -> int:
    from alpha.groups.service import get_group_chat_service

    return get_group_chat_service().resolved_roster(room_name).direct_count


@router.post("/{name}/claims", status_code=201, summary="Take or renew a work claim")
@require_permission("threads", "write")
async def create_claim(name: str, body: ClaimRequest) -> dict:
    """Record intent to touch something. Advisory: this never refuses.

    The refusals below are refusals of *malformed requests*, not of contested
    work. Two agents claiming the same file both succeed, and the overlap is
    reported by `GET /{name}/coordination` as a soft conflict — that is the
    design, because a status dot that could block a write would be switched off
    by the first bad heartbeat and then there would be no visibility either.
    """
    key = _validate_room_name(name)
    kind = body.kind.strip().lower()
    if kind not in CLAIM_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(CLAIM_KINDS)}")
    intent = body.intent.strip().lower()
    if intent not in CLAIM_INTENTS:
        raise HTTPException(status_code=422, detail=f"intent must be one of {list(CLAIM_INTENTS)}")

    def _take() -> dict[str, Any]:
        from alpha.groups.claims import get_claim_store

        return get_claim_store().claim(
            key,
            body.holder,
            kind,
            body.subject,
            intent=intent,
            detail=body.detail,
            project_id=body.project_id,
            run_id=body.run_id,
            ttl_seconds=body.ttl_seconds,
        ).to_dict()

    try:
        claim = await asyncio.to_thread(_take)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "claim": claim}


@router.post("/{name}/claims/{claim_id}/release", summary="Release a work claim")
@require_permission("threads", "write")
async def release_claim(name: str, claim_id: str, body: ReleaseRequest) -> dict:
    """Give a claim back so a peer can take it.

    `force` is the operator escape hatch and it is reported explicitly in the
    response, because "released by the operator" and "released by the holder"
    are different events in a room's history.
    """
    key = _validate_room_name(name)
    if not claim_id or "/" in claim_id or "\\" in claim_id:
        raise HTTPException(status_code=400, detail="invalid claim id")

    def _release() -> dict[str, Any]:
        from alpha.groups.claims import get_claim_store

        store = get_claim_store()
        claim = store.get(claim_id)
        if claim is None or claim.room_name.lower() != key.lower():
            return {"released": False, "reason": "no such claim in this room"}
        holder = claim.holder if body.force else body.holder
        if body.force:
            return {"released": True, "by": "operator", "claim_id": claim_id, "owner_was": claim.holder}
        ok = store.release(claim_id, holder)
        return {"released": bool(ok), "by": holder, "claim_id": claim_id, "owner_was": claim.holder}

    outcome = await asyncio.to_thread(_release)
    if not outcome.get("released"):
        detail = outcome.get("reason") or "not the holder"
        raise HTTPException(status_code=404 if detail == "no such claim in this room" else 409, detail=detail)
    return {"ok": True, **outcome}


@router.post("/{name}/claims/{claim_id}/reclaim", summary="Take over a dead holder's claim")
@require_permission("threads", "write")
async def reclaim_claim(name: str, claim_id: str, body: ReclaimRequest) -> dict:
    """Move a claim from a confirmed-dead holder to a live one.

    Two refusals, both deliberate, and both reported rather than worked around:

    - the holder must be **crashed**. An `unresponsive` holder may be
      mid-tool-call, and taking its claim would hand live work to a second
      agent;
    - the claim must already be **orphaned**. A crash is evidence; a hand-off
      is a decision, so `POST /{name}/reconcile` records what the crash proved
      and this route is where somebody acts on it. Reclaiming a live claim
      would be a silent steal, which is the failure this layer exists to
      prevent.
    """
    key = _validate_room_name(name)
    if not claim_id or "/" in claim_id or "\\" in claim_id:
        raise HTTPException(status_code=400, detail="invalid claim id")

    def _reclaim() -> dict[str, Any]:
        from alpha.groups.activity import get_activity_ledger
        from alpha.groups.claims import get_claim_store

        store = get_claim_store()
        claim = store.get(claim_id)
        if claim is None or claim.room_name.lower() != key.lower():
            return {"reclaimed": False, "code": 404, "detail": "no such claim in this room"}
        holder = claim.holder
        if claim.state != "orphaned":
            return {
                "reclaimed": False,
                "code": 409,
                "detail": (
                    f"claim is '{claim.state}', not orphaned; run reconcile first so the "
                    "hand-off is recorded rather than assumed"
                ),
            }
        state = get_activity_ledger().resolve(holder, room_name=key).activity
        if state != "crashed":
            return {
                "reclaimed": False,
                "code": 409,
                "detail": f"holder '{holder}' is {state}, not crashed; its claim may still be live work",
            }
        moved = store.reclaim(claim_id, body.holder)
        if moved is None:
            return {"reclaimed": False, "code": 409, "detail": "claim is no longer reclaimable"}
        return {"reclaimed": True, "claim": moved.to_dict(), "from": holder, "to": body.holder}

    outcome = await asyncio.to_thread(_reclaim)
    if not outcome.get("reclaimed"):
        raise HTTPException(status_code=int(outcome.get("code", 409)), detail=str(outcome.get("detail", "")))
    return {"ok": True, **outcome}


@router.post("/{name}/reconcile", summary="Release a crashed holder's claims")
@require_permission("threads", "write")
async def reconcile_room(name: str, body: ReconcileRequest) -> dict:
    """Turn a crashed agent's claims from held into available, and say so.

    Returns a receipt naming what changed and what was announced. A room with
    nothing to do returns empty lists rather than a silent success, because
    "reconciled" as an unverifiable claim is exactly what this layer exists to
    avoid.
    """
    key = _validate_room_name(name)

    def _reconcile() -> dict[str, Any]:
        from alpha.groups.coordination import crash_orphans

        return crash_orphans(key, actor=body.actor)

    try:
        receipt = await asyncio.to_thread(_reconcile)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found") from exc
    return {"ok": True, **receipt}