"""Tests for the Dynamic Workflow Gateway API router.

DY-R1 honesty contract at the REST layer, now carried by the P1 orchestrator
kernel (claim -> dispatch -> fail-closed) and its executor registry:

- **unbound** (empty registry, module seam unbound): ``step_workflow_run``
  fails with the engine's exact ``no node_runner bound to execute node '...'``
  refusal — nothing completes, the run fail-closes to ``failed``, and no
  ``<node>_*`` state key is invented.
- **bound**: nodes declare an executor the live registry resolves
  (``alpha.local.digest``); completion evidence on the graph node is that
  executor's REAL, independently recomputable sha256 output (the test
  recomputes the documented formula from scratch, never imports it).
- **executor raises mid-wave**: the ``node_failed`` event carries the REAL
  traceback summary captured at the raise site (unified ``reason`` key), the
  engine fail-closes the run with a single ``workflow_failed`` (the kernel
  guard stays silent), and no state artifact or evidence is fabricated.

The module-level ``alpha.workflow.runtime`` seam is never bound by the router;
the registry is consulted per dispatch, so tests swap the registry module seam
(``executors._REGISTRY``) to choose the bound vs unbound world.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import alpha.orchestrator.executors as executors_module
import alpha.workflow.runtime as runtime_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, ExecutorRegistry
from alpha.orchestrator.mode_mapper import NON_EXPRESSIBLE_REASONS
from alpha.tools.builtins.workflow_dag_tool import workflow_dag_manage
from app.gateway.routers.workflows import (
    DynamicExecuteRequest,
    DynamicPerceiveRequest,
    WorkflowApprovalRequest,
    WorkflowCreateRequest,
    WorkflowReplanRequest,
    WorkflowRunCreateRequest,
    WorkflowTurnRequest,
    compensate_workflow_run,
    execute_dynamic_workflow,
    get_workflow,
    get_workflow_engine,
    get_workflow_events,
    get_workflow_run,
    list_workflow_runs,
    list_workflows,
    patch_workflow_run,
    perceive_dynamic_workflow,
    register_workflow,
    replan_workflow_run,
    replay_workflow_run,
    resolve_approval,
    run_workflow_turn,
    start_workflow_run,
    step_workflow_run,
)

# The digest executor's documented formula, recomputed HERE from scratch so the
# REST test proves evidence is checkable by any consumer:
#   sha256(run_id + "\n" + node_id + "\n" + prompt)
_DIGEST_SUFFIX = "over run_id+node_id+node.prompt"


def _recomputed_sha256(run_id: str, node_id: str, prompt: str) -> str:
    return hashlib.sha256(f"{run_id}\n{node_id}\n{prompt}".encode()).hexdigest()


def _recomputed_evidence(run_id: str, node_id: str, prompt: str) -> str:
    return f"alpha.local.digest sha256={_recomputed_sha256(run_id, node_id, prompt)} {_DIGEST_SUFFIX}"


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _bind_digest_registry(monkeypatch: pytest.MonkeyPatch) -> ExecutorRegistry:
    """Fresh registry with ONLY the real digest executor bound (module-seam swap)."""
    registry = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", registry)
    registry.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)
    return registry


def _unbind_all_executors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty registry + unbound module seam: the engine's exact refusal world."""
    monkeypatch.setattr(executors_module, "_REGISTRY", ExecutorRegistry())
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)


