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
treatment ``routers/plan_mode.py`` gives ``POST /mode``. The admin question is
asked once, through ``app.gateway.deps.is_admin_user``, so this router cannot
drift from the repository's definition of an admin (and inherits its rule that
a PAT credential never carries admin).

**Member routes are owner-scoped, not merely authenticated.** Their collections
are already owner-filtered, and a member read that resolves by id alone would
hand any signed-in user another user's session or goal. The refusal is the same
404 an absent row produces, so an id cannot be used to probe for existence —
the same treatment ``routers/missions.py`` gives ``/{mission_id}``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from importlib import import_module
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alpha.apex.contract import (
    AutonomyProfile,
    ContractViolation,
    contract_from_snapshot,
    narrow_contract,
    profile_for,
)
from alpha.apex.executive import run_cycle
from alpha.apex.goals import (
    GoalState,
    IllegalGoalTransition,
    get_goal_store,
)
from alpha.apex.invariants import invariant_summary
from alpha.apex.mode import DEFAULT_SCOPE as DEFAULT_APEX_SCOPE
from alpha.apex.mode import get_apex_mode_store, set_mode
from alpha.apex.status import SUPERVISOR_STATUS_PATH, apex_status, contract_status
from alpha.apex.store import APEX_EVENTS, ApexSessionState, get_apex_store
from app.gateway.deps import is_admin_user

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


class _BacklogDiscloser:
    """Counts events a live subscriber could not be handed, as one disclosure.

    A bounded queue has to drop when its reader falls behind. What it may not
    do is drop *silently*: the client would then see an uninterrupted stream
    with a hole in it and read the gap as complete history. ``take()`` returns
    the count and clears it, so the stream emits one ``gap`` frame naming the
    number lost and the recovery (replay with ``after_seq``) rather than
    pretending continuity.

    Both sides run on the event loop — ``note_drop`` is invoked through
    ``loop.call_soon_threadsafe`` — so no locking is needed.
    """

    __slots__ = ("_dropped",)

    def __init__(self) -> None:
        self._dropped = 0

    def note_drop(self) -> None:
        self._dropped += 1

    def take(self) -> int:
        dropped, self._dropped = self._dropped, 0
        return dropped


def _enqueue_or_disclose(queue: asyncio.Queue, event: Any, discloser: _BacklogDiscloser) -> None:
    """Hand one event to a live subscriber, or count the drop.

    Extracted to module scope so the overflow path is testable: nested inside
    the generator it would be unreachable, and ``except asyncio.QueueFull: pass``
    is exactly how the previous version promised a disclosure that never
    happened. Counted rather than raised because one slow subscriber must not
    break the mission that emitted the event.
    """
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        discloser.note_drop()


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


class SessionReplanRequest(BaseModel):
    """Operator intent required before another run can follow a failure."""

    reason: str = Field(min_length=1, max_length=1000)
    acknowledge_possible_side_effects: bool = False


class CycleRequest(BaseModel):
    #: Run the cycle for every non-terminal session when no id is named.
    all_sessions: bool = False


class AcceptanceEvidenceItem(BaseModel):
    criterion: str = Field(min_length=1, max_length=4000)
    met: bool
    evidence: str = Field(min_length=1, max_length=4000)


class SessionAcceptanceRequest(BaseModel):
    """A complete, operator-measured result set for one finished APEX run."""

    results: list[AcceptanceEvidenceItem] = Field(min_length=1, max_length=200)


class ModeRequest(BaseModel):
    """The payload for the APEX on/off toggle (spec §3, §33).

    ``scope_key`` is the conversation the toggle applies to. It defaults to the
    caller's own id, so a toggle with no argument always acts on the caller's
    own session and can never reach another user's.
    """

    #: Defaults to ``"assist"`` rather than the most permissive profile: turning
    #: APEX on is not a request for maximum authority (spec §24).
    profile: str = "assist"
    #: Optional explicit scope. A non-admin naming another user's scope is
    #: refused by ``_require_admin``'s sibling check below rather than trusted.
    scope_key: str = ""


class ModeResponse(BaseModel):
    enabled: bool
    profile: str
    scope_key: str
    changed: bool
    contract_digest: str
    contract_enabled: bool
    durable: bool = False
    reason: str = ""
    load_error: str | None = None


