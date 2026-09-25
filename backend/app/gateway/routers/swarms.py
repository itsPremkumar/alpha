"""Owner-aware Gateway API for the Alpha swarm v2 runtime."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from alpha.runtime.user_context import get_effective_user_id
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.models import SwarmBudget, SwarmMode, is_terminal_swarm_status
from app.gateway.deps import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/swarms", tags=["swarms"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage swarms."


class SwarmEvaluateRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=32_000)
    items: list[str] | None = Field(default=None, max_length=512)


class SwarmBudgetRequest(BaseModel):
    max_tokens: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_wall_seconds: float | None = Field(default=None, ge=0)
    max_tasks: int = Field(default=256, ge=1, le=10_000)
    max_replans: int = Field(default=3, ge=0, le=100)
    max_consecutive_failures: int = Field(default=8, ge=1, le=1_000)


class SwarmCreateRequest(BaseModel):
    goal: str | None = Field(default=None, min_length=3, max_length=32_000)
    # Kept as a compatibility alias for older clients/UI payloads.  The server
    # normalizes it to ``goal`` before validation; it is never used as a path.
    objective: str | None = Field(default=None, min_length=3, max_length=32_000)
    mode: str = "auto"
    items: list[str] | None = Field(default=None, max_length=512)
    max_concurrency: int = Field(default=8, ge=1, le=64)
    budget: SwarmBudgetRequest | None = None
    auto_replan: bool = False
    requires_consensus: bool = False
    idempotency_key: str | None = Field(default=None, max_length=256)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_objective(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        goal = data.get("goal")
        objective = data.get("objective")
        if not goal and objective:
            data["goal"] = objective
        if goal and objective and str(goal) != str(objective):
            raise ValueError("goal and legacy objective must match when both are supplied")
        return data


class SwarmTaskCompleteRequest(BaseModel):
    result_summary: str = Field(..., min_length=1, max_length=50_000)
    evidence: list[dict[str, Any]] | None = Field(default=None, max_length=128)
    output_artifacts: list[str] | None = Field(default=None, max_length=128)
    lease_id: str | None = Field(default=None, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)


class SwarmCancelRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


class SwarmExpandRequest(BaseModel):
    new_tasks: list[dict[str, Any]] = Field(..., min_length=1, max_length=256)
    parent_task_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class SwarmMessageRequest(BaseModel):
    topic: str = Field(default="general", min_length=1, max_length=128)
    sender: str = Field(default="operator", min_length=1, max_length=128)
    kind: str = Field(default="observation", min_length=1, max_length=64)
    content: str = Field(default="", max_length=12_000)
    data: dict[str, Any] | None = None
    task_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=256)


class SwarmClaimRequest(BaseModel):
    owner: str = Field(..., min_length=1, max_length=128)
    lease_seconds: float = Field(default=60.0, ge=1.0, le=3_600.0)
    expected_revision: int | None = Field(default=None, ge=0)


def _request_owner(request: Request | None) -> str | None:
    """Resolve the server-owned owner for a route without trusting body fields."""

    if request is None:
        return None
    user = getattr(getattr(request, "state", None), "user", None)
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id else get_effective_user_id()


def _owner_scope(request: Request | None) -> str | None:
    """Admins may inspect all plans; ordinary callers are owner-scoped."""

    if request is None:
        return None
    user = getattr(getattr(request, "state", None), "user", None)
    if getattr(user, "system_role", None) == "admin":
        return None
    return _request_owner(request)


def _ensure_visible(swarm_id: str, request: Request | None):
    coordinator = get_swarm_coordinator()
    plan = coordinator.get_swarm(swarm_id, owner_id=_owner_scope(request))
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")
    return coordinator, plan


@router.post("/evaluate")
async def evaluate_swarm_feasibility(payload: SwarmEvaluateRequest):
    """Evaluate whether a task warrants a swarm and calculate expected speedup."""

    coordinator = get_swarm_coordinator()
    decision = await asyncio.to_thread(coordinator.evaluate_intent, payload.goal, payload.items)
    return decision.to_dict()


@router.post("")
async def create_and_spawn_swarm(
    payload: SwarmCreateRequest,
    request: Request = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Create a validated plan; execution remains an explicit async state transition."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    header_key = idempotency_key.strip() if idempotency_key else ""
    body_key = payload.idempotency_key.strip() if payload.idempotency_key else ""
    if header_key and body_key and header_key != body_key:
        raise HTTPException(status_code=409, detail="Idempotency-Key header and body key must match when both are supplied")
    coordinator = get_swarm_coordinator()
    try:
        mode = SwarmMode((payload.mode or "auto").lower().strip())
    except ValueError:
        mode = SwarmMode.AUTO
    budget = SwarmBudget.from_dict(payload.budget.model_dump() if payload.budget else None)
    try:
        plan = await asyncio.to_thread(
            coordinator.create_swarm,
            goal=payload.goal or payload.objective or "",
            mode=mode,
            items=payload.items,
            max_concurrency=payload.max_concurrency,
            owner_id=_request_owner(request) or "default",
            budget=budget,
            auto_replan=payload.auto_replan,
            requires_consensus=payload.requires_consensus,
            idempotency_key=header_key or body_key or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return plan.to_dict()


@router.get("")
async def list_swarms(limit: int = 20, *, request: Request = None):
    """List owner-visible swarms sorted by creation timestamp."""

    coordinator = get_swarm_coordinator()
    plans = await asyncio.to_thread(coordinator.list_swarms, max(1, min(int(limit), 100)), owner_id=_owner_scope(request))
    return [plan.to_dict() for plan in plans]


@router.get("/{swarm_id}")
async def get_swarm_details(swarm_id: str, request: Request = None):
    """Retrieve the full plan, task DAG, budget, and blackboard summary."""

    coordinator, plan = _ensure_visible(swarm_id, request)
    return plan.to_dict()


@router.post("/{swarm_id}/step")
async def step_swarm(swarm_id: str, request: Request = None):
    """Advance one scheduler tick without pretending it completes model work."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    return await asyncio.to_thread(coordinator.step, swarm_id)


