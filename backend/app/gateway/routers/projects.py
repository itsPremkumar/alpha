"""CRUD API for projects (Phase 1: organization only — no documents/trash)."""

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.gateway.authz import require_permission
from app.gateway.deps import get_project_repo, get_thread_store
from agent_workspace.runtime.secret_context import redact_metadata_secrets
from agent_workspace.utils.time import coerce_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

ProjectStatus = Literal["active", "archived"]


class ProjectResponse(BaseModel):
    id: str
    name: str
    instructions: str
    presentation: dict
    status: str
    created_at: str
    updated_at: str


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    instructions: str = ""
    presentation: dict = Field(default_factory=dict)


class ProjectPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    instructions: str | None = None
    presentation: dict | None = None


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


class ProjectThreadResponse(BaseModel):
    """A thread row from ``GET /api/projects/{id}/threads``.

    Deliberately narrow — only the fields ``ProjectThread`` declares in
    ``frontend/src/core/projects/types.ts``. Store rows carry ownership
    columns (``user_id``, ``assistant_id``) and ``ThreadMetaRow`` may grow;
    without this model those would leak onto the wire and the route's
    OpenAPI schema stays empty. Metadata is redacted here exactly as the
    surrounding thread endpoints redact it via ``_MetadataRedactingResponse``.
    """

    thread_id: str
    display_name: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before", check_fields=False)
    @classmethod
    def _redact_metadata_secrets(cls, value: Any) -> Any:
        return redact_metadata_secrets(value)


