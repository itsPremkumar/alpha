"""Completion-engine REST surface: acceptance contracts, gates, evidence, finalization.

The durable ledger (`alpha.completion`) has always answered *did the work
actually succeed?* — but until this router nothing in the Gateway could ask it.
This is the surface that keeps the engine's promise for the UI, an operator and
a script alike:

* ``GET  /api/completion/health``                — engine posture: enabled/mode/enforcing, limits, ledger readability
* ``GET  /api/completion/checks``                — the registered check names a gate may reference
* ``GET  /api/completion/scopes``                — summaries, filterable by ``thread_id`` / ``status`` / ``owner_id``
* ``POST /api/completion/scopes``                — open a scope: contract + task DAG + gates, validated before it may run
* ``GET  /api/completion/scopes/{id}``           — the full live projection (tasks, gates, evidence, dispatches, budget)
* ``GET  /api/completion/scopes/{id}/contract``  — the fixed obligations and their validation findings
* ``GET  /api/completion/scopes/{id}/events``    — the bounded event journal (one canonical history for UI/logs/agents)
* ``POST /api/completion/scopes/{id}/...``       — start, schedule, dispatch, verify, approve, review, finalize

Honesty contract (each of these is a test in ``tests/test_completion_router.py``):

* **``complete`` is unreachable except through ``finalize``, and the report
  carries ``enforced: false`` rather than a verdict the engine did not apply.**
  In ``observe`` mode (and on a disabled engine) the decision is recorded and
  the status is left alone — an engine that reported a completion it did not
  apply would be the exact lie the subsystem exists to prevent.
* **A worker's prose is ``returned``, never ``verified``** — ``POST
  .../return`` records what the worker said; only the task's own gates make it
  verified. Nothing on this surface can hand a task ``verified`` directly.
* **An unreadable ledger is 503 with its real reason**, never an empty list:
  "I could not look" and "I looked and found nothing" lead to opposite
  decisions, and a healthy-but-empty store is the only thing that reports zero.
* **A contract with blocking findings is 422 with every finding**, and the
  refused scope is still readable — a rejected plan is a record the caller can
  repair, not a silent no-op.
* **Refusals keep the engine's own words.** ``409`` carries the typed error's
  message and ``code`` (``illegal_transition`` / ``scope_state_error`` /
  ``budget_exhausted`` / ``ownership_conflict`` / ``approval_required`` …), so
  a caller can distinguish "never legal" from "not dispatchable" from "policy
  said no" without string-matching prose.
* **Scope:** members read their own scopes (``owner_id`` == caller); a scope
  with an empty ``owner_id`` exists but is admin-only, because "whose scope is
  this?" must not be guessed. Admins see every scope. Every mutation is
  admin-gated: writing the ledger decides what counts as done, and an
  unauthenticated or member caller must not be able to move a scope toward
  ``complete``.
* **Route order is load-bearing.** The fixed collection routes (``/health``,
  ``/checks``, ``/scopes``) are declared before ``/scopes/{scope_id}``, the
  same Starlette registration-order rule as ``skills/{skill_name}`` and
  ``workflows/{workflow_id}``.

This surface drives the engine; it never reimplements it. Every refusal above
comes from a typed ``CompletionError`` raised inside the harness, mapped once
here onto one HTTP status and one machine-readable code.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from alpha.completion import (
    ApprovalRefused,
    ApprovalRequired,
    BudgetExhausted,
    CheckUnavailable,
    CompletionEngine,
    CompletionError,
    ContractInvalid,
    EventKind,
    Forbidden,
    GateInput,
    GateStatus,
    IllegalTransition,
    NotFound,
    OwnershipConflict,
    PathEscape,
    RequirementInput,
    ScopeCreateRequest,
    ScopeStateError,
    ScopeStatus,
    StoreUnreadable,
    TaskInput,
    TaskState,
    scope_summary,
    settings_from_config,
)
from alpha.completion.verifier import REVERIFY_MODES
from alpha.utils.time import coerce_iso
from app.gateway.deps import is_admin_user

router = APIRouter(tags=["completion"])

#: Machine-readable codes carried beside a plain-string ``detail`` (the shape
#: ``skills`` established: the frontend renders ``detail``, and ``code`` lets a
#: caller distinguish an absent resource from a missing route).
NOT_FOUND_CODE = "completion_not_found"
DISABLED_CODE = "completion_disabled"
FORBIDDEN_CODE = "forbidden"
INVALID_FILTER_CODE = "invalid_filter"
#: A body that is structurally unusable (not a filter — the *request* is).
INVALID_INPUT_CODE = "invalid_input"

_LIST_LIMIT_DEFAULT = 100
_LIST_LIMIT_MAX = 500

#: Every scope/task/gate status the API accepts as a filter, in enum order.
#: Kept as tuples (not derived at call time) so the 422 message is stable and
#: the response cannot change shape because an enum gained a member.
_SCOPE_STATUSES: tuple[str, ...] = tuple(status.value for status in ScopeStatus)
_TASK_STATES: tuple[str, ...] = tuple(state.value for state in TaskState)
_GATE_STATUSES: tuple[str, ...] = tuple(status.value for status in GateStatus)
_EVENT_KINDS: tuple[str, ...] = tuple(kind.value for kind in EventKind)


# --------------------------------------------------------------------------------------
# Request shapes (extra="forbid": a field must be chosen, not leaked)
# --------------------------------------------------------------------------------------
class ReviseContractRequest(BaseModel):
    """Replace the scope's obligations; version, digest and revision move together."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[RequirementInput] = Field(..., min_length=1)