@pytest.mark.asyncio
async def test_workflows_router_crud_and_execution(monkeypatch):
    """CRUD + bound execution: real digest evidence lands on the graph node."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    # 1. Register workflow (nodes declare the registry-resolved executor)
    create_body = WorkflowCreateRequest(
        id="api_wf_1",
        name="API Test Workflow",
        description="Testing DWE via FastAPI router",
        graph={
            "version": 1,
            "nodes": {
                "step1": {"id": "step1", "prompt": "Initial task", "executor": DIGEST_EXECUTOR},
                "step2": {
                    "id": "step2",
                    "prompt": "Followup task",
                    "depends_on": ["step1"],
                    "executor": DIGEST_EXECUTOR,
                },
            },
            "edges": [
                {"source": "step1", "target": "step2"},
            ],
        },
    )
    reg_res = await register_workflow(create_body, req)
    assert reg_res["id"] == "api_wf_1"

    # 2. List workflows
    list_res = await list_workflows(req)
    assert any(w["id"] == "api_wf_1" for w in list_res["workflows"])

    # 3. Get workflow definition
    get_res = await get_workflow("api_wf_1", req)
    assert get_res["name"] == "API Test Workflow"
    assert len(get_res["graph"]["nodes"]) == 2

    # Re-registering the exact same graph is idempotent; a different graph may
    # never replace a live workflow id underneath its runs.
    same_res = await register_workflow(create_body, req)
    assert same_res["id"] == "api_wf_1"
    conflicting = create_body.model_copy(
        update={
            "graph": {
                "version": 1,
                "nodes": {"other": {"id": "other", "prompt": "different"}},
                "edges": [],
            }
        }
    )
    with pytest.raises(HTTPException) as duplicate_exc:
        await register_workflow(conflicting, req)
    assert duplicate_exc.value.status_code == 409

    # 4. Start run (mode journaled by the kernel; default is normal mode)
    run_res = await start_workflow_run("api_wf_1", WorkflowRunCreateRequest(initial_state={"env": "test"}), req)
    run_id = run_res["run_id"]
    assert run_res["status"] == "running"
    assert run_res["metrics"]["execution_mode"] == "normal"

    # 5. Step run — one wave per step through the registry-resolved digest
    # executor. The unbound path has its own pin in
    # test_step_without_runner_fails_honestly below.
    step1_res = await step_workflow_run(run_id, req)
    assert "step1" in step1_res["completed_nodes"]
    assert "step2" not in step1_res["completed_nodes"]  # one wave per step: step2 depends on step1
    assert step1_res["status"] == "running"

    # The node's evidence IS the executor's recomputed sha256 output — real,
    # independently checkable work, not engine-authored prose.
    step1_node = get_workflow_engine().get_definition("api_wf_1").graph.nodes["step1"]
    assert step1_node.evidence == [_recomputed_evidence(run_id, "step1", "Initial task")]
    assert step1_node.output == {
        "executor": "alpha.local.digest",
        "node_id": "step1",
        "sha256": _recomputed_sha256(run_id, "step1", "Initial task"),
    }
    # No model call happened, so the token accounting is a real zero.
    assert step1_node.tokens_consumed == 0

    # 6. Apply dynamic patch via router
    patch_payload = {
        "workflow_run_id": run_id,
        "base_graph_version": 1,
        "reason": "Insert intermediate verification",
        "operations": [
            {
                "op": "add_node",
                "args": {"node": {"id": "verify_step", "prompt": "Verify results"}},
            }
        ],
    }
    patch_res = await patch_workflow_run(run_id, patch_payload, req)
    assert patch_res["status"] == "committed"
    assert patch_res["new_graph_version"] == 2

    # 7. Query events
    events_res = await get_workflow_events(run_id, req)
    assert events_res["count"] > 0
    event_types = [e["event_type"] for e in events_res["events"]]
    assert "workflow_started" in event_types
    assert "run_mode_selected" in event_types
    assert "patch_committed" in event_types


@pytest.mark.asyncio
async def test_step_without_runner_fails_honestly(monkeypatch):
    """No executor bound at REST: nothing completes, and the real reason shows.

    The registry module seam is emptied for this test so the engine's exact
    ``no node_runner bound to execute node '...'`` refusal surfaces (the
    production router binds ``alpha.local.digest`` by default — see
    ``bind_default_executors`` in the workflows router).
    """
    _unbind_all_executors(monkeypatch)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_unbound",
        name="Unbound Step Workflow",
        graph={
            "version": 1,
            "nodes": {"solo1": {"id": "solo1", "prompt": "Needs an executor"}},
            "edges": [],
        },
    )
    await register_workflow(create_body, req)
    run_res = await start_workflow_run("api_wf_unbound", WorkflowRunCreateRequest(), req)
    run_id = run_res["run_id"]

    step_res = await step_workflow_run(run_id, req)

    # Honest failure: no fabricated completion, the node failed, and the engine
    # fail-closed the run to a terminal failed status (never left RUNNING).
    assert step_res["completed_nodes"] == []
    assert "solo1" in step_res["failed_nodes"]
    assert step_res["status"] == "failed"

    # The real refusal reason is visible through the REST events endpoint.
    events_res = await get_workflow_events(run_id, req)
    failed = [e for e in events_res["events"] if e["event_type"] == "node_failed"]
    assert failed, "the honest node failure must surface as an event"
    reasons = [str(e["payload"].get("reason", "")) for e in failed]
    assert any("no node_runner bound to execute node 'solo1'" in r for r in reasons)

    # Fail-closed event names the failed node and points at the real reasons.
    fail_closed = [e for e in events_res["events"] if e["event_type"] == "workflow_failed"]
    assert len(fail_closed) == 1, "the engine fail-closes exactly once; the kernel guard stays silent"
    fail_reason = str(fail_closed[-1]["payload"].get("reason", ""))
    assert "solo1" in fail_reason
    assert "node_failed" in fail_reason

    # No state artifact was invented for the unexecuted node.
    assert not [k for k in step_res["state"] if k.startswith("solo1_")]


@pytest.mark.asyncio
async def test_approval_gate_roundtrip_through_router(monkeypatch):
    """HITL REST flow: approval-gated node waits, human approves, then executes."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_approval",
        name="Approval Gate Workflow",
        graph={
            "version": 1,
            "nodes": {
                "gate": {
                    "id": "gate",
                    "prompt": "Deploy to production",
                    "requires_approval": True,
                    "executor": DIGEST_EXECUTOR,
                }
            },
            "edges": [],
        },
    )
    await register_workflow(create_body, req)
    run_res = await start_workflow_run("api_wf_approval", WorkflowRunCreateRequest(), req)
    run_id = run_res["run_id"]

    # Step suspends for approval BEFORE any execution happens.
    wait_res = await step_workflow_run(run_id, req)
    assert wait_res["status"] == "waiting_approval"
    assert wait_res["completed_nodes"] == []

    # Human approves through the REST endpoint.
    approval = await resolve_approval(run_id, "gate", WorkflowApprovalRequest(approved=True, feedback="lgtm"), req)
    assert approval["status"] == "running"

    # The next step executes through the bound registry and completes with the
    # digest executor's recomputable evidence on the graph node.
    done_res = await step_workflow_run(run_id, req)
    assert done_res["completed_nodes"] == ["gate"]
    run = await get_workflow_run(run_id, req)
    assert run["status"] == "completed"
    gate_node = get_workflow_engine().get_definition("api_wf_approval").graph.nodes["gate"]
    assert gate_node.evidence == [_recomputed_evidence(run_id, "gate", "Deploy to production")]