async def _caller(request: Request) -> tuple[str, bool]:
    """Resolve ``(owner_id, is_admin)`` for this request, failing closed.

    Two properties this shape exists to guarantee:

    * **The admin question is asked once.** It goes to
      ``app.gateway.deps.is_admin_user`` rather than to a local copy of the
      rule, because that predicate is the repository's single definition — it
      is what suppresses the admin signal for a PAT credential, and a
      per-router re-implementation is exactly the drift its own docstring
      warns about. It reads ``request.state.user``, which ``AuthMiddleware``
      stamps before any router runs.
    * **No caller resolves to "every owner".** ``owner=None`` means *all*
      owners in every store this router queries, so an unauthenticated caller
      must be 401 rather than an owner-less read that lists everyone's rows.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    owner = str(getattr(user, "id", "") or "")
    return owner, await is_admin_user(request)


async def _require_admin(request: Request) -> str:
    """Resolve the caller, refusing a non-admin control action.

    Returns the caller's own id, which is what the mode store and the session
    store record as the owner of anything created here. A control action that
    widens autonomy is not a self-service action, so the refusal is 403 with
    the reason rather than a silent no-op.
    """
    owner, is_admin = await _caller(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="APEX control actions require an administrator")
    return owner


def _contract_for(profile: str, *, mission_id: str = "") -> Any:
    try:
        return profile_for(profile, mission_id=mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown APEX profile {profile!r}; expected one of {[p.value for p in AutonomyProfile]}") from exc


def _session_contract(session: Any) -> Any:
    """Load the validated frozen policy, retaining compatibility for old rows."""
    snapshot = getattr(session, "contract_snapshot", None)
    if snapshot is None:
        return _contract_for(session.profile, mission_id=session.mission_id)
    try:
        return contract_from_snapshot(snapshot, expected_digest=session.contract_digest)
    except (ValueError, ContractViolation) as exc:
        raise HTTPException(status_code=409, detail="APEX session contract is invalid") from exc


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


async def _session_or_404(request: Request, session_id: str) -> Any:
    """One session the caller may act on, or the reason there is none.

    Three refusals stay distinct, because they lead to opposite next steps: an
    unreadable store is 503 (nobody can say what exists), an absent row is 404,
    and a row belonging to someone else is *also* 404 — the same detail — so a
    foreign id cannot be used to probe which ids exist.
    """
    store = get_apex_store()
    if store.is_degraded:
        raise HTTPException(status_code=503, detail=f"APEX session store unreadable: {store.load_error}")
    session = store.get(session_id)
    if store.is_degraded:
        raise HTTPException(status_code=503, detail=f"APEX session store unreadable: {store.load_error}")
    if session is None:
        raise HTTPException(status_code=404, detail=f"no APEX session {session_id!r}")
    owner, is_admin = await _caller(request)
    if not is_admin and session.owner != owner:
        raise HTTPException(status_code=404, detail=f"no APEX session {session_id!r}")
    return session


async def _resolve_scope(request: Request, requested: str) -> tuple[str, str]:
    """Resolve the toggle's scope, refusing one caller to name another's.

    Returns ``(scope_key, owner)``. An explicit ``scope_key`` is honoured only
    for an admin; for anyone else it must equal their own id. Without this the
    toggle would be a cross-session autonomy grant: any authenticated user could
    switch on a scope they do not own.

    The same "unknown scope is OFF" rule the store applies is stated in the
    response, because a client that asked about a scope nobody configured needs
    to distinguish "not enabled" from "I created it".
    """
    owner, is_admin = await _caller(request)
    scope = str(requested or "").strip() or owner or DEFAULT_APEX_SCOPE
    if requested and scope != owner and not is_admin:
        raise HTTPException(status_code=403, detail="an APEX toggle may only target your own session")
    return scope, owner


def _mode_body(scope_key: str, *, changed: bool, durable: bool | None = None, reason: str = "") -> dict[str, Any]:
    store = get_apex_mode_store()
    record = store.for_scope(scope_key)
    contract = record.contract()
    body: dict[str, Any] = {
        "enabled": record.enabled,
        "profile": record.profile,
        "scope_key": scope_key,
        "changed": changed,
        "contract_digest": contract.digest(),
        # Reported separately from `enabled` on purpose: a corrupt store answers
        # enabled=False, and a record whose profile this build does not know
        # degrades to `assist`. `contract_enabled` is the claim that actually
        # gates work, so it must be visible rather than inferred from the flag.
        "contract_enabled": contract.enabled,
        "durable": durable,
        "reason": reason,
        "enabled_at": record.enabled_at,
        "updated_at": record.updated_at,
    }
    if record.load_note:
        body["load_note"] = record.load_note
    if store.is_degraded:
        # A mode store that cannot be read is reporting every scope as OFF. That
        # is the fail-closed choice, but a client must be able to see it is a
        # degraded read rather than a considered decision.
        body["load_error"] = store.load_error
    # The session this scope controls, when one exists. The control
    # verbs (pause/resume/stop) and the approval gate act on it, so a
    # client rendering the switch can also render the controls that
    # belong to a live session — and can tell "off" from "off because
    # no session was ever created".
    session = get_apex_store().active_for_scope(scope_key)
    body["active_session"] = session.to_dict() if session is not None else None
    return body


# --------------------------------------------------------------------------- #
# Mode toggle — the one surface the UI's ON/OFF switch writes
# --------------------------------------------------------------------------- #
#
# Declared before `/sessions/{session_id}` for the same reason `/status` and
# `/policy` are: Starlette matches in registration order, and a catch-all
# declared first would answer `405 Session 'enable' not found` for a feature
# that exists. Pinned by tests/test_apex_router_route_order.py.


@router.get("/mode", summary="Read the APEX mode for a scope")
async def read_apex_mode(request: Request, scope_key: str = Query(default="")) -> dict[str, Any]:
    """The current on/off state, the profile, and what the contract authorises.

    Read-only, and safe to poll. `changed` is `false` because nothing changed —
    this route exists so a client can render the toggle from server state
    rather than from an optimistic local guess.
    """
    scope, _owner = await _resolve_scope(request, scope_key)
    return _mode_body(scope, changed=False)


@router.post("/enable", summary="Enable APEX autopilot for a scope")
async def enable_apex(payload: ModeRequest, request: Request) -> dict[str, Any]:
    """Turn APEX on at a named profile.

    Requires admin, because turning autonomy on is an operator act — the same
    treatment `POST /mode` (plan mode) receives. A 422 names the valid profiles
    rather than falling back to one, so a typo cannot silently grant a
    different authority than the caller asked for.
    """
    owner = await _require_admin(request)
    scope, _resolved_owner = await _resolve_scope(request, payload.scope_key)
    try:
        outcome = set_mode(scope, True, profile=payload.profile, owner=owner)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    durable = outcome.get("durable") if isinstance(outcome.get("durable"), bool) else None
    return _mode_body(scope, changed=bool(outcome.get("changed")), durable=durable, reason=str(outcome.get("reason", "")))


@router.post("/disable", summary="Disable APEX autopilot for a scope")
async def disable_apex(payload: ModeRequest, request: Request) -> dict[str, Any]:
    """Turn APEX off for a scope, preserving the mission and the profile.

    Idempotent: a second call reports `changed: false` rather than pretending it
    acted. Mission state is untouched — disabling APEX stops the executive from
    choosing, it does not cancel work an existing engine already admitted.
    """
    owner = await _require_admin(request)
    scope, _resolved_owner = await _resolve_scope(request, payload.scope_key)
    try:
        outcome = set_mode(scope, False, owner=owner)
    except ValueError as exc:  # pragma: no cover - disable takes no profile
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    durable = outcome.get("durable") if isinstance(outcome.get("durable"), bool) else None
    return _mode_body(scope, changed=bool(outcome.get("changed")), durable=durable, reason=str(outcome.get("reason", "")))


# --------------------------------------------------------------------------- #
# Session control — pause / resume / stop (spec §3, §30, §33)
# --------------------------------------------------------------------------- #
#
# Each verb targets the *active session for a conversation scope*, not a
# caller-supplied session id: a chat command or a UI button names a
# conversation, and the join is `ApexStore.active_for_scope`. Admin-gated
# like the mode writes, because parking a mission is an operator act.


class ScopeRequest(BaseModel):
    """A control action that targets a conversation scope."""

    scope_key: str = ""


def _control_transition(scope_key: str, target: ApexSessionState, reason: str) -> dict[str, Any]:
    """Move the active session for a scope, or say there is none to move."""
    store = get_apex_store()
    session = store.active_for_scope(scope_key)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"no active APEX session for scope {scope_key!r}; create one with POST /api/apex/sessions",
        )
    if session.is_terminal:
        raise HTTPException(status_code=409, detail=f"session {session.session_id} is terminal ('{session.state.value}')")
    if session.state is ApexSessionState.BLOCKED:
        # The approval gate owns a parked session: only an operator verdict
        # (or a replan) may move it. Without this, pause-then-resume would
        # un-park it in two clicks — the gate with a side door.
        raise HTTPException(
            status_code=409,
            detail=(
                f"session {session.session_id} is parked awaiting approval ({session.blocked_reason or 'blocked'}); "
                "it already decides nothing, and the control verbs do not move a blocked session — decide it with "
                "POST /api/apex/approvals/{approval_id}/approve or .../reject, or replan it"
            ),
        )
    if session.state is target:
        return {"applied": False, "reason": f"already {target.value}", "session": session.to_dict()}
    updated = store.set_state(session.session_id, target, reason=reason)
    if updated is None:
        raise HTTPException(status_code=409, detail="transition refused by the session state machine")
    return {"applied": True, "session": updated.to_dict()}


@router.post("/pause", summary="Park the active session for a scope")
async def pause_apex(payload: ScopeRequest, request: Request) -> dict[str, Any]:
    """Park this conversation's session; the executive decides nothing further.

    Work already admitted to a run is owned by ``RunManager`` and is not
    interrupted by a pause — APEX is a control plane, not a second
    lifecycle owner.
    """
    await _require_admin(request)
    scope, _owner = await _resolve_scope(request, payload.scope_key)
    return _control_transition(scope, ApexSessionState.PAUSED, "operator pause")


@router.post("/resume", summary="Release a paused session")
async def resume_apex(payload: ScopeRequest, request: Request) -> dict[str, Any]:
    """Release a paused session. A parked (blocked) session is not resumed here.

    A session at ``BLOCKED`` is behind the approval gate: only an
    operator approval (``POST /api/apex/approvals/{id}/approve``) or a
    replan moves it, which is the property the gate exists to guarantee.
    """
    await _require_admin(request)
    scope, _owner = await _resolve_scope(request, payload.scope_key)
    return _control_transition(scope, ApexSessionState.ACTIVE, "operator resume")


@router.post("/stop", summary="Stop this mission's APEX work")
async def stop_apex(payload: ScopeRequest, request: Request) -> dict[str, Any]:
    """Stop this mission: park its session so the executive decides no more.

    Two boundaries are stated rather than crossed. In-flight runs belong
    to ``RunManager`` and are not interrupted here, and the whole-fleet
    emergency stop is ``alpha.runtime.control``'s ESTOP — deliberately
    not an APEX route, because spec §24 requires the emergency stop to
    stay outside LLM control.
    """
    await _require_admin(request)
    scope, _owner = await _resolve_scope(request, payload.scope_key)
    body = _control_transition(scope, ApexSessionState.PAUSED, "operator stop")
    body["note"] = "mission work stopped; in-flight runs belong to RunManager and are not interrupted, and the whole-fleet emergency stop is the separate ESTOP, which no APEX route can engage"
    return body


# --------------------------------------------------------------------------- #
# Goals — the Goal Operating System's HTTP surface (spec §7, §8, §33)
# --------------------------------------------------------------------------- #
#
# The goal store is the durable record; these routes expose it. Reads are
# unauthenticated-but-authenticated (any signed-in user, owner-scoped
# unless admin); writes are the same. A goal is a planning artifact, so
# it does not carry the session surface's admin gate — but it never
# answers for another user's goals either.


class GoalCreateRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=4000)
    description: str = ""
    #: Create as a child of this goal. A child may not outrank any ancestor.
    parent_goal_id: str = ""
    session_id: str = ""
    mission_id: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    priority: int = 50
    risk: str = "R1"
    budget: dict[str, Any] = Field(default_factory=dict)


class GoalSteerRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    source: str = "user"


async def _goal_or_404(request: Request, goal_id: str) -> Any:
    """One goal the caller may act on, or the reason there is none.

    Mirrors ``_session_or_404``: an unreadable store is 503 rather than a 404
    that claims the goal is absent (the repository's rule is that an unreadable
    source is *unknown*, never empty), and a foreign goal answers 404 with the
    same detail an absent one does.
    """
    store = get_goal_store()
    if store.is_degraded:
        raise HTTPException(status_code=503, detail=f"APEX goal store unreadable: {store.load_error}")
    goal = store.get(goal_id)
    if store.is_degraded:
        raise HTTPException(status_code=503, detail=f"APEX goal store unreadable: {store.load_error}")
    if goal is None:
        raise HTTPException(status_code=404, detail=f"no APEX goal {goal_id!r}")
    owner, is_admin = await _caller(request)
    if not is_admin and goal.owner != owner:
        raise HTTPException(status_code=404, detail=f"no APEX goal {goal_id!r}")
    return goal


@router.post("/goals", summary="Create an APEX goal or subgoal")
async def create_goal(payload: GoalCreateRequest, request: Request) -> dict[str, Any]:
    """Create a goal, optionally as a child of an existing one.

    ``COMPLETED`` is unreachable from here (or anywhere else) without
    measured evidence — creation only ever starts the clock.
    """
    owner, _is_admin = await _caller(request)
    store = get_goal_store()
    if store.is_degraded:
        raise HTTPException(status_code=503, detail=f"goal store unreadable: {store.load_error}")
    parent = await _goal_or_404(request, payload.parent_goal_id) if payload.parent_goal_id else None
    goal_owner = parent.owner if parent is not None else owner
    if payload.session_id:
        linked_session = await _session_or_404(request, payload.session_id)
        if linked_session.owner != goal_owner:
            raise HTTPException(status_code=422, detail="a goal may only link to a session owned by the goal owner")

    kwargs: dict[str, Any] = {
        "objective": payload.objective,
        "owner": goal_owner,
        "description": payload.description,
        "success_criteria": payload.success_criteria,
        "constraints": payload.constraints,
        "priority": payload.priority,
        "risk": payload.risk,
        "budget": payload.budget,
        "session_id": payload.session_id,
        "mission_id": payload.mission_id,
    }
    try:
        goal = store.create_child(payload.parent_goal_id, **kwargs) if payload.parent_goal_id else store.create(**kwargs)
    except (KeyError, ValueError, IllegalGoalTransition) as exc:
        # 422: the request was well-formed but the goal graph refused it —
        # a missing parent, a terminal parent, or a child that would
        # outrank the work it derives from.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"goal": goal.to_dict()}


@router.get("/goals", summary="List APEX goals")
async def list_goals(
    request: Request,
    state: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    root_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """Goals for the caller (all, for an admin), newest-priority first."""
    owner, is_admin = await _caller(request)
    store = get_goal_store()
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "goals": []}
    goals = store.list(
        owner=None if is_admin else owner,
        state=state,
        root_only=root_only,
        session_id=session_id or "",
        limit=limit,
    )
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "goals": []}
    return {"available": True, "count": len(goals), "goals": [g.to_dict() for g in goals]}


@router.get("/goals/{goal_id}", summary="One APEX goal and its subtree")
async def get_goal(goal_id: str, request: Request) -> dict[str, Any]:
    store = get_goal_store()
    goal = await _goal_or_404(request, goal_id)
    return {"goal": goal.to_dict(), "tree": store.tree(goal_id)}


@router.post("/goals/{goal_id}/steer", summary="Record a constraint on a goal")
async def steer_goal(goal_id: str, payload: GoalSteerRequest, request: Request) -> dict[str, Any]:
    """Attach one steering constraint (spec §57). A constraint, not a rewrite."""
    await _goal_or_404(request, goal_id)
    store = get_goal_store()
    updated = store.add_constraint(goal_id, payload.instruction, source=payload.source)
    if updated is None:
        current = store.get(goal_id)
        reason = "goal is terminal" if current and current.is_terminal else "constraint refused"
        raise HTTPException(status_code=409, detail=reason)
    return {"goal": updated.to_dict()}


@router.post("/goals/{goal_id}/replan", summary="Move a goal back to replanning")
async def replan_goal(goal_id: str, request: Request) -> dict[str, Any]:
    """Spec §37's escalation: a stop is not a dead end."""
    await _goal_or_404(request, goal_id)
    store = get_goal_store()
    try:
        updated = store.transition(goal_id, GoalState.REPLANNING, reason="operator replan")
    except IllegalGoalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"goal": updated.to_dict()}


