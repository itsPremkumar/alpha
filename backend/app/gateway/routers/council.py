"""Verifier council API: independent quorum over tier-1 artifacts."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/council", tags=["council"])


class OpenCaseRequest(BaseModel):
    artifact_id: str = Field(..., min_length=1, max_length=128)
    artifact_ref: str = Field(..., min_length=1, max_length=500)
    tier: int = Field(default=1, ge=1, le=3)
    min_reviews: int = Field(default=2, ge=1, le=5)


class ReviewRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=64)
    verdict: str = Field(..., min_length=1, max_length=16)
    evidence: str = Field(default="", max_length=10000)


@router.post("/cases", status_code=201)
async def open_case(body: OpenCaseRequest) -> dict:
    def _do():
        from alpha.council import get_council_engine

        return get_council_engine().open_case(body.artifact_id, body.artifact_ref, tier=body.tier, min_reviews=body.min_reviews).to_dict()

    return await asyncio.to_thread(_do)


@router.get("/cases")
async def list_cases(status: str | None = None) -> dict:
    def _do():
        from alpha.council import get_council_engine

        rows = get_council_engine().list_cases(status=status)
        return {"cases": [c.to_dict() for c in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


@router.get("/cases/{case_id}")
async def get_case(case_id: str) -> dict:
    def _do():
        from alpha.council import get_council_engine

        case = get_council_engine().get_case(case_id)
        return case.to_dict() if case else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Council case not found.")
    return result


@router.post("/cases/{case_id}/reviews", status_code=201)
async def submit_review(case_id: str, body: ReviewRequest) -> dict:
    if body.verdict not in ("approve", "block", "abstain"):
        raise HTTPException(status_code=422, detail="verdict must be approve|block|abstain.")

    def _do():
        from alpha.council import get_council_engine

        out = get_council_engine().submit_review(case_id, body.reviewer, body.verdict, evidence=body.evidence)
        if out is None:
            return None
        review, outcome, reason = out
        return {"review": review.to_dict(), "outcome": outcome, "reason": reason}

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Case not found, closed, or reviewer already voted.")
    return result