@pytest.mark.asyncio
async def test_executor_failure_surfaces_real_traceback_and_fails_closed(monkeypatch):
    """A raising executor mid-wave: real traceback in node_failed, run fail-closed."""
    registry = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", registry)

    def _exploding_executor(node, run):
        raise ValueError("simulated executor backend unavailable")

    registry.register("test.explode", _exploding_executor)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_boom",
        name="Failing Executor Workflow",
        graph={
            "version": 1,
            "nodes": {"boom": {"id": "boom", "prompt": "Will explode", "executor": "test.explode"}},
            "edges": [],
        },
    )
    await register_workflow(create_body, req)
    run_res = await start_workflow_run("api_wf_boom", WorkflowRunCreateRequest(), req)
    run_id = run_res["run_id"]

    step_res = await step_workflow_run(run_id, req)

    # Honest failure: terminal failed status, no completion, no invented keys.
    assert step_res["status"] == "failed"
    assert step_res["completed_nodes"] == []
    assert "boom" in step_res["failed_nodes"]
    assert not [k for k in step_res["state"] if k.startswith("boom_")]
    boom_node = get_workflow_engine().get_definition("api_wf_boom").graph.nodes["boom"]
    assert boom_node.evidence == []  # nothing executed, nothing claimed

    events_res = await get_workflow_events(run_id, req)
    node_failed = [e for e in events_res["events"] if e["event_type"] == "node_failed"]
    assert node_failed, "the executor failure must surface as a node_failed event"

    # The REAL traceback captured at the raise site, journaled under the
    # unified ``reason`` key (gap 2 root-cause fix: the exception path now
    # goes through ``_fail_node`` like every other node failure).
    error = str(node_failed[-1]["payload"].get("reason", ""))
    assert "error" not in node_failed[-1]["payload"], "gap 2: unified reason key"
    assert "executor 'test.explode' raised ValueError: simulated executor backend unavailable" in error
    assert "Traceback (most recent call last)" in error
    assert 'File "' in error  # a real captured frame, not a fabricated summary
    assert "simulated executor backend unavailable" in error

    # The single fail-closed event names the failed node and points at node_failed.
    fail_closed = [e for e in events_res["events"] if e["event_type"] == "workflow_failed"]
    assert len(fail_closed) == 1, "exactly one fail-closed event (engine emits; kernel guard silent)"
    fail_reason = str(fail_closed[-1]["payload"].get("reason", ""))
    assert "boom" in fail_reason
    assert "node_failed" in fail_reason