@router.post("/goals/{goal_id}/verify", summary="Verify a goal against its criteria")
async def verify_goal(goal_id: str, request: Request) -> dict[str, Any]:
    """Verification-first completion (spec §16).

    A goal with unmeasured criteria enters ``VERIFYING``; a goal whose
    criteria are all measured closes through the acceptance gate. A
    criterion that measured false is named in the 409 — a partial is
    reported as partial, never folded into a completion.
    """
    await _goal_or_404(request, goal_id)
    store = get_goal_store()
    try:
        updated = store.verify(goal_id)
    except IllegalGoalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"goal": updated.to_dict()}


@router.get("/goals/{goal_id}/tasks", summary="A goal's decomposition")
async def goal_tasks(goal_id: str, request: Request) -> dict[str, Any]:
    """The goal's children — the subgoals a decomposition created."""
    await _goal_or_404(request, goal_id)
    store = get_goal_store()
    children = store.children(goal_id)
    return {"goal_id": goal_id, "count": len(children), "tasks": [c.to_dict() for c in children]}


@router.get("/goals/{goal_id}/agents", summary="Specialists recorded against a goal")
async def goal_agents(goal_id: str, request: Request) -> dict[str, Any]:
    """The agents *asked for* against this goal.

    The subagent lifecycle manager owns the agents themselves — leases,
    heartbeats, recovery. This is the record of the asks, which is what
    makes an empty list mean "none recorded" rather than "none exist".
    """
    goal = await _goal_or_404(request, goal_id)
    return {
        "goal_id": goal_id,
        "count": len(goal.agent_records),
        "agents": [dict(record) for record in goal.agent_records],
        "note": "agents are owned by alpha.subagents.lifecycle; this records the asks made against this goal",
    }


