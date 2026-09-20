"""Scheduled-occurrence delivery ledger API (exactly-once auditing)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["deliveries"])


class DeliveryMarkRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=16)
    artifact_ref: str | None = None
    channel: str | None = None
    error: str | None = Field(default=None, max_length=2000)


@router.get("/scheduled-tasks/{task_id}/deliveries")
async def list_deliveries(task_id: str) -> dict:
    def _do():
        from alpha.scheduler.delivery import get_delivery_ledger

        rows = get_delivery_ledger().for_task(task_id)
        return {"task_id": task_id, "deliveries": [r.to_dict() for r in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


@router.post("/scheduled-tasks/{task_id}/deliveries/claim", status_code=201)
async def claim_delivery(task_id: str, occurrence_id: str) -> dict:
    def _do():
        from alpha.scheduler.delivery import get_delivery_ledger

        rec, is_new = get_delivery_ledger().claim(task_id, occurrence_id)
        return {"delivery": rec.to_dict(), "is_new": is_new}

    return await asyncio.to_thread(_do)


@router.post("/scheduled-tasks/{task_id}/deliveries/{occurrence_id}/mark")
async def mark_delivery(task_id: str, occurrence_id: str, body: DeliveryMarkRequest) -> dict:
    if body.status not in ("delivered", "failed", "skipped", "pending"):
        raise HTTPException(status_code=422, detail="Invalid delivery status.")

    def _do():
        from alpha.scheduler.delivery import get_delivery_ledger

        rec = get_delivery_ledger().mark(occurrence_id, body.status, artifact_ref=body.artifact_ref, channel=body.channel, error=body.error)
        return rec.to_dict() if rec else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Delivery record not found; claim it first.")
    return result


@router.get("/scheduled-tasks/blueprints")
async def list_blueprints() -> dict:
    def _do():
        from alpha.scheduler.blueprints import list_blueprints as _list

        return _list()

    return {"blueprints": await asyncio.to_thread(_do)}


class BlueprintLaunchRequest(BaseModel):
    values: dict = Field(default_factory=dict)


@router.post("/scheduled-tasks/blueprints/{blueprint_id}/launch", status_code=201)
async def launch_blueprint(blueprint_id: str, body: BlueprintLaunchRequest) -> dict:
    def _do():
        from alpha.scheduler.blueprints import get_blueprint
        from alpha.scheduler.cron_manager import get_cron_manager

        blueprint = get_blueprint(blueprint_id)
        if blueprint is None:
            return None
        rendered = blueprint.render(**body.values)
        job = get_cron_manager().add_job(rendered["name"], rendered["cron_expression"], rendered["command_or_prompt"])
        return job.to_dict()

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Blueprint '{blueprint_id}' not found.")
    return result


@router.get("/scheduled-tasks/{task_id}/incidents")
async def list_incidents(task_id: str, unresolved_only: bool = False) -> dict:
    def _do():
        from alpha.scheduler.incidents import get_incident_tracker

        rows = get_incident_tracker().list(task_id=task_id, unresolved_only=unresolved_only)
        return {"task_id": task_id, "incidents": [r.to_dict() for r in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


@router.post("/scheduled-tasks/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str) -> dict:
    def _do():
        from alpha.scheduler.incidents import get_incident_tracker

        rec = get_incident_tracker().resolve(incident_id)
        return rec.to_dict() if rec else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Incident not found or already resolved.")
    return result
