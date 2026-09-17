"""Autonomous Agent Swarms API Router.

Provides Gateway endpoints for swarm feasibility evaluation, dynamic DAG planning,
hybrid execution coordination, straggler recovery, and deliverable aggregation.
All storage and coordinator operations run via asyncio.to_thread to maintain Gateway async rules.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import require_admin_user
from agent_workspace.swarm.coordinator import get_swarm_coordinator
from agent_workspace.swarm.models import SwarmMode

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/swarms", tags=["swarms"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage swarms."


class SwarmEvaluateRequest(BaseModel):
    goal: str = Field(..., min_length=3)
    items: list[str] | None = None


class SwarmCreateRequest(BaseModel):
    goal: str = Field(..., min_length=3)
    mode: str = "auto"
    items: list[str] | None = None
    max_concurrency: int = Field(default=8, ge=1, le=12)


class SwarmTaskCompleteRequest(BaseModel):
    result_summary: str = Field(..., min_length=1)
    evidence: list[dict] | None = None
    output_artifacts: list[str] | None = None


class SwarmCancelRequest(BaseModel):
    reason: str = ""


@router.post("/evaluate")
async def evaluate_swarm_feasibility(payload: SwarmEvaluateRequest):
    """Evaluates whether a task warrants an autonomous swarm and calculates expected speedup."""
    coordinator = get_swarm_coordinator()
    decision = await asyncio.to_thread(coordinator.evaluate_intent, payload.goal, payload.items)
    return decision.to_dict()


@router.post("")
async def create_and_spawn_swarm(payload: SwarmCreateRequest, request: Request):
    """Decomposes a goal into a DAG and spawns an autonomous swarm."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()

    try:
        mode = SwarmMode(payload.mode.lower().strip())
    except ValueError:
        mode = SwarmMode.AUTO

    plan = await asyncio.to_thread(
        coordinator.create_swarm,
        goal=payload.goal,
        mode=mode,
        items=payload.items,
        max_concurrency=payload.max_concurrency,
    )
    return plan.to_dict()


@router.get("")
async def list_swarms(limit: int = 20):
    """Lists existing swarms sorted by creation timestamp."""
    coordinator = get_swarm_coordinator()
    plans = await asyncio.to_thread(coordinator.list_swarms, limit)
    return [p.to_dict() for p in plans]


@router.get("/{swarm_id}")
async def get_swarm_details(swarm_id: str):
    """Retrieves full details of a swarm including the task DAG and progress metrics."""
    coordinator = get_swarm_coordinator()
    plan = await asyncio.to_thread(coordinator.get_swarm, swarm_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")
    return plan.to_dict()


@router.post("/{swarm_id}/step")
async def step_swarm(swarm_id: str, request: Request):
    """Drives execution: dispatches ready tasks, reconciles stragglers, and triggers aggregation."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    res = await asyncio.to_thread(coordinator.step, swarm_id)
    return res


@router.post("/{swarm_id}/pause")
async def pause_swarm(swarm_id: str, request: Request):
    """Pauses an ongoing swarm."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    ok = await asyncio.to_thread(coordinator.pause_swarm, swarm_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot pause swarm (not found or already completed/cancelled).")
    return {"status": "paused", "swarm_id": swarm_id}


@router.post("/{swarm_id}/resume")
async def resume_swarm(swarm_id: str, request: Request):
    """Resumes a paused swarm."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    ok = await asyncio.to_thread(coordinator.resume_swarm, swarm_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot resume swarm (not found or not in paused state).")
    return {"status": "running", "swarm_id": swarm_id}


@router.post("/{swarm_id}/cancel")
async def cancel_swarm(swarm_id: str, payload: SwarmCancelRequest, request: Request):
    """Cancels an ongoing swarm."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    ok = await asyncio.to_thread(coordinator.cancel_swarm, swarm_id, reason=payload.reason)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")
    return {"status": "cancelled", "swarm_id": swarm_id}


@router.get("/{swarm_id}/events")
async def get_swarm_events(swarm_id: str, limit: int = 50):
    """Retrieves chronological audit events for a swarm."""
    coordinator = get_swarm_coordinator()
    events = await asyncio.to_thread(coordinator.get_events, swarm_id, limit)
    return [e.to_dict() for e in events]


@router.post("/{swarm_id}/tasks/{task_id}/complete")
async def complete_swarm_task(swarm_id: str, task_id: str, payload: SwarmTaskCompleteRequest, request: Request):
    """Records completion of an individual swarm task by a worker."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    plan = await asyncio.to_thread(coordinator.get_swarm, swarm_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")

    from agent_workspace.swarm.scheduler import SwarmScheduler

    scheduler = SwarmScheduler(plan)
    updated = scheduler.mark_completed(
        task_id=task_id,
        result_summary=payload.result_summary,
        evidence=payload.evidence,
        output_artifacts=payload.output_artifacts,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found in swarm '{swarm_id}'.")

    await asyncio.to_thread(coordinator.checkpoint, swarm_id)
    await asyncio.to_thread(
        coordinator.append_event,
        swarm_id,
        "TASK_COMPLETED",
        task_id=task_id,
        details={"summary": payload.result_summary},
    )
    return updated.to_dict()


class SwarmExpandRequest(BaseModel):
    new_tasks: list[dict]
    parent_task_id: str | None = None


@router.post("/{swarm_id}/run-async")
async def run_swarm_background(swarm_id: str, request: Request):
    """Starts autonomous execution of the swarm in a background event loop."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    plan = await asyncio.to_thread(coordinator.get_swarm, swarm_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")
    coordinator.start_async(swarm_id)
    return {"status": "started_async", "swarm_id": swarm_id}


@router.post("/{swarm_id}/expand")
async def expand_swarm(swarm_id: str, payload: SwarmExpandRequest, request: Request):
    """Dynamically injects new tasks into an active swarm DAG mid-flight."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    coordinator = get_swarm_coordinator()
    added_ids = await asyncio.to_thread(
        coordinator.dynamic_expand,
        swarm_id,
        payload.new_tasks,
        payload.parent_task_id,
    )
    return {"added_task_ids": added_ids, "swarm_id": swarm_id}


@router.get("/{swarm_id}/incidents")
async def get_swarm_incidents(swarm_id: str):
    """Retrieves recorded failure incidents and succession recovery actions."""
    from agent_workspace.swarm.incidents import get_swarm_incident_manager

    inc_mgr = get_swarm_incident_manager()
    incidents = await asyncio.to_thread(inc_mgr.get_incidents, swarm_id)
    return [i.to_dict() for i in incidents]


@router.get("/{swarm_id}/memory")
async def get_swarm_memory(swarm_id: str):
    """Retrieves operational facts and discovered artifacts from the Swarm Blackboard."""
    from agent_workspace.swarm.memory import get_swarm_memory_manager

    mem_mgr = get_swarm_memory_manager()
    facts = await asyncio.to_thread(mem_mgr.get_facts, swarm_id)
    artifacts = await asyncio.to_thread(mem_mgr.get_artifacts, swarm_id)
    return {"swarm_id": swarm_id, "facts": facts, "artifacts": artifacts}


@router.get("/governor/status")
async def get_resource_governor_status():
    """Retrieves model tier routing and rate-limit throttle status."""
    from agent_workspace.swarm.governor import get_swarm_resource_governor

    gov = get_swarm_resource_governor()
    status = await asyncio.to_thread(gov.get_status)
    return status
