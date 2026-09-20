"""Durable missions API: long-lived objectives above threads."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission

router = APIRouter(prefix="/api/missions", tags=["missions"])


class MissionCreateRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=5000)
    constraints: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)


def _owner(request: Request) -> str:
    try:
        from alpha.runtime.user_context import get_effective_user_id

        return get_effective_user_id() or "local-user"
    except Exception:
        return "local-user"


@router.post("", status_code=201)
@require_permission("threads", "write")
async def create_mission(body: MissionCreateRequest, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        m = get_mission_store().create(owner, body.objective, constraints=body.constraints, budget=body.budget)
        return m.to_dict()

    return await asyncio.to_thread(_do)


@router.get("")
@require_permission("threads", "read")
async def list_missions(request: Request, status: str | None = None) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        rows = get_mission_store().list(owner=owner, status=status)
        return {"missions": [m.to_dict() for m in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


@router.get("/{mission_id}")
@require_permission("threads", "read")
async def get_mission(mission_id: str, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        m = get_mission_store().get(mission_id)
        if m is None or m.owner != owner:
            return None
        return m.to_dict()

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return result


@router.post("/{mission_id}/transition")
@require_permission("threads", "write")
async def transition_mission(mission_id: str, request: Request, to: str = "active") -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        store = get_mission_store()
        m = store.get(mission_id)
        if m is None or m.owner != owner:
            return None
        return store.transition(mission_id, to)

    if to not in ("active", "paused", "completed", "cancelled"):
        raise HTTPException(status_code=422, detail="Invalid mission transition.")
    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Mission not found or transition illegal.")
    return result.to_dict()


class AttachThreadRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, max_length=64)


@router.post("/{mission_id}/threads")
@require_permission("threads", "write")
async def attach_thread(mission_id: str, body: AttachThreadRequest, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        store = get_mission_store()
        m = store.get(mission_id)
        if m is None or m.owner != owner:
            return None
        return store.attach_thread(mission_id, body.thread_id)

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Mission not found.")
    return result.to_dict()