@router.get("/goals/{goal_id}/workflow", summary="A goal's strategy and plan version")
async def goal_workflow(goal_id: str, request: Request) -> dict[str, Any]:
    """What the goal records about its own strategy.

    ``available: false` is deliberate: workflow *graphs* are owned by the
    dynamic workflow engine, and a goal records which strategy it is
    pursuing, not a graph. Reporting a graph here would be a second,
    divergent copy of the truth.
    """
    goal = await _goal_or_404(request, goal_id)
    return {
        "goal_id": goal_id,
        "strategy": goal.current_strategy,
        "plan_version": goal.plan_version,
        "replan_count": goal.replan_count,
        "available": False,
        "reason": "workflow graphs are owned by the dynamic workflow engine; a goal records its strategy, not a graph",
    }


@router.get("/goals/{goal_id}/events", summary="A goal's event history")
async def goal_events(
    goal_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_EVENT_LIMIT, ge=1, le=MAX_EVENT_LIMIT),
) -> dict[str, Any]:
    """Replay the goal's journal — real history, bounded."""
    await _goal_or_404(request, goal_id)
    events = get_apex_store().read_events(goal_id, after_seq=after_seq)[:limit]
    return {"goal_id": goal_id, "count": len(events), "events": [e.to_dict() for e in events]}


