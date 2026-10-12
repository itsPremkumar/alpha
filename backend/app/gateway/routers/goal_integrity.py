"""Gateway REST Router for Goal Integrity and Scope Creep Auditing."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from alpha.planning.integrity import GoalIntegrityEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/goal-integrity", tags=["goal-integrity"])


class GoalAuditRequest(BaseModel):
    mission_goal: str = Field(..., min_length=2, description="The primary mission or user prompt objective.")
    subtasks: list[dict[str, Any]] | list[str] = Field(..., description="List of proposed or active subtask descriptions or dicts with 'description'.")


@router.post("/audit")
async def audit_goal_integrity(payload: GoalAuditRequest):
    """Audits subtasks against the core mission for semantic drift, scope creep, and overengineering."""
    report = GoalIntegrityEngine.audit_plan(
        mission_goal=payload.mission_goal,
        subtasks=payload.subtasks,
    )
    return report.model_dump()
