"""Sub-Agent Control Plane Gateway API Router.

Provides endpoints for asynchronous subagent provisioning, real-time heartbeats,
task lease renewal, step checkpointing, hot-replacement, parent-failure orphan adoption,
and dynamic promotion into permanent Specialist Bots.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import require_admin_user
from agent_workspace.subagents.lifecycle import (
    SubagentContract,
    SubagentStatusEnum,
    get_subagent_lifecycle_manager,
)
from agent_workspace.subagents.promotion import get_subagent_promotion_manager
from agent_workspace.subagents.resilience import get_subagent_resilience_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/subagents/control", tags=["subagents-control"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage subagent control plane operations."


class SpawnSubagentRequest(BaseModel):
    parent_agent_id: str = "lead-agent"
    objective: str = Field(..., min_length=3)
    role: str = "specialist"
    instructions: str = ""
    model_override: str | None = None
    workspace_mode: str = "isolated"
    timeout_seconds: int = 600
    lease_duration_seconds: int = 60
    parent_task_id: str | None = None
    depth: int = 1


class SubagentHeartbeatRequest(BaseModel):
    current_action: str = "executing"
    progress_percent: float = 0.0
    last_tool: str | None = None
    tokens_used: int = 0


class SubagentCheckpointRequest(BaseModel):
    step_index: int = 1
    intermediate_artifacts: list[str] | None = None
    decisions: list[dict] | None = None
    evidence: list[dict] | None = None
    next_action: str = ""


class SubagentReplaceRequest(BaseModel):
    reason: str = "Worker stalled or encountered fatal error"


class SubagentCancelRequest(BaseModel):
    reason: str = "User/Parent requested cancellation"


class SubagentAdoptRequest(BaseModel):
    new_parent_id: str = Field(..., min_length=1)
    specific_subagent_ids: list[str] | None = None


class SubagentPromoteRequest(BaseModel):
    role: str = Field(..., min_length=1)
    bot_name: str | None = None
    display_name: str | None = None
    description: str | None = None


@router.post("/spawn")
async def spawn_subagent(payload: SpawnSubagentRequest, request: Request):
    """Asynchronously provisions a new task-scoped subagent with an active lease."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    lifecycle = get_subagent_lifecycle_manager()

    contract = SubagentContract(
        objective=payload.objective,
        role=payload.role,
        instructions=payload.instructions,
        model_override=payload.model_override,
        workspace_mode=payload.workspace_mode,
        timeout_seconds=payload.timeout_seconds,
        lease_duration_seconds=payload.lease_duration_seconds,
    )

    try:
        rec = await asyncio.to_thread(
            lifecycle.spawn_subagent,
            parent_agent_id=payload.parent_agent_id,
            contract=contract,
            parent_task_id=payload.parent_task_id,
            depth=payload.depth,
        )
        return rec.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{subagent_id}")
async def get_subagent_details(subagent_id: str):
    """Retrieves full metadata, lease validity, and heartbeat status for a subagent."""
    lifecycle = get_subagent_lifecycle_manager()
    rec = await asyncio.to_thread(lifecycle.get_subagent, subagent_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Subagent '{subagent_id}' not found.")
    return rec.to_dict()


@router.get("/{subagent_id}/result")
async def get_subagent_result(subagent_id: str):
    """Retrieves the deliverable findings and artifacts of a completed subagent."""
    lifecycle = get_subagent_lifecycle_manager()
    rec = await asyncio.to_thread(lifecycle.get_subagent, subagent_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Subagent '{subagent_id}' not found.")
    return rec.result.to_dict() if rec.result else {"status": rec.status.value, "result": None}


@router.post("/{subagent_id}/heartbeat")
async def record_subagent_heartbeat(subagent_id: str, payload: SubagentHeartbeatRequest):
    """Emits a liveness heartbeat, updates progress, and extends the worker lease."""
    lifecycle = get_subagent_lifecycle_manager()
    ok = await asyncio.to_thread(
        lifecycle.record_heartbeat,
        subagent_id=subagent_id,
        current_action=payload.current_action,
        progress_percent=payload.progress_percent,
        last_tool=payload.last_tool,
        tokens_used=payload.tokens_used,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot record heartbeat (subagent not found or in terminal state).")
    return {"status": "heartbeat_recorded", "subagent_id": subagent_id}


@router.post("/{subagent_id}/checkpoint")
async def save_subagent_checkpoint(subagent_id: str, payload: SubagentCheckpointRequest, request: Request):
    """Saves a step checkpoint with intermediate artifacts and decisions."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    resilience = get_subagent_resilience_engine()

    cp = await asyncio.to_thread(
        resilience.save_checkpoint,
        subagent_id=subagent_id,
        step_index=payload.step_index,
        intermediate_artifacts=payload.intermediate_artifacts,
        decisions=payload.decisions,
        evidence=payload.evidence,
        next_action=payload.next_action,
    )
    return cp.to_dict()


@router.post("/{subagent_id}/replace")
async def replace_subagent(subagent_id: str, payload: SubagentReplaceRequest, request: Request):
    """Hot-replaces a failed/stalled subagent, restoring the latest checkpoint onto a new worker."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    resilience = get_subagent_resilience_engine()

    try:
        new_worker = await asyncio.to_thread(resilience.hot_replace_subagent, subagent_id, payload.reason)
        return {
            "replaced_worker_id": subagent_id,
            "new_worker": new_worker.to_dict(),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{subagent_id}/cancel")
async def cancel_subagent(subagent_id: str, payload: SubagentCancelRequest, request: Request):
    """Cancels a subagent and propagates cancellation to all child subagents."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    lifecycle = get_subagent_lifecycle_manager()

    ok = await asyncio.to_thread(lifecycle.cancel_subagent, subagent_id, payload.reason)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot cancel subagent (not found or already completed/cancelled).")
    return {"status": "cancelled", "subagent_id": subagent_id}


@router.post("/adopt")
async def adopt_orphans(payload: SubagentAdoptRequest, request: Request):
    """Adopts orphaned subagents whose parent agent crashed."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    resilience = get_subagent_resilience_engine()

    adopted = await asyncio.to_thread(
        resilience.adopt_orphaned_subagents,
        new_parent_id=payload.new_parent_id,
        specific_subagent_ids=payload.specific_subagent_ids,
    )
    return {
        "new_parent_id": payload.new_parent_id,
        "adopted_count": len(adopted),
        "adopted_ids": adopted,
    }


@router.post("/promote")
async def promote_subagent_role(payload: SubagentPromoteRequest, request: Request):
    """Promotes a recurring subagent role into a permanent Specialist Bot profile."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    promotion = get_subagent_promotion_manager()

    profile = await asyncio.to_thread(
        promotion.promote_to_specialist_bot,
        role=payload.role,
        bot_name=payload.bot_name,
        display_name=payload.display_name,
        description=payload.description,
    )
    return profile


@router.get("")
async def list_subagents(
    parent_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
):
    """Lists subagents with optional filtering by parent or status."""
    lifecycle = get_subagent_lifecycle_manager()
    status_filter = SubagentStatusEnum(status.lower().strip()) if status else None
    recs = await asyncio.to_thread(
        lifecycle.list_subagents,
        parent_id=parent_id,
        status=status_filter,
        limit=limit,
    )
    return [r.to_dict() for r in recs]
