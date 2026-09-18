"""Bounded evolution API: propose, benchmark, gate, promote, roll back."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


class ProposeRequest(BaseModel):
    surface: str = Field(..., min_length=1, max_length=32)
    target: str = Field(..., min_length=1, max_length=200)
    payload: dict = Field(default_factory=dict)
    parent_id: str | None = None


class BenchmarkReport(BaseModel):
    benchmark: dict = Field(default_factory=dict)


class GateRequest(BaseModel):
    baseline: dict = Field(default_factory=dict)
    human_approved: bool = False
    autonomous_mode: bool = True


@router.post("/candidates", status_code=201)
async def propose_candidate(body: ProposeRequest) -> dict:
    def _do():
        from agent_workspace.evolution import get_evolution_engine

        return get_evolution_engine().propose(body.surface, body.target, body.payload, parent_id=body.parent_id).to_dict()

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/candidates/{candidate_id}/benchmark")
async def record_benchmark(candidate_id: str, body: BenchmarkReport) -> dict:
    def _do():
        from agent_workspace.evolution import get_evolution_engine

        cand = get_evolution_engine().record_benchmark(candidate_id, body.benchmark)
        return cand.to_dict() if cand else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return result


@router.post("/candidates/{candidate_id}/gate")
async def gate_candidate(candidate_id: str, body: GateRequest) -> dict:
    def _do():
        from agent_workspace.evolution import get_evolution_engine

        return get_evolution_engine().gate(
            candidate_id,
            body.baseline,
            human_approved=body.human_approved,
            autonomous_mode=body.autonomous_mode,
        )

    promoted, reason = await asyncio.to_thread(_do)
    return {"promoted": promoted, "reason": reason}


@router.post("/candidates/{candidate_id}/rollback")
async def rollback_candidate(candidate_id: str, reason: str = "") -> dict:
    def _do():
        from agent_workspace.evolution import get_evolution_engine

        return get_evolution_engine().rollback(candidate_id, reason)

    return {"rolled_back": await asyncio.to_thread(_do)}


@router.get("/ledger")
async def evolution_ledger(limit: int = 100) -> dict:
    def _do():
        from agent_workspace.evolution import get_evolution_engine

        return get_evolution_engine().ledger(limit=min(limit, 500))

    return {"events": await asyncio.to_thread(_do)}
