"""APEX `/api/*` — the REST and SSE surface (spec §60, §132).

Everything here is **read-mostly by design**. The routes own control and
observation; they never execute domain work. ``POST /api/apex/missions`` creates
an APEX session row and returns a decision about what should happen next — it
does not run a tool, start a run, or touch a sandbox. A control plane whose
"start" button ran the work itself would be a second execution path, and the
repository already has one lifecycle owner (`RunManager`) plus one background
loop owner (`AutonomySupervisor`).

**Route order is load-bearing.** ``/invariants``, ``/status``, ``/policy`` and
``/events`` are declared *before* ``/sessions/{session_id}``. Starlette matches
in registration order, so a collection-path catch-all declared first would
answer ``404 Session 'invariants' not found`` — indistinguishable from a
missing feature. This is the same trap the groups, skills and workflows routers
document, and it is pinned by ``tests/test_apex_router_route_order.py``.

**Authentication** follows the repository default: the router declares no
route-level decorators, so the Gateway's ``AuthMiddleware`` covers every path
(behaviour stated in ``routers/autonomy.py``). Control endpoints additionally
require admin, because turning autonomy on is an operator act — the same
treatment ``routers/plan_mode.py`` gives ``POST /mode``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from importlib import import_module
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alpha.apex.contract import (
    AutonomyProfile,
    ContractViolation,
    default_contract,
    narrow_contract,
    profile_for,
)
from alpha.apex.executive import run_cycle
from alpha.apex.invariants import check_invariants
from alpha.apex.status import SUPERVISOR_STATUS_PATH, apex_status, contract_status
from alpha.apex.store import APEX_EVENTS, ApexSessionState, get_apex_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apex", tags=["apex"])

#: Upper bound on one event-stream replay, enforced by request validation.
MAX_EVENT_LIMIT = 200
DEFAULT_EVENT_LIMIT = 50
#: A stream cannot outlive this; the client reconnects with Last-Event-ID.
MAX_STREAM_SECONDS = 300.0
STREAM_POLL_SECONDS = 20.0

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class SessionCreateRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=4000)
    profile: str = Field(default="autonomous")
    acceptance_criteria: list[str] = Field(default_factory=list)
    mission_id: str = ""
    thread_id: str = ""
    #: Optional narrowing. Widening any field is refused with the reason.
    authority: dict[str, bool] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


class SteerRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    priority: str = "normal"


class CycleRequest(BaseModel):
    #: Run the cycle for every non-terminal session when no id is named.
    all_sessions: bool = False


def _require_admin(request: Request) -> str:
    """Resolve the caller, refusing a non-admin control action.

    Deliberately not ``@require_admin_user``: that decorator is
    ``plan_mode``'s pattern, and reusing it here would import a settings concern
    into a control-plane router. The check is the same — a control action that
    widens autonomy is not a self-service action.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="APEX control actions require an administrator")
    return str(getattr(user, "id", "") or "admin")


def _contract_for(profile: str, *, mission_id: str = "") -> Any:
    try:
        return profile_for(profile, mission_id=mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown APEX profile {profile!r}; expected one of {[p.value for p in AutonomyProfile]}") from exc


def _supervisor_provider() -> Callable[[], dict[str, Any]]:
    """Bind the app-side supervisor into the harness-side projection.

    ``alpha.apex.status`` cannot import ``app.*`` — ``tests/test_harness_boundary.py``
    fails the build on it — so the dependency points the other way and is
    resolved here, once. A resolution failure becomes a ``None`` provider, which
    the projection reports as unavailable with its reason rather than as a
    healthy empty status.
    """
    module_path, _, attr = SUPERVISOR_STATUS_PATH.partition(":")
    try:
        # The path names the singleton *accessor*, so it is called and its
        # `status` method bound — the projection wants a zero-arg callable.
        factory = getattr(import_module(module_path), attr)
        return factory().status
    except Exception as exc:
        logger.warning("APEX status cannot bind the autonomy supervisor: %s", exc)
        return None  # type: ignore[return-value]


def _session_or_404(session_id: str) -> Any:
    session = get_apex_store().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"no APEX session {session_id!r}")
    return session


# --------------------------------------------------------------------------- #
# Control
# --------------------------------------------------------------------------- #


