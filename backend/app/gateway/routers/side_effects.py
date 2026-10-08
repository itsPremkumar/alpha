"""Side-effect journal REST surface: the reconciliation queue and its verdicts.

The durable ledger (`alpha.runtime.side_effects`) has always answered *which
external effects are unaccounted for* — `list_unknown`, `reconcile`,
`reclaim_expired` — but until this router nothing in the Gateway could ask it.
"A support engineer needs a list, a reconciler needs a queue" (see
`runtime/side_effects/AGENTS.md`) is a promise the ledger's own docstring makes;
this is the surface that keeps it:

* ``GET  /api/side-effects``               — entries, filterable (``status``/``level``/``tool_name``/``run_id``/``thread_id``)
* ``GET  /api/side-effects/summary``       — counts by status and level, oldest-unknown age
* ``GET  /api/side-effects/{tool_call_id}``— one entry, explicit projection
* ``POST /api/side-effects/{tool_call_id}/reconcile`` — record a verdict on an ``UNKNOWN`` entry

Honesty contract (each of these is a test in
``tests/test_side_effects_router.py``):

* **No payload ever crosses this API.** Entries project ``arguments_digest`` /
  ``result_digest`` (SHA-256) and never the arguments or results themselves —
  a ledger row is durable, lands in support bundles, and outlives the thread.
* **An unreadable or absent store is 503 with its real reason, never a list of
  zeros.** "I could not look" and "I looked and found nothing" lead to opposite
  decisions, so a healthy-but-empty ledger is the *only* thing that reports
  ``0``; ``database.backend: memory`` has no ledger at all and says so with
  ``code: side_effect_store_unavailable``.
* **Reconciliation is admin-gated and transition-checked by the ledger, not by
  this router.** The refusal comes from ``validate_transition`` (deterministic —
  e.g. a ``completed`` entry) or ``SideEffectTransitionLost`` (somebody settled
  it first); both surface as 409 with the ledger's own words. ``undetermined``
  *reopens* — the response says ``reopened: true`` rather than presenting a
  reopened row as an outcome. ``reconciled`` never becomes ``completed``: a
  settled entry carries a ``verdict`` the reader must consult.
* **Route order is load-bearing.** ``/summary`` is declared before
  ``/{tool_call_id}``, or Starlette answers ``404 tool_call_id='summary'`` —
  the same trap as ``skills/{skill_name}`` and ``workflows/{workflow_id}``.
* **Scope:** members read their own entries (``user_id`` == caller); entries
  with an empty ``user_id`` exist but are admin-only, because "whose entry is
  this?" must not be guessed. Admins see every scope.

This surface **records and reports only**: it never cancels, resumes or replays
a run, and it never decides an effect happened. `SafeRunRecoveryService` remains
the sole safe-continuation authority (see `runtime/side_effects/AGENTS.md`,
"What is deliberately absent").
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from alpha.persistence.side_effects import SideEffectTransitionLost
from alpha.runtime.side_effects import get_side_effect_recorder
from alpha.runtime.side_effects.ledger import ReconciliationResult, SideEffectLedger
from alpha.runtime.side_effects.statuses import (
    IllegalSideEffectTransition,
    ReconciliationVerdict,
    SideEffectEntry,
    SideEffectLevel,
    SideEffectStatus,
)
from alpha.utils.time import coerce_iso
from app.gateway.deps import is_admin_user

router = APIRouter(tags=["side-effects"])

#: Machine-readable codes carried beside a plain-string ``detail`` (the shape
#: ``skills`` established: the frontend renders ``detail``, and ``code`` lets a
#: caller distinguish an absent resource from a missing route).
NOT_FOUND_CODE = "side_effect_not_found"
NO_STORE_CODE = "side_effect_store_unavailable"
UNREADABLE_CODE = "side_effect_store_unreadable"
TRANSITION_CODE = "side_effect_transition_refused"
INVALID_FILTER_CODE = "invalid_filter"
FORBIDDEN_CODE = "forbidden"

#: Every status/level the API accepts as a filter, in enum order. Kept as a
#: tuple (not derived from the enum at call time) so the 422 message is stable
#: and the response cannot change shape because an enum gained a member.
_STATUSES: tuple[str, ...] = tuple(status.value for status in SideEffectStatus)
_LEVELS: tuple[str, ...] = tuple(level.value for level in SideEffectLevel)

_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 500


class ReconcileRequest(BaseModel):
    """An operator's verdict on one ``UNKNOWN`` entry.

    ``reason`` is required on purpose: a verdict with no stated basis is the
    thing this ledger exists to prevent. It is stored as the entry's ``detail``
    — the entry keeps digests only, so the reason is the one human-readable
    field a row carries.
    """

    verdict: ReconciliationVerdict
    reason: str = Field(..., min_length=1, max_length=2000, description="Why this verdict: what was checked to establish it.")


def _error(status: int, code: str, detail: str) -> JSONResponse:
    """The one error shape this router returns: ``detail`` + ``code``, top-level."""
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


async def _caller(request: Request) -> tuple[str, bool]:
    """Resolve ``(caller_id, is_admin)``, failing closed.

    Same contract as ``routers/apex.py::_caller``: the admin question goes to
    ``app.gateway.deps.is_admin_user`` (the repository's single definition — it
    is what suppresses the admin signal for a PAT), and no caller resolves to
    "every owner", because owner-less reads list everyone's rows.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    owner = str(getattr(user, "id", "") or "")
    is_admin = await is_admin_user(request)
    if not owner and not is_admin:
        # A member with no id cannot be scoped: every filter would degrade to
        # "no filter", which for this surface means *everyone's* rows. Refuse
        # rather than resolve the caller to "every owner".
        raise HTTPException(status_code=401, detail="authenticated caller carries no id; cannot scope a member read")
    return owner, is_admin