@router.post("/{swarm_id}/pause")
async def pause_swarm(swarm_id: str, request: Request = None):
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    ok = await asyncio.to_thread(coordinator.pause_swarm, swarm_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot pause swarm (not found or already terminal).")
    return {"status": "paused", "swarm_id": swarm_id}


@router.post("/{swarm_id}/resume")
async def resume_swarm(swarm_id: str, request: Request = None):
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    ok = await asyncio.to_thread(coordinator.resume_swarm, swarm_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot resume swarm (not found or not paused).")
    return {"status": "running", "swarm_id": swarm_id}


@router.post("/{swarm_id}/cancel")
async def cancel_swarm(swarm_id: str, payload: SwarmCancelRequest, request: Request = None):
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    ok = await asyncio.to_thread(coordinator.cancel_swarm, swarm_id, payload.reason)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")
    return {"status": "cancelled", "swarm_id": swarm_id, "reason": payload.reason}


@router.get("/{swarm_id}/events")
async def get_swarm_events(swarm_id: str, limit: int = 50, *, request: Request = None):
    """Retrieve bounded, ordered audit events for one swarm."""

    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    events = await asyncio.to_thread(coordinator.get_events, swarm_id, max(1, min(int(limit), 500)))
    return [event.to_dict() for event in events]


@router.get("/{swarm_id}/events/stream")
async def stream_swarm_events(swarm_id: str, request: Request = None, timeout_seconds: int = 60):
    """Stream ordered swarm audit events until terminal state or timeout."""

    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()

    async def _stream():
        cursor = 0
        deadline = asyncio.get_running_loop().time() + max(1, min(int(timeout_seconds), 300))
        while asyncio.get_running_loop().time() < deadline:
            events = await asyncio.to_thread(coordinator.get_events, swarm_id, 500)
            for event in events:
                if event.sequence <= cursor:
                    continue
                cursor = event.sequence
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
            plan = coordinator.get_swarm(swarm_id)
            if plan is None or is_terminal_swarm_status(plan.status):
                yield f"event: end\ndata: {json.dumps({'swarm_id': swarm_id, 'status': plan.status if plan else 'not_found'})}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{swarm_id}/tasks/{task_id}/complete")
async def complete_swarm_task(swarm_id: str, task_id: str, payload: SwarmTaskCompleteRequest, request: Request = None):
    """Record a completion only when the task's current lease/attempt is valid."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _coordinator, plan = _ensure_visible(swarm_id, request)
    if task_id not in plan.tasks:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found in swarm '{swarm_id}'.")
    coordinator = get_swarm_coordinator()
    updated = await asyncio.to_thread(
        coordinator.complete_task,
        swarm_id,
        task_id,
        result_summary=payload.result_summary,
        evidence=payload.evidence,
        output_artifacts=payload.output_artifacts,
        lease_id=payload.lease_id,
        expected_revision=payload.expected_revision,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Task completion was fenced by a newer lease, revision, or terminal state.")
    if updated.state.value != "completed":
        raise HTTPException(status_code=409, detail="Task completion was fenced by a newer lease or terminal state.")
    return updated.to_dict()


@router.get("/{swarm_id}/tasks/{task_id}")
async def get_swarm_task(swarm_id: str, task_id: str, request: Request = None):
    _coordinator, plan = _ensure_visible(swarm_id, request)
    task = plan.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found in swarm '{swarm_id}'.")
    return {
        "swarm_id": swarm_id,
        "task": task.to_dict(),
        "lease": {"lease_id": task.lease_id, "owner": task.lease_owner, "expires_at": task.lease_expires_at},
    }


@router.post("/{swarm_id}/tasks/{task_id}/claim")
async def claim_swarm_task(swarm_id: str, task_id: str, payload: SwarmClaimRequest, request: Request = None):
    """Claim a ready task for an external worker with an explicit lease."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    lease = await asyncio.to_thread(
        coordinator.claim_task,
        swarm_id,
        task_id,
        owner=payload.owner,
        lease_seconds=payload.lease_seconds,
        expected_revision=payload.expected_revision,
    )
    if lease is None:
        raise HTTPException(status_code=409, detail="Task is not ready, already claimed, or the swarm is not running.")
    return lease.to_dict()


