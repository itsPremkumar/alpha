"""Policy engine API: evaluate actions, manage approval policies and requests."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.utils.time import coerce_iso

router = APIRouter(prefix="/api/policy", tags=["policy"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage policy."

# ---------------------------------------------------------------------------
# Wire projection (ISO 8601 at the API boundary only).
#
# `ApprovalPolicy.created_at` (alpha/policy/engine.py:62) and the
# `ApprovalRequest.created_at` declared below are `time.time()` floats, and both
# `to_dict()`s are plain `dataclasses.asdict`, so `/api/policy/policies` and
# `/api/policy/approvals` emitted a raw epoch while every sibling Gateway route
# emitted ISO 8601.
#
# `ApprovalPolicy.to_dict()` is ALSO the on-disk format: `PolicyEngine._save`
# writes `[p.to_dict() for p in self._policies]` to `policies.json`, and
# `ApprovalPolicy.from_dict` reads the same `created_at` back. That is exactly
# why the coercion lives in the router and not in the model - changing the
# stored type would make every existing policy file unreadable.
# ---------------------------------------------------------------------------


def _wire_ts(value: object) -> str:
    """A stored epoch float as ISO 8601.

    Both fields are `default_factory=time.time`: they are stamped when the row
    is created, so they have no "never" state and therefore no epoch-zero
    sentinel to translate. Rendering a stored ``0.0`` as the real epoch it
    claims to be is more honest than inventing a null.
    """
    return coerce_iso(value)


def _policy_to_wire(policy: Any) -> dict[str, Any]:
    row = policy.to_dict()
    return {**row, "created_at": _wire_ts(row.get("created_at"))}


def _approval_to_wire(approval: ApprovalRequest) -> dict[str, Any]:
    row = approval.to_dict()
    return {**row, "created_at": _wire_ts(row.get("created_at"))}


class EvaluateRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=500)
    actor: str = Field(default="*", max_length=64)
    project_id: str = Field(default="*", max_length=64)


class PolicyCreateRequest(BaseModel):
    action_pattern: str = Field(..., min_length=1, max_length=500)
    actor: str = Field(default="*", max_length=64)
    project_id: str = Field(default="*", max_length=64)
    auto: str = Field(default="allow", max_length=16)
    note: str = Field(default="", max_length=1000)


@dataclass
class ApprovalRequest:
    request_id: str
    action: str
    actor: str
    project_id: str
    reason: str = ""
    status: str = "pending"
    decided_by: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_approvals: dict[str, ApprovalRequest] = {}
_approvals_lock = threading.Lock()


class ApprovalCreateRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=500)
    actor: str = Field(default="agent", max_length=64)
    project_id: str = Field(default="*", max_length=64)
    reason: str = Field(default="", max_length=2000)


class ApprovalDecideRequest(BaseModel):
    approved: bool = True
    decided_by: str = Field(default="operator", max_length=64)


@router.post("/evaluate")
async def evaluate_action(body: EvaluateRequest) -> dict:
    def _do():
        from alpha.policy import get_policy_engine

        return get_policy_engine().evaluate(body.action, actor=body.actor, project_id=body.project_id).to_dict()

    return await asyncio.to_thread(_do)


@router.get("/policies")
async def list_policies() -> dict:
    def _do():
        from alpha.policy import get_policy_engine

        return [_policy_to_wire(p) for p in get_policy_engine().list_policies()]

    return {"policies": await asyncio.to_thread(_do)}


@router.post("/policies", status_code=201)
async def add_policy(body: PolicyCreateRequest) -> dict:
    if body.auto not in ("allow", "deny", "approval"):
        raise HTTPException(status_code=422, detail="auto must be allow|deny|approval.")

    def _do():
        from alpha.policy import get_policy_engine

        return _policy_to_wire(get_policy_engine().add_policy(body.action_pattern, actor=body.actor, project_id=body.project_id, auto=body.auto, note=body.note))

    return await asyncio.to_thread(_do)


@router.delete("/policies/{policy_id}")
async def remove_policy(policy_id: str) -> dict:
    def _do():
        from alpha.policy import get_policy_engine

        return get_policy_engine().remove_policy(policy_id)

    return {"removed": await asyncio.to_thread(_do)}


@router.post("/approvals", status_code=201)
async def create_approval(body: ApprovalCreateRequest) -> dict:
    req = ApprovalRequest(request_id=f"ap-{uuid.uuid4().hex[:10]}", action=body.action, actor=body.actor, project_id=body.project_id, reason=body.reason)
    with _approvals_lock:
        _approvals[req.request_id] = req
    return _approval_to_wire(req)


@router.get("/approvals")
async def list_approvals(status: str | None = None) -> dict:
    with _approvals_lock:
        rows = list(_approvals.values())
    if status:
        rows = [r for r in rows if r.status == status]
    return {"approvals": [_approval_to_wire(r) for r in rows], "count": len(rows)}


@router.post("/approvals/{request_id}/decide")
async def decide_approval(request_id: str, body: ApprovalDecideRequest) -> dict:
    with _approvals_lock:
        req = _approvals.get(request_id)
        if not req or req.status != "pending":
            raise HTTPException(status_code=404, detail="Approval request not found or already decided.")
        req.status = "approved" if body.approved else "rejected"
        req.decided_by = body.decided_by
        return _approval_to_wire(req)
