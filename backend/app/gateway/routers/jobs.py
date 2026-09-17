"""Gateway REST Router for Decoupled External Jobs."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.auth_disabled import AUTH_SOURCE_PAT, AUTH_SOURCE_SESSION
from app.gateway.authz import require_permission
from app.gateway.deps import get_current_user_from_request, require_admin_user
from agent_workspace.jobs import (
    JobPriority,
    JobSpec,
    JobStatus,
    PersistentJobQueue,
)
from agent_workspace.jobs.runner import ExternalJobRunner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_GLOBAL_QUEUE = PersistentJobQueue(max_concurrency=4)
_GLOBAL_RUNNER = ExternalJobRunner(queue=_GLOBAL_QUEUE)


async def _job_owner(request: Request) -> str:
    user = await get_current_user_from_request(request)
    if getattr(request.state, "auth_source", None) == AUTH_SOURCE_PAT:
        raise HTTPException(status_code=403, detail="PAT credentials are not permitted on this route")
    owner_id = getattr(user, "id", None)
    if owner_id is None or not str(owner_id).strip():
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(owner_id)


class JobSubmitRequest(BaseModel):
    command: list[str] | str = Field(..., description="Executable command or shell string")
    title: str = Field(default="Background Task", description="Human-readable job label")
    working_dir: str | None = Field(default=None, description="Working directory path")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    priority: str = Field(default="normal", description="Priority: critical, high, normal, low")
    timeout_seconds: float = Field(default=300.0, description="Hard timeout in seconds")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")


@router.post("")
@require_permission("runs", "create")
async def submit_job(payload: JobSubmitRequest, request: Request, owner_id: str = Depends(_job_owner)):
    """Submits a decoupled background job for execution."""
    await require_admin_user(request, detail="Authenticated operator permission is required for host jobs")
    if getattr(request.state, "auth_source", None) != AUTH_SOURCE_SESSION:
        raise HTTPException(status_code=403, detail="Host jobs require an authenticated operator session")
    p_enum = JobPriority.NORMAL
    try:
        p_enum = JobPriority(payload.priority.lower().strip())
    except ValueError:
        pass

    spec = JobSpec(
        owner_id=owner_id,
        title=payload.title,
        command=payload.command,
        working_dir=payload.working_dir,
        env=payload.env,
        priority=p_enum,
        tags=payload.tags,
    )
    spec.resources.timeout_seconds = payload.timeout_seconds

    try:
        job_id = await _GLOBAL_RUNNER.submit_async(spec, authorized_operator=True)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    return {
        "status": "queued",
        "job_id": job_id,
        "title": spec.title,
        "priority": spec.priority.value,
        "timeout_seconds": spec.resources.timeout_seconds,
    }


@router.get("")
@require_permission("runs", "read")
async def list_jobs(request: Request, status: str | None = None, tag: str | None = None, limit: int = Query(default=50, ge=1, le=100), owner_id: str = Depends(_job_owner)):
    """Lists registered background jobs with optional status/tag filtering."""
    s_enum = None
    if status:
        try:
            s_enum = JobStatus(status.lower().strip())
        except ValueError:
            pass
    jobs = _GLOBAL_QUEUE.list_jobs(status=s_enum, tag=tag, limit=limit, owner_id=owner_id)
    return [j.model_dump() for j in jobs]


@router.get("/{job_id}")
@require_permission("runs", "read")
async def get_job_status(job_id: str, request: Request, owner_id: str = Depends(_job_owner)):
    """Retrieves status and metadata of a specific job."""
    res = _GLOBAL_QUEUE.get_status(job_id, owner_id=owner_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return res.model_dump()


@router.get("/{job_id}/logs")
@require_permission("runs", "read")
async def get_job_logs(job_id: str, request: Request, owner_id: str = Depends(_job_owner)):
    """Retrieves stdout/stderr outputs for a specific job."""
    res = _GLOBAL_QUEUE.get_status(job_id, owner_id=owner_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return {
        "job_id": job_id,
        "status": res.status.value,
        "exit_code": res.exit_code,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "execution_seconds": res.execution_seconds,
    }


@router.post("/{job_id}/cancel")
@require_permission("runs", "cancel")
async def cancel_job(job_id: str, request: Request, owner_id: str = Depends(_job_owner)):
    """Cancels a queued or currently executing background job."""
    if _GLOBAL_QUEUE.get_status(job_id, owner_id=owner_id) is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found or already finished.")
    cancelled = _GLOBAL_RUNNER.cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found or already finished.")
    return {"status": "cancelled", "job_id": job_id}