@router.post("/{swarm_id}/expand")
async def expand_swarm(swarm_id: str, payload: SwarmExpandRequest, request: Request = None):
    """Inject validated tasks into an active DAG as one bounded transition."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _coordinator, plan = _ensure_visible(swarm_id, request)
    if is_terminal_swarm_status(plan.status):
        raise HTTPException(status_code=409, detail=f"Swarm '{swarm_id}' is already terminal.")
    coordinator = get_swarm_coordinator()
    try:
        added_ids = await asyncio.to_thread(
            coordinator.dynamic_expand,
            swarm_id,
            payload.new_tasks,
            payload.parent_task_id,
            expected_revision=payload.expected_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    current = coordinator.get_swarm(swarm_id)
    return {
        "added_task_ids": added_ids,
        "swarm_id": swarm_id,
        "replans": current.replan_count if current is not None else 0,
    }


@router.post("/{swarm_id}/replan")
async def replan_swarm(swarm_id: str, payload: SwarmExpandRequest, request: Request = None):
    """Explicit alias for a validated mid-flight replan."""

    return await expand_swarm(swarm_id, payload, request)


@router.post("/{swarm_id}/run-async")
async def run_swarm_background(swarm_id: str, request: Request = None):
    """Start one idempotent background runner for a non-terminal swarm."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator, plan = _ensure_visible(swarm_id, request)
    if is_terminal_swarm_status(plan.status):
        raise HTTPException(status_code=409, detail=f"Swarm '{swarm_id}' is already in terminal status '{plan.status}'.")
    coordinator.start_async(swarm_id)
    return {"status": "started_async", "swarm_id": swarm_id, "runner_status": plan.status}


@router.get("/{swarm_id}/incidents")
async def get_swarm_incidents(swarm_id: str, request: Request = None):
    _ensure_visible(swarm_id, request)
    from alpha.swarm.incidents import get_swarm_incident_manager

    manager = get_swarm_incident_manager()
    incidents = await asyncio.to_thread(manager.get_incidents, swarm_id)
    return [incident.to_dict() for incident in incidents]


@router.get("/{swarm_id}/memory")
async def get_swarm_memory(swarm_id: str, request: Request = None):
    _ensure_visible(swarm_id, request)
    from alpha.swarm.memory import get_swarm_memory_manager

    manager = get_swarm_memory_manager()
    facts, artifacts, task_results = await asyncio.gather(
        asyncio.to_thread(manager.get_facts, swarm_id),
        asyncio.to_thread(manager.get_artifacts, swarm_id),
        asyncio.to_thread(manager.get_task_results, swarm_id),
    )
    return {"swarm_id": swarm_id, "facts": facts, "artifacts": artifacts, "task_results": task_results}


@router.get("/{swarm_id}/messages")
async def get_swarm_messages(
    swarm_id: str,
    topic: str | None = None,
    request: Request = None,
    task_id: str | None = None,
    since_sequence: int = 0,
    limit: int = 50,
):
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    messages = await asyncio.to_thread(
        coordinator.get_messages,
        swarm_id,
        topic=topic,
        task_id=task_id,
        since_sequence=max(0, int(since_sequence)),
        limit=max(1, min(int(limit), 256)),
    )
    return [message.to_dict() for message in messages]


@router.post("/{swarm_id}/messages")
async def publish_swarm_message(swarm_id: str, payload: SwarmMessageRequest, request: Request = None):
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    try:
        message = await asyncio.to_thread(
            coordinator.publish_message,
            swarm_id,
            topic=payload.topic,
            sender=_request_owner(request) or "operator",
            kind=payload.kind,
            content=payload.content,
            data=payload.data,
            task_id=payload.task_id,
            trust="untrusted",
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return message.to_dict()


@router.get("/{swarm_id}/metrics")
async def get_swarm_metrics(swarm_id: str, request: Request = None):
    _ensure_visible(swarm_id, request)
    coordinator = get_swarm_coordinator()
    return await asyncio.to_thread(coordinator.metrics, swarm_id)


@router.get("/{swarm_id}/leader")
async def get_swarm_leader(swarm_id: str, request: Request = None):
    _coordinator, plan = _ensure_visible(swarm_id, request)
    return {"swarm_id": swarm_id, "leader_election": plan.metrics.get("leader_election", {"leader": None, "reason": "not evaluated"})}


@router.get("/governor/status")
async def get_resource_governor_status():
    from alpha.swarm.governor import get_swarm_resource_governor

    governor = get_swarm_resource_governor()
    return await asyncio.to_thread(governor.get_status)