def _entry_to_wire(entry: SideEffectEntry) -> dict[str, Any]:
    """Explicit projection — never ``entry.__dict__``.

    A new field on ``SideEffectEntry`` must be *chosen* to appear here, not leak
    onto the wire by default. Timestamps are ISO 8601 (the wire contract every
    evidence/ops surface keeps); digests are the only payload-shaped values.
    """
    return {
        "tool_call_id": entry.tool_call_id,
        "tool_name": entry.tool_name,
        "status": entry.status.value,
        "level": entry.level.value,
        "thread_id": entry.thread_id,
        "run_id": entry.run_id,
        "user_id": entry.user_id,
        "arguments_digest": entry.arguments_digest,
        "result_digest": entry.result_digest,
        "verdict": entry.verdict.value if entry.verdict is not None else None,
        "detail": entry.detail,
        "owner_worker_id": entry.owner_worker_id,
        "lease_expires_at": coerce_iso(entry.lease_expires_at) if entry.lease_expires_at is not None else None,
        "attempt": entry.attempt,
        "created_at": coerce_iso(entry.created_at),
        "updated_at": coerce_iso(entry.updated_at),
        "needs_reconciliation": entry.needs_reconciliation,
        # Alias the frontend reads first: "can a verdict be recorded on this?"
        "reconcilable": entry.status is SideEffectStatus.UNKNOWN,
    }


def _scope(entries: list[SideEffectEntry], caller: str, is_admin: bool) -> list[SideEffectEntry]:
    """Apply owner scoping. Admin sees everything; a member sees their own rows.

    An entry with an empty ``user_id`` has no owner to show a member — it stays
    admin-only rather than being guessed onto somebody's screen.
    """
    if is_admin:
        return entries
    return [entry for entry in entries if entry.user_id and entry.user_id == caller]


def _no_store() -> JSONResponse:
    return _error(
        503,
        NO_STORE_CODE,
        "no side-effect ledger is configured in this process (database.backend: memory installs none) — this is 'could not look', not 'nothing recorded'",
    )


async def _read_entries(ledger: SideEffectLedger) -> list[SideEffectEntry] | JSONResponse:
    """``ledger.all()`` with the unreadable-store path made explicit."""
    try:
        return list(await ledger.all())
    except Exception as exc:  # noqa: BLE001 — the store's real reason is the payload
        return _error(503, UNREADABLE_CODE, f"{type(exc).__name__}: {exc}")


