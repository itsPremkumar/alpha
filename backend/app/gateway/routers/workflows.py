"""REST API router for Alpha Dynamic Workflow Engine (DWE)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.config.runtime_paths import runtime_home
from alpha.orchestrator.executors import COMPENSATION_EXECUTOR, bind_default_executors, get_executor_registry
from alpha.orchestrator.loop import ExecutionKernel, TurnContext, run_turn
from alpha.orchestrator.replay import replay_run
from alpha.workflow.event_log import DurableEventLog, DurableEventLogError
from alpha.workflow.events import WorkflowEvent, get_event_dispatcher
from alpha.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowPatch,
    WorkflowRunStatus,
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
    record = log.append(event)
    # Project at stable lifecycle seams so a normal terminal run is
    # restart-readable without requiring a separate operator call.  Mid-wave
    # events remain journal-only; a crash there hydrates as stale rather than
    # pretending an older snapshot is current.
    if event.event_type in {
        "workflow_started",
        "workflow_completed",
        "workflow_failed",
        "workflow_cancelled",
    }:
        run = get_workflow_engine().get_run(event.workflow_run_id)
        if run is not None:
            definition = get_workflow_engine().get_definition(run.workflow_id)
            private_graph = get_workflow_engine()._run_graph_for(run)
            graphs = {f"{run.workflow_id}:v{run.graph_version}": private_graph}
            log.project(
                run=run,
                definition=definition,
                graphs=graphs,
                last_event=record,
                event_count=record.seq,
            )


get_event_dispatcher().attach_durable_sink(_durable_event_sink)
get_event_dispatcher().fail_closed_on_durable_error = True
_PLAN_STORE = PlanGraphStore()


def get_workflow_engine() -> DynamicWorkflowEngine:
    return _GLOBAL_ENGINE


def get_workflow_kernel() -> ExecutionKernel:
    """Return the Gateway-owned kernel for correlated child workflows."""
    return _KERNEL


def _workflow_owner(request: Request) -> str | None:
    """Resolve the authenticated workflow owner without trusting request data.

    Direct unit callers sometimes pass a lightweight request double.  Owner
    enforcement is a Gateway boundary and therefore applies only to a real
    Starlette request; embedded/library callers remain explicitly unscoped.
    """
    if not isinstance(request, Request):
        return None
    try:
        from alpha.runtime.user_context import get_effective_user_id

        return get_effective_user_id()
    except Exception:
        return None


def _ensure_plan_base(
    workflow_id: str,
    version: int,
    graph: WorkflowGraph,
    *,
    owner_id: str | None = None,
) -> None:
    """Seed an explicit base revision before a run patch advances it.

    Workflow registration intentionally does not write a plan revision: the
    dedicated plan endpoint is the append-only authoring surface.  A run may
    nevertheless be patched before an operator records that plan, so the first
    patch must establish the real vN base and then CAS the vN+1 candidate.  A
    same-version, same-graph record is idempotent; a different graph is never
    overwritten.
    """
    store = PlanGraphStore(runtime_home() / "workflow_store" / "plans")
    try:
        store.record_revision(
            workflow_id,
            graph,
            source="register",
            note="implicit base revision before first run patch",
            version=version,
            owner_id=owner_id,
        )
    except PlanVersionConflict:
        existing = store.get(workflow_id, version)
        if existing is None or existing.graph != graph or (owner_id is not None and existing.owner_id != owner_id):
            raise PlanVersionConflict(f"plan revision {workflow_id!r} v{version} belongs to another owner or graph")


def _assert_workflow_owner(obj: Any, request: Request) -> None:
    owner = _workflow_owner(request)
    if not owner:
        return
    resource_owner = getattr(obj, "owner_id", None)
    if resource_owner != owner:
        raise HTTPException(status_code=404, detail="Workflow resource not found.")


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
    # Opt-in full perception/discovery loop.  The default remains the legacy
    # paradigm mapper so existing clients do not unexpectedly incur workflow
    # planning or resource side effects.
    dynamic: bool = False


class WorkflowApprovalRequest(BaseModel):
    approved: bool
    feedback: str = ""
    approval_request_id: str | None = None


class DynamicPerceiveRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    context: dict[str, Any] = Field(default_factory=dict)


class DynamicExecuteRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    context: dict[str, Any] = Field(default_factory=dict)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    mode: str = "normal"
    auto_execute: bool = True
    max_steps: int = Field(default=40, ge=1, le=200)
    default_executor: str = "alpha.local.digest"


class WorkflowReplanRequest(BaseModel):
    failed_node_id: str | None = None
    error_message: str | None = None
    resume: bool = True


@router.post("", status_code=201)
@require_permission("runs", "create")
async def register_workflow(body: WorkflowCreateRequest, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    graph = WorkflowGraph(**body.graph) if body.graph else WorkflowGraph()
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="workflow graph must contain at least one node")
    for node_id, node in graph.nodes.items():
        if node.id != node_id:
            raise HTTPException(status_code=400, detail=f"graph node key '{node_id}' must match node id '{node.id}'")
    for edge in graph.edges:
        if edge.source not in graph.nodes or edge.target not in graph.nodes:
            raise HTTPException(status_code=400, detail=f"edge {edge.source!r}->{edge.target!r} references an unknown node")
    owner = _workflow_owner(request)
    existing = engine.get_definition(body.id)
    if existing is not None:
        if existing.owner_id != owner or existing.graph != graph:
            raise HTTPException(status_code=409, detail=f"workflow id '{body.id}' already exists")
        # Exact same-owner re-registration is idempotent; never replace a live
        # definition or graph underneath an existing run.
        return {"id": existing.id, "name": existing.name, "version": existing.version}
    definition = WorkflowDefinition(
        id=body.id,
        name=body.name,
        owner_id=owner,
        description=body.description,
        graph=graph,
        variables=body.variables,
        policies=body.policies,
    )
    engine.register_definition(definition)
    # Plan revisions are explicit, append-only records.  Registration creates
    # the in-memory definition; the dedicated plan endpoint (and dynamic
    # compilation/patch paths) records durable revisions when requested, so a
    # later manual record is not forced to collide with an implicit v1 write.
    return {"id": definition.id, "name": definition.name, "version": definition.version}


@router.get("")
@require_permission("runs", "read")
async def list_workflows(request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    owner = _workflow_owner(request)
    definitions = [definition for definition in engine.definitions.values() if not owner or definition.owner_id == owner]
    return {
        "workflows": [
            {
                "id": d.id,
                "name": d.name,
                "version": d.version,
                "description": d.description,
                "node_count": len(d.graph.nodes),
            }
            for d in definitions
        ],
        "count": len(definitions),
    }


def _run_summary(run: Any) -> dict[str, Any]:
    """Serialize the stable list projection without inventing missing fields."""
    created_at = getattr(run, "created_at", "")
    updated_at = getattr(run, "updated_at", "")
    return {
        "run_id": str(getattr(run, "run_id", "")),
        "workflow_id": str(getattr(run, "workflow_id", "")),
        "status": str(getattr(getattr(run, "status", "unknown"), "value", getattr(run, "status", "unknown"))),
        "graph_version": int(getattr(run, "graph_version", 1) or 1),
        "active_nodes": list(getattr(run, "active_nodes", []) or []),
        "completed_nodes": list(getattr(run, "completed_nodes", []) or []),
        "failed_nodes": list(getattr(run, "failed_nodes", []) or []),
        "waiting_nodes": list(getattr(run, "waiting_nodes", []) or []),
        "waiting_reason": getattr(run, "waiting_reason", None),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
    }


@router.get("/runs")
@require_permission("runs", "read")
async def list_workflow_runs(
    request: Request,
    status: str | None = None,
    workflow_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    engine = get_workflow_engine()
    owner = _workflow_owner(request)
    runs_dict = {run_id: run for run_id, run in engine.runs.items() if not owner or run.owner_id == owner}

    # A restarted Gateway may know only the JSONL event log.  Merge those
    # measured projections into the listing instead of pretending that a live
    # engine is the complete history.  Corrupt logs are disclosed, not hidden.
    durable_entries, durable_disclosures = await asyncio.to_thread(_durable_run_summaries, owner)
    results_by_id = {run_id: _run_summary(run) for run_id, run in runs_dict.items()}
    results_by_id.update(durable_entries)

    results: list[dict[str, Any]] = []
    for entry in results_by_id.values():
        if status and entry["status"] != status:
            continue
        if workflow_id and entry["workflow_id"] != workflow_id:
            continue
        results.append(entry)

    results.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    total = len(results_by_id)
    if limit > 0:
        results = results[:limit]

    return {
        "status": "degraded" if durable_disclosures else "ok",
        "runs": results,
        "count": len(results),
        "total": total,
        "disclosures": durable_disclosures,
    }


@router.get("/system/registries")
@require_permission("runs", "read")
async def get_workflow_registries(request: Request) -> dict[str, Any]:
    """Expose the read-only capability selection plane used by DWE planning."""

    def _collect() -> dict[str, Any]:
        from alpha.workflow.registry import REGISTRY_KINDS, get_workflow_registry

        registry = get_workflow_registry()
        result: dict[str, Any] = {"status": "ok", "registries": {}}
        for kind in REGISTRY_KINDS:
            try:
                child = registry.registry(kind)
                result["registries"][kind] = {
                    "health": child.health().model_dump(mode="json"),
                    "descriptors": [item.model_dump(mode="json") for item in child.list()[:100]],
                }
            except Exception as exc:
                result["registries"][kind] = {
                    "health": {
                        "registry": kind,
                        "status": "unavailable",
                        "count": None,
                        "error": f"{type(exc).__name__}: {exc}",
                        "evidence_kind": "measured",
                    },
                    "descriptors": [],
                }
        return result

    return await asyncio.to_thread(_collect)


@router.post("/dynamic/perceive")
@require_permission("runs", "read")
async def perceive_dynamic_workflow(body: DynamicPerceiveRequest, request: Request) -> dict[str, Any]:
    def _preview() -> dict[str, Any]:
        from alpha.bots.cloning import get_bot_clone_engine
        from alpha.bots.registry import get_bot_registry
        from alpha.skills.hub.discovery import get_skills_hub
        from alpha.skills.mcp_lifecycle import SkillMcpLifecycleManager
        from alpha.workflow.dynamic_assembler import DynamicResourceAssembler
        from alpha.workflow.dynamic_decomposer import get_dynamic_decomposer
        from alpha.workflow.dynamic_perception import get_dynamic_perception_engine

        perception_engine = get_dynamic_perception_engine()
        intent = perception_engine.perceive(body.prompt, body.context)
        decomposer = get_dynamic_decomposer()
        goal = decomposer.decompose(intent, body.prompt)
        assembler = DynamicResourceAssembler(
            bot_registry=get_bot_registry(),
            clone_engine=get_bot_clone_engine(),
            skills_hub=get_skills_hub(),
            mcp_manager=SkillMcpLifecycleManager(),
        )
        resources = assembler.assemble(goal, body.prompt, provision=False)
        return {
            "status": "ok",
            "perception": intent.to_dict(),
            "goal": goal.to_dict(),
            "resources": resources.to_dict(),
        }

    return await asyncio.to_thread(_preview)


@router.post("/dynamic/execute", status_code=201)
@require_permission("runs", "create")
async def execute_dynamic_workflow(body: DynamicExecuteRequest, request: Request) -> dict[str, Any]:
    writable, durability_detail = await asyncio.to_thread(_store().probe_writable)
    if not writable:
        raise HTTPException(status_code=503, detail=f"workflow durability unavailable: {durability_detail}")
    owner = _workflow_owner(request)
    if body.mode not in ("normal", "bot"):
        raise HTTPException(status_code=400, detail="mode must be one of ('normal', 'bot')")
    if body.default_executor != "alpha.local.digest":
        raise HTTPException(
            status_code=400,
            detail="default_executor is server-owned for the public dynamic route; bind a host executor through DynamicWorkflowService",
        )

    def _execute() -> dict[str, Any]:
        from alpha.bots.cloning import get_bot_clone_engine
        from alpha.bots.registry import get_bot_registry
        from alpha.orchestrator.executors import get_executor_registry
        from alpha.skills.hub.discovery import get_skills_hub
        from alpha.skills.mcp_lifecycle import SkillMcpLifecycleManager
        from alpha.workflow.dynamic_assembler import DynamicResourceAssembler
        from alpha.workflow.dynamic_bridge import DynamicWorkflowBridge
        from alpha.workflow.dynamic_decomposer import get_dynamic_decomposer
        from alpha.workflow.dynamic_perception import get_dynamic_perception_engine

        engine = get_workflow_engine()
        perception_engine = get_dynamic_perception_engine()
        intent = perception_engine.perceive(body.prompt, body.context)
        decomposer = get_dynamic_decomposer()
        goal = decomposer.decompose(intent, body.prompt)
        assembler = DynamicResourceAssembler(
            bot_registry=get_bot_registry(),
            clone_engine=get_bot_clone_engine(),
            skills_hub=get_skills_hub(),
            mcp_manager=SkillMcpLifecycleManager(),
        )
        # Compile-only requests remain side-effect free.
        resources = assembler.assemble(goal, body.prompt, provision=body.auto_execute)
        bridge = DynamicWorkflowBridge(
            engine=engine,
            kernel=_KERNEL,
            mode=body.mode,
            owner_id=owner,
            require_compensation_receipt=True,
        )
        definition = bridge.build_workflow_definition(goal, resources, default_executor=body.default_executor)
        definition.owner_id = owner
        engine.register_definition(definition)

        store = PlanGraphStore(runtime_home() / "workflow_store" / "plans")
        store.record_revision(
            definition.id,
            definition.graph,
            source="register",
            note=f"Dynamic compile: {body.prompt[:60]}",
            owner_id=owner,
        )
        if not body.auto_execute:
            return {
                "status": "compiled",
                "workflow_id": definition.id,
                "run_id": None,
                "task_count": len(goal.tasks),
                "goal": goal.to_dict(),
                "perception": intent.to_dict(),
                "resources": resources.to_dict(),
                "auto_execute": False,
            }

        runner = get_executor_registry().build_runner()
        exec_bridge = DynamicWorkflowBridge(
            engine=engine,
            kernel=_KERNEL,
            node_runner=runner,
            compensation_runner=None,
            execution_label="local_digest_projection" if runner is not None else "unbound",
            owner_id=owner,
            mode=body.mode,
            require_compensation_receipt=True,
        )
        result = exec_bridge.execute_goal(
            goal,
            resources,
            initial_state=body.initial_state,
            max_steps=body.max_steps,
            default_executor=body.default_executor,
        )
        persisted_run = engine.get_run(result.run_id)
        if persisted_run is not None:
            persisted_run.owner_id = owner
        return {
            "status": result.status,
            "mode": body.mode,
            "run_id": result.run_id,
            "workflow_id": result.workflow_id,
            "perception": intent.to_dict(),
            "goal": goal.to_dict(),
            "resources": resources.to_dict(),
            "total_steps": result.total_steps,
            "task_count": len(goal.tasks),
            "completed_count": len(result.completed_nodes),
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
            "compensated_nodes": result.compensated_nodes,
            "node_outputs": result.node_outputs,
            "replans_count": result.replans_count,
            "duration_ms": result.duration_ms,
            "error_summary": result.error_summary,
            "metadata": result.metadata,
            "waves": goal.execution_waves,
        }

    try:
        return await asyncio.to_thread(_execute)
    except PlanGraphError as exc:
        raise HTTPException(status_code=503, detail=f"workflow plan persistence failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Keep arbitrary assembler/executor failures honest as 500s.
        raise HTTPException(status_code=500, detail=f"dynamic workflow execution failed: {type(exc).__name__}: {exc}") from exc


@router.get("/{workflow_id}")
@require_permission("runs", "read")
async def get_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    definition = engine.get_definition(workflow_id)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    _assert_workflow_owner(definition, request)
    return definition.model_dump()


@router.post("/{workflow_id}/runs", status_code=201)
@require_permission("runs", "create")
async def start_workflow_run(workflow_id: str, body: WorkflowRunCreateRequest, request: Request) -> dict[str, Any]:
    writable, detail = await asyncio.to_thread(_store().probe_writable)
    if not writable:
        raise HTTPException(status_code=503, detail=f"workflow durability unavailable: {detail}")
    definition = get_workflow_engine().get_definition(workflow_id)
    if definition:
        _assert_workflow_owner(definition, request)
    try:
        run = await asyncio.to_thread(
            _KERNEL.start_run,
            workflow_id,
            initial_state=body.initial_state,
            mode=body.mode,
            owner_id=_workflow_owner(request),
        )
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
    _assert_workflow_owner(run, request)
    return run.model_dump()


@router.post("/runs/{run_id}/step")
@require_permission("runs", "create")
async def step_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    run = get_workflow_engine().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    _assert_workflow_owner(run, request)
    try:
        # One claim -> one scheduling wave through the executor registry, then
        # the kernel's fail-closed policy (a run with failed nodes ends FAILED
        # with an honest ``workflow_failed`` event, never left RUNNING).
        run = await asyncio.to_thread(_KERNEL.dispatch, run_id)
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/cancel")
@require_permission("runs", "cancel")
async def cancel_workflow_run(
    run_id: str,
    request: Request,
    reason: str = "operator requested cancellation",
) -> dict[str, Any]:
    run = get_workflow_engine().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    _assert_workflow_owner(run, request)
    try:
        return (await asyncio.to_thread(_KERNEL.cancel, run_id, reason=reason[:2000])).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/patch")
@require_permission("runs", "create")
async def patch_workflow_run(run_id: str, patch_data: dict[str, Any], request: Request) -> dict[str, Any]:
    try:
        run = get_workflow_engine().get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        _assert_workflow_owner(run, request)
        patch = WorkflowPatch(**patch_data)
        base_graph = get_workflow_engine()._run_graph_for(run)
        # Claimed application: concurrent patches serialize, so an optimistic
        # -concurrency rejection is deterministic instead of a lost update.
        new_graph, validation = await asyncio.to_thread(_KERNEL.apply_patch, run_id, patch)
        if not validation.allowed:
            raise HTTPException(status_code=400, detail=f"Patch rejected: {validation.reason}")
        if base_graph is None:
            raise HTTPException(status_code=503, detail="workflow patch base graph is unavailable for durable persistence")
        try:
            await asyncio.to_thread(
                lambda: _ensure_plan_base(
                    run.workflow_id,
                    new_graph.version - 1,
                    base_graph,
                    owner_id=run.owner_id,
                )
            )
            await asyncio.to_thread(
                lambda: PlanGraphStore(runtime_home() / "workflow_store" / "plans").compare_and_set(
                    run.workflow_id,
                    expected_version=new_graph.version - 1,
                    new_graph=new_graph,
                    source="patch",
                    note=patch.reason,
                    owner_id=run.owner_id,
                )
            )
        except PlanGraphError as exc:
            raise HTTPException(status_code=503, detail=f"workflow patch persistence failed: {exc}") from exc
        return {"status": "committed", "new_graph_version": new_graph.version}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/runs/{run_id}/replan")
@require_permission("runs", "create")
async def replan_workflow_run(run_id: str, body: WorkflowReplanRequest, request: Request) -> dict[str, Any]:
    from alpha.workflow.replanner import RuntimeReplanner

    engine = get_workflow_engine()
    run = engine.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    _assert_workflow_owner(run, request)

    failed_node_id = body.failed_node_id
    if not failed_node_id:
        if run.failed_nodes:
            failed_node_id = run.failed_nodes[0]
        else:
            return {"status": "no_op", "reason": "No failed nodes found to replan", "run": run.model_dump()}

    graph = engine._run_graph_for(run)
    base_graph = graph

    replanner = RuntimeReplanner()
    error_msg = body.error_message or f"Node '{failed_node_id}' failed in run '{run_id}'"
    patch = replanner.propose_failure_repair_patch(
        failed_node_id=failed_node_id,
        error_message=error_msg,
        graph=graph,
        run=run,
    )

    new_graph, validation = await asyncio.to_thread(_KERNEL.apply_patch, run_id, patch)
    if not validation.allowed:
        raise HTTPException(status_code=400, detail=f"Replan patch rejected: {validation.reason}")
    if base_graph is None:
        raise HTTPException(status_code=503, detail="workflow replan base graph is unavailable for durable persistence")
    try:
        await asyncio.to_thread(
            lambda: _ensure_plan_base(
                run.workflow_id,
                patch.base_graph_version,
                base_graph,
                owner_id=run.owner_id,
            )
        )
        await asyncio.to_thread(
            lambda: PlanGraphStore(runtime_home() / "workflow_store" / "plans").compare_and_set(
                run.workflow_id,
                expected_version=patch.base_graph_version,
                new_graph=new_graph,
                source="patch",
                note=patch.reason,
                owner_id=run.owner_id,
            )
        )
    except PlanGraphError as exc:
        raise HTTPException(status_code=503, detail=f"workflow replan persistence failed: {exc}") from exc

    resumed_run = run
    resume_error: str | None = None
    if body.resume:
        # The engine fail-closes a failed wave.  A committed repair patch is
        # therefore not executable until the run is explicitly re-opened; the
        # retry operation resets the exact failed target under the kernel
        # claim.  Do not report a successful resume when dispatch refuses.
        run.status = WorkflowRunStatus.RUNNING
        run.waiting_reason = None
        run.approval_request_id = None
        engine.events.emit(
            "run_reopened",
            run_id,
            reason="replan retry requested",
        )
        try:
            resumed_run = await asyncio.to_thread(_KERNEL.dispatch, run_id)
        except Exception as exc:
            resume_error = f"{type(exc).__name__}: {exc}"
        if resume_error is None and resumed_run.status == WorkflowRunStatus.FAILED:
            resume_error = "replan dispatch ended failed; see the run's node_failed events"

    return {
        "status": "committed" if resume_error is None else "committed_resume_failed",
        "new_graph_version": new_graph.version,
        "patch_operations": [op.op for op in patch.operations],
        "resume_error": resume_error,
        "run": resumed_run.model_dump(),
    }


@router.post("/runs/{run_id}/compensate")
@require_permission("runs", "create")
async def compensate_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    """Execute only real saga callbacks; never manufacture a rollback receipt."""
    from alpha.workflow.models import NodeStatus, NodeType, WorkflowRunStatus

    engine = get_workflow_engine()
    run = engine.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    _assert_workflow_owner(run, request)

    graph = engine._run_graph_for(run)

    comp_nodes = [n for n in graph.nodes.values() if n.type == NodeType.COMPENSATION]
    if not comp_nodes:
        return {
            "run_id": run_id,
            "status": "no_compensations",
            "executed": False,
            "compensated_nodes": [],
            "count": 0,
        }

    already_compensated = [node.id for node in comp_nodes if node.status == NodeStatus.SUCCEEDED and node.evidence]
    targets = [node for node in comp_nodes if node.config.get("target_rollback_node") in run.completed_nodes and node.id not in already_compensated]
    if not targets:
        return {
            "run_id": run_id,
            "status": "already_compensated" if already_compensated else "no_targets_needed_compensation",
            "executed": False,
            "compensated_nodes": already_compensated,
            "count": 0,
            "reason": "compensation already has verified evidence" if already_compensated else "no completed target has a declared compensation",
        }

    registry = get_executor_registry()
    if not registry.has(COMPENSATION_EXECUTOR):
        # Keep the response non-success and disclose the missing seam.  The
        # legacy status token is retained for clients that only understand the
        # original vocabulary, but no node state is changed.
        return {
            "run_id": run_id,
            "status": "no_targets_needed_compensation",
            "executed": False,
            "compensated_nodes": [],
            "count": 0,
            "reason": f"no compensation executor bound under '{COMPENSATION_EXECUTOR}'",
        }

    # Admit only the real compensation seam.  A previously terminal run may be
    # resumed for rollback, but the original failure markers remain in history.
    run.status = WorkflowRunStatus.RUNNING
    for node in targets:
        node.status = NodeStatus.COMPENSATING
        run.node_states[node.id] = NodeStatus.COMPENSATING
        node.config["requires_dedicated_compensation_executor"] = True

    try:
        await asyncio.to_thread(_KERNEL.dispatch, run_id)
    except Exception as exc:
        return {
            "run_id": run_id,
            "status": "compensation_failed",
            "executed": False,
            "compensated_nodes": [],
            "count": 0,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    compensated = already_compensated + [node.id for node in targets if run.node_states.get(node.id) == NodeStatus.SUCCEEDED and node.evidence]
    return {
        "run_id": run_id,
        "status": "compensated" if compensated else "compensation_failed",
        "executed": bool(compensated),
        "compensated_nodes": compensated,
        "count": len(compensated),
        "reason": None if compensated else "compensation callback returned no verified evidence",
    }


@router.post("/runs/{run_id}/approvals/{node_id}")
@require_permission("runs", "create")
async def resolve_approval(run_id: str, node_id: str, body: WorkflowApprovalRequest, request: Request) -> dict[str, Any]:
    try:
        existing = get_workflow_engine().get_run(run_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        _assert_workflow_owner(existing, request)
        run = await asyncio.to_thread(
            _KERNEL.resolve_approval,
            run_id,
            node_id,
            approved=body.approved,
            feedback=body.feedback,
            approval_request_id=body.approval_request_id,
        )
        return run.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/runs/{run_id}/events")
@require_permission("runs", "read")
async def get_workflow_events(run_id: str, request: Request) -> dict[str, Any]:
    engine = get_workflow_engine()
    run = engine.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    _assert_workflow_owner(run, request)
    events = engine.events.get_events(run_id)
    if not events:
        events, _ = await asyncio.to_thread(_store().read_events, run_id)
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
    if body.dynamic:
        from alpha.orchestrator.dynamic_service import DynamicRequest, DynamicWorkflowService
        from alpha.orchestrator.executors import DIGEST_EXECUTOR

        owner = _workflow_owner(request)
        service = DynamicWorkflowService(kernel=_KERNEL)
        try:
            result = await asyncio.to_thread(
                service.execute,
                DynamicRequest(
                    prompt=body.prompt,
                    mode=body.mode,
                    context={**body.initial_state, "owner_id": owner},
                    initial_state=body.initial_state,
                    max_steps=body.max_waves,
                    default_executor=DIGEST_EXECUTOR,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.status == "unavailable":
            raise HTTPException(status_code=422, detail=result.reason or "dynamic workflow unavailable")
        return result.to_dict()

    context = TurnContext(
        mode=body.mode,
        paradigm=body.paradigm,
        initial_state=body.initial_state,
        max_waves=body.max_waves,
        handoff_to=body.handoff_to,
        owner_id=_workflow_owner(request),
    )
    try:
        outcome = await asyncio.to_thread(run_turn, body.prompt, context, kernel=_KERNEL)
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
    _assert_workflow_owner(live, request)
    definition = engine.get_definition(live.workflow_id)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Workflow '{live.workflow_id}' not found.")
    events = engine.events.get_events(run_id)
    if not events:
        events, disclosures = await asyncio.to_thread(_store().read_events, run_id)
        if disclosures:
            raise HTTPException(status_code=409, detail=f"durable event log is corrupt: {disclosures[0]}")
    log_size_before = len(engine.events.get_events())
    try:
        _replay_engine, replayed = await asyncio.to_thread(replay_run, events, definition)
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


def _durable_run_summaries(owner_id: str | None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read restart-only run summaries from projections or validated events."""
    entries: dict[str, dict[str, Any]] = {}
    disclosures: list[str] = []
    log = _store()
    for run_id in log.list_runs():
        if run_id in get_workflow_engine().runs:
            continue
        try:
            snapshot = log.load_snapshot(run_id)
        except Exception as exc:
            disclosures.append(f"run {run_id}: projection unavailable: {type(exc).__name__}: {exc}")
            continue
        if snapshot is not None:
            if owner_id and snapshot.run.owner_id != owner_id:
                continue
            entries[run_id] = _run_summary(snapshot.run)
            continue

        records, corrupt = log.records_for(run_id)
        if corrupt:
            disclosures.append(f"run {run_id}: durable event log corrupt at line {corrupt[0].get('line_number')}")
            continue
        if not records:
            continue
        started = next((record for record in records if record.event_type == "workflow_started"), None)
        latest = records[-1]
        recorded_owner = started.payload.get("owner_id") if started else None
        if owner_id and recorded_owner != owner_id:
            continue
        workflow_id = str((started.payload.get("workflow_id") if started else None) or "")
        status = {
            "workflow_completed": "completed",
            "workflow_failed": "failed",
            "workflow_cancelled": "cancelled",
            "budget_exhausted": "budget_exhausted",
            "approval_requested": "waiting_approval",
        }.get(latest.event_type, "unknown")
        entries[run_id] = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "status": status,
            "graph_version": int(latest.payload.get("graph_version", 1) or 1),
            "active_nodes": [],
            "completed_nodes": [],
            "failed_nodes": [],
            "waiting_nodes": [],
            "waiting_reason": latest.payload.get("reason"),
            "created_at": started.timestamp if started else latest.timestamp,
            "updated_at": latest.timestamp,
        }
    return entries, disclosures


