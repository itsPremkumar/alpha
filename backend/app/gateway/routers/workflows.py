"""REST API router for Alpha Dynamic Workflow Engine (DWE)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.orchestrator.executors import bind_default_executors
from alpha.orchestrator.loop import ExecutionKernel, TurnContext, run_turn
from alpha.orchestrator.replay import replay_run
from alpha.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowPatch,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from app.gateway.authz import require_permission

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_GLOBAL_ENGINE = DynamicWorkflowEngine()

# P1 (DY-R4): bind the real executor registry for REST execution and route run
# mutations through the orchestrator kernel that drives this engine through its
# public API only (claim -> dispatch -> handoff). With the registry bound,
# ``step`` executes genuinely bound nodes (nodes declare an ``executor`` name;
# the built-in ``alpha.local.digest`` does recomputable local work). Nodes whose
# executor is not registered — or an empty registry — fail honestly through the
# engine's documented ``no node_runner bound ...`` refusal path; nothing is
# fabricated here.
bind_default_executors()
_KERNEL = ExecutionKernel(engine=_GLOBAL_ENGINE)


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
    # Execution mode (section 13): both modes share the one kernel; validated
    # by it before any run starts, so an unknown mode is a 400, never a run.
    mode: str = "normal"


class WorkflowTurnRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    # Execution mode + meta-planner paradigm (DY-R4): both validated by the
    # kernel/mode mapper before any run starts; an unknown paradigm comes back
    # as an honest refusal (400 + the mapper's verbatim reason), never a run.
    mode: str = "normal"
    paradigm: str = "direct_agent"
    initial_state: dict[str, Any] = Field(default_factory=dict)
    max_waves: int = Field(default=50, ge=1, le=500)
    handoff_to: str | None = None


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
    try:
        run = _KERNEL.start_run(workflow_id, initial_state=body.initial_state, mode=body.mode)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        # Kernel refuses unknown modes before a run exists (fail-closed input).
        raise HTTPException(status_code=400, detail=str(e))


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
    try:
        # One claim -> one scheduling wave through the executor registry, then
        # the kernel's fail-closed policy (a run with failed nodes ends FAILED
        # with an honest ``workflow_failed`` event, never left RUNNING).
        run = _KERNEL.dispatch(run_id)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/patch")
@require_permission("runs", "create")
async def patch_workflow_run(run_id: str, patch_data: dict[str, Any], request: Request) -> dict[str, Any]:
    try:
        patch = WorkflowPatch(**patch_data)
        # Claimed application: concurrent patches serialize, so an optimistic
        # -concurrency rejection is deterministic instead of a lost update.
        new_graph, validation = _KERNEL.apply_patch(run_id, patch)
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
    try:
        run = _KERNEL.resolve_approval(run_id, node_id, approved=body.approved, feedback=body.feedback)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs/{run_id}/events")
@require_permission("runs", "read")
async def get_workflow_events(run_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    events = engine.events.get_events(run_id)
    return {"run_id": run_id, "events": [e.model_dump() for e in events], "count": len(events)}


@router.post("/turns", status_code=201)
@require_permission("runs", "create")
async def run_workflow_turn(body: WorkflowTurnRequest, request: Request) -> dict[str, Any]:
    """One orchestrated turn through the P1 kernel (section 6 seam at REST).

    The kernel's ``TurnOutcome`` is returned verbatim field-for-field: a run
    that genuinely failed reports ``status="failed"`` with its real
    ``failed_nodes`` (a run was created and journalled), while a paradigm the
    mode mapper refuses starts NO run and surfaces the mapper's exact reason as
    a 400 — never a silent success.
    """
    context = TurnContext(
        mode=body.mode,
        paradigm=body.paradigm,
        initial_state=body.initial_state,
        max_waves=body.max_waves,
        handoff_to=body.handoff_to,
    )
    try:
        outcome = run_turn(body.prompt, context, kernel=_KERNEL)
    except ValueError as e:
        # Fail-closed input (e.g. mode outside ('normal', 'bot')).
        raise HTTPException(status_code=400, detail=str(e))
    if outcome.status == "not_expressible":
        raise HTTPException(status_code=400, detail=outcome.reason)
    return {
        "run_id": outcome.run_id,
        "workflow_id": outcome.workflow_id,
        "mode": outcome.mode,
        "paradigm": outcome.paradigm,
        "status": outcome.status,
        "waves": outcome.waves,
        "failed_nodes": list(outcome.failed_nodes),
        "reason": outcome.reason,
        "handoff": outcome.handoff.to_dict() if outcome.handoff is not None else None,
    }


# Run fields the event log journals faithfully enough to compare a replayed
# projection against the live run (see ``alpha.orchestrator.replay`` for the
# fold rules and the disclosed payload gaps).
_REPLAY_COVERED_FIELDS: tuple[str, ...] = (
    "status",
    "state",
    "completed_nodes",
    "failed_nodes",
    "node_states",
    "graph_version",
)


@router.post("/runs/{run_id}/replay")
@require_permission("runs", "read")
async def replay_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    """Fold this run's append-only log into a fresh projection and compare.

    Reports ``matches_live`` over the covered fields plus every mismatch (real
    values on both sides). ``events_emitted_during_replay`` must be 0: replay
    reads the shared log and never writes to it.
    """
    engine = get_workflow_engine()
    live = engine.get_run(run_id)
    if not live:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    definition = engine.get_definition(live.workflow_id)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Workflow '{live.workflow_id}' not found.")
    events = engine.events.get_events(run_id)
    log_size_before = len(engine.events.get_events())
    try:
        _replay_engine, replayed = replay_run(events, definition)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events_emitted = len(engine.events.get_events()) - log_size_before

    mismatches: list[dict[str, Any]] = []
    for field in _REPLAY_COVERED_FIELDS:
        live_value = getattr(live, field)
        replayed_value = getattr(replayed, field)
        if live_value != replayed_value:
            mismatches.append({"field": field, "live": live_value, "replayed": replayed_value})
    return {
        "run_id": run_id,
        "events_folded": len(events),
        "events_emitted_during_replay": events_emitted,
        "covered_fields": list(_REPLAY_COVERED_FIELDS),
        "matches_live": not mismatches,
        "mismatches": mismatches,
        "replayed": {field: getattr(replayed, field) for field in _REPLAY_COVERED_FIELDS},
    }