class AddTasksRequest(BaseModel):
    """Extend the plan under the scope's task/depth ceilings (never clamped)."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[Any] = Field(..., min_length=1, description="TaskInput rows; see GET /api/completion/checks for the shape the engine accepts.")
    gates: list[Any] = Field(default_factory=list, description="GateInput rows registered alongside the tasks.")


class DispatchRequest(BaseModel):
    """Ask for one task's launch. The task stays READY until a host confirms."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., min_length=1, max_length=64)
    agent: str = Field(default="", max_length=200, description="Which worker this is offered to; empty records an unattributed request.")


class ConfirmLaunchRequest(BaseModel):
    """A host confirmed a dispatch: the handle is what makes a task in-flight."""

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(..., min_length=1, max_length=200)


class LaunchFailedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str = Field(..., min_length=1, max_length=2000)


class ReturnRequest(BaseModel):
    """A worker reported a result — recorded as RETURNED, never VERIFIED."""

    model_config = ConfigDict(extra="forbid")

    artifacts: list[str] = Field(default_factory=list, max_length=200)
    reason: str = Field(default="", max_length=2000)


class BlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


class VerifyRequest(BaseModel):
    """Run a task's gates. ``status`` computes without executing; ``reverify``
    re-runs required gates even when they previously passed."""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="verify", description=f"One of {list(REVERIFY_MODES)}")
    worker: str = Field(default="", max_length=200)


class RunGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str = Field(default="", max_length=200)


class ApproveRequest(BaseModel):
    """Approve a gate's *current* definition; the grant is bound to its digest."""

    model_config = ConfigDict(extra="forbid")

    approver: str = Field(..., min_length=1, max_length=200)
    reason: str = Field(default="", max_length=2000)


class RevokeApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