async def _assert_persisted_run_owner(run_id: str, request: Request) -> None:
    """Enforce owner scope even when a run exists only on disk.

    A fresh Gateway has no in-memory ``WorkflowRun`` to check.  Prefer the
    projection's owner, then the owner recorded on the first durable event;
    legacy records without an owner remain readable under the repository's
    historical shared-resource rule.  Corrupt logs fail closed.
    """
    live = get_workflow_engine().get_run(run_id)
    if live is not None:
        _assert_workflow_owner(live, request)
        return
    owner = _workflow_owner(request)
    if not owner:
        return

    def _check() -> bool:
        log = _store()
        snapshot = log.load_snapshot(run_id)
        if snapshot is not None:
            return snapshot.run.owner_id == owner
        records, disclosures = log.records_for(run_id)
        if disclosures:
            raise DurableEventLogError(f"event log for run {run_id!r} has a corrupt tail: {disclosures[0]}")
        if not records:
            return False
        for record in records:
            recorded_owner = record.payload.get("owner_id")
            if recorded_owner is not None:
                return str(recorded_owner) == owner
        return False

    try:
        allowed = await asyncio.to_thread(_check)
    except DurableEventLogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not allowed:
        raise HTTPException(status_code=404, detail="Workflow resource not found.")


def hydrate_workflow_engine_from_store(*, owner_id: str | None = None) -> dict[str, Any]:
    """Startup/recovery helper for hosts that own the Gateway lifespan."""
    return _store().hydrate(get_workflow_engine(), owner_id=owner_id).model_dump(mode="json")


