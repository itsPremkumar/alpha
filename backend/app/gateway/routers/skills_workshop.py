"""API router for Skill Synthesis Workshop.

Exposes endpoints for extracting, validating, and publishing reusable skill
packages from execution traces and session workflows, plus the additive
skill self-evolution pipeline (propose / evaluate / promote / rollback)
backed by ``alpha.skills.evolution_engine``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from alpha.config.runtime_paths import runtime_home
from alpha.skills.authoring import validate_skill_draft
from alpha.skills.evolution_engine import (
    InvalidTransitionError,
    SkillEvolutionDisabledError,
    SkillEvolutionEngine,
    UnknownProposalError,
)
from alpha.skills.skillscan.orchestrator import StaticScanBlockedError
from alpha.skills.workshop import SkillDraft, SkillWorkshopEngine
from app.gateway.deps import get_config, require_admin_user

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
    except StaticScanBlockedError as exc:
        # Audit-before-activation: publish_skill's static scan rejected the
        # content before any file was written. Client-content outcome, honest
        # scanner reason, nothing activated.
        raise HTTPException(
            status_code=422,
            detail={"message": "Skill draft blocked by static security scan before activation", "reason": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("Failed to publish skill")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Skill self-evolution pipeline (additive surface; see evolution_engine.py).
#
# Response bodies are the honest proposal record: evidence references, the
# validation kind actually performed, real statuses
# (proposed/validated/rejected/promoted/rolled_back), rejection reasons, and
# "NOT verified" notes. Nothing here invents a pass.
# ---------------------------------------------------------------------------

_EVOLUTION_ADMIN_DETAIL = "Skill evolution evaluation, promotion, rollback, and review require admin."


class EvolutionProposeRequest(BaseModel):
    """Payload for proposing a versioned candidate from evidence references."""

    skill_name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$", description="Existing skill to evolve (lowercase-hyphenated)")
    candidate_markdown: str = Field(..., min_length=1, max_length=65536, description="Candidate SKILL.md body (never written to the active skill before promotion)")
    evidence: list[str] = Field(..., min_length=1, max_length=50, description="Evidence references motivating the candidate")
    created_by: str = Field(default="", max_length=200)


class EvolutionReviewRequest(BaseModel):
    """Promotion payload: auto-promote defaults off, so approve is explicit."""

    approve: bool = Field(default=False, description="Explicit human approval; required while config skill_evolution.auto_promote is false (the default)")
    reason: str = Field(default="", max_length=2000)


class EvolutionRollbackRequest(BaseModel):
    """Rollback payload."""

    reason: str = Field(default="", max_length=2000)


def _request_actor(request: Request) -> str:
    """Best-effort actor id from the stamped request user (history only)."""
    user = getattr(request.state, "user", None)
    return str(getattr(user, "id", "") or "")


def _build_evolution_engine(config: Any) -> SkillEvolutionEngine:
    """Engine bound to ``runtime_home()`` paths.

    Store: ``runtime_home()/skill_evolution``; skills root:
    ``runtime_home()/skills`` (same root the curator maintains).
    """
    home = runtime_home()
    return SkillEvolutionEngine(
        store_dir=home / "skill_evolution",
        skills_root=home / "skills",
        config=config.skill_evolution,
    )


def _evolution_http_error(exc: Exception) -> HTTPException:
    """Map engine errors to honest HTTP statuses carrying the real reason."""
    if isinstance(exc, UnknownProposalError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SkillEvolutionDisabledError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    logger.exception("Skill evolution operation failed")
    return HTTPException(status_code=500, detail=str(exc))


@router.post("/evolution", status_code=201, summary="Propose a Versioned Skill-Evolution Candidate from Evidence")
async def propose_evolution_candidate(body: EvolutionProposeRequest, request: Request, config: Any = Depends(get_config)) -> dict[str, Any]:
    """Create a candidate record; the active skill body is never touched here."""

    def _do() -> dict[str, Any]:
        actor = _request_actor(request)
        record = _build_evolution_engine(config).propose(
            body.skill_name,
            body.candidate_markdown,
            body.evidence,
            created_by=body.created_by or actor,
        )
        return record.to_dict()

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        raise _evolution_http_error(exc) from exc


@router.get("/evolution", summary="List Skill-Evolution Proposals")
async def list_evolution_proposals(request: Request, status: str | None = Query(default=None), config: Any = Depends(get_config)) -> dict[str, Any]:
    await require_admin_user(request, detail=_EVOLUTION_ADMIN_DETAIL)

    def _do() -> list[dict[str, Any]]:
        return [record.to_dict() for record in _build_evolution_engine(config).list_proposals(status=status)]

    try:
        return {"proposals": await asyncio.to_thread(_do)}
    except Exception as exc:
        raise _evolution_http_error(exc) from exc


@router.get("/evolution/{proposal_id}", summary="Get One Skill-Evolution Proposal Record")
async def get_evolution_proposal(proposal_id: str, request: Request, config: Any = Depends(get_config)) -> dict[str, Any]:
    await require_admin_user(request, detail=_EVOLUTION_ADMIN_DETAIL)

    def _do() -> dict[str, Any]:
        return _build_evolution_engine(config).get(proposal_id).to_dict()

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        raise _evolution_http_error(exc) from exc


@router.post("/evolution/{proposal_id}/evaluate", summary="Evaluate a Skill-Evolution Candidate Offline")
async def evaluate_evolution_proposal(proposal_id: str, request: Request, config: Any = Depends(get_config)) -> dict[str, Any]:
    """Run structural/AST/consistency checks (+ declared suite via subprocess) and moderation.

    Admin-gated because a candidate may declare a ``test-command`` that is
    executed as a subprocess against a staged copy of the skill package.
    Evaluation never fabricates a pass: failures reject with the real reason,
    and ``security_fail_closed`` decides what an evaluation error means.
    """

    def _do() -> dict[str, Any]:
        return _build_evolution_engine(config).evaluate(proposal_id).to_dict()

    await require_admin_user(request, detail=_EVOLUTION_ADMIN_DETAIL)
    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        raise _evolution_http_error(exc) from exc


@router.post("/evolution/{proposal_id}/promote", summary="Promote a Validated Skill-Evolution Candidate")
async def promote_evolution_proposal(proposal_id: str, body: EvolutionReviewRequest, request: Request, config: Any = Depends(get_config)) -> dict[str, Any]:
    """Apply the policy gate and, when approved, atomically install the candidate.

    With ``auto_promote`` off (the default) and ``approve=false`` the
    proposal stays ``validated`` with a hold note - nothing is written.
    A gate failure returns ``promoted=false`` plus the real ``reason``.
    """

    def _do() -> dict[str, Any]:
        record = _build_evolution_engine(config).promote(
            proposal_id,
            approve=body.approve,
            reason=body.reason,
            actor=_request_actor(request),
        )
        payload = record.to_dict()
        payload["promoted"] = record.status == "promoted"
        payload["reason"] = record.reject_reason
        return payload

    await require_admin_user(request, detail=_EVOLUTION_ADMIN_DETAIL)
    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        raise _evolution_http_error(exc) from exc


@router.post("/evolution/{proposal_id}/rollback", summary="Roll Back a Promoted Skill-Evolution Candidate")
async def rollback_evolution_proposal(proposal_id: str, body: EvolutionRollbackRequest, request: Request, config: Any = Depends(get_config)) -> dict[str, Any]:
    """Restore the retained pre-promotion body; refuses to clobber later edits."""

    def _do() -> dict[str, Any]:
        record = _build_evolution_engine(config).rollback(
            proposal_id,
            reason=body.reason,
            actor=_request_actor(request),
        )
        payload = record.to_dict()
        payload["rolled_back"] = record.status == "rolled_back"
        return payload

    await require_admin_user(request, detail=_EVOLUTION_ADMIN_DETAIL)
    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        raise _evolution_http_error(exc) from exc