@pytest.mark.asyncio
async def test_run_mode_is_journaled_and_validated_at_rest(monkeypatch):
    """Section 13 mode integration at REST: journaled honestly, unknown refused."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_mode",
        name="Mode Journal Workflow",
        graph={
            "version": 1,
            "nodes": {"only": {"id": "only", "prompt": "One step", "executor": DIGEST_EXECUTOR}},
            "edges": [],
        },
    )
    await register_workflow(create_body, req)

    run_res = await start_workflow_run("api_wf_mode", WorkflowRunCreateRequest(mode="bot"), req)
    run_id = run_res["run_id"]
    assert run_res["metrics"]["execution_mode"] == "bot"
    events_res = await get_workflow_events(run_id, req)
    mode_events = [e for e in events_res["events"] if e["event_type"] == "run_mode_selected"]
    assert mode_events, "the selected mode must be journaled as an event"
    assert mode_events[-1]["payload"]["mode"] == "bot"

    # Unknown modes are refused before any run starts (kernel validates MODES).
    with pytest.raises(HTTPException) as excinfo:
        await start_workflow_run("api_wf_mode", WorkflowRunCreateRequest(mode="chaos"), req)
    assert excinfo.value.status_code == 400
    assert "mode must be one of" in str(excinfo.value.detail)


# --------------------------------------------- P1 kernel at REST: concurrency


def test_concurrent_steps_through_rest_serialize_exactly_once(monkeypatch):
    """Two concurrent REST step calls: waves serialize, each node runs once.

    The handler threads are the real entry points (``asyncio.run`` per thread,
    exactly FastAPI's invocation shape). ``ExecutionKernel.claim`` must make
    the two waves non-overlapping: the recording executor's intervals may
    never intersect, and each node executes exactly once in dependency order.
    """
    registry = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", registry)

    intervals: list[tuple[str, float, float]] = []
    intervals_lock = threading.Lock()

    def recording_executor(node, run):
        start = time.monotonic()
        time.sleep(0.05)  # wide enough that two overlapping waves would intersect
        end = time.monotonic()
        with intervals_lock:
            intervals.append((node.id, start, end))
        return {
            "status": "completed",
            "output": f"ran:{node.id}",
            "evidence": f"interval-evidence:{node.id}",
            "tokens_used": 0,
        }

    registry.register("test.interval", recording_executor)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_race",
        name="Concurrent Step Workflow",
        graph={
            "version": 1,
            "nodes": {
                "n1": {"id": "n1", "prompt": "First", "executor": "test.interval"},
                "n2": {"id": "n2", "prompt": "Second", "depends_on": ["n1"], "executor": "test.interval"},
            },
            "edges": [{"source": "n1", "target": "n2"}],
        },
    )

    async def _setup() -> dict:
        await register_workflow(create_body, req)
        return await start_workflow_run("api_wf_race", WorkflowRunCreateRequest(), req)

    run_id = asyncio.run(_setup())["run_id"]

    barrier = threading.Barrier(2)  # both threads hit dispatch at the same instant
    results: list[dict] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            res = asyncio.run(step_workflow_run(run_id, req))
            with results_lock:
                results.append(res)
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            with results_lock:
                errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=60)

    assert errors == []
    assert len(results) == 2

    # Exactly once, in dependency order: 2 waves, 2 nodes, nothing re-run.
    counts = Counter(node_id for node_id, _start, _end in intervals)
    assert dict(counts) == {"n1": 1, "n2": 1}
    ordered = sorted(intervals, key=lambda item: item[1])
    assert [item[0] for item in ordered] == ["n1", "n2"]

    # Serialization proof: no two executor intervals overlap. Without the
    # per-run claim both threads would execute the same ready wave.
    for (_, _start_a, end_a), (_, start_b, _end_b) in zip(ordered, ordered[1:]):
        assert end_a <= start_b, f"executor intervals overlapped: wave 1 ended {end_a} > wave 2 started {start_b}"

    run = asyncio.run(get_workflow_run(run_id, req))
    assert run["status"] == "completed"
    assert run["completed_nodes"] == ["n1", "n2"]


def test_concurrent_patches_through_rest_commit_once_and_occ_reject_loser(monkeypatch):
    """Same-base concurrent REST patches: one 200-equivalent, one honest 400."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_patch_race",
        name="Concurrent Patch Workflow",
        graph={
            "version": 1,
            "nodes": {"solo": {"id": "solo", "prompt": "One step", "executor": DIGEST_EXECUTOR}},
            "edges": [],
        },
    )

    async def _setup() -> dict:
        await register_workflow(create_body, req)
        return await start_workflow_run("api_wf_patch_race", WorkflowRunCreateRequest(), req)

    run_id = asyncio.run(_setup())["run_id"]

    def make_payload(node_id: str) -> dict:
        return {
            "workflow_run_id": run_id,
            "base_graph_version": 1,
            "reason": f"add {node_id}",
            "operations": [{"op": "add_node", "args": {"node": {"id": node_id, "prompt": node_id}}}],
        }

    barrier = threading.Barrier(2)
    committed: list[dict] = []
    rejected: list[HTTPException] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def worker(node_id: str) -> None:
        try:
            barrier.wait(timeout=10)
            res = asyncio.run(patch_workflow_run(run_id, make_payload(node_id), req))
            with results_lock:
                committed.append(res)
        except HTTPException as exc:
            with results_lock:
                rejected.append(exc)
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            with results_lock:
                errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, "race_a"), pool.submit(worker, "race_b")]
        for future in futures:
            future.result(timeout=60)

    assert errors == []
    # Exactly one commit; the loser gets the patch layer's REAL OCC reason.
    assert len(committed) == 1, f"exactly one patch may commit: {committed}"
    assert len(rejected) == 1, f"exactly one patch may be rejected: {rejected}"
    assert committed[0] == {"status": "committed", "new_graph_version": 2}
    assert rejected[0].status_code == 400
    assert "Optimistic concurrency violation" in str(rejected[0].detail)

    run = asyncio.run(get_workflow_run(run_id, req))
    assert run["graph_version"] == 2
    assert len(run["patches_applied"]) == 1

    events = asyncio.run(get_workflow_events(run_id, req))
    event_types = [e["event_type"] for e in events["events"]]
    assert event_types.count("patch_committed") == 1
    assert event_types.count("patch_rejected") == 1