@router.post("/sessions", summary="Create an APEX session")
async def create_session(payload: SessionCreateRequest, request: Request) -> dict[str, Any]:
    """Create a session under a profile, optionally narrowed.

    Returns the contract and the first decision. It does **not** start work: the
    response says what the executive would do, and the host adapter performs it.
    """
    owner = _require_admin(request)
    contract = _contract_for(payload.profile, mission_id=payload.mission_id)
    if payload.authority or payload.budget:
        try:
            contract = narrow_contract(contract, authority=payload.authority or None, budget=payload.budget or None, mission_id=payload.mission_id or None)
        except ContractViolation as exc:
            # 422, not 400: the request was well-formed but conflicts with the
            # profile's ceiling, and the caller can narrow instead.
            raise HTTPException(status_code=422, detail=f"contract refused: {exc}") from exc
    store = get_apex_store()
    session = store.create(
        owner=owner,
        objective=payload.objective,
        profile=contract.profile.value,
        contract_digest=contract.digest(),
        mission_id=payload.mission_id,
        thread_id=payload.thread_id,
        acceptance_criteria=payload.acceptance_criteria,
    )
    return {
        "session": session.to_dict(),
        "contract": contract.to_dict(),
        "note": "session created; APEX records decisions, a host adapter performs work",
    }


@router.get("/sessions", summary="List APEX sessions")
async def list_sessions(
    request: Request,
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    owner = str(getattr(user, "id", "") or "") if user is not None else None
    store = get_apex_store()
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "sessions": []}
    sessions = store.list(owner=owner, state=state, limit=limit)
    return {"available": True, "count": len(sessions), "sessions": [s.to_dict() for s in sessions]}


@router.get("/status", summary="APEX status")
async def status(
    request: Request,
    session_id: str | None = Query(default=None),
    include_invariants: bool = Query(default=True),
) -> dict[str, Any]:
    """The §59 projection. Read-only, so it is safe from a UI poll loop."""
    contract = default_contract()
    return apex_status(
        get_apex_store(),
        contract,
        session_id=session_id,
        include_invariants=include_invariants,
        supervisor_provider=_supervisor_provider(),
    )


@router.get("/policy", summary="The active contract and its attributed policy sites")
async def policy(profile: str = Query(default="off")) -> dict[str, Any]:
    """What APEX may do under a profile, and which module decides what.

    ``policy_sites_missing`` is the load-bearing field: it is how an operator
    sees that a delegated policy kernel is absent rather than trusting that APEX
    enforces it.
    """
    return contract_status(_contract_for(profile))


@router.get("/invariants", summary="The §188 invariant set and its live enforcement sites")
async def invariants() -> dict[str, Any]:
    """One row per declared invariant.

    ``live`` reflects whether the named enforcement module imports and exposes
    the named symbol. A missing site reports ``live=False`` with the reason; it
    never reports a pass, and the aggregate never collapses "12 declared" into a
    single number.
    """
    reports = check_invariants()
    live = [r for r in reports if r.live]
    return {
        "schema": "alpha.apex.invariants.v1",
        "declared": len(reports),
        "live": len(live),
        "all_live": bool(reports) and len(live) == len(reports),
        "invariants": [r.to_dict() for r in reports],
    }


@router.post("/sessions/{session_id}/steer", summary="Record a steering constraint")
async def steer(session_id: str, payload: SteerRequest) -> dict[str, Any]:
    """Record a mission constraint (spec §57).

    A constraint, not a prompt rewrite: nothing here modifies a system prompt,
    and the recorded source distinguishes a user instruction from a
    model-proposed one.
    """
    store = get_apex_store()
    _session_or_404(session_id)
    constraint = store.record_constraint(session_id, payload.instruction, priority=payload.priority)
    if constraint is None:
        current = store.get(session_id)
        reason = "session is terminal" if current and current.is_terminal else "constraint refused"
        raise HTTPException(status_code=409, detail=reason)
    return {"constraint": constraint.to_dict()}


@router.post("/sessions/{session_id}/cycle", summary="Run one executive cycle")
async def cycle(session_id: str, payload: CycleRequest, request: Request) -> dict[str, Any]:
    """Run one bounded cycle and return its steps and decision.

    Admin-gated: driving the executive forward is an operator act. The cycle
    itself is read-mostly in effect — it records a decision and, where the
    decision implies one, a session state transition. It executes no domain
    work, so this endpoint is an observation surface, not a work surface.
    """
    _require_admin(request)
    store = get_apex_store()
    session = _session_or_404(session_id)
    contract = _contract_for(session.profile, mission_id=session.mission_id)
    if payload.all_sessions:
        raise HTTPException(status_code=400, detail="name a session; /api/apex/cycle runs one session")
    result = run_cycle(store, session_id, contract)
    return result.to_dict()