@router.get(
    "/api/side-effects/summary",
    summary="Counts of recorded side effects by status and level",
    description=(
        "Headline counts for the effect journal. Honest about not knowing: a missing or unreadable ledger is 503 "
        "with its reason (`code: side_effect_store_unavailable` / `side_effect_store_unreadable`), never a body of "
        "zeros; `oldest_unknown_age_seconds` is null (never 0) when nothing is waiting."
    ),
)
async def side_effect_summary(request: Request) -> Any:
    caller, is_admin = await _caller(request)
    ledger = get_side_effect_recorder().ledger
    if ledger is None:
        return _no_store()
    entries = await _read_entries(ledger)
    if isinstance(entries, JSONResponse):
        return entries
    entries = _scope(entries, caller, is_admin)

    by_status = {status: 0 for status in _STATUSES}
    by_level = {level: 0 for level in _LEVELS}
    oldest_unknown: float | None = None
    for entry in entries:
        by_status[entry.status.value] = by_status.get(entry.status.value, 0) + 1
        by_level[entry.level.value] = by_level.get(entry.level.value, 0) + 1
        if entry.status is SideEffectStatus.UNKNOWN:
            oldest_unknown = entry.updated_at if oldest_unknown is None else min(oldest_unknown, entry.updated_at)

    return {
        "reported": True,
        "scope": "all" if is_admin else "owner",
        "total": len(entries),
        "by_status": by_status,
        "by_level": by_level,
        "unknown": by_status.get(SideEffectStatus.UNKNOWN.value, 0),
        "oldest_unknown_age_seconds": (time.time() - oldest_unknown) if oldest_unknown is not None else None,
        "generated_at": coerce_iso(time.time()),
    }


