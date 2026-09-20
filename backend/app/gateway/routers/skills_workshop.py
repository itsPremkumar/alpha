"""API router for Skill Synthesis Workshop.

Exposes endpoints for extracting, validating, and publishing reusable skill
packages from execution traces and session workflows.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.skills.authoring import validate_skill_draft
from alpha.skills.workshop import SkillDraft, SkillWorkshopEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills/workshop", tags=["skills-workshop"])


class DistillRequest(BaseModel):
    """Payload for distilling a skill from execution steps."""

    name: str = Field(..., min_length=1, max_length=64, description="Skill name (lowercase-hyphenated)")
    description: str = Field(..., min_length=1, max_length=120, description="Short capability description")
    steps: list[dict[str, Any]] = Field(default_factory=list, description="Execution trace steps")
    verification_command: str | None = Field(default=None, description="Command to verify skill execution")
    when_to_use: list[str] | None = Field(default=None, description="Trigger situations")
    prerequisites: list[str] | None = Field(default=None, description="Prerequisite dependencies")
    pitfalls: list[str] | None = Field(default=None, description="Operational caveats")


class PublishRequest(BaseModel):
    """Payload for publishing a verified skill draft."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=60)
    markdown_content: str = Field(..., min_length=10)
    overwrite: bool = Field(default=False)


@router.post("/distill", summary="Distill Execution Trace into Reusable Skill Draft")
def distill_skill(req: DistillRequest) -> dict[str, Any]:
    """Extract, parameterize, and structure a skill draft from steps."""
    try:
        draft = SkillWorkshopEngine.distill_from_trace(
            name=req.name,
            description=req.description,
            trace_steps=req.steps,
            when_to_use=req.when_to_use,
            prerequisites=req.prerequisites,
            pitfalls=req.pitfalls,
            verification_cmd=req.verification_command,
        )
        return draft.to_dict()
    except Exception as exc:
        logger.exception("Failed to distill skill trace")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/publish", summary="Validate and Publish Skill Draft to Custom Skills Directory")
def publish_skill(req: PublishRequest) -> dict[str, Any]:
    """Validate and persist the skill draft as a custom skill."""
    findings = validate_skill_draft(req.name, req.description, req.markdown_content)
    if findings:
        raise HTTPException(
            status_code=422,
            detail={"message": "Skill draft failed validation", "findings": findings},
        )

    draft = SkillDraft(
        name=req.name,
        description=req.description,
        markdown_content=req.markdown_content,
        is_valid=True,
    )

    try:
        saved_path = SkillWorkshopEngine.publish_skill(draft, overwrite=req.overwrite)
        return {
            "status": "published",
            "name": draft.name,
            "path": str(saved_path),
        }
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to publish skill")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