def _to_response(row: dict) -> ProjectResponse:
    return ProjectResponse(
        id=row["id"],
        name=row["name"],
        instructions=row.get("instructions", ""),
        presentation=row.get("presentation") or {},
        status=row["status"],
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


def _not_found() -> HTTPException:
    # Fail closed: foreign projects are indistinguishable from missing ones.
    return HTTPException(status_code=404, detail="Project not found")


@router.post("", response_model=ProjectResponse, status_code=201)
@require_permission("projects", "write")
async def create_project(body: ProjectCreateRequest, request: Request) -> ProjectResponse:
    repo = get_project_repo(request)
    return _to_response(await repo.create(name=body.name, instructions=body.instructions, presentation=body.presentation))


@router.get("", response_model=ProjectListResponse)
@require_permission("projects", "read")
async def list_projects(request: Request, status: ProjectStatus | None = None) -> ProjectListResponse:
    repo = get_project_repo(request)
    return ProjectListResponse(projects=[_to_response(r) for r in await repo.list(status=status)])


@router.get("/{project_id}", response_model=ProjectResponse)
@require_permission("projects", "read")
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    row = await get_project_repo(request).get(project_id)
    if row is None:
        raise _not_found()
    return _to_response(row)


@router.patch("/{project_id}", response_model=ProjectResponse)
@require_permission("projects", "write")
async def patch_project(project_id: str, body: ProjectPatchRequest, request: Request) -> ProjectResponse:
    row = await get_project_repo(request).patch(project_id, name=body.name, instructions=body.instructions, presentation=body.presentation)
    if row is None:
        raise _not_found()
    return _to_response(row)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
@require_permission("projects", "write")
async def archive_project(project_id: str, request: Request) -> ProjectResponse:
    row = await get_project_repo(request).set_status(project_id, "archived")
    if row is None:
        raise _not_found()
    return _to_response(row)


@router.post("/{project_id}/restore", response_model=ProjectResponse)
@require_permission("projects", "write")
async def restore_project(project_id: str, request: Request) -> ProjectResponse:
    row = await get_project_repo(request).set_status(project_id, "active")
    if row is None:
        raise _not_found()
    return _to_response(row)


@router.delete("/{project_id}", status_code=204)
@require_permission("projects", "delete")
async def delete_project(project_id: str, request: Request) -> None:
    if not await get_project_repo(request).delete(project_id):
        raise _not_found()


@router.get("/{project_id}/threads", response_model=list[ProjectThreadResponse])
@require_permission("projects", "read")
@require_permission("threads", "read")
async def list_project_threads(project_id: str, request: Request, limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> list[ProjectThreadResponse]:
    if await get_project_repo(request).get(project_id) is None:
        raise _not_found()
    # Active members only, mirroring the sidebar's `archived: false` lists:
    # an archived chat leaves the project's pages the same way it leaves the
    # sidebar and returns only via the global Archived tab.
    rows = await get_thread_store(request).search(
        project_id=project_id,
        archived=False,
        limit=limit,
        offset=offset,
    )
    return [
        ProjectThreadResponse(
            thread_id=r["thread_id"],
            display_name=r.get("display_name"),
            created_at=coerce_iso(r.get("created_at", "")),
            updated_at=coerce_iso(r.get("updated_at", "")),
            metadata=r.get("metadata", {}),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Workforce layer: membership, presence, constitution, state, memory, locks,
# handoffs, events, context, goals, conflicts, evidence.
# ---------------------------------------------------------------------------


async def _require_project(project_id: str, request: Request) -> None:
    if await get_project_repo(request).get(project_id) is None:
        raise _not_found()


class JoinRequest(BaseModel):
    bot_name: str = Field(..., min_length=1, max_length=64)
    role_in_project: str = Field(default="worker", max_length=64)


class LeaveRequest(BaseModel):
    bot_name: str = Field(..., min_length=1, max_length=64)


class HeartbeatRequest(BaseModel):
    bot_name: str = Field(..., min_length=1, max_length=64)
    status: str | None = Field(default=None, max_length=16)
    current_task_id: str | None = Field(default=None, max_length=64)
    blocked_reason: str | None = Field(default=None, max_length=500)


@router.post("/{project_id}/join")
@require_permission("projects", "write")
async def join_project(project_id: str, body: JoinRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.events import get_event_bus
        from agent_workspace.projects.membership import get_membership_store
        from agent_workspace.projects.workspace import ensure_workspace

        ensure_workspace(project_id)
        m = get_membership_store().join(project_id, body.bot_name, body.role_in_project)
        get_event_bus(project_id).emit("agent_joined", m.bot_name, {"role": m.role_in_project})
        return m.to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/leave")
@require_permission("projects", "write")
async def leave_project(project_id: str, body: LeaveRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.events import get_event_bus
        from agent_workspace.projects.membership import get_membership_store

        ok = get_membership_store().leave(project_id, body.bot_name)
        if ok:
            get_event_bus(project_id).emit("agent_left", body.bot_name.lower().strip(), {})
        return {"left": ok}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/heartbeat")
@require_permission("projects", "write")
async def project_heartbeat(project_id: str, body: HeartbeatRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.membership import get_membership_store

        m = get_membership_store().heartbeat(project_id, body.bot_name, status=body.status, current_task_id=body.current_task_id, blocked_reason=body.blocked_reason)
        if m is None:
            return None
        return m.to_dict()

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Membership not found; join the project first.")
    return result


@router.get("/{project_id}/presence")
@require_permission("projects", "read")
async def project_presence(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.membership import get_membership_store

        rows = get_membership_store().presence(project_id)
        return {"project_id": project_id, "members": [m.to_dict() for m in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


class ConstitutionRequest(BaseModel):
    markdown: str = Field(..., min_length=50, max_length=60000)


@router.get("/{project_id}/constitution")
@require_permission("projects", "read")
async def get_constitution(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects import constitution as const_mod

        const = const_mod.get_constitution(project_id)
        if const is None:
            return {"project_id": project_id, "present": False, "template": const_mod.DEFAULT_CONSTITUTION_TEMPLATE}
        return {"project_id": project_id, "present": True, "markdown": const.markdown, "sha16": const.sha16, "updated_at": const.updated_at}

    return await asyncio.to_thread(_do)


@router.put("/{project_id}/constitution")
@require_permission("projects", "write")
async def put_constitution(project_id: str, body: ConstitutionRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects import constitution as const_mod
        from agent_workspace.projects.events import get_event_bus

        const = const_mod.put_constitution(project_id, body.markdown)
        get_event_bus(project_id).emit("constitution_updated", "operator", {"sha16": const.sha16})
        return {"project_id": project_id, "sha16": const.sha16, "updated_at": const.updated_at}

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{project_id}/state")
@require_permission("projects", "read")
async def get_project_state(project_id: str, request: Request, refresh: bool = Query(default=False)) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects import state as state_mod

        state = state_mod.refresh_state(project_id) if refresh else state_mod.get_state(project_id)
        return state.to_dict()

    return await asyncio.to_thread(_do)


class PhaseRequest(BaseModel):
    phase: str = Field(..., min_length=1, max_length=32)


@router.post("/{project_id}/phase")
@require_permission("projects", "write")
async def set_project_phase(project_id: str, body: PhaseRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects import state as state_mod

        return state_mod.set_phase(project_id, body.phase.strip().lower()).to_dict()

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class DecisionRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    body: str = Field(..., min_length=1, max_length=20000)
    reason: str = Field(default="", max_length=5000)
    made_by: str = Field(default="", max_length=64)
    approved_by: str | None = Field(default=None, max_length=64)
    arch_version: str | None = Field(default=None, max_length=32)


@router.get("/{project_id}/decisions")
@require_permission("projects", "read")
async def list_decisions(project_id: str, request: Request, q: str | None = Query(default=None, max_length=200)) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.decisions import get_decision_log

        log = get_decision_log(project_id)
        rows = log.search(q) if q else log.list()
        return {"project_id": project_id, "decisions": [d.to_dict() for d in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/decisions", status_code=201)
@require_permission("projects", "write")
async def record_decision(project_id: str, body: DecisionRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.decisions import get_decision_log

        return get_decision_log(project_id).record(body.title, body.body, reason=body.reason, made_by=body.made_by, approved_by=body.approved_by, arch_version=body.arch_version).to_dict()

    return await asyncio.to_thread(_do)


class LockRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=16)
    path: str = Field(..., min_length=1, max_length=500)
    owner_bot: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)
    ttl_seconds: float = Field(default=1800.0, ge=60.0, le=86400.0)


@router.get("/{project_id}/locks")
@require_permission("projects", "read")
async def list_locks(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.locks import get_lock_manager

        manager = get_lock_manager()
        manager.sweep_expired()
        return {
            "project_id": project_id,
            "locks": [lk.to_dict() for lk in manager.list_locks(project_id)],
            "pending_requests": [r.to_dict() for r in manager.list_requests(project_id)],
        }

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/locks", status_code=201)
@require_permission("projects", "write")
async def acquire_lock(project_id: str, body: LockRequest, request: Request) -> dict:
    await _require_project(project_id, request)
    if body.scope not in ("file", "dir", "task", "artifact"):
        raise HTTPException(status_code=422, detail="scope must be file|dir|task|artifact")

    def _do():
        from agent_workspace.projects.events import get_event_bus
        from agent_workspace.projects.locks import LockConflictError, get_lock_manager

        try:
            lk = get_lock_manager().acquire(project_id, body.scope, body.path, body.owner_bot, reason=body.reason, ttl_seconds=body.ttl_seconds)
        except LockConflictError as exc:
            return {"conflict": True, "holder": exc.holder.to_dict()}
        get_event_bus(project_id).emit("file_locked", lk.owner_bot, {"lock_id": lk.lock_id, "scope": lk.scope, "path": lk.path})
        return {"conflict": False, "lock": lk.to_dict()}

    result = await asyncio.to_thread(_do)
    if result.get("conflict"):
        raise HTTPException(status_code=423, detail={"message": "Resource is locked", "holder": result["holder"]})
    return result["lock"]


class ReleaseLockRequest(BaseModel):
    requester_bot: str = Field(..., min_length=1, max_length=64)


@router.delete("/{project_id}/locks/{lock_id}")
@require_permission("projects", "write")
async def release_lock(project_id: str, lock_id: str, request: Request, requester_bot: str = Query(...)) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.events import get_event_bus
        from agent_workspace.projects.locks import get_lock_manager

        ok = get_lock_manager().release(lock_id, requester_bot)
        if ok:
            get_event_bus(project_id).emit("file_unlocked", requester_bot.lower().strip(), {"lock_id": lock_id})
        return {"released": ok}

    return await asyncio.to_thread(_do)


class LockAccessRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=16)
    path: str = Field(..., min_length=1, max_length=500)
    requester_bot: str = Field(..., min_length=1, max_length=64)
    mode: str = Field(default="shared", max_length=16)


@router.post("/{project_id}/lock-requests", status_code=201)
@require_permission("projects", "write")
async def request_lock_access(project_id: str, body: LockAccessRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.locks import get_lock_manager

        return get_lock_manager().request_access(project_id, body.scope, body.path, body.requester_bot, mode=body.mode).to_dict()

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class ResolveLockRequest(BaseModel):
    approver_bot: str = Field(..., min_length=1, max_length=64)
    approve: bool = True


@router.post("/{project_id}/lock-requests/{request_id}/resolve")
@require_permission("projects", "write")
async def resolve_lock_access(project_id: str, request_id: str, body: ResolveLockRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.locks import get_lock_manager

        rq = get_lock_manager().resolve_request(request_id, body.approver_bot, approve=body.approve)
        return rq.to_dict() if rq else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Request not found, already resolved, or approver is not the lock owner.")
    return result


class HandoffRequest(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=64)
    from_bot: str = Field(..., min_length=1, max_length=64)
    to_bot: str = Field(..., min_length=1, max_length=64)
    objective: str = Field(..., min_length=1, max_length=2000)
    completed_work: str = Field(default="", max_length=20000)
    findings: str = Field(default="", max_length=20000)
    files_modified: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    remaining_work: str = Field(default="", max_length=20000)
    known_risks: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    recommended_next_action: str = Field(default="", max_length=2000)


@router.get("/{project_id}/handoffs")
@require_permission("projects", "read")
async def list_handoffs(project_id: str, request: Request, status: str | None = Query(default=None)) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.handoffs import get_handoff_store

        rows = get_handoff_store(project_id).list(status=status)
        return {"project_id": project_id, "handoffs": [r.to_dict() for r in rows]}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/handoffs", status_code=201)
@require_permission("projects", "write")
async def create_handoff(project_id: str, body: HandoffRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.handoffs import get_handoff_store

        return (
            get_handoff_store(project_id)
            .create(
                body.task_id,
                body.from_bot,
                body.to_bot,
                body.objective,
                completed_work=body.completed_work,
                findings=body.findings,
                files_modified=body.files_modified,
                decisions=body.decisions,
                remaining_work=body.remaining_work,
                known_risks=body.known_risks,
                tests=body.tests,
                recommended_next_action=body.recommended_next_action,
            )
            .to_dict()
        )

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class AcceptHandoffRequest(BaseModel):
    to_bot: str = Field(..., min_length=1, max_length=64)


@router.post("/{project_id}/handoffs/{handoff_id}/accept")
@require_permission("projects", "write")
async def accept_handoff(project_id: str, handoff_id: str, body: AcceptHandoffRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.handoffs import get_handoff_store

        rec = get_handoff_store(project_id).accept(handoff_id, body.to_bot)
        return rec.to_dict() if rec else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Handoff not found, not addressed to this bot, or already accepted.")
    return result


@router.get("/{project_id}/events")
@require_permission("projects", "read")
async def read_project_events(project_id: str, request: Request, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=1000), q: str | None = Query(default=None, max_length=200)) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.events import get_event_bus

        bus = get_event_bus(project_id)
        rows = bus.search(q, limit=limit) if q else bus.read(after_seq=after_seq, limit=limit)
        return {"project_id": project_id, "events": [e.to_dict() for e in rows]}

    return await asyncio.to_thread(_do)


@router.get("/{project_id}/context")
@require_permission("projects", "read")
async def get_project_context(project_id: str, request: Request, bot_role: str = Query(default="worker", max_length=64)) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.context import build_context

        return build_context(project_id, bot_role).to_dict()

    return await asyncio.to_thread(_do)


class GoalCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=500)
    acceptance: list[str] = Field(default_factory=list)


@router.post("/{project_id}/goals", status_code=201)
@require_permission("projects", "write")
async def create_goal_tree(project_id: str, body: GoalCreateRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        import uuid

        from agent_workspace.projects.goals import GoalTree

        goal_id = f"goal-{uuid.uuid4().hex[:8]}"
        tree = GoalTree(project_id, goal_id)
        root = tree.add_root(body.title, acceptance=body.acceptance)
        return {"goal_id": goal_id, "root": root.to_dict(), "progress": tree.progress()}

    return await asyncio.to_thread(_do)


class SubgoalRequest(BaseModel):
    parent_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=3, max_length=500)
    acceptance: list[str] = Field(default_factory=list)


@router.post("/{project_id}/goals/{goal_id}/subgoals", status_code=201)
@require_permission("projects", "write")
async def add_subgoal(project_id: str, goal_id: str, body: SubgoalRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.goals import GoalTree

        try:
            return GoalTree(project_id, goal_id).add_subgoal(body.parent_id, body.title, acceptance=body.acceptance).to_dict()
        except ValueError as exc:
            return {"error": str(exc)}

    result = await asyncio.to_thread(_do)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class GoalStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=16)
    blocked_reason: str | None = Field(default=None, max_length=1000)


@router.post("/{project_id}/goals/{goal_id}/nodes/{node_id}/status")
@require_permission("projects", "write")
async def set_goal_status(project_id: str, goal_id: str, node_id: str, body: GoalStatusRequest, request: Request) -> dict:
    await _require_project(project_id, request)
    if body.status not in ("open", "in_progress", "blocked", "satisfied", "abandoned"):
        raise HTTPException(status_code=422, detail="Invalid goal status.")

    def _do():
        from agent_workspace.projects.goals import GoalTree

        tree = GoalTree(project_id, goal_id)
        node = tree.set_status(node_id, body.status, blocked_reason=body.blocked_reason)
        if node is None:
            return None
        return {"node": node.to_dict(), "progress": tree.progress()}

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Goal node not found.")
    return result


@router.get("/{project_id}/goals/{goal_id}")
@require_permission("projects", "read")
async def get_goal_tree(project_id: str, goal_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.goals import GoalTree

        tree = GoalTree(project_id, goal_id)
        return {**tree.to_dict(), "progress": tree.progress()}

    return await asyncio.to_thread(_do)


class ConflictRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=16)
    subject: str = Field(..., min_length=1, max_length=500)
    parties: list[str] = Field(default_factory=list)
    details: str = Field(default="", max_length=5000)


@router.post("/{project_id}/conflicts/detect")
@require_permission("projects", "read")
async def detect_conflicts(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.conflicts import detect_lock_conflicts

        found = detect_lock_conflicts(project_id)
        return {"project_id": project_id, "conflicts": [c.to_dict() for c in found]}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/conflicts", status_code=201)
@require_permission("projects", "write")
async def raise_conflict(project_id: str, body: ConflictRequest, request: Request) -> dict:
    await _require_project(project_id, request)
    if body.kind not in ("file", "task", "requirement", "architecture", "decision"):
        raise HTTPException(status_code=422, detail="Invalid conflict kind.")

    def _do():
        from agent_workspace.projects.conflicts import raise_conflict as _raise

        return _raise(project_id, body.kind, body.subject, body.parties, body.details).to_dict()

    return await asyncio.to_thread(_do)


class ResolveConflictRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=16)
    subject: str = Field(..., min_length=1, max_length=500)
    parties: list[str] = Field(default_factory=list)
    details: str = Field(default="", max_length=5000)
    resolution: str = Field(..., min_length=1, max_length=5000)
    resolved_by: str = Field(..., min_length=1, max_length=64)


@router.post("/{project_id}/conflicts/resolve")
@require_permission("projects", "write")
async def resolve_conflict(project_id: str, body: ResolveConflictRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.conflicts import raise_conflict as _raise
        from agent_workspace.projects.conflicts import resolve_conflict as _resolve

        conflict = _raise(project_id, body.kind, body.subject, body.parties, body.details, actor=body.resolved_by)
        return _resolve(project_id, conflict, body.resolution, resolved_by=body.resolved_by).to_dict()

    return await asyncio.to_thread(_do)


class CompletionCheckRequest(BaseModel):
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    task_kind: str = Field(default="code", max_length=16)


@router.post("/{project_id}/completion-check")
@require_permission("projects", "read")
async def completion_check(project_id: str, body: CompletionCheckRequest, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.evidence import check_completion

        return check_completion(body.evidence, task_kind=body.task_kind).to_dict()

    return await asyncio.to_thread(_do)


@router.get("/{project_id}/war-room")
@require_permission("projects", "read")
async def get_war_room(project_id: str, request: Request) -> dict:
    """Aggregated War Room dashboard state for autonomous multi-agent workforce."""
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.bots.kill_switch import get_kill_switch_status
        from agent_workspace.projects import decisions as dec_mod
        from agent_workspace.projects import events as events_mod
        from agent_workspace.projects import handoffs as handoff_mod
        from agent_workspace.projects import locks as locks_mod
        from agent_workspace.projects import membership as mem_mod
        from agent_workspace.projects import state as state_mod

        # 1. Members Presence
        mem_store = mem_mod.get_membership_store()
        presence_list = [m.to_dict() for m in mem_store.presence(project_id)]

        # 2. Project State
        st = state_mod.get_state(project_id)

        # 3. Active Locks & Pending Requests
        lock_mgr = locks_mod.get_lock_manager()
        active_locks = [l.to_dict() for l in lock_mgr.list_locks(project_id)]
        pending_requests = [r.to_dict() for r in lock_mgr.list_requests(project_id, pending_only=True)]

        # 4. Handoffs
        handoffs = [h.to_dict() for h in handoff_mod.get_handoff_store(project_id).list()[-10:]]

        # 5. Decisions
        decisions = [d.to_dict() for d in dec_mod.get_decision_log(project_id).list()[-10:]]

        # 6. Events Stream
        event_records = [e.to_dict() for e in events_mod.get_event_bus(project_id).read(limit=30)]

        # 7. Kill switch status
        ks = get_kill_switch_status()

        # 8. Pending Approvals
        from agent_workspace.projects.approval_queue import get_approval_queue
        pending_approvals = [a.to_dict() for a in get_approval_queue(project_id).list_pending()]

        # 9. Task Contracts
        from agent_workspace.projects.contracts import get_contract_gatekeeper
        contracts = [c.to_dict() for c in get_contract_gatekeeper(project_id).list_contracts()]

        # 10. Living Specification
        from agent_workspace.projects.living_spec import get_living_spec_engine
        living_spec = get_living_spec_engine(project_id).get_spec().to_dict()

        # 11. Cost & Token Governance
        from agent_workspace.models.cost_governor import get_cost_governor
        cost_summary = get_cost_governor().get_project_summary(project_id)

        # 12. Async Standup Briefing
        from agent_workspace.projects.standup_engine import get_standup_engine
        standup_data = get_standup_engine(project_id).generate_standup().to_dict()

        # 13. Workspace Checkpoints
        from agent_workspace.projects.checkpoint_engine import get_checkpoint_engine
        checkpoints = [c.to_dict() for c in get_checkpoint_engine(project_id).list_checkpoints()[:5]]

        # 14. Arena Bot Leaderboard
        from agent_workspace.benchmarks.arena import get_benchmark_arena
        leaderboard = [l.to_dict() for l in get_benchmark_arena(project_id).get_leaderboard()[:5]]

        # 15. Canary Watchdog Status
        from agent_workspace.projects.canary_watchdog import get_canary_watchdog
        canary_history = [c.to_dict() for c in get_canary_watchdog(project_id).get_history()[-3:]]

        # 16. Visual QA Receipts
        from agent_workspace.projects.visual_verifier import get_visual_qa_engine
        visual_qa = [v.to_dict() for v in get_visual_qa_engine(project_id).get_history()[-3:]]

        # 17. AVO Genetic Optimization Lineage & Pareto Frontier
        try:
            from agent_workspace.avo import get_avo_runner
            avo_runner = get_avo_runner(project_id)
            avo_lineage = {
                "head_id": avo_runner.lineage.head_id,
                "versions": [v.to_dict() for v in avo_runner.lineage.get_history()[-6:]],
                "pareto_frontier": [v.to_dict() for v in avo_runner.lineage.get_pareto_frontier()[:5]],
                "supervisor_status": avo_runner.supervisor.diagnose_state(),
            }
        except Exception:
            avo_lineage = {"head_id": None, "versions": [], "pareto_frontier": [], "supervisor_status": "standby"}

        # 18. Epistemic Belief Graph
        try:
            from agent_workspace.epistemics import get_epistemic_engine
            ep_engine = get_epistemic_engine(project_id)
            epistemic_claims = [c.to_dict() for c in ep_engine.list_all()]
        except Exception:
            epistemic_claims = []

        # 19. Controlled RSI Closed-Loop Status
        try:
            from agent_workspace.rsi import get_rsi_engine
            rsi_engine = get_rsi_engine(project_id)
            rsi_status = rsi_engine.get_status()
        except Exception:
            rsi_status = {"stage": "idle", "active_configurations": {}, "last_cycle_summary": "standby"}

        # 20. Deterministic Trajectory Store
        try:
            from agent_workspace.trajectory.store import get_trajectory_store
            traj_store = get_trajectory_store(project_id)
            goal_ids = traj_store.list_goal_ids()[:3]
            trajectories = [traj_store.get_trajectory(gid).to_dict() for gid in goal_ids]
        except Exception:
            trajectories = []

        return {
            "project_id": project_id,
            "status": "active",
            "state": st.to_dict(),
            "members": presence_list,
            "active_locks": active_locks,
            "pending_lock_requests": pending_requests,
            "pending_approvals": pending_approvals,
            "contracts": contracts,
            "living_spec": living_spec,
            "cost_summary": cost_summary,
            "standup": standup_data,
            "checkpoints": checkpoints,
            "leaderboard": leaderboard,
            "canary_history": canary_history,
            "visual_qa": visual_qa,
            "avo_lineage": avo_lineage,
            "epistemic_claims": epistemic_claims,
            "rsi_status": rsi_status,
            "trajectories": trajectories,
            "handoffs": handoffs,
            "decisions": decisions,
            "events": event_records,
            "kill_switch": ks,
        }

    return await asyncio.to_thread(_do)


class ResolveApprovalBody(BaseModel):
    approved: bool
    resolved_by: str = Field(default="human_operator", max_length=64)
    comment: str = Field(default="", max_length=1000)


@router.get("/{project_id}/approvals")
@require_permission("projects", "read")
async def list_project_approvals(project_id: str, request: Request, status: str | None = None) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.approval_queue import get_approval_queue

        q = get_approval_queue(project_id)
        return {"project_id": project_id, "approvals": [r.to_dict() for r in q.list_requests(status=status)]}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/approvals/{request_id}/resolve")
@require_permission("projects", "write")
async def resolve_project_approval(project_id: str, request_id: str, body: ResolveApprovalBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.approval_queue import get_approval_queue

        q = get_approval_queue(project_id)
        req = q.resolve_request(request_id, approved=body.approved, resolved_by=body.resolved_by, comment=body.comment)
        return req.to_dict()

    try:
        return await asyncio.to_thread(_do)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CreateCheckpointBody(BaseModel):
    tag: str = Field(default="manual", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/{project_id}/checkpoints")
@require_permission("projects", "write")
async def create_project_checkpoint(project_id: str, body: CreateCheckpointBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.checkpoint_engine import get_checkpoint_engine

        return get_checkpoint_engine(project_id).create_checkpoint(tag=body.tag, metadata=body.metadata).to_dict()

    return await asyncio.to_thread(_do)


@router.get("/{project_id}/checkpoints")
@require_permission("projects", "read")
async def list_project_checkpoints(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.checkpoint_engine import get_checkpoint_engine

        return {
            "project_id": project_id,
            "checkpoints": [c.to_dict() for c in get_checkpoint_engine(project_id).list_checkpoints()],
        }

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/checkpoints/{checkpoint_id}/restore")
@require_permission("projects", "write")
async def restore_project_checkpoint(project_id: str, checkpoint_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.checkpoint_engine import get_checkpoint_engine

        return get_checkpoint_engine(project_id).restore_checkpoint(checkpoint_id)

    try:
        return await asyncio.to_thread(_do)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


class CanaryProbeBody(BaseModel):
    port: int = Field(default=3000, ge=1024, le=65535)
    mock_success: bool = Field(default=False)


@router.post("/{project_id}/canary/probe")
@require_permission("projects", "write")
async def probe_project_canary(project_id: str, body: CanaryProbeBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.projects.canary_watchdog import get_canary_watchdog

        return get_canary_watchdog(project_id).probe_staging(port=body.port, mock_success=body.mock_success).to_dict()

    return await asyncio.to_thread(_do)


@router.get("/{project_id}/benchmarks/leaderboard")
@require_permission("projects", "read")
async def get_project_bot_leaderboard(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.benchmarks.arena import get_benchmark_arena

        return {
            "project_id": project_id,
            "leaderboard": [e.to_dict() for e in get_benchmark_arena(project_id).get_leaderboard()],
        }

    return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# Phase 7 ASI Core Endpoints: AVO, Epistemics, RSI, and Trajectories
# ---------------------------------------------------------------------------


class AVOIterateBody(BaseModel):
    hypothesis: str = Field(default="Vectorize tensor operations to reduce memory latency", max_length=500)
    modification: str = Field(default="torch.matmul -> fused_kernel", max_length=500)
    correctness: bool = Field(default=True)
    performance_score: float = Field(default=0.88, ge=0.0, le=1.0)
    quality_score: float = Field(default=0.92, ge=0.0, le=1.0)
    parent_id: str | None = None


@router.get("/{project_id}/avo/lineage")
@require_permission("projects", "read")
async def get_project_avo_lineage(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.avo import get_avo_runner

        runner = get_avo_runner(project_id)
        return {
            "project_id": project_id,
            "head_id": runner.lineage.head_id,
            "versions": [v.to_dict() for v in runner.lineage.get_history()],
            "pareto_frontier": [v.to_dict() for v in runner.lineage.get_pareto_frontier()],
            "supervisor_status": runner.supervisor.diagnose_state(),
        }

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/avo/iterate")
@require_permission("projects", "write")
async def run_project_avo_iteration(project_id: str, body: AVOIterateBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.avo import VersionRecord, get_avo_runner

        runner = get_avo_runner(project_id)
        parent = body.parent_id or runner.lineage.head_id

        candidate = VersionRecord(
            parent_id=parent,
            hypothesis=body.hypothesis,
            modification=body.modification,
            correctness=body.correctness,
            performance_score=body.performance_score,
            quality_score=body.quality_score,
        )
        committed = runner.lineage.commit_candidate(candidate)
        signature = f"{body.modification[:30]}_{body.correctness}"
        stagnated, directive, diag = runner.supervisor.observe_step(
            improved=committed,
            signature=signature,
            backtrack_candidate=parent,
        )

        return {
            "version_id": candidate.version_id,
            "committed": committed,
            "composite_score": candidate.composite_score,
            "current_head": runner.lineage.head_id,
            "stagnation_detected": stagnated,
            "diagnostic": diag,
            "active_directive": directive.to_dict() if directive else None,
            "pareto_frontier_size": len(runner.lineage.get_pareto_frontier()),
        }

    return await asyncio.to_thread(_do)


class CreateClaimBody(BaseModel):
    text: str = Field(..., min_length=3, max_length=500)
    status: str = Field(default="hypothesis")
    prior_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    falsification_test: str = Field(default="", max_length=500)
    verification_method: str = Field(default="", max_length=500)


class AddEvidenceBody(BaseModel):
    evidence: str = Field(..., min_length=3, max_length=1000)
    is_supporting: bool = Field(default=True)
    likelihood_ratio: float = Field(default=3.0, ge=1.0, le=100.0)


@router.get("/{project_id}/epistemics/claims")
@require_permission("projects", "read")
async def list_project_epistemic_claims(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.epistemics import get_epistemic_engine

        engine = get_epistemic_engine(project_id)
        return {
            "project_id": project_id,
            "claims": [c.to_dict() for c in engine.list_all()],
            "unverified_assumptions": [c.to_dict() for c in engine.get_unverified_assumptions()],
        }

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/epistemics/claims")
@require_permission("projects", "write")
async def register_project_epistemic_claim(project_id: str, body: CreateClaimBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.epistemics import EpistemicStatus, get_epistemic_engine

        engine = get_epistemic_engine(project_id)
        try:
            status_enum = EpistemicStatus(body.status.lower())
        except ValueError:
            status_enum = EpistemicStatus.HYPOTHESIS

        claim = engine.register_claim(
            text=body.text,
            status=status_enum,
            prior_confidence=body.prior_confidence,
            falsification_test=body.falsification_test,
            verification_method=body.verification_method,
        )
        return claim.to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/epistemics/claims/{claim_id}/evidence")
@require_permission("projects", "write")
async def add_project_epistemic_evidence(project_id: str, claim_id: str, body: AddEvidenceBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.epistemics import get_epistemic_engine

        engine = get_epistemic_engine(project_id)
        claim = engine.update_with_evidence(
            claim_id=claim_id,
            evidence=body.evidence,
            is_supporting=body.is_supporting,
            likelihood_ratio=body.likelihood_ratio,
        )
        return claim.to_dict()

    try:
        return await asyncio.to_thread(_do)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


class TriggerRSICycleBody(BaseModel):
    bottleneck: str = Field(default="Context window saturation during long-running tasks", max_length=500)
    target_component: str = Field(default="compaction", max_length=64)
    force_promote: bool = Field(default=False)


@router.get("/{project_id}/rsi/status")
@require_permission("projects", "read")
async def get_project_rsi_status(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.rsi import get_rsi_engine

        return {
            "project_id": project_id,
            "status": get_rsi_engine(project_id).get_status(),
        }

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/rsi/cycle")
@require_permission("projects", "write")
async def run_project_rsi_cycle(project_id: str, body: TriggerRSICycleBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.rsi import get_rsi_engine

        engine = get_rsi_engine(project_id)
        result = engine.run_rsi_cycle(
            bottleneck=body.bottleneck,
            target_component=body.target_component,
            force_promote=body.force_promote,
        )
        return result.to_dict()

    return await asyncio.to_thread(_do)


class RecordStepBody(BaseModel):
    step_index: int = Field(..., ge=0)
    thought: str = Field(default="", max_length=2000)
    tool_name: str = Field(default="", max_length=128)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_output: str = Field(default="")
    milestone_id: str = Field(default="", max_length=128)
    status: str = Field(default="success", max_length=32)
    error: str = Field(default="")


class ReplayTrajectoryBody(BaseModel):
    from_step_index: int = Field(default=0, ge=0)


@router.get("/{project_id}/trajectories")
@require_permission("projects", "read")
async def list_project_trajectories(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.trajectory.store import get_trajectory_store

        store = get_trajectory_store(project_id)
        goal_ids = store.list_goal_ids()
        return {
            "project_id": project_id,
            "goals": [store.get_trajectory(gid).to_dict() for gid in goal_ids],
        }

    return await asyncio.to_thread(_do)


@router.get("/{project_id}/trajectories/{goal_id}")
@require_permission("projects", "read")
async def get_project_trajectory(project_id: str, goal_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.trajectory.store import get_trajectory_store

        store = get_trajectory_store(project_id)
        return store.get_trajectory(goal_id).to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/trajectories/{goal_id}/step")
@require_permission("projects", "write")
async def record_project_trajectory_step(project_id: str, goal_id: str, body: RecordStepBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.trajectory.store import get_trajectory_store

        store = get_trajectory_store(project_id)
        step = store.record_step(
            goal_id=goal_id,
            step_index=body.step_index,
            thought=body.thought,
            tool_name=body.tool_name,
            tool_input=body.tool_input,
            tool_output=body.tool_output,
            milestone_id=body.milestone_id,
            status=body.status,
            error=body.error,
        )
        return step.to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/trajectories/{goal_id}/replay")
@require_permission("projects", "write")
async def replay_project_trajectory(project_id: str, goal_id: str, body: ReplayTrajectoryBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.trajectory.store import get_trajectory_store

        store = get_trajectory_store(project_id)
        return store.replay_from_step(goal_id=goal_id, from_step_index=body.from_step_index)

    return await asyncio.to_thread(_do)


# ==============================================================================
# Autonomous Self-Configuration Engine Endpoints
# ==============================================================================

class SelfConfigInferBody(BaseModel):
    goal: str = Field(..., min_length=1, max_length=5000)
    context: dict[str, Any] = Field(default_factory=dict)


class SelfConfigTuneBody(BaseModel):
    reasoning_budget_tokens: int | None = Field(default=None, ge=256, le=65536)
    context_compaction_threshold: int | None = Field(default=None, ge=1000, le=200000)
    loop_detection_limit: int | None = Field(default=None, ge=1, le=20)
    primary_model: str | None = Field(default=None, max_length=128)
    operating_mode: str | None = Field(default=None, max_length=64)
    thought_depth: str | None = Field(default=None, max_length=32)
    extra_tools: list[str] | None = Field(default=None)
    disabled_tools: list[str] | None = Field(default=None)


class SelfConfigApplyBody(BaseModel):
    operating_mode: str = Field(default="autonomous")
    model_tier: str = Field(default="reasoning_frontier")
    primary_model: str = Field(default="claude-3-7-sonnet-thinking")
    fallback_model: str = Field(default="gpt-4o")
    active_tools: list[str] = Field(default_factory=list)
    reasoning_budget_tokens: int = Field(default=8192)
    thought_depth: str = Field(default="deep")
    max_turns: int = Field(default=50)
    context_compaction_threshold: int = Field(default=50000)
    loop_detection_limit: int = Field(default=3)
    topology: str = Field(default="hierarchical")


@router.get("/{project_id}/self-config/status")
@require_permission("projects", "read")
async def get_project_self_config_status(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.autoconfig import get_self_config_engine

        engine = get_self_config_engine(project_id)
        return engine.get_status()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/self-config/infer")
@require_permission("projects", "write")
async def infer_project_self_config(project_id: str, body: SelfConfigInferBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.autoconfig import get_self_config_engine

        engine = get_self_config_engine(project_id)
        analysis = engine.analyze_goal(body.goal, body.context)
        profile = engine.synthesize_profile(analysis, project_id)
        return {
            "analysis": analysis.to_dict(),
            "recommended_profile": profile.to_dict(),
        }

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/self-config/tune")
@require_permission("projects", "write")
async def tune_project_self_config(project_id: str, body: SelfConfigTuneBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.autoconfig import RuntimeTuningUpdate, get_self_config_engine

        engine = get_self_config_engine(project_id)
        updates = RuntimeTuningUpdate(
            reasoning_budget_tokens=body.reasoning_budget_tokens,
            context_compaction_threshold=body.context_compaction_threshold,
            loop_detection_limit=body.loop_detection_limit,
            primary_model=body.primary_model,
            operating_mode=body.operating_mode,
            thought_depth=body.thought_depth,
            extra_tools=body.extra_tools,
            disabled_tools=body.disabled_tools,
        )
        updated = engine.tune_profile(updates)
        return updated.to_dict()

    return await asyncio.to_thread(_do)


# ==============================================================================
# Agent Meta-Compiler & Self-Replication Endpoints
# ==============================================================================

class MetaCompileBody(BaseModel):
    parent_id: str | None = Field(default=None, max_length=128)
    optimization_target: str = Field(default="performance_and_reasoning", max_length=128)
    mutation_notes: str = Field(default="", max_length=1000)
    specialist_domain: str | None = Field(default=None, max_length=64)


class MetaBenchmarkBody(BaseModel):
    blueprint_id: str = Field(..., min_length=1, max_length=128)
    baseline_score: float = Field(default=0.80, ge=0.0, le=1.0)


class MetaHotSwapBody(BaseModel):
    blueprint_id: str = Field(..., min_length=1, max_length=128)
    force: bool = Field(default=False)


class MetaRollbackBody(BaseModel):
    target_blueprint_id: str = Field(..., min_length=1, max_length=128)


@router.get("/{project_id}/meta-compiler/lineage")
@require_permission("projects", "read")
async def get_project_meta_compiler_lineage(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.metacompiler import get_meta_compiler_lineage

        store = get_meta_compiler_lineage(project_id)
        return store.get_status()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/meta-compiler/compile")
@require_permission("projects", "write")
async def compile_next_gen_agent(project_id: str, body: MetaCompileBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.metacompiler import AgentMetaCompiler, get_meta_compiler_lineage

        store = get_meta_compiler_lineage(project_id)
        parent = store._blueprints.get(body.parent_id) if body.parent_id else store.active_head
        if not parent:
            parent = store.active_head

        if body.specialist_domain:
            candidate = AgentMetaCompiler.synthesize_specialist(body.specialist_domain, parent)
        else:
            candidate = AgentMetaCompiler.compile_next_generation(
                parent=parent,
                optimization_target=body.optimization_target,
                mutation_notes=body.mutation_notes,
            )

        store.register_blueprint(candidate)
        return candidate.to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/meta-compiler/benchmark")
@require_permission("projects", "write")
async def benchmark_candidate_agent(project_id: str, body: MetaBenchmarkBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.metacompiler import MetaBenchmarkHarness, get_meta_compiler_lineage

        store = get_meta_compiler_lineage(project_id)
        candidate = store._blueprints.get(body.blueprint_id)
        if not candidate:
            raise HTTPException(status_code=404, detail=f"Blueprint {body.blueprint_id} not found")

        scorecard = MetaBenchmarkHarness.evaluate_blueprint(candidate, baseline_score=body.baseline_score)
        store.record_benchmark(scorecard)
        return scorecard.to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/meta-compiler/hotswap")
@require_permission("projects", "write")
async def hotswap_candidate_agent(project_id: str, body: MetaHotSwapBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.metacompiler import get_meta_compiler_lineage

        store = get_meta_compiler_lineage(project_id)
        outcome = store.promote_blueprint(body.blueprint_id, force=body.force)
        return outcome.to_dict()

    try:
        return await asyncio.to_thread(_do)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{project_id}/meta-compiler/rollback")
@require_permission("projects", "write")
async def rollback_agent_architecture(project_id: str, body: MetaRollbackBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.metacompiler import get_meta_compiler_lineage

        store = get_meta_compiler_lineage(project_id)
        success = store.rollback(body.target_blueprint_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Blueprint {body.target_blueprint_id} not found")
        return {
            "success": True,
            "active_head_id": store.active_head.blueprint_id,
            "generation": store.active_head.generation,
        }

    return await asyncio.to_thread(_do)


# ==============================================================================
# Perpetual Never-Ending Autonomous Daemon Endpoints
# ==============================================================================

class PerpetualGoalBody(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    description: str = Field(default="", max_length=2000)
    priority: int = Field(default=1, ge=1, le=10)


@router.get("/{project_id}/perpetual/status")
@require_permission("projects", "read")
async def get_project_perpetual_status(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        return daemon.get_status()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/perpetual/start")
@require_permission("projects", "write")
async def start_project_perpetual_daemon(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        daemon.start()
        return {"project_id": project_id, "state": daemon.state.value}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/perpetual/stop")
@require_permission("projects", "write")
async def stop_project_perpetual_daemon(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        daemon.stop()
        return {"project_id": project_id, "state": daemon.state.value}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/perpetual/heartbeat")
@require_permission("projects", "write")
async def trigger_project_perpetual_heartbeat(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        return daemon.step_heartbeat()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/perpetual/discover")
@require_permission("projects", "write")
async def trigger_project_perpetual_discovery(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        discovered = daemon.trigger_discovery()
        return {"discovered_count": len(discovered), "tasks": discovered}

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/perpetual/consolidate")
@require_permission("projects", "write")
async def trigger_project_perpetual_consolidation(project_id: str, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        return daemon.trigger_consolidation()

    return await asyncio.to_thread(_do)


@router.post("/{project_id}/perpetual/goals")
@require_permission("projects", "write")
async def create_project_perpetual_goal(project_id: str, body: PerpetualGoalBody, request: Request) -> dict:
    await _require_project(project_id, request)

    def _do():
        from agent_workspace.perpetual import get_perpetual_daemon

        daemon = get_perpetual_daemon(project_id)
        goal = daemon.create_goal(title=body.title, description=body.description, priority=body.priority)
        return goal.to_dict()

    return await asyncio.to_thread(_do)