@router.get(
    "/api/side-effects",
    summary="List recorded side effects",
    description=(
        "Filterable view over the ledger. `status=unknown` serves the reconciliation queue in the ledger's own "
        "oldest-first order (the entry that has waited longest first); every other view is newest-first. Entries "
        "project SHA-256 digests only — arguments and results never cross this API. Unreadable store is 503 with "
        "its reason, never an empty list that reads as 'nothing was ever recorded'."
    ),
)
async def list_side_effects(
    request: Request,
    status: str | None = Query(None, description=f"Exact status filter: {' | '.join(_STATUSES)}"),
    level: str | None = Query(None, description=f"Exact risk level: {' | '.join(_LEVELS)}"),
    tool_name: str | None = Query(None, min_length=1, description="Exact tool name"),
    run_id: str | None = Query(None, description="Exact run id"),
    thread_id: str | None = Query(None, description="Exact thread id"),
    user_id: str | None = Query(None, description="Owner filter (admins only; members are always scoped to themselves)"),
    limit: int = Query(_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX, description="Maximum rows returned"),
) -> Any:
    caller, is_admin = await _caller(request)
    if status is not None and status not in _STATUSES:
        return _error(422, INVALID_FILTER_CODE, f"unknown status {status!r}; expected one of {list(_STATUSES)}")
    if level is not None and level not in _LEVELS:
        return _error(422, INVALID_FILTER_CODE, f"unknown level {level!r}; expected one of {list(_LEVELS)}")

    ledger = get_side_effect_recorder().ledger
    if ledger is None:
        return _no_store()

    if status == SideEffectStatus.UNKNOWN.value and level is None and tool_name is None:
        # The queue, served by the protocol method built for it. A member's
        # queue is their own: the ledger takes the owner as a filter, so the
        # scope is applied at the source rather than after the limit.
        owner_filter = caller if not is_admin else (user_id or "")
        try:
            entries = list(await ledger.list_unknown(thread_id=thread_id or "", run_id=run_id or "", user_id=owner_filter, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return _error(503, UNREADABLE_CODE, f"{type(exc).__name__}: {exc}")
        return {
            "reported": True,
            "scope": "all" if is_admin else "owner",
            "status_filter": status,
            "order": "oldest_first",
            "count": len(entries),
            "entries": [_entry_to_wire(entry) for entry in entries],
        }

    entries = await _read_entries(ledger)
    if isinstance(entries, JSONResponse):
        return entries
    entries = _scope(entries, caller, is_admin)
    if not is_admin:
        user_id = None  # a member asking for another owner is scoped back to themselves
    if status is not None:
        entries = [entry for entry in entries if entry.status.value == status]
    if level is not None:
        entries = [entry for entry in entries if entry.level.value == level]
    if tool_name is not None:
        entries = [entry for entry in entries if entry.tool_name == tool_name]
    if run_id is not None:
        entries = [entry for entry in entries if entry.run_id == run_id]
    if thread_id is not None:
        entries = [entry for entry in entries if entry.thread_id == thread_id]
    if user_id is not None:
        entries = [entry for entry in entries if entry.user_id == user_id]
    entries.sort(key=lambda entry: (entry.created_at, entry.tool_call_id), reverse=True)
    entries = entries[:limit]
    return {
        "reported": True,
        "scope": "all" if is_admin else "owner",
        "status_filter": status,
        "order": "newest_first",
        "count": len(entries),
        "entries": [_entry_to_wire(entry) for entry in entries],
    }


@router.get(
    "/api/side-effects/{tool_call_id}",
    summary="One side-effect entry",
    description="Explicit projection of a single ledger row: digests, lease, verdict. Absent id is 404 with `code: side_effect_not_found`.",
)
async def get_side_effect(request: Request, tool_call_id: str) -> Any:
    caller, is_admin = await _caller(request)
    ledger = get_side_effect_recorder().ledger
    if ledger is None:
        return _no_store()
    try:
        entry = await ledger.get(tool_call_id)
    except Exception as exc:  # noqa: BLE001
        return _error(503, UNREADABLE_CODE, f"{type(exc).__name__}: {exc}")
    if entry is None:
        return _error(404, NOT_FOUND_CODE, f"side-effect entry {tool_call_id!r} is not recorded in this ledger")
    visible = _scope([entry], caller, is_admin)
    if not visible:
        # Absent and not-yours answer identically: a member read that resolved
        # by id alone would be an existence oracle across users.
        return _error(404, NOT_FOUND_CODE, f"side-effect entry {tool_call_id!r} is not recorded in this ledger")
    return _entry_to_wire(entry)


@router.post(
    "/api/side-effects/{tool_call_id}/reconcile",
    summary="Record a reconciliation verdict on an UNKNOWN entry",
    description=(
        "Admin-only: a verdict asserts what an external system actually did, which is an operator act. The ledger "
        "owns the transition rules — a settled or in-flight entry refuses with 409 and the ledger's own words, "
        "`undetermined` reopens the entry (`reopened: true`, status back to `unknown`) rather than settling it, and "
        "a confirmed failure on a high-risk/destructive effect reports `escalated: true`. Nothing here cancels, "
        "resumes or replays a run."
    ),
)
async def reconcile_side_effect(request: Request, tool_call_id: str, body: ReconcileRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"reconciliation requires an administrator (caller {caller!r} is a member)")
    ledger = get_side_effect_recorder().ledger
    if ledger is None:
        return _no_store()
    try:
        result: ReconciliationResult = await ledger.reconcile(tool_call_id, body.verdict, detail=body.reason)
    except KeyError:
        return _error(404, NOT_FOUND_CODE, f"side-effect entry {tool_call_id!r} is not recorded in this ledger")
    except (IllegalSideEffectTransition, SideEffectTransitionLost) as exc:
        # Deterministic refusal ("this is never allowed") and lost race ("somebody
        # got there first") are distinct inside the ledger; both mean "not this
        # entry, not this way", so both are 409 carrying the ledger's own words.
        return _error(409, TRANSITION_CODE, str(exc))
    except Exception as exc:  # noqa: BLE001
        return _error(503, UNREADABLE_CODE, f"{type(exc).__name__}: {exc}")
    return {
        "tool_call_id": tool_call_id,
        "verdict": result.verdict.value,
        "status": result.entry.status.value,
        "reopened": result.entry.status is SideEffectStatus.UNKNOWN,
        "escalated": result.escalated,
        "reconcilable": result.entry.status is SideEffectStatus.UNKNOWN,
        "entry": _entry_to_wire(result.entry),
    }