# ------------------------------------------------ P1 turns + replay at REST


@pytest.mark.asyncio
async def test_turn_endpoint_completes_and_refuses_swarm_honestly(monkeypatch):
    """``POST /turns``: a real journalled run comes back; swarm is a 400."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    res = await run_workflow_turn(WorkflowTurnRequest(prompt="summarize the outage"), req)
    assert res["status"] == "completed"
    assert res["mode"] == "normal"
    assert res["paradigm"] == "direct_agent"
    assert res["run_id"] and res["workflow_id"]
    assert res["failed_nodes"] == []
    assert res["waves"] >= 1
    assert res["handoff"] is not None
    assert res["handoff"]["objective"] == "summarize the outage"
    assert res["handoff"]["completed"] == ["direct"]

    # The turn produced a real run, queryable through the ordinary run route.
    run = await get_workflow_run(res["run_id"], req)
    assert run["status"] == "completed"
    assert run["metrics"]["execution_mode"] == "normal"

    # Swarm: no run is created; the 400 detail is the mapper's VERBATIM reason.
    with pytest.raises(HTTPException) as excinfo:
        await run_workflow_turn(WorkflowTurnRequest(prompt="self-organize now", paradigm="swarm"), req)
    assert excinfo.value.status_code == 400
    assert str(excinfo.value.detail) == NON_EXPRESSIBLE_REASONS["swarm"]

    # Fail-closed input: an unknown mode never starts a run either.
    with pytest.raises(HTTPException) as mode_excinfo:
        await run_workflow_turn(WorkflowTurnRequest(prompt="anything", mode="chaos"), req)
    assert mode_excinfo.value.status_code == 400
    assert "mode must be one of" in str(mode_excinfo.value.detail)


@pytest.mark.asyncio
async def test_replay_endpoint_folds_log_and_reports_honest_matches(monkeypatch):
    """``POST /runs/{id}/replay``: completed run matches; gaps reported as-is."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_replay",
        name="Replay Workflow",
        graph={
            "version": 1,
            "nodes": {"s1": {"id": "s1", "prompt": "Replay me", "executor": DIGEST_EXECUTOR}},
            "edges": [],
        },
    )
    await register_workflow(create_body, req)
    run_res = await start_workflow_run("api_wf_replay", WorkflowRunCreateRequest(), req)
    run_id = run_res["run_id"]
    step_res = await step_workflow_run(run_id, req)
    assert step_res["status"] == "completed"

    events_before = await get_workflow_events(run_id, req)
    replay_res = await replay_workflow_run(run_id, req)

    assert replay_res["run_id"] == run_id
    assert replay_res["events_folded"] > 0
    # Replay reads the shared log and must never write to it.
    assert replay_res["events_emitted_during_replay"] == 0
    assert replay_res["covered_fields"] == [
        "status",
        "state",
        "completed_nodes",
        "failed_nodes",
        "node_states",
        "graph_version",
    ]
    assert replay_res["matches_live"] is True
    assert replay_res["mismatches"] == []
    assert replay_res["replayed"]["status"] == "completed"
    assert replay_res["replayed"]["completed_nodes"] == ["s1"]
    assert replay_res["replayed"]["failed_nodes"] == []
    assert replay_res["replayed"]["graph_version"] == 1

    events_after = await get_workflow_events(run_id, req)
    assert [e["event_id"] for e in events_after["events"]] == [e["event_id"] for e in events_before["events"]]

    # An approval-paused run: gap 7 root-cause fix - approval events journal the
    # per-node node_status, so the replayed WAITING gate matches live and the
    # endpoint reports an honest full match.
    approval_body = WorkflowCreateRequest(
        id="api_wf_replay_paused",
        name="Paused Replay Workflow",
        graph={
            "version": 1,
            "nodes": {
                "gate": {"id": "gate", "prompt": "Approve", "requires_approval": True, "executor": DIGEST_EXECUTOR},
            },
            "edges": [],
        },
    )
    await register_workflow(approval_body, req)
    paused_run = await start_workflow_run("api_wf_replay_paused", WorkflowRunCreateRequest(), req)
    paused_id = paused_run["run_id"]
    paused_step = await step_workflow_run(paused_id, req)
    assert paused_step["status"] == "waiting_approval"

    paused_replay = await replay_workflow_run(paused_id, req)
    assert paused_replay["events_emitted_during_replay"] == 0
    assert paused_replay["matches_live"] is True  # gap 7: WAITING folds now
    assert paused_replay["mismatches"] == []
    assert paused_replay["replayed"]["status"] == "waiting_approval"
    assert paused_replay["replayed"]["node_states"]["gate"] == "waiting"

    # Unknown run: honest 404, no fabricated projection.
    with pytest.raises(HTTPException) as excinfo:
        await replay_workflow_run("run_does_not_exist", req)
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_list_workflow_runs(monkeypatch):
    """Test GET /api/workflows/runs enumerates active runs with status."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    body = WorkflowCreateRequest(
        id="api_wf_list_runs",
        name="List Runs Test",
        graph={
            "version": 1,
            "nodes": {
                "n1": {"id": "n1", "prompt": "Task 1", "executor": DIGEST_EXECUTOR},
            },
            "edges": [],
        },
    )
    await register_workflow(body, req)
    started = await start_workflow_run("api_wf_list_runs", WorkflowRunCreateRequest(), req)
    run_id = started["run_id"]

    res = await list_workflow_runs(req)
    assert res["status"] == "ok"
    assert isinstance(res["runs"], list)
    run_entry = next((r for r in res["runs"] if r["run_id"] == run_id), None)
    assert run_entry is not None
    assert run_entry["workflow_id"] == "api_wf_list_runs"
    assert run_entry["status"] in ("pending", "running", "completed")


@pytest.mark.asyncio
async def test_perceive_dynamic_workflow(monkeypatch):
    """Test POST /api/workflows/dynamic/perceive returns perception, DAG waves, and resources."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    body = DynamicPerceiveRequest(
        prompt="/boost Build full-stack dynamic workflow with automated testing and deployment",
        context={"non_interactive": True},
    )
    res = await perceive_dynamic_workflow(body, req)

    assert res["status"] == "ok"
    assert "perception" in res
    assert "Build full-stack" in res["perception"]["raw_prompt"]
    assert "goal" in res
    assert len(res["goal"]["tasks"]) >= 1
    assert len(res["goal"]["execution_waves"]) >= 1
    assert "resources" in res
    assert "bots" in res["resources"]
    assert isinstance(res["resources"]["tools"], list)


