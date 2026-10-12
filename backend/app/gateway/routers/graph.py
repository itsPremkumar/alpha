"""Graph plane Gateway API routes.

These routes expose the graph plane's execution and introspection endpoints.
They are only registered when the graph execution capability is enabled.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.graph.models import GoalMode
from alpha.graph.state import GraphStatus
from app.gateway.deps import get_stream_bridge

router = APIRouter(prefix="/api/graph", tags=["graph"])


# Request/Response models


class GraphRunCreate(BaseModel):
    """Request to create a graph run."""

    user_request: str = Field(..., min_length=1, description="The user's request")
    thread_id: str = Field(..., description="Thread identifier")
    mode: GoalMode = Field(default=GoalMode.EXECUTE, description="Execution intent")
    plan_tasks: list[dict[str, Any]] | None = Field(default=None, description="Pre-planned tasks")
    verify_checks: list[dict[str, Any]] | None = Field(default=None, description="Verification checks")
    artifact_request: dict[str, Any] | None = Field(default=None, description="Artifact to capture")
    config: dict[str, Any] | None = Field(default=None, description="Runtime config overrides")
    metadata: dict[str, Any] | None = Field(default=None, description="Run metadata")
    stream_mode: list[str] | None = Field(default=None, description="Stream modes")
    idempotency_key: str | None = Field(default=None, description="Idempotency key")


class GraphRunResponse(BaseModel):
    """Response for a graph run operation."""

    run_id: str
    thread_id: str
    status: GraphStatus
    goal_id: str | None = None
    current_node: str | None = None
    next_node: str | None = None
    model_calls_used: int = 0
    tool_calls_used: int = 0
    planning_iteration: int = 0
    verify_cycle: int = 0
    repair_attempts: int = 0
    retry_count: int = 0
    cancel_requested: bool = False
    failure_summary: str | None = None
    final_result: str | None = None


class GraphRunStatus(BaseModel):
    """Current status of a graph run."""

    run_id: str
    thread_id: str
    goal_id: str | None = None
    status: GraphStatus
    current_node: str | None = None
    next_node: str | None = None
    model_calls_used: int = 0
    tool_calls_used: int = 0
    planning_iteration: int = 0
    verify_cycle: int = 0
    repair_attempts: int = 0
    retry_count: int = 0
    cancel_requested: bool = False
    failure_summary: str | None = None
    final_result: str | None = None
    tasks: list[dict[str, Any]] | None = None
    events: list[dict[str, Any]] | None = None


# Dependencies


async def get_graph_runtime(request: Request) -> Any:
    """Get the graph runtime from app state."""
    runtime = getattr(request.app.state, "graph_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Graph execution is not enabled. Enable the 'graph_execution' capability in config.yaml.")
    return runtime


def require_graph_execution(request: Request) -> None:
    """Require that graph execution is enabled."""
    config = request.app.state.get("startup_config")
    if config is None:
        # Fallback for testing
        return
    if not config.capabilities.is_graph_execution_enabled():
        raise HTTPException(status_code=503, detail="Graph execution is not enabled. Enable the 'graph_execution' capability in config.yaml.")


# Routes


@router.post(
    "/runs",
    response_model=GraphRunResponse,
    dependencies=[Depends(require_graph_execution)],
    summary="Create and execute a graph run",
)
async def create_graph_run(
    request: Request,
    body: GraphRunCreate,
    bridge=Depends(get_stream_bridge),
    runtime=Depends(get_graph_runtime),
) -> GraphRunResponse:
    """Create and execute a graph-plane run.

    This starts a new graph execution with the given user request.
    The run is executed asynchronously; use GET /api/graph/runs/{run_id}
    to poll for status or use the SSE stream.
    """
    from alpha.graph import GraphRuntimeConfig, build_graph_runtime
    from alpha.graph.runtime import build_initial_state

    config = request.app.state.get("startup_config")
    if config is None:
        raise HTTPException(status_code=503, detail="App config not available")

    # Build initial state
    state = build_initial_state(
        user_request=body.user_request,
        run_id=body.thread_id,  # Using thread_id as run_id for now
        thread_id=body.thread_id,
        mode=body.mode,
        plan_tasks=body.plan_tasks,
        verify_checks=body.verify_checks,
        artifact_request=body.artifact_request,
    )

    # Build runtime config
    runtime_config = GraphRuntimeConfig(
        max_concurrency=config.capabilities.capabilities.get("graph_execution_max_concurrency", 2),
        enable_checkpoints=True,
        checkpointer=request.app.state.get("checkpointer"),
    )

    # Build and invoke the graph
    graph_runtime = build_graph_runtime(ledger=None, config=runtime_config)
    final_state = await graph_runtime.graph.ainvoke(state, config={"configurable": {"thread_id": body.thread_id}})

    return GraphRunResponse(
        run_id=body.thread_id,
        thread_id=body.thread_id,
        status=final_state.get("status", GraphStatus.CREATED),
        goal_id=final_state.get("goal_id"),
        current_node=final_state.get("current_node"),
        next_node=final_state.get("next_node"),
        model_calls_used=final_state.get("model_calls_used", 0),
        tool_calls_used=final_state.get("tool_calls_used", 0),
        planning_iteration=final_state.get("planning_iteration", 0),
        verify_cycle=final_state.get("verify_cycle", 0),
        repair_attempts=final_state.get("repair_attempts", 0),
        retry_count=final_state.get("retry_count", 0),
        cancel_requested=final_state.get("cancel_requested", False),
        failure_summary=final_state.get("failure_summary"),
        final_result=final_state.get("final_result"),
    )


@router.get(
    "/runs/{run_id}",
    response_model=GraphRunStatus,
    dependencies=[Depends(require_graph_execution)],
    summary="Get graph run status",
)
async def get_graph_run(
    request: Request,
    run_id: str,
    runtime=Depends(get_graph_runtime),
) -> GraphRunStatus:
    """Get the current status of a graph run."""
    # TODO: Implement ledger-based status lookup
    # For now, return a placeholder
    raise HTTPException(status_code=501, detail="Graph run status lookup not yet implemented")


@router.post(
    "/runs/{run_id}/cancel",
    dependencies=[Depends(require_graph_execution)],
    summary="Cancel a graph run",
)
async def cancel_graph_run(
    request: Request,
    run_id: str,
    runtime=Depends(get_graph_runtime),
) -> dict[str, str]:
    """Request cancellation of a running graph."""
    # TODO: Implement cancellation
    raise HTTPException(status_code=501, detail="Graph run cancellation not yet implemented")


@router.get(
    "/runs/{run_id}/tasks",
    dependencies=[Depends(require_graph_execution)],
    summary="Get graph run tasks",
)
async def get_graph_run_tasks(
    request: Request,
    run_id: str,
    runtime=Depends(get_graph_runtime),
) -> dict[str, Any]:
    """Get the tasks for a graph run."""
    # TODO: Implement ledger-based task lookup
    raise HTTPException(status_code=501, detail="Graph run task lookup not yet implemented")


@router.get(
    "/runs/{run_id}/events",
    dependencies=[Depends(require_graph_execution)],
    summary="Get graph run events",
)
async def get_graph_run_events(
    request: Request,
    run_id: str,
    runtime=Depends(get_graph_runtime),
) -> dict[str, Any]:
    """Get the events for a graph run."""
    # TODO: Implement ledger-based event lookup
    raise HTTPException(status_code=501, detail="Graph run event lookup not yet implemented")


# Register the router factory


def include_graph_router(app: Any) -> None:
    """Include the graph router if the feature is enabled.

    This should be called during app startup after config is loaded.
    """
    config = getattr(app.state, "startup_config", None)
    if config and config.capabilities.is_graph_execution_enabled():
        from fastapi import FastAPI

        if isinstance(app, FastAPI):
            app.include_router(router)