class ReviewRequest(BaseModel):
    """A human verdict on a ``manual.review`` gate, attributed to a named reviewer."""

    model_config = ConfigDict(extra="forbid")

    verdict: str = Field(..., description="'pass' or 'fail'")
    reviewer: str = Field(..., min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


class FinalizeRequest(BaseModel):
    """The completion guard. ``rerun_required`` re-executes required gates at
    this boundary so a stale pass cannot answer for work done since."""

    model_config = ConfigDict(extra="forbid")

    rerun_required: bool = True
    rerun_gate_ids: list[str] | None = Field(default=None, max_length=200)


class RecoverRequest(BaseModel):
    """Reconnect reconciliation: fold what the host reports back into the ledger."""

    model_config = ConfigDict(extra="forbid")

    host_states: dict[str, str] = Field(..., min_length=1, description="dispatch_id (or task_id) -> the host's own state string.")


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _error(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    """The one error shape this router returns: ``detail`` + ``code``, top-level."""
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


async def _caller(request: Request) -> tuple[str, bool]:
    """Resolve ``(caller_id, is_admin)``, failing closed.

    Same contract as ``routers/side_effects.py::_caller``: the admin question
    goes to ``app.gateway.deps.is_admin_user`` (the repository's single
    definition — it is what suppresses the admin signal for a PAT), and no
    caller resolves to "every owner", because owner-less reads list everyone's
    scopes.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    owner = str(getattr(user, "id", "") or "")
    is_admin = await is_admin_user(request)
    if not owner and not is_admin:
        # A member with no id cannot be scoped: every filter would degrade to
        # "no filter", which for this surface means *everyone's* scopes.
        raise HTTPException(status_code=401, detail="authenticated caller carries no id; cannot scope a member read")
    return owner, is_admin


async def _require_admin(request: Request) -> str:
    """Resolve the caller, refusing a non-admin mutation.

    Returns the caller's own id (the owner recorded on anything created here).
    Writing this ledger is what decides whether work counts as done, so a
    member cannot move a scope toward ``complete``; the refusal is 403 with the
    reason rather than a silent no-op.
    """
    owner, is_admin = await _caller(request)
    if not is_admin:
        raise Forbidden(f"completion mutations require an administrator (caller {owner!r} is a member)")
    return owner


def _engine() -> CompletionEngine:
    """Build an engine from the live (hot-reloaded) config.

    Synchronous and config-reading, so callers must reach it through
    ``asyncio.to_thread``: ``get_app_config()`` stats the config file on every
    call, and the engine's own checks read the filesystem and run bounded
    subprocesses. Both would stall the Gateway event loop inline.
    """
    return CompletionEngine(settings_from_config())


async def _with_engine(fn) -> Any:
    """Run ``fn(engine)`` off the event loop, mapping typed refusals to HTTP."""
    engine = await asyncio.to_thread(_engine)
    try:
        return await asyncio.to_thread(fn, engine)
    except CompletionError as exc:
        return _refusal(exc)


def _refusal(exc: CompletionError) -> JSONResponse:
    """One typed refusal -> one status + one machine-readable code.

    Store failures are 503 (``could not look``), an absent resource is
    404, contract/path problems are 422 (the *request* is unusable),
    lifecycle/policy refusals are 409 (the *state* refuses this move),
    and anything unmapped is a 400 carrying its own code rather than a
    500.
    """
    if isinstance(exc, StoreUnreadable):  # unreadable, corrupt, and write failures
        return _error(503, exc.code, str(exc))
    if isinstance(exc, NotFound):
        return _error(404, NOT_FOUND_CODE, str(exc), subject=exc.subject)
    if isinstance(exc, ContractInvalid):
        return _error(
            422,
            exc.code,
            str(exc),
            findings=[finding.model_dump(mode="json") for finding in getattr(exc, "findings", [])],
            subject=exc.subject,
        )
    if isinstance(exc, PathEscape):
        return _error(422, exc.code, str(exc), subject=exc.subject)
    if isinstance(exc, Forbidden):
        return _error(403, exc.code, str(exc))
    if isinstance(exc, OwnershipConflict):
        return _error(409, exc.code, str(exc), subject=exc.subject, conflicts=getattr(exc, "conflicts", []))
    if isinstance(exc, (IllegalTransition, ScopeStateError, BudgetExhausted, CheckUnavailable, ApprovalRequired, ApprovalRefused)):
        return _error(409, exc.code, str(exc), subject=exc.subject)
    return _error(400, exc.code, str(exc), subject=exc.subject)


def _visible_scope(engine: CompletionEngine, scope_id: str, caller: str, is_admin: bool) -> Any:
    """Load one scope for reading, applying owner scoping.

    Absent and not-yours answer identically (404): a member read resolved by id
    alone would be an existence oracle across users. A scope recorded without
    an owner is admin-only for the same reason an owner-less side-effect row
    is — nobody's name is on it to show a member.
    """
    scope = engine.repo.load(scope_id)  # raises NotFound -> 404
    if is_admin or (scope.owner_id and scope.owner_id == caller):
        return scope
    raise NotFound(f"scope {scope_id!r} is not recorded for this caller", subject=scope_id)


def _iso_times(payload: dict[str, Any]) -> dict[str, Any]:
    """Render every ``*_at`` epoch as ISO 8601, the wire contract the other
    evidence/ops surfaces keep. ``None`` stays ``None`` (an open row has no
    terminal time, and a zero timestamp would read as one)."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key.endswith("_at") and isinstance(value, (int, float)):
            out[key] = coerce_iso(value)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------------------
# Engine posture (fixed collection routes — declared before the /scopes catch-all)
# --------------------------------------------------------------------------------------
@router.get(
    "/api/completion/health",
    summary="Completion-engine posture: enabled/mode/enforcing, limits, ledger readability",
    description=(
        "What this deployment's completion engine will actually do. `enforcing` is enabled *and* enforce mode, so a "
        "disabled engine never reads as a live gate; `readable: false` carries the ledger's real reason (503-adjacent "
        "honesty in one place) and `count` is null, never 0, when the store could not be read."
    ),
)
async def completion_health(request: Request) -> Any:
    await _caller(request)
    payload = await _with_engine(lambda engine: engine.health())
    if isinstance(payload, JSONResponse):
        return payload
    payload["generated_at"] = coerce_iso(time.time())
    return payload


@router.get(
    "/api/completion/checks",
    summary="The registered check implementations a gate may reference",
    description=("The allowlist behind every `check_ref`. Contract validation refuses an unregistered name *before* dispatch, so this list is what a planner writes gates against — never free-form shell text from a model."),
)
async def completion_checks(request: Request) -> Any:
    await _caller(request)
    engine = await asyncio.to_thread(_engine)
    settings = engine.settings
    return {
        "checks": sorted(engine.registry.names()),
        "reverify_modes": list(REVERIFY_MODES),
        "scope_statuses": list(_SCOPE_STATUSES),
        "task_states": list(_TASK_STATES),
        "gate_statuses": list(_GATE_STATUSES),
        "event_kinds": list(_EVENT_KINDS),
        "policy": {
            "enabled": settings.enabled,
            "mode": settings.mode,
            "enforcing": settings.enforcing,
            "guarding": settings.guarding,
            "policy_id": settings.policy_id,
            "allow_network_checks": settings.allow_network_checks,
            "preapproved_commands": sorted(settings.preapproved_commands),
            "workspace_root": str(settings.workspace_root),
        },
        "limits": {
            "max_tasks": settings.max_tasks,
            "max_depth": settings.max_depth,
            "max_concurrency": settings.max_concurrency,
            "max_gate_runs": settings.max_gate_runs,
            "wall_clock_seconds": settings.wall_clock_seconds,
            "no_progress_limit": settings.no_progress_limit,
            "max_scopes": settings.max_scopes,
        },
    }


# --------------------------------------------------------------------------------------
# Scopes: collection
# --------------------------------------------------------------------------------------
@router.get(
    "/api/completion/scopes",
    summary="List completion scopes (summaries)",
    description=(
        "Newest-first summaries. A member sees only their own scopes; a scope recorded without an owner is admin-only; "
        "an admin sees every scope (and may filter by `owner_id`). `status=unknown` is refused rather than matching "
        "nothing, so a typo cannot read as 'no work ever ran'."
    ),
)
async def list_scopes(
    request: Request,
    thread_id: str | None = Query(None, description="Exact thread id filter"),
    status: str | None = Query(None, description=f"Exact scope status: {' | '.join(_SCOPE_STATUSES)}"),
    owner_id: str | None = Query(None, description="Owner filter (admins only; members are always scoped to themselves)"),
    include_terminal: bool = Query(True, description="Include complete/cancelled scopes"),
    limit: int = Query(_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
) -> Any:
    caller, is_admin = await _caller(request)
    if status is not None and status not in _SCOPE_STATUSES:
        return _error(422, INVALID_FILTER_CODE, f"unknown status {status!r}; expected one of {list(_SCOPE_STATUSES)}")

    def _run(engine: CompletionEngine) -> list[dict[str, Any]]:
        owner_filter = caller if not is_admin else owner_id
        return engine.list_scopes(owner_id=owner_filter, thread_id=thread_id or None, limit=limit)

    rows = await _with_engine(_run)
    if isinstance(rows, JSONResponse):
        return rows
    return {
        "reported": True,
        "scope": "all" if is_admin else "owner",
        "count": len(rows),
        "scopes": [_iso_times(row) for row in rows],
    }


@router.post(
    "/api/completion/scopes",
    status_code=201,
    summary="Open a scope: acceptance contract + task DAG + gates",
    description=(
        "Validated before it may run. A contract with blocking findings is 422 carrying every finding — and the refused "
        "scope is still readable, because a rejected plan is a record the caller can repair. An unknown `check_ref` is a "
        "finding, not a runtime surprise. Only an administrator opens a scope; `owner_id` attributes it (an operator "
        "recording work on a user's behalf) and defaults to the caller — a member cannot create one at all."
    ),
)
async def create_scope(request: Request) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return _error(422, INVALID_INPUT_CODE, f"invalid JSON body: {exc}")

    try:
        payload = ScopeCreateRequest.model_validate(body)
    except Exception as exc:  # noqa: BLE001 - a malformed body is a 422 with the reason
        return _error(422, INVALID_INPUT_CODE, f"invalid scope request: {exc}")

    if not payload.owner_id:
        payload.owner_id = caller

    def _run(engine: CompletionEngine) -> Any:
        scope = engine.create_scope(payload)
        return _iso_times(scope_summary(scope))

    try:
        summary = await _with_engine(_run)
    except ContractInvalid as exc:
        # create_scope persists the refused scope before raising, so the
        # findings travel with the id the caller reads back.
        return _error(
            422,
            exc.code,
            str(exc),
            findings=[finding.model_dump(mode="json") for finding in exc.findings],
            subject=exc.subject,
        )
    if isinstance(summary, JSONResponse):
        return summary
    return {"created": True, "scope": summary}


# --------------------------------------------------------------------------------------
# Scopes: one scope (reads)
# --------------------------------------------------------------------------------------
@router.get(
    "/api/completion/scopes/{scope_id}",
    summary="Full live projection of one scope",
    description=(
        "Summary + contract + task DAG (with readiness and per-gate statuses) + gates (with latest evidence and freshness) "
        "+ dispatches + ownership + budget + scheduling + the last final report. Freshness is recomputed on read, so an "
        "edited file demotes a stale green gate rather than answering for work done after the pass."
    ),
)
async def get_scope(request: Request, scope_id: str) -> Any:
    caller, is_admin = await _caller(request)

    def _run(engine: CompletionEngine) -> Any:
        _visible_scope(engine, scope_id, caller, is_admin)
        return _iso_times(engine.status(scope_id)["scope"])

    payload = await _with_engine(_run)
    if isinstance(payload, JSONResponse):
        return payload
    return {"scope": payload}


@router.get(
    "/api/completion/scopes/{scope_id}/contract",
    summary="The fixed obligations and their validation findings",
    description=(
        "`valid: false` with blocking findings means dispatch is refused until the contract is repaired. Findings name the requirement/task/gate at fault so a planner can fix the plan instead of guessing why work would not start."
    ),
)
async def get_contract(request: Request, scope_id: str) -> Any:
    caller, is_admin = await _caller(request)

    def _run(engine: CompletionEngine) -> Any:
        scope = _visible_scope(engine, scope_id, caller, is_admin)
        return {
            "scope_id": scope.scope_id,
            "request_revision": scope.request_revision,
            "request_snapshot": scope.request_snapshot,
            "contract": scope.contract.model_dump(mode="json"),
        }

    payload = await _with_engine(_run)
    if isinstance(payload, JSONResponse):
        return payload
    return payload


@router.get(
    "/api/completion/scopes/{scope_id}/events",
    summary="The scope's bounded event journal",
    description=("One canonical history for the UI, monitoring agents and diagnostics, so three consumers cannot each invent their own status. Bounded by `completion.max_events`: trimming drops the oldest and records that it did."),
)
async def get_events(
    request: Request,
    scope_id: str,
    limit: int = Query(200, ge=1, le=2000),
    kind: list[str] | None = Query(None, description=f"Repeatable filter; one of {list(_EVENT_KINDS)[:6]}…"),
) -> Any:
    caller, is_admin = await _caller(request)
    kinds: set[str] | None = None
    if kind:
        unknown = [value for value in kind if value not in _EVENT_KINDS]
        if unknown:
            return _error(422, INVALID_FILTER_CODE, f"unknown event kind(s) {unknown}; expected one of {list(_EVENT_KINDS)}")
        kinds = set(kind)

    def _run(engine: CompletionEngine) -> Any:
        _visible_scope(engine, scope_id, caller, is_admin)
        return engine.events(scope_id, limit=limit, kinds=kinds)

    rows = await _with_engine(_run)
    if isinstance(rows, JSONResponse):
        return rows
    return {
        "scope_id": scope_id,
        "count": len(rows),
        "events": [{**row, "at": coerce_iso(row["at"]) if isinstance(row.get("at"), (int, float)) else row.get("at")} for row in rows],
    }


@router.get(
    "/api/completion/scopes/{scope_id}/reconciliation",
    summary="Read-only audit of the stored request against the contract",
    description=(
        "Which request clauses have no requirement, which mandatory requirements lack current evidence, and which are bound to no observable at all. The basis is named (`heuristic_clause_match`) because it is a heuristic and says so."
    ),
)
async def get_reconciliation(request: Request, scope_id: str) -> Any:
    caller, is_admin = await _caller(request)

    def _run(engine: CompletionEngine) -> Any:
        _visible_scope(engine, scope_id, caller, is_admin)
        return engine.reconcile_request(scope_id)

    report = await _with_engine(_run)
    if isinstance(report, JSONResponse):
        return report
    return report.model_dump(mode="json")


@router.get(
    "/api/completion/scopes/{scope_id}/finalization/preview",
    summary="The completion decision without applying it",
    description=("What finalize *would* decide, from the evidence recorded now. The report is labelled `enforced: false` — this route never moves a scope, and an observe-mode engine never claims a completion it did not apply."),
)
async def preview_finalization(request: Request, scope_id: str) -> Any:
    caller, is_admin = await _caller(request)

    def _run(engine: CompletionEngine) -> Any:
        _visible_scope(engine, scope_id, caller, is_admin)
        return engine.preview_finalization(scope_id)

    report = await _with_engine(_run)
    if isinstance(report, JSONResponse):
        return report
    payload = report.model_dump(mode="json")
    payload["generated_at"] = coerce_iso(report.generated_at)
    return payload


# --------------------------------------------------------------------------------------
# Scopes: lifecycle mutations (admin)
# --------------------------------------------------------------------------------------
@router.post(
    "/api/completion/scopes/{scope_id}/start",
    summary="Start the work (or resume a parked scope with an honest reason)",
    description=("Refused while the contract is invalid, and refused on a terminal scope — a silent no-op would leave the caller believing a cancelled scope was started. A parked scope resumes with its reason recorded."),
)
async def start_scope(request: Request, scope_id: str) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    scope = await _with_engine(lambda e: e.start(scope_id))
    if isinstance(scope, JSONResponse):
        return scope
    return {"started": True, "status": scope.status.value, "status_reason": scope.status_reason}


@router.post(
    "/api/completion/scopes/{scope_id}/cancel",
    summary="Cancel a scope with a recorded reason",
    description="Live tasks are abandoned (not verified) and their path claims released; the reason is journalled.",
)
async def cancel_scope(request: Request, scope_id: str, body: CancelRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    scope = await _with_engine(lambda e: e.cancel(scope_id, body.reason))
    if isinstance(scope, JSONResponse):
        return scope
    return {"cancelled": True, "status": scope.status.value, "status_reason": scope.status_reason}


@router.post(
    "/api/completion/scopes/{scope_id}/contract/revise",
    summary="Amend the obligations: version, digest and request revision move together",
    description=("Gates that previously passed are demoted to STALE: the obligations moved, so what a gate proved no longer answers the question now being asked. A revised contract with blocking findings is 422 carrying them."),
)
async def revise_contract(request: Request, scope_id: str, body: ReviseContractRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    result = await _with_engine(lambda e: e.revise_contract(scope_id, body.requirements))
    if isinstance(result, JSONResponse):
        return result
    return {"revised": True, "version": result.contract.version, "request_revision": result.request_revision, "valid": result.contract.valid, "digest": result.contract.digest}


@router.post(
    "/api/completion/scopes/{scope_id}/tasks",
    summary="Extend the plan under the scope's task/depth ceilings",
    description=("Ceilings are refused with the numbers named, never clamped: a plan that needs more room must be split or handed off rather than silently growing. Gates registered here are attached to their task automatically."),
)
async def add_tasks(request: Request, scope_id: str, body: AddTasksRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")

    try:
        tasks = [TaskInput.model_validate(item) for item in body.tasks]
        gates = [GateInput.model_validate(item) for item in body.gates]
    except Exception as exc:  # noqa: BLE001
        return _error(422, INVALID_INPUT_CODE, f"invalid task/gate input: {exc}")

    def _run(engine: CompletionEngine) -> Any:
        scope = engine.add_tasks(scope_id, tasks, gates)
        return {"tasks": len(scope.tasks), "gates": len(scope.gates)}

    result = await _with_engine(_run)
    if isinstance(result, JSONResponse):
        return result
    return {"added": True, **result}


@router.post(
    "/api/completion/scopes/{scope_id}/schedule",
    summary="Recompute readiness and report what may start under the ceiling",
    description=("Promotes tasks whose dependencies are now verified, then reports the dispatchable set and the remaining capacity. A planning scope must be started first."),
)
async def schedule_scope(request: Request, scope_id: str) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    result = await _with_engine(lambda e: e.schedule(scope_id))
    if isinstance(result, JSONResponse):
        return result
    return result


@router.post(
    "/api/completion/scopes/{scope_id}/dispatch",
    summary="Request one task's launch (the task stays READY until a host confirms)",
    description=(
        "Refused on an invalid contract, an exhausted budget, or a task that is not dispatchable — each with the engine's "
        "own reason. Declared `owns:` paths are claimed here, before launch, so two workers cannot be pointed at the same files."
    ),
)
async def dispatch_task(request: Request, scope_id: str, body: DispatchRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    record = await _with_engine(lambda e: e.dispatch(scope_id, body.task_id, agent=body.agent))
    if isinstance(record, JSONResponse):
        return record
    return record.model_dump(mode="json")


@router.post(
    "/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/confirm",
    summary="A host confirmed a launch: the handle is what makes a task in-flight",
    description="The handle is never fabricated here — it is what the host reported.",
)
async def confirm_launch(request: Request, scope_id: str, dispatch_id: str, body: ConfirmLaunchRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    record = await _with_engine(lambda e: e.confirm_launch(scope_id, dispatch_id, body.handle))
    if isinstance(record, JSONResponse):
        return record
    return record.model_dump(mode="json")


@router.post(
    "/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/launch-failed",
    summary="Record a failed launch (persisted, not retried into a loop)",
    description="A partial launch wave stays auditable: launched handles and failed launches are both recorded.",
)
async def launch_failed(request: Request, scope_id: str, dispatch_id: str, body: LaunchFailedRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    record = await _with_engine(lambda e: e.launch_failed(scope_id, dispatch_id, body.error))
    if isinstance(record, JSONResponse):
        return record
    return record.model_dump(mode="json")


@router.post(
    "/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return",
    summary="Record a worker's result as RETURNED — never VERIFIED",
    description=("The worker's prose and artifacts are recorded; the task moves to `returned` and is verified only by its own gates. This route cannot hand a task `verified`, which is the whole point of the state machine."),
)
async def worker_returned(request: Request, scope_id: str, dispatch_id: str, body: ReturnRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    task = await _with_engine(lambda e: e.worker_returned(scope_id, dispatch_id, artifacts=body.artifacts, reason=body.reason))
    if isinstance(task, JSONResponse):
        return task
    return {"task_id": task.task_id, "state": task.state.value, "attempts": task.attempts}


@router.post(
    "/api/completion/scopes/{scope_id}/tasks/{task_id}/block",
    summary="Mark a task blocked, parking everything that depends on it",
    description="A blocked task carries its reason; dependents are parked rather than silently bypassed.",
)
async def block_task(request: Request, scope_id: str, task_id: str, body: BlockRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    task = await _with_engine(lambda e: e.mark_task_blocked(scope_id, task_id, body.reason))
    if isinstance(task, JSONResponse):
        return task
    return {"task_id": task.task_id, "state": task.state.value, "blocked_reason": task.blocked_reason}


@router.post(
    "/api/completion/scopes/{scope_id}/tasks/{task_id}/verify",
    summary="Run a task's gates and record the evidence",
    description=(
        "`status` computes without executing; `verify` runs only what is not already proven current; `reverify` re-runs "
        "required gates even when they previously passed. Retry exhaustion moves the task to FAILED with the reason, never "
        "to VERIFIED."
    ),
)
async def verify_task(request: Request, scope_id: str, task_id: str, body: VerifyRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    if body.mode not in REVERIFY_MODES:
        return _error(422, INVALID_FILTER_CODE, f"unknown mode {body.mode!r}; expected one of {list(REVERIFY_MODES)}")
    result = await _with_engine(lambda e: e.verify_task(scope_id, task_id, mode=body.mode, worker=body.worker))
    if isinstance(result, JSONResponse):
        return result
    return result.to_dict()


@router.post(
    "/api/completion/scopes/{scope_id}/gates/{gate_id}/run",
    summary="Execute one gate and record its evidence",
    description=("Budget-charged and policy-checked. A gate whose approval is missing or stale is BLOCKED (and the required approval is named), never passed — `not run` is not `passed`."),
)
async def run_gate(request: Request, scope_id: str, gate_id: str, body: RunGateRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    evidence = await _with_engine(lambda e: e.run_gate(scope_id, gate_id, worker=body.worker))
    if isinstance(evidence, JSONResponse):
        return evidence
    return evidence.model_dump(mode="json")


@router.get(
    "/api/completion/scopes/{scope_id}/gates/{gate_id}/approval",
    summary="Describe what an approval for this gate would cover",
    description="The grant is bound to the gate's definition digest. The response names the digest and the reason the gate is asking, so the UI can ask honestly without guessing.",
)
async def approval_request(request: Request, scope_id: str, gate_id: str) -> Any:
    caller, is_admin = await _caller(request)

    def _run(engine: CompletionEngine) -> Any:
        _visible_scope(engine, scope_id, caller, is_admin)
        return engine.approval_request(scope_id, gate_id)

    payload = await _with_engine(_run)
    if isinstance(payload, JSONResponse):
        return payload
    return payload


@router.post(
    "/api/completion/scopes/{scope_id}/gates/{gate_id}/approve",
    summary="Approve a gate's current definition (bound to its digest)",
    description=("The grant is bound to the gate's definition digest: changing the check, its parameters or its expectation invalidates the approval rather than carrying it to a gate that was never reviewed."),
)
async def approve_gate(request: Request, scope_id: str, gate_id: str, body: ApproveRequest) -> Any:
    await _require_admin(request)
    record = await _with_engine(lambda e: e.approve(scope_id, gate_id, body.approver, reason=body.reason))
    if isinstance(record, JSONResponse):
        return record
    return record


@router.post(
    "/api/completion/scopes/{scope_id}/gates/{gate_id}/revoke",
    summary="Revoke a gate's approval",
    description="The approval table row is dropped and the revocation is journalled; a later run needs a fresh grant.",
)
async def revoke_gate(request: Request, scope_id: str, gate_id: str, body: RevokeApprovalRequest) -> Any:
    await _require_admin(request)
    existed = await _with_engine(lambda e: e.revoke_approval(scope_id, gate_id, reason=body.reason))
    if isinstance(existed, JSONResponse):
        return existed
    return {"revoked": bool(existed)}


@router.post(
    "/api/completion/scopes/{scope_id}/gates/{gate_id}/review",
    summary="Record a human verdict on a manual-review gate",
    description=("The verdict lands as evidence attributed to a named reviewer — never disguised as an automated pass. A failed review is FAILED evidence, not NOT_RUN. Only `manual.review` gates accept a review."),
)
async def review_gate(request: Request, scope_id: str, gate_id: str, body: ReviewRequest) -> Any:
    await _require_admin(request)
    if body.verdict not in {"pass", "fail"}:
        return _error(422, INVALID_FILTER_CODE, f"verdict must be 'pass' or 'fail', got {body.verdict!r}")
    gate = await _with_engine(lambda e: e.review_gate(scope_id, gate_id, verdict=body.verdict, reviewer=body.reviewer, notes=body.notes))
    if isinstance(gate, JSONResponse):
        return gate
    return {"gate_id": gate.gate_id, "status": gate.status.value, "last_failure": gate.last_failure}


@router.post(
    "/api/completion/scopes/{scope_id}/recover",
    summary="Reconnect reconciliation: fold what the host reports into the ledger",
    description=("For a Gateway restart or a worker that outlived its connection: reports the changed dispatches, any expired ownership claims, and launch waves whose fate is still unresolved — rather than assuming a wave completed."),
)
async def recover_scope(request: Request, scope_id: str, body: RecoverRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")
    result = await _with_engine(lambda e: e.recover(scope_id, body.host_states))
    if isinstance(result, JSONResponse):
        return result
    return result


@router.post(
    "/api/completion/scopes/{scope_id}/finalize",
    summary="The completion guard — the only route to `complete`",
    description=(
        "Evaluates the contract, every required gate's freshness, the task DAG and the original request, then moves the "
        "scope. `rerun_required` (default true) re-executes required gates at this boundary so a stale pass cannot answer "
        "for work done since. A non-complete outcome is 200 with `complete: false` and its blockers — the refusal *is* the "
        "answer, not a transport error. In observe mode (or on a disabled engine) the decision is recorded with "
        "`enforced: false` and the status is left alone."
    ),
)
async def finalize_scope(request: Request, scope_id: str, body: FinalizeRequest) -> Any:
    caller, is_admin = await _caller(request)
    if not is_admin:
        return _error(403, FORBIDDEN_CODE, f"completion mutations require an administrator (caller {caller!r} is a member)")

    def _run(engine: CompletionEngine) -> Any:
        report = engine.finalize(scope_id, rerun_required=body.rerun_required, rerun_gate_ids=body.rerun_gate_ids)
        payload = report.model_dump(mode="json")
        payload["generated_at"] = coerce_iso(report.generated_at)
        payload["scope_status"] = engine.repo.load(scope_id).status.value
        return payload

    report = await _with_engine(_run)
    if isinstance(report, JSONResponse):
        return report
    return report