@pytest.mark.asyncio
async def test_execute_dynamic_workflow_compile_only(monkeypatch):
    """Test POST /api/workflows/dynamic/execute with auto_execute=False only compiles & registers."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    body = DynamicExecuteRequest(
        prompt="Design dynamic caching layer for microservices",
        auto_execute=False,
    )
    res = await execute_dynamic_workflow(body, req)

    assert res["status"] == "compiled"
    assert "workflow_id" in res
    assert res["run_id"] is None
    assert res["task_count"] >= 1

    # Verify workflow definition is registered and retrievable
    wf_def = await get_workflow(res["workflow_id"], req)
    assert wf_def["id"] == res["workflow_id"]


@pytest.mark.asyncio
async def test_execute_dynamic_workflow_end_to_end(monkeypatch):
    """Test POST /api/workflows/dynamic/execute runs end-to-end dynamically to completion."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    body = DynamicExecuteRequest(
        prompt="/boost Automate API contract regression tests",
        auto_execute=True,
    )
    res = await execute_dynamic_workflow(body, req)

    assert res["status"] == "completed"
    assert res["workflow_id"].startswith("wf_")
    assert res["run_id"] is not None
    assert res["completed_count"] == res["task_count"]
    assert len(res["waves"]) >= 1


@pytest.mark.asyncio
async def test_replan_and_compensate_workflow_run(monkeypatch):
    """Test POST /api/workflows/runs/{run_id}/replan and /compensate endpoints."""
    _bind_digest_registry(monkeypatch)
    req = MagicMock()

    # Create a dynamic execution run
    body = DynamicExecuteRequest(
        prompt="Test replanning and saga compensation",
        auto_execute=True,
    )
    res = await execute_dynamic_workflow(body, req)
    run_id = res["run_id"]
    assert run_id is not None

    # Test replan on completed run (should report no_op since no failed nodes)
    replan_res = await replan_workflow_run(run_id, WorkflowReplanRequest(resume=False), req)
    assert replan_res["status"] == "no_op"
    assert "No failed nodes" in replan_res["reason"]

    # Test saga compensation rollback on completed tasks
    comp_res = await compensate_workflow_run(run_id, req)
    assert comp_res["status"] in ("compensated", "no_compensations", "no_targets_needed_compensation")
    assert isinstance(comp_res["compensated_nodes"], list)


