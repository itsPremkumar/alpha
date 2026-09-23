"""REST API router for Alpha Dynamic Workflow Engine (DWE)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowPatch,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from app.gateway.authz import require_permission

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_GLOBAL_ENGINE = DynamicWorkflowEngine()


def get_workflow_engine() -> DynamicWorkflowEngine:
    return _GLOBAL_ENGINE


class WorkflowCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    graph: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunCreateRequest(BaseModel):
    initial_state: dict[str, Any] = Field(default_factory=dict)


class WorkflowApprovalRequest(BaseModel):
    approved: bool
    feedback: str = ""


@router.post("", status_code=201)
@require_permission("runs", "create")
async def register_workflow(body: WorkflowCreateRequest, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    graph = WorkflowGraph(**body.graph) if body.graph else WorkflowGraph()
    definition = WorkflowDefinition(
        id=body.id,
        name=body.name,
        description=body.description,
        graph=graph,
        variables=body.variables,
        policies=body.policies,
    )
    engine.register_definition(definition)
    return {"id": definition.id, "name": definition.name, "version": definition.version}


@router.get("")
@require_permission("runs", "read")
async def list_workflows(request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    return {
        "workflows": [
            {
                "id": d.id,
                "name": d.name,
                "version": d.version,
                "description": d.description,
                "node_count": len(d.graph.nodes),
            }
            for d in engine.definitions.values()
        ],
        "count": len(engine.definitions),
    }


@router.get("/{workflow_id}")
@require_permission("runs", "read")
async def get_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    definition = engine.get_definition(workflow_id)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    return definition.model_dump()


@router.post("/{workflow_id}/runs", status_code=201)
@require_permission("runs", "create")
async def start_workflow_run(workflow_id: str, body: WorkflowRunCreateRequest, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    try:
        run = engine.start_run(workflow_id, initial_state=body.initial_state)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs/{run_id}")
@require_permission("runs", "read")
async def get_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    run = engine.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return run.model_dump()


@router.post("/runs/{run_id}/step")
@require_permission("runs", "create")
async def step_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    try:
        run = engine.execute_step(run_id)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/patch")
@require_permission("runs", "create")
async def patch_workflow_run(run_id: str, patch_data: dict[str, Any], request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    try:
        patch = WorkflowPatch(**patch_data)
        new_graph, validation = engine.apply_patch(run_id, patch)
        if not validation.allowed:
            raise HTTPException(status_code=400, detail=f"Patch rejected: {validation.reason}")
        return {"status": "committed", "new_graph_version": new_graph.version}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/runs/{run_id}/approvals/{node_id}")
@require_permission("runs", "create")
async def resolve_approval(run_id: str, node_id: str, body: WorkflowApprovalRequest, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    try:
        run = engine.resolve_approval(run_id, node_id, approved=body.approved, feedback=body.feedback)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs/{run_id}/events")
@require_permission("runs", "read")
async def get_workflow_events(run_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    events = engine.events.get_events(run_id)
    return {"run_id": run_id, "events": [e.model_dump() for e in events], "count": len(events)}
