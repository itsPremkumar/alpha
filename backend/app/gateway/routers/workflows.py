"""REST API router for Alpha Dynamic Workflow Engine (DWE)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.config.runtime_paths import runtime_home
from alpha.orchestrator.executors import bind_default_executors
from alpha.orchestrator.loop import ExecutionKernel, TurnContext, run_turn
from alpha.orchestrator.replay import replay_run
from alpha.workflow.event_log import DurableEventLog, DurableEventLogError
from alpha.workflow.events import WorkflowEvent, get_event_dispatcher
from alpha.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowPatch,
)
from alpha.workflow.plan_graph import PlanGraphError, PlanGraphStore, PlanVersionConflict
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

# W-N1 durability: the append-only event log is attached to the GLOBAL event
# dispatcher, so every event the engine and the loop emit is journaled without
# touching runtime code (22+ emit sites). The store root is resolved at CALL
# time from ``runtime_home()`` and cached per root, so a test (or a launcher)
# that redirects ``AGENT_WORKSPACE_HOME`` gets its own store and can never
# write into the real workspace. A sink failure is counted and disclosed by
# the dispatcher (see ``GET /api/workflows/durability``), never swallowed.
_DURABLE_LOGS: dict[str, DurableEventLog] = {}


def _durable_event_sink(event: WorkflowEvent) -> None:
    root = runtime_home() / "workflow_store"
    key = str(root)
    log = _DURABLE_LOGS.get(key)
    if log is None:
        log = DurableEventLog(root)
        _DURABLE_LOGS[key] = log
    log.append(event)


get_event_dispatcher().attach_durable_sink(_durable_event_sink)
_PLAN_STORE = PlanGraphStore()


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


# ---------------------------------------------------------------------------
# W-N1 durability surface (additive; every disk touch is offloaded to a thread)
# ---------------------------------------------------------------------------


def _store() -> DurableEventLog:
    """The durable store for the CURRENT runtime home (env-resolved per call)."""
    root = runtime_home() / "workflow_store"
    key = str(root)
    log = _DURABLE_LOGS.get(key)
    if log is None:
        log = DurableEventLog(root)
        _DURABLE_LOGS[key] = log
    return log


@router.get("/system/durability")
@require_permission("runs", "read")
async def workflow_durability_status(request: Request) -> dict[str, Any]:
    """What is journaled, where, and what failed — never a green light by default."""

    def _collect() -> dict[str, Any]:
        log = _store()
        writable, detail = log.probe_writable()
        runs = log.list_runs()
        return {
            "store_dir": str(log.root),
            "writable": writable,
            "writable_detail": detail,
            "persisted_runs": runs,
            "persisted_run_count": len(runs),
        }

    return {
        "dispatcher": get_event_dispatcher().durable_status(),
        "store": await asyncio.to_thread(_collect),
    }


@router.get("/runs/{run_id}/events/durable")
@require_permission("runs", "read")
async def get_durable_workflow_events(run_id: str, request: Request) -> dict[str, Any]:
    """The append-only JSONL log for a run, plus any corrupt-tail disclosure."""

    def _read() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records, disclosures = _store().records_for(run_id)
        return [record.model_dump(mode="json") for record in records], disclosures

    records, disclosures = await asyncio.to_thread(_read)
    return {"run_id": run_id, "count": len(records), "events": records, "corrupt_tail": disclosures}


@router.post("/runs/{run_id}/project")
@require_permission("runs", "create")
async def project_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    """Materialize the live run's projection so a fresh process can hydrate it."""
    engine = get_workflow_engine()
    live = engine.get_run(run_id)
    if not live:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    definition = engine.get_definition(live.workflow_id)
    prefix = f"{live.workflow_id}:v"
    graphs = {key: graph for key, graph in engine.graphs.items() if key.startswith(prefix)}

    def _write() -> dict[str, Any]:
        log = _store()
        records, disclosures = log.records_for(run_id)
        if disclosures:
            raise DurableEventLogError(
                f"event log for run {run_id} has a corrupt tail at line {disclosures[0]['line_number']}: {disclosures[0]['error']}"
            )
        snapshot = log.project(
            run=live,
            definition=definition,
            graphs=graphs,
            last_event=records[-1] if records else None,
            event_count=len(records),
        )
        return {
            "run_id": run_id,
            "status": live.status.value,
            "last_seq": snapshot.last_seq,
            "event_count": snapshot.event_count,
            "graph_versions": sorted(snapshot.graphs),
            "definition_recorded": definition is not None,
        }

    try:
        return await asyncio.to_thread(_write)
    except DurableEventLogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/hydrate")
@require_permission("runs", "create")
async def hydrate_workflow_engine(request: Request) -> dict[str, Any]:
    """Install persisted runs into THIS process's engine; report every refusal.

    The response is the honest ``HydrationReport``: ``degraded`` names the runs
    that were corrupt, stale, or missing pieces instead of pretending the
    engine is whole.
    """
    engine = get_workflow_engine()

    def _hydrate() -> dict[str, Any]:
        return _store().hydrate(engine).model_dump(mode="json")

    return await asyncio.to_thread(_hydrate)


@router.get("/{workflow_id}/plans")
@require_permission("runs", "read")
async def list_workflow_plans(workflow_id: str, request: Request) -> dict[str, Any]:
    """Durable graph-revision history for a workflow (plan-graph store)."""

    def _history() -> dict[str, Any]:
        store = PlanGraphStore(runtime_home() / "workflow_store" / "plans")
        history = store.history(workflow_id)
        return {
            "workflow_id": workflow_id,
            "versions": [record.version for record in history],
            "count": len(history),
            "latest_source": history[-1].source if history else None,
        }

    try:
        return await asyncio.to_thread(_history)
    except PlanGraphError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{workflow_id}/plans")
@require_permission("runs", "create")
async def record_workflow_plan(workflow_id: str, request: Request) -> dict[str, Any]:
    """Record the engine's current graph for this workflow as a new revision.

    Refuses when that version already exists (``PlanVersionConflict``) — history
    is append-only, never rewritten in place.
    """
    engine = get_workflow_engine()
    definition = engine.get_definition(workflow_id)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    graph = engine.graphs.get(f"{workflow_id}:v{definition.graph.version}", definition.graph)
    try:
        body = await request.json()
    except Exception:
        body = {}
    requested_source = str(body.get("source", "manual")) if isinstance(body, dict) else "manual"
    source = requested_source if requested_source in {"register", "patch", "replay", "hydration", "manual"} else "manual"
    note = str(body.get("note", "")) if isinstance(body, dict) else ""

    def _record() -> dict[str, Any]:
        store = PlanGraphStore(runtime_home() / "workflow_store" / "plans")
        record = store.record_revision(workflow_id, graph, source=source, note=note)
        return {"workflow_id": workflow_id, "version": record.version, "source": record.source, "created_at": record.created_at}

    try:
        return await asyncio.to_thread(_record)
    except PlanVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlanGraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
