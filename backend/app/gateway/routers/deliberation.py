"""Universal Deliberation Engine API Router.

Provides Gateway endpoints for:
- Pre-flight deliberation evaluation (difficulty, risk, worthwhile assessment)
- Full multi-model deliberation execution (Single, Ensemble, Council, Debate)
All processing runs via asyncio.to_thread to maintain Gateway non-blocking concurrency invariants.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent_workspace.deliberation.engine import get_master_deliberation_engine
from agent_workspace.deliberation.models import DeliberationStrategy
from agent_workspace.deliberation.router import DeliberationRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deliberation", tags=["deliberation"])


class DeliberationEvaluateRequest(BaseModel):
    prompt: str = Field(..., min_length=2, description="The query or architectural problem to evaluate.")


class DeliberationRunRequest(BaseModel):
    prompt: str = Field(..., min_length=2, description="The query or problem to deliberate.")
    strategy: str = Field(default="auto", description="Strategy: auto, single, ensemble, council, debate.")
    max_rounds: int = Field(default=3, ge=1, le=5, description="Maximum rounds if debate is chosen.")
    code_test_command: str | None = Field(default=None, description="Optional shell test command for deterministic verification.")


@router.post("/evaluate")
async def evaluate_deliberation_feasibility(payload: DeliberationEvaluateRequest):
    """Evaluates whether deliberation is worthwhile and recommends the optimal multi-model strategy."""
    eval_res = await asyncio.to_thread(DeliberationRouter.classify, payload.prompt)
    return eval_res.to_dict()


@router.post("/run")
async def run_deliberation(payload: DeliberationRunRequest):
    """Executes multi-model deliberation via Single, Ensemble, Anonymous Council, or Sparse Debate."""
    engine = get_master_deliberation_engine()

    try:
        strategy = DeliberationStrategy(payload.strategy.lower().strip())
    except ValueError:
        strategy = DeliberationStrategy.AUTO

    result = await asyncio.to_thread(
        engine.deliberate,
        prompt=payload.prompt,
        strategy=strategy,
        max_rounds=payload.max_rounds,
        code_test_command=payload.code_test_command,
    )
    return result.to_dict()