@router.get("/goals/{goal_id}/decisions", summary="The cycle decisions of the goal's session")
async def goal_decisions(goal_id: str, request: Request) -> dict[str, Any]:
    """The executive's cycle decisions for the session this goal belongs to.

    Derived from the session's journal, never recomputed: a goal with no
    session link has no cycle decisions, and says so rather than implying
    an empty history it never had.
    """
    goal = await _goal_or_404(request, goal_id)
    if not goal.session_id:
        return {
            "goal_id": goal_id,
            "available": True,
            "decisions": [],
            "note": "goal is not linked to a session, so it has no cycle decisions",
        }
    session_store = get_apex_store()
    if session_store.is_degraded:
        raise HTTPException(status_code=503, detail=f"APEX session store unreadable: {session_store.load_error}")
    linked_session = session_store.get(goal.session_id)
    if session_store.is_degraded:
        raise HTTPException(status_code=503, detail=f"APEX session store unreadable: {session_store.load_error}")
    if linked_session is None:
        return {
            "goal_id": goal_id,
            "session_id": goal.session_id,
            "available": False,
            "count": None,
            "decisions": [],
            "reason": "the linked APEX session is unavailable",
        }
    if linked_session.owner != goal.owner:
        # Older snapshots may contain links written before the creation path
        # enforced the owner boundary. Never let that stale link turn a
        # goal-scoped read into a cross-owner session journal read.
        raise HTTPException(status_code=409, detail="goal session link violates the owner boundary")
    events = session_store.read_events(goal.session_id)
    decisions = [e.to_dict() for e in events if e.event_type.startswith("cycle.")]
    return {
        "goal_id": goal_id,
        "session_id": goal.session_id,
        "count": len(decisions),
        "decisions": decisions,
    }


@router.get("/goals/{goal_id}/evidence", summary="A goal's measured evidence")
async def goal_evidence(goal_id: str, request: Request) -> dict[str, Any]:
    """Every measurement, plus the criteria that still decide nothing."""
    goal = await _goal_or_404(request, goal_id)
    return {
        "goal_id": goal_id,
        "count": len(goal.evidence),
        "criteria": list(goal.success_criteria),
        "criteria_without_evidence": goal.criteria_without_evidence(),
        "criteria_failed": goal.criteria_failed(),
        "evidence": [e.to_dict() for e in goal.evidence],
    }


@router.get("/goals/{goal_id}/failures", summary="A goal's failures, derived from its record")
async def goal_failures(goal_id: str, request: Request) -> dict[str, Any]:
    """What failed, from the record itself — never from a guess.

    Failed criteria carry their latest measurement's provenance; the
    blocked reason and a FAILED state are failures too. An empty list is
    a real answer: nothing has failed yet.
    """
    goal = await _goal_or_404(request, goal_id)
    latest = goal.latest_evidence()
    failures: list[dict[str, Any]] = [
        {
            "kind": "criterion_failed",
            "criterion": criterion,
            "source": latest[criterion].source,
            "detail": latest[criterion].detail,
            "recorded_at": latest[criterion].recorded_at,
        }
        for criterion in goal.criteria_failed()
    ]
    if goal.blocked_reason:
        failures.append({"kind": "blocked", "reason": goal.blocked_reason})
    if goal.state is GoalState.FAILED:
        failures.append({"kind": "goal_state", "state": "failed"})
    return {"goal_id": goal_id, "count": len(failures), "failures": failures}


# --------------------------------------------------------------------------- #
# Approvals — the operator's side of the approval gate (spec §24, §27, §30)
# --------------------------------------------------------------------------- #


class ApprovalDecisionRequest(BaseModel):
    note: str = ""


@router.get("/approvals", summary="Operator decisions on parked work")
async def list_approvals(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """Every approval, newest session first, owner-scoped unless admin.

    The returned list is bounded, but ``count``/``pending`` still describe the
    *whole* matching set — otherwise a truncated response would report a
    smaller backlog than the gate is actually holding. ``returned`` and
    ``truncated`` say which of the two the caller is looking at.
    """
    owner, is_admin = await _caller(request)
    store = get_apex_store()
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "pending": None, "approvals": []}
    approvals = store.approvals(owner=None if is_admin else owner)
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "pending": None, "approvals": []}
    pending = [a for a in approvals if a.get("status") == "pending"]
    bounded = approvals[:limit]
    return {
        "available": True,
        "count": len(approvals),
        "pending": len(pending),
        "returned": len(bounded),
        "truncated": len(bounded) < len(approvals),
        "approvals": bounded,
    }