@router.get("/system/durability")
@require_permission("runs", "read")
async def workflow_durability_status(request: Request) -> dict[str, Any]:
    """What is journaled, where, and what failed — never a green light by default."""

    def _collect() -> dict[str, Any]:
        log = _store()
        writable, detail = log.probe_writable()
        owner = _workflow_owner(request)
        entries, disclosures = _durable_run_summaries(owner)
        runs = sorted(entries)
        return {
            "store_dir": str(log.root),
            "writable": writable,
            "writable_detail": detail,
            "persisted_runs": runs,
            "persisted_run_count": len(runs),
            "disclosures": disclosures,
        }

    return {
        "dispatcher": get_event_dispatcher().durable_status(),
        "store": await asyncio.to_thread(_collect),
    }


@router.get("/runs/{run_id}/events/durable")
@require_permission("runs", "read")
async def get_durable_workflow_events(run_id: str, request: Request) -> dict[str, Any]:
    """The append-only JSONL log for a run, plus any corrupt-tail disclosure."""
    await _assert_persisted_run_owner(run_id, request)

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
    _assert_workflow_owner(live, request)
    definition = engine.get_definition(live.workflow_id)
    prefix = f"{live.workflow_id}:v"
    graphs = {key: graph for key, graph in engine.graphs.items() if key.startswith(prefix)}

    def _write() -> dict[str, Any]:
        log = _store()
        records, disclosures = log.records_for(run_id)
        if disclosures:
            raise DurableEventLogError(f"event log for run {run_id} has a corrupt tail at line {disclosures[0]['line_number']}: {disclosures[0]['error']}")
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
        return _store().hydrate(engine, owner_id=_workflow_owner(request)).model_dump(mode="json")

    return await asyncio.to_thread(_hydrate)


@router.get("/{workflow_id}/plans")
@require_permission("runs", "read")
async def list_workflow_plans(workflow_id: str, request: Request) -> dict[str, Any]:
    """Durable graph-revision history for a workflow (plan-graph store)."""
    definition = get_workflow_engine().get_definition(workflow_id)
    if definition:
        _assert_workflow_owner(definition, request)

    def _history() -> dict[str, Any]:
        store = PlanGraphStore(runtime_home() / "workflow_store" / "plans")
        history = store.history(workflow_id, owner_id=_workflow_owner(request))
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
    _assert_workflow_owner(definition, request)
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
        record = store.record_revision(
            workflow_id,
            graph,
            source=source,
            note=note,
            owner_id=_workflow_owner(request),
        )
        return {"workflow_id": workflow_id, "version": record.version, "source": record.source, "created_at": record.created_at}

    try:
        return await asyncio.to_thread(_record)
    except PlanVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlanGraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
