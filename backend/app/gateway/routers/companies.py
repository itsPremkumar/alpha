"""Owner-scoped Gateway API for the Company OS.

Tenancy is resolved here and nowhere else: every handler takes ``Request`` and
derives the owner from the authenticated principal, so no request body can assert
ownership of another tenant's company. An ownerless read of a company that exists
under a different owner answers **404**, not 403, so this surface never confirms
that another tenant's company exists.

Route families mirror the domain: a company has a charter, an org chart, a
workforce, projects, work, groups, schedules, a budget, a loop, and an approval
queue. Each sub-resource is nested under ``/companies/{company_id}`` so the
tenant boundary is visible in the path rather than implied by a query parameter.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from alpha.company_os.models import ApprovalStatus, AutonomyTier, CompanyState
from alpha.company_os.service import get_company_service
from alpha.company_os.store import CompanyNotFound, CompanyStoreUnreadable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/companies", tags=["company-os"])


def _owner(request: Request) -> str:
    """Resolve the server-owned owner. Never read from a body or query."""
    from alpha.runtime.user_context import get_effective_user_id

    user = getattr(getattr(request, "state", None), "user", None)
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id else get_effective_user_id()


def _load(company_id: str, request: Request):
    try:
        return get_company_service().require(company_id, _owner(request))
    except CompanyNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Company '{company_id}' not found.") from exc
    except CompanyStoreUnreadable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _enumeration_company_id(request: Request, explicit: str | None) -> str:
    """Resolve the company for a query-param-only read.

    Falling back to "the first company" is what made the old ``/api/company/status``
    return an arbitrary company; the client cannot tell that apart from an
    intentional one. A caller must therefore name the company.
    """
    if not explicit:
        raise HTTPException(status_code=400, detail="A company_id is required; no default company is assumed.")
    return explicit


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #


class CreateCompanyRequest(BaseModel):
    prompt: str = Field(..., min_length=5, description="The company's mission, in plain language.")
    name: str = Field(default="", description="Display name. Defaults from the archetype.")
    archetype: str | None = Field(default=None, description="company | open_source | security_soc | research_lab | custom")
    description: str = ""
    vision: str = ""
    values: list[str] = Field(default_factory=list)
    autonomy_tier: AutonomyTier = Field(default=AutonomyTier.T1_ADVISE)
    budget_total_usd: float | None = None
    budget_daily_usd: float | None = None
    currency: str = "USD"
    hire: bool = Field(default=True, description="Hire the blueprint's workforce immediately.")


class PreviewRequest(BaseModel):
    prompt: str = Field(..., min_length=5)
    name: str = ""
    archetype: str | None = None
    autonomy_tier: AutonomyTier = AutonomyTier.T1_ADVISE
    budget_total_usd: float | None = None
    budget_daily_usd: float | None = None


class CharterPatch(BaseModel):
    mission: str | None = None
    vision: str | None = None
    values: list[str] | None = None
    constraints: list[str] | None = None
    autonomy_tier: AutonomyTier | None = None
    budget_total_usd: float | None = None
    budget_daily_usd: float | None = None
    currency: str | None = None


class StateRequest(BaseModel):
    state: CompanyState


class ObjectiveRequest(BaseModel):
    title: str = Field(..., min_length=3)
    rationale: str = ""
    owner_unit_id: str | None = None
    key_results: list[dict[str, Any]] = Field(default_factory=list)


class HireRequest(BaseModel):
    handle: str = Field(..., min_length=1, max_length=64)
    role_title: str = Field(..., min_length=1)
    unit_name: str = Field(..., min_length=1)
    reports_to_handle: str | None = None
    bot_template: str | None = None
    #: Optional **authority** grant from the real capability vocabulary
    #: (observe, reason, dispatch, workspace_write, process_exec). Omit it to let
    #: the registry apply its own default. Free-form domain tags are NOT accepted
    #: here: the authority ceiling treats every unrecognised name as a violation.
    authority_grant: list[str] | None = None


class CloneRequest(BaseModel):
    source: str = Field(..., min_length=1)
    handle: str = Field(..., min_length=1, max_length=64)
    display_name: str | None = None
    role: str | None = None
    department: str | None = None
    reports_to: str | None = None
    model: str | None = None


class HandleRequest(BaseModel):
    handle: str = Field(..., min_length=1, max_length=64)


class ReviewRequest(BaseModel):
    score: float | None = Field(default=None, description="A measured score, or null for 'no measurement'.")
    note: str = ""


class AttachProjectRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    title: str = ""
    objective_ids: list[str] = Field(default_factory=list)


class IndexProjectsRequest(BaseModel):
    """Project rows supplied by the caller.

    The harness cannot enumerate projects: ``get_project_repo`` lives in
    ``app.gateway.deps`` and ``alpha`` must never import ``app.*``. The Gateway
    reads its own repository and passes rows here.
    """

    rows: list[dict[str, Any]] | None = None


class CreateWorkItemRequest(BaseModel):
    title: str = Field(..., min_length=3)
    description: str = ""
    priority: str = "normal"
    assignee_agent_handle: str | None = None
    definition_of_done: list[str] = Field(default_factory=list)
    objective_id: str | None = None


class MoveWorkItemRequest(BaseModel):
    column: str = Field(..., min_length=1)


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=2)
    topic: str = ""
    members: list[str] = Field(default_factory=list)


class AnnounceRequest(BaseModel):
    room_id: str = Field(..., min_length=1)
    sender: str = Field(default="company", min_length=1)
    content: str = Field(..., min_length=1)


class AttachScheduleRequest(BaseModel):
    schedule_id: str = Field(..., min_length=1)
    backend_task_id: str = ""
    backend: str = "scheduler"
    title: str = ""
    recurrence: str = ""
    owner_agent_handle: str | None = None


class LoopPolicyRequest(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = None
    max_actions_per_tick: int | None = None
    daily_cost_ceiling_usd: float | None = None
    no_progress_tolerance: int | None = None
    failure_storm_threshold: float | None = None
    failure_storm_window: int | None = None
    standup_cadence: str | None = None
    review_cadence: str | None = None
    okr_cadence: str | None = None
    board_cadence: str | None = None


class DecisionRequest(BaseModel):
    note: str = ""


# --------------------------------------------------------------------------- #
# Catalogue, portfolio, lifecycle
# --------------------------------------------------------------------------- #


@router.get("/archetypes")
async def list_archetypes() -> dict[str, Any]:
    """The bundled organization archetypes an operator can start from."""
    return {"archetypes": get_company_service().archetypes()}


@router.post("/preview")
async def preview_blueprint(payload: PreviewRequest, request: Request) -> dict[str, Any]:
    """Build and return a full org blueprint without creating anything.

    Previewing first is what makes onboarding safe: the operator sees every
    planned hire, unit, role and duty before a single bot profile exists.
    """
    service = get_company_service()
    return await asyncio.to_thread(
        service.preview_blueprint,
        name=payload.name or "New Company",
        mission=payload.prompt,
        archetype=payload.archetype,
        autonomy_tier=payload.autonomy_tier,
        budget_total_usd=payload.budget_total_usd,
        budget_daily_usd=payload.budget_daily_usd,
    )


@router.get("")
async def list_companies(request: Request) -> dict[str, Any]:
    """Every company owned by the caller."""
    service = get_company_service()
    companies = await asyncio.to_thread(service.list_companies, _owner(request))
    return {"companies": [c.model_dump(mode="json") for c in companies], "count": len(companies)}


@router.post("", status_code=201)
async def create_company(payload: CreateCompanyRequest, request: Request) -> dict[str, Any]:
    """Create a company, optionally hiring its workforce."""
    service = get_company_service()
    company, hire = await asyncio.to_thread(
        service.create_company,
        _owner(request),
        name=payload.name or "Autonomous Company",
        mission=payload.prompt,
        archetype=payload.archetype,
        description=payload.description,
        vision=payload.vision,
        values=payload.values,
        autonomy_tier=payload.autonomy_tier,
        budget_total_usd=payload.budget_total_usd,
        budget_daily_usd=payload.budget_daily_usd,
        currency=payload.currency,
        hire=payload.hire,
    )
    return {"company": company.model_dump(mode="json"), "hire": hire}


@router.get("/{company_id}")
async def get_company(company_id: str, request: Request) -> dict[str, Any]:
    company = await asyncio.to_thread(_load, company_id, request)
    return {"company": company.model_dump(mode="json")}


@router.patch("/{company_id}/charter")
async def patch_charter(company_id: str, body: CharterPatch, request: Request) -> dict[str, Any]:
    _load(company_id, request)
    company = await asyncio.to_thread(get_company_service().update_charter, company_id, _owner(request), **body.model_dump(exclude_none=True))
    return {"company": company.model_dump(mode="json")}


@router.post("/{company_id}/state")
async def set_state(company_id: str, body: StateRequest, request: Request) -> dict[str, Any]:
    _load(company_id, request)
    company = await asyncio.to_thread(get_company_service().set_state, company_id, _owner(request), body.state)
    return {"company": company.model_dump(mode="json")}


@router.post("/{company_id}/archive")
async def archive_company(company_id: str, request: Request) -> dict[str, Any]:
    """Archive a company and disable its loop. Archiving is the normal removal."""
    _load(company_id, request)
    company = await asyncio.to_thread(get_company_service().archive, company_id, _owner(request))
    return {"company": company.model_dump(mode="json")}


@router.get("/{company_id}/overview")
async def overview(company_id: str, request: Request) -> dict[str, Any]:
    """The full briefing: charter, measured metrics, health, cost, loop state."""
    _load(company_id, request)
    return await asyncio.to_thread(get_company_service().digest, company_id, _owner(request))


@router.get("/{company_id}/metrics")
async def metrics(company_id: str, request: Request) -> dict[str, Any]:
    _load(company_id, request)
    m = await asyncio.to_thread(get_company_service().metrics, company_id, _owner(request))
    return {"metrics": m.model_dump(mode="json")}


@router.get("/{company_id}/org")
async def org_chart(company_id: str, request: Request) -> dict[str, Any]:
    """The reporting tree, built from real employments."""
    _load(company_id, request)
    return await asyncio.to_thread(get_company_service().org_chart, company_id, _owner(request))


# --------------------------------------------------------------------------- #
# Objectives
# --------------------------------------------------------------------------- #


@router.post("/{company_id}/objectives", status_code=201)
async def add_objective(company_id: str, body: ObjectiveRequest, request: Request) -> dict[str, Any]:
    _load(company_id, request)
    company = await asyncio.to_thread(
        get_company_service().add_objective,
        company_id,
        _owner(request),
        title=body.title,
        rationale=body.rationale,
        key_results=body.key_results,
        owner_unit_id=body.owner_unit_id,
    )
    return {"company": company.model_dump(mode="json")}


@router.delete("/{company_id}/objectives/{objective_id}")
async def delete_objective(company_id: str, objective_id: str, request: Request) -> dict[str, Any]:
    _load(company_id, request)
    company = await asyncio.to_thread(get_company_service().delete_objective, company_id, _owner(request), objective_id)
    return {"company": company.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# Workforce
# --------------------------------------------------------------------------- #


@router.post("/{company_id}/employees", status_code=201)
async def hire_employee(company_id: str, body: HireRequest, request: Request) -> dict[str, Any]:
    """Hire one employee, creating a real bot profile in the shared roster."""
    _load(company_id, request)
    company, outcome = await asyncio.to_thread(
        get_company_service().hire,
        company_id,
        _owner(request),
        handle=body.handle,
        role_title=body.role_title,
        unit_name=body.unit_name,
        reports_to_handle=body.reports_to_handle,
        bot_template=body.bot_template,
        authority_grant=body.authority_grant,
    )
    if not outcome.get("ok"):
        raise HTTPException(status_code=409, detail=outcome.get("reason", "The hire was refused."))
    return {"company": company.model_dump(mode="json"), "outcome": outcome}


@router.post("/{company_id}/employees/clone", status_code=201)
async def clone_employee(company_id: str, body: CloneRequest, request: Request) -> dict[str, Any]:
    """Specialise an existing employee by cloning their real profile."""
    _load(company_id, request)
    company, outcome = await asyncio.to_thread(
        get_company_service().clone_employee,
        company_id,
        _owner(request),
        source=body.source,
        handle=body.handle,
        display_name=body.display_name,
        role=body.role,
        department=body.department,
        reports_to=body.reports_to,
        model=body.model,
    )
    if not outcome.get("ok"):
        raise HTTPException(status_code=409, detail=outcome.get("reason", "The clone was refused."))
    return {"company": company.model_dump(mode="json"), "outcome": outcome}


@router.post("/{company_id}/employees/terminate")
async def terminate_employee(company_id: str, body: HandleRequest, request: Request) -> dict[str, Any]:
    """Retire a real profile and re-point anything that depended on it."""
    _load(company_id, request)
    company, outcome = await asyncio.to_thread(get_company_service().terminate_employee, company_id, _owner(request), body.handle)
    if not outcome.get("ok"):
        raise HTTPException(status_code=404, detail=outcome.get("reason", "No such employee."))
    return {"company": company.model_dump(mode="json"), "outcome": outcome}


@router.post("/{company_id}/employees/promote")
async def promote_employee(company_id: str, body: HandleRequest, request: Request) -> dict[str, Any]:
    _load(company_id, request)
    company, outcome = await asyncio.to_thread(get_company_service().promote_employee, company_id, _owner(request), body.handle)
    if not outcome.get("ok"):
        raise HTTPException(status_code=404, detail=outcome.get("reason", "No such employee."))
    return {"company": company.model_dump(mode="json"), "outcome": outcome}


@router.post("/{company_id}/employees/review")
async def review_employee(company_id: str, body: ReviewRequest, request: Request) -> dict[str, Any]:
    """Record a performance review. A null score is recorded as unmeasured."""
    _load(company_id, request)
    company, outcome = await asyncio.to_thread(get_company_service().review_employee, company_id, _owner(request), body.handle, score=body.score, note=body.note)
    if not outcome.get("ok"):
        raise HTTPException(status_code=404, detail=outcome.get("reason", "No such employee."))
    return {"company": company.model_dump(mode="json"), "outcome": outcome}


@router.get("/{company_id}/employees/{handle}")
async def employee_detail(company_id: str, handle: str, request: Request) -> dict[str, Any]:
    """One employee, combining the company record with the real bot profile."""
    company = await asyncio.to_thread(_load, company_id, request)
    employment = company.employment(handle)
    if employment is None:
        raise HTTPException(status_code=404, detail=f"'{handle}' is not an employee of this company.")
    from alpha.company_os import workforce

    return {
        "employment": employment.model_dump(mode="json"),
        "role": company.role(employment.role_id).model_dump(mode="json") if company.role(employment.role_id) else None,
        "unit": company.unit(employment.unit_id).model_dump(mode="json") if company.unit(employment.unit_id) else None,
        "agent_profile": workforce.describe_agent(employment.agent_handle),
    }


@router.get("/{company_id}/employees")
async def list_employees(company_id: str, request: Request) -> dict[str, Any]:
    """The workforce, with a drift report against the real bot registry."""
    company = await asyncio.to_thread(_load, company_id, request)
    from alpha.company_os import workforce

    drift = await asyncio.to_thread(workforce.sync_workforce_status, company)
    return {
        "employments": [e.model_dump(mode="json") for e in company.employments],
        "count": len(company.employments),
        "active_count": len(company.active_employments()),
        "roster": drift,
    }


# --------------------------------------------------------------------------- #
# Projects, work, groups, schedules
# --------------------------------------------------------------------------- #


@router.post("/{company_id}/projects", status_code=201)
async def attach_project(company_id: str, body: AttachProjectRequest, request: Request) -> dict[str, Any]:
    """Link an existing real project into the company's portfolio."""
    _load(company_id, request)
    company = await asyncio.to_thread(
        get_company_service().attach_project,
        company_id,
        _owner(request),
        project_id=body.project_id,
        title=body.title,
        objective_ids=body.objective_ids,
    )
    return {"company": company.model_dump(mode="json")}