def _decide_approval_route(approval_id: str, verdict: str, operator: str, note: str) -> dict[str, Any]:
    """Apply one verdict, distinguishing a missing id from a decided one."""
    store = get_apex_store()
    outcome = store.decide_approval(approval_id, verdict=verdict, operator=operator, note=note)
    if outcome is None:
        # A second decision is a conflict; a never-existing id is not found.
        if any(a["approval_id"] == approval_id for a in store.approvals()):
            raise HTTPException(status_code=409, detail=f"approval {approval_id} was already decided")
        raise HTTPException(status_code=404, detail=f"no approval {approval_id!r}")
    record, resumed = outcome
    return {
        "approval": record.to_dict(),
        "resumed": resumed is not None,
        "session": resumed.to_dict() if resumed is not None else None,
    }


@router.post("/approvals/{approval_id}/approve", summary="Approve parked work")
async def approve_approval(approval_id: str, payload: ApprovalDecisionRequest, request: Request) -> dict[str, Any]:
    """The only thing that un-parks a blocked session.

    Admin-gated: an approval is an operator act, the exact point where
    autonomy asks a human. The executive cannot grant its own approval,
    which is the property the gate exists to guarantee.
    """
    operator = await _require_admin(request)
    return _decide_approval_route(approval_id, "approved", operator, payload.note)


@router.post("/approvals/{approval_id}/reject", summary="Reject parked work; the park stands")
async def reject_approval(approval_id: str, payload: ApprovalDecisionRequest, request: Request) -> dict[str, Any]:
    """Record a rejection. The session stays parked, with the blocker on record."""
    operator = await _require_admin(request)
    return _decide_approval_route(approval_id, "rejected", operator, payload.note)


# --------------------------------------------------------------------------- #
# Control
# --------------------------------------------------------------------------- #


@router.post("/sessions", summary="Create an APEX session")
async def create_session(payload: SessionCreateRequest, request: Request) -> dict[str, Any]:
    """Create a session under a profile, optionally narrowed.

    Returns the contract and the first decision. It does **not** start work: the
    response says what the executive would do, and the host adapter performs it.
    """
    owner = await _require_admin(request)
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
        contract_snapshot=contract.to_dict(),
        mission_id=payload.mission_id,
        # A blank request scope uses the caller's APEX mode scope as its
        # conversation id. start_run can then create the thread on first
        # dispatch while subsequent cycles retain one stable per-owner path.
        thread_id=payload.thread_id or owner,
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
    owner, is_admin = await _caller(request)
    store = get_apex_store()
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "sessions": []}
    sessions = store.list(owner=None if is_admin else owner, state=state, limit=limit)
    if store.is_degraded:
        return {"available": False, "reason": store.load_error, "count": None, "sessions": []}
    return {"available": True, "count": len(sessions), "sessions": [s.to_dict() for s in sessions]}


@router.get("/status", summary="APEX status")
async def status(
    request: Request,
    session_id: str | None = Query(default=None),
    include_invariants: bool = Query(default=True),
    scope_key: str = Query(default=""),
) -> dict[str, Any]:
    """The §59 projection. Read-only, so it is safe from a UI poll loop.

    Two things are **resolved** here rather than defaulted, and each was a
    defect:

    * *The contract is the one in force for the scope being reported on*, not
      the ``OFF`` contract this build ships with (``default_contract()``). Two
      other surfaces already report the scope's live profile — ``GET /mode``
      and the ``/apex status`` slash command — so a projection answering
      "Active profile: off (no mission control)" on the line beneath an enabled
      switch contradicted the panel it belongs to. Because ``session_summary``
      measures drift against this same contract, the hard-coded default also
      reported ``contract_drift: true`` for *every* session ever created: a
      warning with no event behind it.
    * *A named session is owner-checked* through ``_session_or_404``. This
      route hands back the objective and the last 25 events, so an unchecked
      ``?session_id=`` was a cross-owner read of another user's mission log —
      answering 404 with the same detail an absent id does, so the refusal
      cannot be used to probe which ids exist.

    ``scope_key`` is resolved by the same helper the toggle uses, so a
    non-admin naming another scope is 403 here exactly as it is on ``/mode``.
    """
    scope, _owner = await _resolve_scope(request, scope_key)
    if session_id:
        session = await _session_or_404(request, session_id)
        # A named session is reported against the contract in force where that
        # mission runs, not against whichever scope the operator happened to be
        # looking from — otherwise an admin inspecting someone else's session
        # would be told it had drifted because of where *they* were standing.
        scope = session.thread_id or scope
    contract = get_apex_mode_store().contract_for(scope)
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


@router.get("/invariants", summary="The §188 invariant set and declared-site availability")
async def invariants() -> dict[str, Any]:
    """One row per declared invariant and an honest description of probe scope.

    Module and symbol presence is a structural probe only; it does not prove
    that runtime paths invoke the guard. ``live`` stays for API compatibility.
    """
    return {"schema": "alpha.apex.invariants.v1", **invariant_summary()}


@router.post("/sessions/{session_id}/steer", summary="Record a steering constraint")
async def steer(session_id: str, payload: SteerRequest, request: Request) -> dict[str, Any]:
    """Record a mission constraint (spec §57).

    A constraint, not a prompt rewrite: nothing here modifies a system prompt,
    and the recorded source distinguishes a user instruction from a
    model-proposed one. Narrowing is deliberately *not* an admin act — the
    owner of a session may always make it stricter — but it is still the
    owner's session to narrow.
    """
    store = get_apex_store()
    await _session_or_404(request, session_id)
    constraint = store.record_constraint(session_id, payload.instruction, priority=payload.priority)
    if constraint is None:
        current = store.get(session_id)
        reason = "session is terminal" if current and current.is_terminal else "constraint refused"
        raise HTTPException(status_code=409, detail=reason)
    return {"constraint": constraint.to_dict()}


