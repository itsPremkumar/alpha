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

from app.gateway.deps import require_admin_user
from alpha.planning.bridge import AutonomousDispatchBridge
from alpha.planning.meta_planner import CognitiveMetaPlanner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plan-mode", tags=["plan-mode"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to trigger autonomous dispatch."


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
