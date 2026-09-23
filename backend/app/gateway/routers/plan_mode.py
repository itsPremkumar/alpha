"""Cognitive Plan Mode API Router.

Provides Gateway endpoints for:
- 8-Dimensional Strategic Plan Evaluation (Paradigm, Swarm Mode, Reasoning, Workforce, Model, Isolation, Risk, Proof Obligations)
- Autonomous Dispatch across execution subsystems (Swarm, Bot Profile, MoA, Deep Research, Deep Think, Ephemeral Subagent, Direct Agent)
All computation runs via asyncio.to_thread to maintain Gateway non-blocking concurrency invariants.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from alpha.planning.bridge import AutonomousDispatchBridge
from alpha.planning.meta_planner import CognitiveMetaPlanner
from app.gateway.deps import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plan-mode", tags=["plan-mode"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to trigger autonomous dispatch."
# Same require_admin_user + module-level detail-constant pattern as dispatch;
# mode-specific wording so the 403 states the real reason.
_MODE_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to change the execution mode."


class PlanEvaluateRequest(BaseModel):
    prompt: str = Field(..., min_length=2, description="The user objective or prompt.")
    items: list[str] | None = Field(default=None, description="Optional batch items for parallel map-reduce.")
    max_concurrency: int = Field(default=8, ge=1, le=12, description="Concurrency limit for parallel tasks.")


class PlanDispatchRequest(BaseModel):
    prompt: str = Field(..., min_length=2, description="The user objective or prompt.")
    items: list[str] | None = Field(default=None, description="Optional batch items for parallel map-reduce.")
    max_concurrency: int = Field(default=8, ge=1, le=12, description="Concurrency limit for parallel tasks.")


@router.post("/evaluate")
async def evaluate_plan_mode(payload: PlanEvaluateRequest):
    """Evaluates the user prompt across all 8 strategic dimensions and returns the compiled MetaPlan."""
    plan = await asyncio.to_thread(
        CognitiveMetaPlanner.evaluate_and_plan,
        prompt=payload.prompt,
        items=payload.items,
        max_concurrency=payload.max_concurrency,
    )
    return plan.to_dict()


@router.post("/dispatch")
async def dispatch_plan_mode(payload: PlanDispatchRequest, request: Request):
    """Evaluates the prompt and immediately executes autonomous dispatch to the target subsystem."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    plan = await asyncio.to_thread(
        CognitiveMetaPlanner.evaluate_and_plan,
        prompt=payload.prompt,
        items=payload.items,
        max_concurrency=payload.max_concurrency,
    )

    dispatch_res = await AutonomousDispatchBridge.dispatch_async(plan)

    return {
        "plan": plan.to_dict(),
        "dispatch": dispatch_res.to_dict(),
    }


class InterviewStartRequest(BaseModel):
    objective: str = Field(..., min_length=3, description="The goal to interview about.")
    known: dict = Field(default_factory=dict, description="Already-known facts keyed by gap area.")


class InterviewReviewRequest(BaseModel):
    plan: dict = Field(..., description="The plan artifact under review.")
    reviewer: str = Field(..., min_length=1, max_length=64)
    verdict: str = Field(..., description="approve|request_changes|reject")
    comments: str = Field(default="", max_length=10000)


@router.post("/interview/questions")
async def interview_questions(payload: InterviewStartRequest) -> dict:
    """Derive gap questions that must be answered before planning is safe."""
    from alpha.planning.interview import derive_gap_questions, new_plan

    questions = await asyncio.to_thread(derive_gap_questions, payload.objective, known=payload.known)
    plan = new_plan(payload.objective)
    return {"plan": plan.to_dict(), "questions": [q.to_dict() for q in questions]}


@router.post("/interview/review")
async def interview_review(payload: InterviewReviewRequest) -> dict:
    """Record one review round (cap 3); approval pins the plan hash."""
    if payload.verdict not in ("approve", "request_changes", "reject"):
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="verdict must be approve|request_changes|reject.")

    def _review():
        from alpha.planning.interview import PlanArtifact, record_review

        steps = payload.plan.get("steps", [])
        risks = payload.plan.get("risks", [])
        plan = PlanArtifact(
            plan_id=str(payload.plan.get("plan_id", "pln-unknown")),
            objective=str(payload.plan.get("objective", "")),
            steps=steps,
            risks=risks,
            review_rounds=int(payload.plan.get("review_rounds", 0)),
            reviews=list(payload.plan.get("reviews", [])),
            status=payload.plan.get("status", "draft"),
            plan_hash=str(payload.plan.get("plan_hash", "")),
        )
        return record_review(plan, payload.reviewer, verdict=payload.verdict, comments=payload.comments).to_dict()

    try:
        return await asyncio.to_thread(_review)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Unified execution mode (WorkSwarm gap 7): GET/POST /api/plan-mode/mode
# ---------------------------------------------------------------------------


class ExecutionModeRequest(BaseModel):
    mode: str = Field(..., min_length=1, description="Unified execution mode: work.normal | work.plan | code.normal | code.plan.")
    actor: str = Field(default="", max_length=128, description="Who is changing the mode (recorded on disk in the actor field).")


@router.get("/mode")
async def get_execution_mode() -> dict:
    """Read the unified execution mode honestly: active mode + real note.

    A missing or corrupt persisted file returns the default mode WITH its
    disclosed note (``default: no persisted mode found``) — never a
    fabricated last-mode, timestamp, or actor.
    """
    from alpha.runtime.execution_mode import mode_status

    return await asyncio.to_thread(mode_status)


@router.post("/mode")
async def set_execution_mode(payload: ExecutionModeRequest, request: Request):
    """Persist the unified execution mode; admin-gated like /dispatch.

    Honesty contract: ``persisted: true`` is only returned after the file has
    been re-read and proven to contain the mode. A failed write returns a 5xx
    carrying ``persisted: false`` plus the real error; an unknown mode is 422.
    """
    await require_admin_user(request, detail=_MODE_ADMIN_REQUIRED_DETAIL)

    from alpha.runtime.execution_mode import set_mode

    try:
        result = await asyncio.to_thread(set_mode, payload.mode, actor=payload.actor)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not result.get("persisted"):
        from fastapi.responses import JSONResponse

        # Not a success: the file does not contain the mode. Surface the real
        # error with an explicit failure status instead of claiming a change.
        return JSONResponse(status_code=500, content=result)
    return result