@router.post("/sessions/{session_id}/acceptance", summary="Submit measured evidence for a finished APEX run")
async def submit_session_acceptance(
    session_id: str,
    payload: SessionAcceptanceRequest,
    request: Request,
) -> dict[str, Any]:
    """Evaluate every declared criterion and let the executive decide the outcome.

    This is an owner-scoped evidence boundary, not model self-attestation. The
    caller must provide one measured result and its evidence text for every
    declared criterion after a RunManager run finishes. Failed evidence is
    journaled and selects a fresh recovery dispatch; only the acceptance gate
    can complete the session.
    """
    store = get_apex_store()
    session = await _session_or_404(request, session_id)
    if session.state is ApexSessionState.BLOCKED:
        raise HTTPException(status_code=409, detail="blocked sessions require their pending approval before acceptance can be submitted")
    if session.is_terminal:
        raise HTTPException(status_code=409, detail="terminal sessions cannot accept new evidence")
    if session.state is not ApexSessionState.ACTIVE:
        raise HTTPException(status_code=409, detail="acceptance evidence requires an active session")
    if session.dispatch_state != "awaiting_verification" or session.run_status not in {"completed", "success"}:
        raise HTTPException(status_code=409, detail="acceptance evidence is accepted only after a linked RunManager run completes")
    criteria = [str(criterion) for criterion in session.acceptance_criteria]
    if not criteria:
        raise HTTPException(status_code=409, detail="session has no declared acceptance criteria")
    if len(set(criteria)) != len(criteria):
        raise HTTPException(status_code=409, detail="session declares duplicate acceptance criteria")
    supplied = [item.criterion.strip() for item in payload.results]
    if any(not item.evidence.strip() for item in payload.results):
        raise HTTPException(status_code=422, detail="each result must cite non-empty measured evidence")
    if len(set(supplied)) != len(supplied):
        raise HTTPException(status_code=422, detail="each acceptance criterion must appear exactly once")
    missing = sorted(set(criteria) - set(supplied))
    unexpected = sorted(set(supplied) - set(criteria))
    if missing or unexpected:
        detail = "evidence must cover every declared criterion exactly once"
        if missing:
            detail += f"; missing: {', '.join(missing)}"
        if unexpected:
            detail += f"; undeclared: {', '.join(unexpected)}"
        raise HTTPException(status_code=422, detail=detail)

    from alpha.mission.acceptance import AcceptanceReport, CriterionResult, CriterionVerdict

    by_criterion = {item.criterion.strip(): item for item in payload.results}
    report = AcceptanceReport(
        criteria=[
            CriterionResult(
                criterion=criterion,
                verdict=CriterionVerdict.MET if by_criterion[criterion].met else CriterionVerdict.NOT_MET,
                evidence=by_criterion[criterion].evidence.strip(),
                evaluated_at=time.time(),
            )
            for criterion in criteria
        ],
        evaluator="owner_submitted_measured_evidence",
        notes=["Evidence was submitted by the session owner; verify the cited checks or artifacts independently when required."],
    )
    store.update(session_id, acceptance=report.to_dict())
    store.emit(session_id, "acceptance.report_submitted", report=report.to_dict(), run_id=session.run_id)
    cycle_result = run_cycle(store, session_id, _session_contract(session))
    updated = store.get(session_id)
    return {
        "report": report.to_dict(),
        "cycle": cycle_result.to_dict(),
        "session": updated.to_dict() if updated is not None else None,
    }


@router.post("/sessions/{session_id}/cycle", summary="Run one executive cycle")
async def cycle(session_id: str, payload: CycleRequest, request: Request) -> dict[str, Any]:
    """Run one bounded cycle and return its steps and decision.

    Admin-gated: driving the executive forward is an operator act. The cycle
    itself is read-mostly in effect — it records a decision and, where the
    decision implies one, a session state transition. It executes no domain
    work, so this endpoint is an observation surface, not a work surface.
    """
    await _require_admin(request)
    store = get_apex_store()
    session = await _session_or_404(request, session_id)
    contract = _session_contract(session)
    if payload.all_sessions:
        raise HTTPException(status_code=400, detail="name a session; /api/apex/cycle runs one session")
    result = run_cycle(store, session_id, contract)
    return result.to_dict()


@router.post("/sessions/{session_id}/dispatch", summary="Dispatch one APEX objective through RunManager")
async def dispatch_session(session_id: str, request: Request) -> dict[str, Any]:
    """Start or observe this session's idempotent host-adapter run.

    This is the consequential counterpart to `/cycle`: it uses the shared
    Gateway `start_run` choke point, and the `RunManager` remains the lifecycle
    owner. Repeating the request observes or reuses the same durable dispatch
    generation; it does not create a second run.
    """
    await _require_admin(request)
    await _session_or_404(request, session_id)
    from app.gateway.autonomy.loops import apex_execution_tick

    return await apex_execution_tick(request.app, session_id=session_id)