@pytest.mark.asyncio
async def test_workflow_dag_manage_dynamic_actions(monkeypatch):
    """Test workflow_dag_manage agent tool with dynamic actions: perceive, decompose, boost."""
    import json

    _bind_digest_registry(monkeypatch)

    # 1. Action: perceive
    p_raw = await workflow_dag_manage.ainvoke({"action": "perceive", "prompt": "Deploy microservices to Kubernetes"})
    p_res = json.loads(p_raw)
    assert "raw_prompt" in p_res
    assert "primary_domain" in p_res

    # 2. Action: decompose
    d_raw = await workflow_dag_manage.ainvoke({"action": "decompose", "prompt": "Migrate database schema with zero downtime"})
    d_res = json.loads(d_raw)
    assert "tasks" in d_res
    assert len(d_res["tasks"]) >= 1
    assert len(d_res["execution_waves"]) >= 1

    # 3. Action: boost (perceive + decompose + resource assembly + live execution)
    b_raw = await workflow_dag_manage.ainvoke({"action": "boost", "prompt": "Execute end-to-end integration pipeline"})
    b_res = json.loads(b_raw)
    assert b_res["status"] == "completed"
    assert "workflow_id" in b_res
    assert "run_id" in b_res
    assert len(b_res["completed_nodes"]) >= 1