@router.post("/{company_id}/projects/index")
async def index_projects(company_id: str, body: IndexProjectsRequest, request: Request) -> dict[str, Any]:
    """Refresh the project index from rows the caller read from its repository."""
    company = await asyncio.to_thread(_load, company_id, request)
    from alpha.company_os import portfolio

    result = await asyncio.to_thread(portfolio.index_projects, company, body.rows)
    if result.get("reachable"):
        await asyncio.to_thread(get_company_service().save, company, _owner(request))
    return {**result, "company": company.model_dump(mode="json")}


@router.get("/{company_id}/work")
async def list_work(company_id: str, request: Request) -> dict[str, Any]:
    """The company's real Kanban board and its measured item summary."""
    company = await asyncio.to_thread(_load, company_id, request)
    from alpha.company_os import portfolio

    summary = await asyncio.to_thread(portfolio.measure_work, company)
    return {"work": summary, "items": [w.model_dump(mode="json") for w in company.work_items]}


@router.post("/{company_id}/work", status_code=201)
async def create_work_item(company_id: str, body: CreateWorkItemRequest, request: Request) -> dict[str, Any]:
    """Create a real Kanban card on the company's board."""
    _load(company_id, request)
    company, result = await asyncio.to_thread(
        get_company_service().create_work_item,
        company_id,
        _owner(request),
        title=body.title,
        description=body.description,
        priority=body.priority,
        assignee_agent_handle=body.assignee_agent_handle,
        definition_of_done=body.definition_of_done,
        objective_id=body.objective_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error", "The work board could not be written."))
    return {"company": company.model_dump(mode="json"), "result": result}


@router.post("/{company_id}/work/{task_id}/move")
async def move_work_item(company_id: str, task_id: str, body: MoveWorkItemRequest, request: Request) -> dict[str, Any]:
    """Transition a real card. The board store is the authority."""
    company = await asyncio.to_thread(_load, company_id, request)
    from alpha.company_os import portfolio

    result = await asyncio.to_thread(portfolio.move_work_item, company, task_id, body.column)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "Unknown card."))
    await asyncio.to_thread(get_company_service().save, company, _owner(request))
    return {**result, "company": company.model_dump(mode="json")}