@router.post("/sessions/{session_id}/replan", summary="Replan after a terminal failed APEX run")
async def replan_failed_session(
    session_id: str,
    payload: SessionReplanRequest,
    request: Request,
) -> dict[str, Any]:
    """Prepare a new generation after an operator-reviewed terminal failure.

    APEX never automatically replays an ambiguous terminal run. This explicit
    path verifies the linked RunManager record is terminal and failed, requires
    an operator reason and side-effect acknowledgement, then records the reason
    as a mission constraint. It only makes the session dispatchable; the
    existing supervisor remains the sole owner of the next run admission.
    """
    await _require_admin(request)
    session = await _session_or_404(request, session_id)
    if not payload.acknowledge_possible_side_effects:
        raise HTTPException(
            status_code=422,
            detail="acknowledge_possible_side_effects must be true after reviewing whether the failed run completed an external action",
        )
    if not payload.reason.strip():
        raise HTTPException(status_code=422, detail="reason must contain non-whitespace text")
    if session.state is not ApexSessionState.ACTIVE or session.dispatch_state != "failed":
        raise HTTPException(status_code=409, detail="only an active session linked to a failed dispatch can be replanned")
    if not session.run_id:
        raise HTTPException(
            status_code=409,
            detail="the failed dispatch has no linked RunManager record; inspect it and create a fresh session if its outcome is unknown",
        )
    if session.acceptance is not None:
        raise HTTPException(status_code=409, detail="this run has acceptance evidence; submit or review it through the acceptance gate")

    run_manager = getattr(getattr(request.app, "state", None), "run_manager", None)
    if run_manager is None:
        raise HTTPException(status_code=503, detail="RunManager is unavailable; the failed run cannot be verified")
    try:
        run = await run_manager.get(session.run_id, user_id=session.owner, raise_on_store_error=True)
    except Exception as exc:
        logger.warning("Could not verify failed APEX run %s before operator replan: %s", session.run_id, exc)
        raise HTTPException(status_code=503, detail="RunManager could not verify the failed run") from exc
    if run is None:
        raise HTTPException(status_code=503, detail="the linked RunManager record is unavailable; the failed run cannot be verified")
    run_status = getattr(getattr(run, "status", None), "value", str(getattr(run, "status", "unknown"))).lower()
    if run_status not in {"error", "failed", "interrupted", "cancelled"}:
        raise HTTPException(status_code=409, detail=f"linked run is {run_status}, not a terminal failed run")

    store = get_apex_store()
    if not store.record_run_status(session_id, run_id=session.run_id, status=run_status):
        raise HTTPException(status_code=409, detail="the linked run changed while its status was being verified")
    current = store.get(session_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"no APEX session {session_id!r}")
    if current.run_id in current.usage.retry_reservations:
        raise HTTPException(
            status_code=409,
            detail="safe checkpoint recovery has already reserved this failed run; review that recovery outcome before requesting a replan",
        )
    contract = _session_contract(current)
    updated = store.replan_failed_run(
        session_id,
        run_id=session.run_id,
        run_status=run_status,
        reason=payload.reason.strip(),
        max_replans=contract.budget.max_replans,
    )
    if updated is None:
        limit = contract.budget.max_replans
        used = max(0, int(current.usage.replans or 0))
        if limit is not None and used >= limit:
            raise HTTPException(status_code=409, detail=f"APEX replan ceiling reached ({used}/{limit}); create a new session to continue")
        raise HTTPException(
            status_code=409,
            detail="session state changed, an approval is pending, or safe checkpoint recovery reserved this run; reload and review the current session state",
        )
    return {
        "replanned": True,
        "session": updated.to_dict(),
        "note": "operator replan recorded; the supervisor may dispatch a new generation",
    }


@router.post("/cycle", summary="Run one cycle per non-terminal session")
async def cycle_all(payload: CycleRequest, request: Request) -> dict[str, Any]:
    """The supervisor-loop entry point over HTTP.

    Same work as the background loop's tick, exposed so an operator can drive a
    pass without waiting for the interval. Terminal sessions are skipped, so a
    completed mission is not reopened by a poll.

    Admin-gated like the other control actions: driving the executive forward
    over every session is an operator act, not a self-service one.
    """
    await _require_admin(request)
    store = get_apex_store()
    results = []
    for session in store.list(limit=200):
        if session.is_terminal:
            continue
        contract = _session_contract(session)
        results.append(run_cycle(store, session.session_id, contract).to_dict())
    return {"cycles": len(results), "results": results}


@router.get("/sessions/{session_id}", summary="One APEX session")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    return (await _session_or_404(request, session_id)).to_dict()


@router.get("/sessions/{session_id}/events", summary="APEX session event stream (SSE)")
async def session_events(
    session_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_EVENT_LIMIT, ge=1, le=MAX_EVENT_LIMIT),
) -> StreamingResponse:
    """Replay the durable journal, then tail the live feed.

    Shaped on ``routers/missions.py``: journal first via a worker thread (the
    read touches disk), then a subscriber queue with per-event dedupe, a bounded
    lifetime, and an unsubscribe in ``finally``.
    """
    await _session_or_404(request, session_id)
    store = get_apex_store()

    async def _events():
        import json as _json

        loop = asyncio.get_running_loop()
        seen: set[int] = set()

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        discloser = _BacklogDiscloser()

        def _publish(event: Any) -> None:
            loop.call_soon_threadsafe(_enqueue_or_disclose, queue, event, discloser)

        # Subscribe before taking the journal snapshot. Events emitted before
        # the snapshot are replayed; events emitted after it are queued here.
        # Sequence dedupe below joins the two streams without a blind window.
        APEX_EVENTS.subscribe(session_id, _publish)
        deadline = loop.time() + MAX_STREAM_SECONDS
        try:
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

            while loop.time() < deadline:
                # Checked on every turn, keepalive included: a drop during a
                # quiet stretch must be announced at once rather than at the
                # next event, which for a slow subscriber may never come.
                dropped = discloser.take()
                if dropped:
                    yield (f"event: gap\ndata: {_json.dumps({'dropped': dropped, 'reason': 'subscriber backlog overflow; these events were not delivered live', 'recovery': 'replay with after_seq'})}\n\n")
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
async def set_state(session_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Move a session, refusing an illegal or terminal transition.

    Admin-gated and owner-scoped like the other control writes: the state
    machine is what decides what a session may do next, so a caller who may
    not turn autonomy on may not move the machine either.

    ``COMPLETED`` is **not** reachable here: it requires a passing acceptance
    report and only the executive cycle may set it. A route that could write
    that state directly would reopen the false-completion hole spec §46 exists
    to close.
    """
    await _require_admin(request)
    store = get_apex_store()
    await _session_or_404(request, session_id)
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
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    """Remove a session row.

    Admin-gated, because ``POST /api/apex/sessions`` is: whoever may create a
    session may remove one, and nobody else may. The row is resolved through
    the same owner-scoped lookup first, so a foreign id answers 404 rather
    than telling the caller it exists.
    """
    await _require_admin(request)
    await _session_or_404(request, session_id)
    if not get_apex_store().delete(session_id):
        raise HTTPException(status_code=404, detail=f"no APEX session {session_id!r}")
    return {"deleted": session_id}
