"""HTTP API for arena runs.

Owner-scoped CRUD and control. Mutations require
admin or ownership; reads are owner-scoped (never
cross-owner). Route order is load-bearing: literal
routes before ``/{run_id}`` catch-alls.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from alpha.arena.config import arena_config
from alpha.arena.service import ArenaConfirmationRequired, ArenaRunError, ArenaService
from alpha.config import get_app_config
from app.gateway.deps import get_current_user_from_request, require_admin_user

router = APIRouter(prefix="/api/arena", tags=["arena"])

#: The service is a process-wide singleton, initialized
#: lazily on first request. The runner factory is bound
#: per-request in the handlers.
_service: ArenaService | None = None


def get_service() -> ArenaService:
    global _service
    if _service is None:
        _service = ArenaService()
    return _service


# ------------------------------------------------------------------ models


class ArenaEstimateRequest(BaseModel):
    agents: int | None = Field(default=None, ge=1)
    wave: int | None = Field(default=None, ge=1)
    baseline: bool = False
    task: str = ""
    profile: str | None = None
    mode: str | None = None


class ArenaCreateRequest(BaseModel):
    task: str = Field(..., min_length=1)
    agents: int | None = Field(default=None, ge=1)
    wave: int | None = Field(default=None, ge=1)
    seed: str | int | None = None
    baseline: str | None = None
    max_subagent_calls: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_seconds: float | None = Field(default=None, ge=1)
    profile: str | None = None
    mode: str | None = None


class ArenaStartRequest(BaseModel):
    confirm: bool = False


class ArenaCardRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)


# ------------------------------------------------------------------ routes


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """The effective arena configuration."""
    config = arena_config(get_app_config())
    return {
        "enabled": config.enabled,
        "default_agents": config.default_agents,
        "max_agents": config.max_agents,
        "default_wave": config.default_wave,
        "max_wave": config.max_wave,
        "estimate_tokens_per_call": config.estimate_tokens_per_call,
        "require_confirmation_over_calls": config.require_confirmation_over_calls,
        "max_subagent_calls_per_run": config.max_subagent_calls_per_run,
        "max_tokens_per_run": config.max_tokens_per_run,
        "max_wall_seconds_per_run": config.max_wall_seconds_per_run,
        "max_judge_attempts": config.max_judge_attempts,
        "reasoning_bank_seed": config.reasoning_bank_seed,
    }


@router.get("/profiles")
async def get_profiles(
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """The declared budget profiles and run modes.

    Read-only: what a run *can* be, before one is created. The
    ``implemented_modes`` list is the honest boundary - a declared
    mode the executor does not run is refused by name at create.
    """
    return get_service().profiles()


@router.post("/estimate")
async def estimate(
    body: ArenaEstimateRequest,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Project a run's cost before anything exists."""
    service = get_service()
    try:
        return service.estimate(
            agents=body.agents,
            wave=body.wave,
            baseline=body.baseline,
            task=body.task,
            profile=body.profile,
            mode=body.mode,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/runs")
async def create_run(
    body: ArenaCreateRequest,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Deal cards and persist a draft run."""
    service = get_service()
    try:
        state = service.create(
            owner_id=user,
            thread_id=None,
            task=body.task,
            agents=body.agents,
            wave=body.wave,
            seed=body.seed,
            baseline=body.baseline,
            max_subagent_calls=body.max_subagent_calls,
            max_tokens=body.max_tokens,
            max_wall_seconds=body.max_wall_seconds,
            profile=body.profile,
            mode=body.mode,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {
        "run_id": state["run_id"],
        "agents_n": state["agents_n"],
        "status": state["status"],
        "profile": state.get("profile", "custom"),
        "route_reason": state.get("route_reason", ""),
        "mode": state.get("mode", "decide"),
    }


@router.get("/runs")
async def list_runs(
    user: str = Depends(get_current_user_from_request),
) -> list[dict[str, Any]]:
    """List the caller's arena runs, newest first."""
    service = get_service()
    return service.list_runs(owner_id=user)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Full run summary (bounded)."""
    service = get_service()
    return service.status(run_id, owner_id=user)


async def _executing_service(user: str, thread_id: str | None) -> ArenaService:
    """Bind a real sub-agent runner for HTTP execution.

    Reads are served by the shared service, which needs no runner;
    only ``start``/``resume`` execute jobs, so only they pay for tool
    assembly — and that assembly blocks on MCP discovery and config
    reads, so it runs off-loop through ``run_assembly`` rather than on
    the request's event loop. The run's own ``thread_id`` rides the
    factory so competitors share the run's sandbox/workspace.
    """
    from alpha.subagents.config import SubagentConfig
    from alpha.subagents.executor import SubagentExecutor
    from alpha.tools import get_available_tools
    from alpha.utils.assembly_io import run_assembly

    app_config = await asyncio.to_thread(get_app_config)
    tools = await run_assembly(
        get_available_tools,
        model_name=None,
        groups=None,
        include_mcp=True,
        # Competitors are sub-agents: no nested delegation.
        subagent_enabled=False,
    )

    def factory(sub_config: SubagentConfig) -> SubagentExecutor:
        return SubagentExecutor(
            config=sub_config,
            tools=tools,
            app_config=app_config,
            thread_id=thread_id,
            user_id=user,
        )

    return ArenaService(config=arena_config(app_config), runner_factory=factory)


@router.post("/runs/{run_id}/start")
async def start_run(
    run_id: str,
    body: ArenaStartRequest,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Drive a draft run to completion.

    If the projected call count exceeds the confirmation
    ceiling, ``confirm=true`` is required.
    """
    service = get_service()
    try:
        # Gate first: a refusal must cost no tool assembly.
        state = service.check_start(run_id, owner_id=user, confirm=body.confirm)
        executing = await _executing_service(user, state.get("thread_id"))
        state = await executing.start(run_id, owner_id=user, confirm=body.confirm)
    except ArenaConfirmationRequired as error:
        raise HTTPException(status_code=400, detail=str(error))
    except ArenaRunError as error:
        raise HTTPException(status_code=409, detail=str(error))
    return {"run_id": run_id, "status": state["status"], "champion": state.get("champion")}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Continue a run that stopped mid-bracket.

    Terminal runs (completed, stopped, failed, budget_exhausted)
    cannot be resumed.
    """
    service = get_service()
    try:
        # Gate first: refusing a terminal run must cost no tool assembly.
        state = service.check_resume(run_id, owner_id=user)
        executing = await _executing_service(user, state.get("thread_id"))
        state = await executing.resume(run_id, owner_id=user)
    except ArenaRunError as error:
        raise HTTPException(status_code=409, detail=str(error))
    return {"run_id": run_id, "status": state["status"], "champion": state.get("champion")}


@router.get("/runs/{run_id}/pairings")
async def get_pairings(
    run_id: str,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """The current round's pairings with per-match progress."""
    service = get_service()
    try:
        return service.pairings(run_id, owner_id=user)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")


@router.get("/runs/{run_id}/winner")
async def get_winner(
    run_id: str,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """The champion, its card, and the final check."""
    service = get_service()
    try:
        return service.winner(run_id, owner_id=user)
    except ArenaRunError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/runs/{run_id}/card")
async def get_card(
    run_id: str,
    body: ArenaCardRequest,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """The strategy card dealt to one competitor."""
    service = get_service()
    try:
        return service.card(run_id, owner_id=user, agent_id=body.agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"agent '{body.agent_id}' not found in run '{run_id}'")


@router.post("/runs/{run_id}/stop")
async def stop_run(
    run_id: str,
    user: str = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Stop a running run."""
    service = get_service()
    try:
        return service.stop(run_id, owner_id=user)
    except ArenaRunError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.delete("/runs/{run_id}")
async def delete_run(
    run_id: str,
    user: str = Depends(get_current_user_from_request),
    _admin: None = Depends(require_admin_user),
) -> dict[str, str]:
    """Delete a run and its work files. Admin-only."""
    service = get_service()
    try:
        service.delete(run_id, owner_id=user)
    except ArenaRunError as error:
        raise HTTPException(status_code=409, detail=str(error))
    return {"detail": f"deleted run '{run_id}'"}


# ------------------------------------------------------------------ re-export for app.py

__all__ = ["router"]