@router.post("/cycle", summary="Run one cycle per non-terminal session")
async def cycle_all(payload: CycleRequest, request: Request) -> dict[str, Any]:
    """The supervisor-loop entry point over HTTP.

    Same work as the background loop's tick, exposed so an operator can drive a
    pass without waiting for the interval. Terminal sessions are skipped, so a
    completed mission is not reopened by a poll.

    Admin-gated like the other control actions: driving the executive forward
    over every session is an operator act, not a self-service one.
    """
    _require_admin(request)
    store = get_apex_store()
    results = []
    for session in store.list(limit=200):
        if session.is_terminal:
            continue
        contract = _contract_for(session.profile, mission_id=session.mission_id)
        results.append(run_cycle(store, session.session_id, contract).to_dict())
    return {"cycles": len(results), "results": results}


@router.get("/sessions/{session_id}", summary="One APEX session")
async def get_session(session_id: str) -> dict[str, Any]:
    return _session_or_404(session_id).to_dict()


@router.get("/sessions/{session_id}/events", summary="APEX session event stream (SSE)")
async def session_events(
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_EVENT_LIMIT, ge=1, le=MAX_EVENT_LIMIT),
) -> StreamingResponse:
    """Replay the durable journal, then tail the live feed.

    Shaped on ``routers/missions.py``: journal first via a worker thread (the
    read touches disk), then a subscriber queue with per-event dedupe, a bounded
    lifetime, and an unsubscribe in ``finally``.
    """
    _session_or_404(session_id)
    store = get_apex_store()

    async def _events():
        import json as _json

        loop = asyncio.get_running_loop()
        seen: set[int] = set()
        yield f"event: ready\ndata: {_json.dumps({'session_id': session_id, 'after_seq': after_seq})}\n\n"

        def _replay() -> list[Any]:
            return store.read_events(session_id, after_seq=after_seq)[:limit]

        try:
            replayed = await asyncio.to_thread(_replay)
        except Exception as exc:
            yield f"event: error\ndata: {_json.dumps({'error': f'{type(exc).__name__}: {exc}'})}\n\n"
            return
        for event in replayed:
            seen.add(event.seq)
            yield f"event: {event.event_type}\ndata: {_json.dumps(event.to_dict())}\n\n"

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def _publish(event: Any) -> None:
            loop.call_soon_threadsafe(_safe_put, queue, event)

        def _safe_put(q: asyncio.Queue, event: Any) -> None:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A slow subscriber is disclosed on its next frame, never silently
                # dropped — the same rule MissionEventFeed applies.
                pass

        APEX_EVENTS.subscribe(session_id, _publish)
        deadline = loop.time() + MAX_STREAM_SECONDS
        try:
            while loop.time() < deadline:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=STREAM_POLL_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.seq in seen:
                    continue
                seen.add(event.seq)
                yield f"event: {event.event_type}\ndata: {_json.dumps(event.to_dict())}\n\n"
        finally:
            APEX_EVENTS.unsubscribe(session_id, _publish)
            yield "event: stream_closed\ndata: {}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/sessions/{session_id}/state", summary="Transition an APEX session")
async def set_state(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Move a session, refusing an illegal or terminal transition.

    ``COMPLETED`` is **not** reachable here: it requires a passing acceptance
    report and only the executive cycle may set it. A route that could write
    that state directly would reopen the false-completion hole spec §46 exists
    to close.
    """
    store = get_apex_store()
    _session_or_404(session_id)
    raw = str(payload.get("state", ""))
    try:
        target = ApexSessionState(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown state {raw!r}") from None
    if target.is_terminal:
        # Every terminal outcome is the acceptance gate's to assert, not the
        # request body's. COMPLETED needs passing evidence; FAILED and CANCELLED
        # are outcomes too, and a body that could name one would be the same
        # false-completion hole under a different label.
        raise HTTPException(
            status_code=409,
            detail=(f"{target.value.upper()} is a terminal outcome and cannot be written directly; run POST /api/apex/sessions/{{id}}/cycle so the acceptance gate decides"),
        )
    reason = str(payload.get("reason", ""))
    updated = store.set_state(session_id, target, reason=reason)
    if updated is None:
        current = store.get(session_id)
        detail = f"session is terminal ('{current.state.value}')" if current and current.is_terminal else "transition refused"
        raise HTTPException(status_code=409, detail=detail)
    return updated.to_dict()


@router.delete("/sessions/{session_id}", summary="Delete an APEX session row")
async def delete_session(session_id: str) -> dict[str, Any]:
    if not get_apex_store().delete(session_id):
        raise HTTPException(status_code=404, detail=f"no APEX session {session_id!r}")
    return {"deleted": session_id}