@router.post("/{company_id}/groups", status_code=201)
async def create_group(company_id: str, body: CreateGroupRequest, request: Request) -> dict[str, Any]:
    """Create a real group room and index it."""
    _load(company_id, request)
    company, result = await asyncio.to_thread(
        get_company_service().create_group,
        company_id,
        _owner(request),
        name=body.name,
        topic=body.topic,
        members=body.members,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error", "The group room could not be created."))
    return {"company": company.model_dump(mode="json"), "result": result}


@router.post("/{company_id}/announce")
async def announce(company_id: str, body: AnnounceRequest, request: Request) -> dict[str, Any]:
    """Post a company announcement to one of its real rooms."""
    _load(company_id, request)
    result = await asyncio.to_thread(
        get_company_service().announce,
        company_id,
        _owner(request),
        room_id=body.room_id,
        sender=body.sender,
        content=body.content,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "The announcement could not be posted."))
    return result


@router.post("/{company_id}/schedules", status_code=201)
async def attach_schedule(company_id: str, body: AttachScheduleRequest, request: Request) -> dict[str, Any]:
    """Index an existing real schedule. The company never fires it."""
    _load(company_id, request)
    company = await asyncio.to_thread(
        get_company_service().attach_schedule,
        company_id,
        _owner(request),
        schedule_id=body.schedule_id,
        backend_task_id=body.backend_task_id,
        backend=body.backend,
        title=body.title,
        recurrence=body.recurrence,
        owner_agent_handle=body.owner_agent_handle,
    )
    return {"company": company.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


@router.patch("/{company_id}/loop")
async def set_loop_policy(company_id: str, body: LoopPolicyRequest, request: Request) -> dict[str, Any]:
    """Change the loop's cadence, bounds and autonomy gates."""
    _load(company_id, request)
    company = await asyncio.to_thread(
        get_company_service().set_loop_policy,
        company_id,
        _owner(request),
        **body.model_dump(exclude_none=True),
    )
    return {"company": company.model_dump(mode="json")}


@router.post("/{company_id}/loop/tick")
async def run_tick(company_id: str, request: Request) -> dict[str, Any]:
    """Run one bounded loop tick and append its ledger record.

    Model-free by design, so this is safe to call synchronously. A refusal is a
    normal answer carrying a reason, not an error.
    """
    _load(company_id, request)
    view = await asyncio.to_thread(get_company_service().run_tick, company_id, _owner(request))
    return view.as_dict()


@router.get("/{company_id}/loop")
async def loop_history(
    company_id: str,
    request: Request,
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    """The recent tick ledger, newest first."""
    _load(company_id, request)
    ticks = await asyncio.to_thread(get_company_service().loop_history, company_id, _owner(request), limit)
    return {"ticks": ticks, "count": len(ticks)}


@router.get("/{company_id}/loop/rituals")
async def loop_rituals(company_id: str, request: Request) -> dict[str, Any]:
    """Which cadenced rituals are due, and when each last ran."""
    _load(company_id, request)
    return await asyncio.to_thread(get_company_service().rituals, company_id, _owner(request))


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #


@router.get("/{company_id}/approvals")
async def list_approvals(
    company_id: str,
    request: Request,
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """The approval queue. A proposal is never an action."""
    company = await asyncio.to_thread(_load, company_id, request)
    rows = company.approvals
    if status:
        rows = [a for a in rows if a.status.value == status]
    return {"approvals": [a.model_dump(mode="json") for a in rows], "count": len(rows)}


@router.post("/{company_id}/approvals/{approval_id}/approve")
async def approve(company_id: str, approval_id: str, body: DecisionRequest, request: Request) -> dict[str, Any]:
    """Approve a pending proposal. The only path by which one becomes real."""
    _load(company_id, request)
    company, result = await asyncio.to_thread(get_company_service().approve, company_id, _owner(request), approval_id, note=body.note)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("detail") or result.get("error", "Approval failed."))
    return {"company": company.model_dump(mode="json"), "result": result}


@router.post("/{company_id}/approvals/{approval_id}/reject")
async def reject(company_id: str, approval_id: str, body: DecisionRequest, request: Request) -> dict[str, Any]:
    """Decline a proposal. The record is kept so the decision stays auditable."""
    _load(company_id, request)
    company, result = await asyncio.to_thread(get_company_service().reject, company_id, _owner(request), approval_id, note=body.note)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "No such approval."))
    return {"company": company.model_dump(mode="json"), "result": result}


@router.get("/{company_id}/approvals/{approval_id}")
async def get_approval(company_id: str, approval_id: str, request: Request) -> dict[str, Any]:
    company = await asyncio.to_thread(_load, company_id, request)
    target = next((a for a in company.approvals if a.approval_id == approval_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"No approval '{approval_id}' on this company.")
    return {"approval": target.model_dump(mode="json"), "is_pending": target.status is ApprovalStatus.PENDING}


__all__ = ["router"]
