"""P1 orchestrator kernel tests: claim -> dispatch -> handoff over the real DWE.

DY-R4 kernel contract covered here (see ``alpha/orchestrator/loop.py``):

- **claim**: two concurrent step dispatches on one run serialize — each node
  executes exactly once in dependency order, no lost updates;
- **claim**: two concurrent patches with the same base version serialize so the
  patch layer's optimistic-concurrency control rejects the loser with its REAL
  reason (deterministic single commit, never a silent double-commit);
- **dispatch**: an executor that raises mid-wave yields the honest
  ``node_failed`` (real traceback) and the kernel fail-closes the run to FAILED
  — the engine itself leaves it RUNNING (reported engine gap, compensated in
  the kernel without touching ``runtime.py``);
- **dispatch**: an empty registry passes the engine's exact
  ``no node_runner bound ...`` refusal straight through;
- **mode**: ``run_turn`` runs normal and bot mode through ONE kernel with the
  mapped node kinds, and refuses the non-expressible paradigm honestly;
- **handoff**: the section 13 contract is built only from real run state.
"""

from __future__ import annotations

import threading

import pytest

import alpha.orchestrator.executors as executors_module
import alpha.workflow.runtime as runtime_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, ExecutorRegistry
from alpha.orchestrator.loop import ExecutionKernel, TurnContext, run_turn
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    PatchOperation,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


@pytest.fixture()
def registry(monkeypatch):
    """Fresh EMPTY executor registry per test (module-seam swap, auto-restored)."""
    reg = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", reg)
    return reg


def _bind_digest(reg: ExecutorRegistry) -> None:
    reg.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)


def _chain_definition(workflow_id: str, executor_name: str) -> WorkflowDefinition:
    """n1 -> n2, both declaring ``executor_name``."""
    n1 = WorkflowNode(id="n1", prompt="First task", executor=executor_name)
    n2 = WorkflowNode(id="n2", prompt="Second task", executor=executor_name, depends_on=["n1"])
    graph = WorkflowGraph(version=1, nodes={"n1": n1, "n2": n2}, edges=[WorkflowEdge(source="n1", target="n2")])
    return WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph)


def _single_definition(workflow_id: str, executor_name: str, node_id: str = "only") -> WorkflowDefinition:
    node = WorkflowNode(id=node_id, prompt="One task", executor=executor_name)
    graph = WorkflowGraph(version=1, nodes={node_id: node}, edges=[])
    return WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph)


def _kernel_for(definition: WorkflowDefinition) -> ExecutionKernel:
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    return ExecutionKernel(engine=engine)


def _bare_kernel() -> ExecutionKernel:
    """Kernel over a fresh engine with no definitions (mode mapper registers its own)."""
    return ExecutionKernel(engine=DynamicWorkflowEngine())


# --------------------------------------------------------------------- claim


def test_concurrent_steps_serialize_without_lost_updates(registry):
    """Two threads dispatching one run serialize: each node runs exactly once."""
    calls: list[str] = []
    violations: list[str] = []
    calls_lock = threading.Lock()

    def counting_executor(node, run):
        with calls_lock:
            calls.append(node.id)
            for dep in node.depends_on:
                if dep not in run.completed_nodes:
                    violations.append(f"{node.id} executed before its dependency {dep}")
        return {"status": "completed", "output": f"ran:{node.id}", "evidence": f"ev:{node.id}", "tokens_used": 0}

    registry.register("test.count", counting_executor)
    kernel = _kernel_for(_chain_definition("wf_concurrent_steps", "test.count"))
    run = kernel.start_run("wf_concurrent_steps")

    barrier = threading.Barrier(2)
    snapshots: list[list[str]] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def target() -> None:
        try:
            barrier.wait(timeout=10)
            observed = kernel.dispatch(run.run_id)
            with results_lock:
                snapshots.append(list(observed.completed_nodes))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            with results_lock:
                errors.append(exc)

    threads = [threading.Thread(target=target) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(snapshots) == 2

    # Serialization proof: without the per-run claim both threads would run the
    # same ready wave and n1 would execute twice (or n2 would never unblock).
    assert calls == ["n1", "n2"], f"waves did not serialize cleanly: {calls}"
    assert violations == []

    final = kernel.engine.get_run(run.run_id)
    assert final is not None
    assert final.status == WorkflowRunStatus.COMPLETED
    assert final.completed_nodes == ["n1", "n2"]
    assert final.node_states["n1"] == NodeStatus.SUCCEEDED
    assert final.node_states["n2"] == NodeStatus.SUCCEEDED

    # No update was lost: every completion each thread observed survives, and
    # nothing was recorded twice.
    observed_union = {nid for snapshot in snapshots for nid in snapshot}
    assert observed_union == {"n1", "n2"}
    assert set(final.completed_nodes) == observed_union

    # The append-only log agrees: exactly one completion per node.
    completed_events = [e for e in kernel.engine.events.get_events(run.run_id) if e.event_type == "node_completed"]
    assert [e.payload["node_id"] for e in completed_events] == ["n1", "n2"]


def test_concurrent_patches_conflict_honestly_via_occ(registry):
    """Same-base concurrent patches: one commits, the loser gets the real OCC reason."""
    kernel = _kernel_for(_chain_definition("wf_concurrent_patches", DIGEST_EXECUTOR))
    run = kernel.start_run("wf_concurrent_patches")

    def make_patch(node_id: str) -> WorkflowPatch:
        return WorkflowPatch(
            workflow_run_id=run.run_id,
            base_graph_version=1,
            reason=f"add {node_id}",
            operations=[PatchOperation(op="add_node", args={"node": {"id": node_id, "prompt": node_id}})],
        )

    barrier = threading.Barrier(2)
    outcomes: dict[str, tuple[WorkflowGraph, object]] = {}
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def target(label: str, patch: WorkflowPatch) -> None:
        try:
            barrier.wait(timeout=10)
            graph, validation = kernel.apply_patch(run.run_id, patch)
            with results_lock:
                outcomes[label] = (graph, validation)
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            with results_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=target, args=("a", make_patch("node_a"))),
        threading.Thread(target=target, args=("b", make_patch("node_b"))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(outcomes) == 2, "both patch attempts must return a real validation result"

    allowed = {label: validation.allowed for label, (_, validation) in outcomes.items()}
    assert sorted(allowed.values()) == [False, True], f"exactly one patch may commit: {allowed}"
    loser = next(label for label, ok in allowed.items() if not ok)

    # The loser carries the patch layer's REAL optimistic-concurrency reason.
    loser_reason = outcomes[loser][1].reason
    assert loser_reason == (
        "Optimistic concurrency violation: patch base version 1 does not match current graph version 2."
    )

    # No lost update: one graph version, one recorded patch, winner's node only.
    final = kernel.engine.get_run(run.run_id)
    assert final is not None
    assert final.graph_version == 2
    assert len(final.patches_applied) == 1
    winner_label = "b" if loser == "a" else "a"
    winner_node = "node_b" if winner_label == "b" else "node_a"
    loser_node = "node_a" if winner_label == "b" else "node_b"
    current_graph = kernel.engine.graphs[f"wf_concurrent_patches:v{final.graph_version}"]
    assert winner_node in current_graph.nodes
    assert loser_node not in current_graph.nodes

    events = kernel.engine.events.get_events(run.run_id)
    committed = [e for e in events if e.event_type == "patch_committed"]
    rejected = [e for e in events if e.event_type == "patch_rejected"]
    assert len(committed) == 1
    assert len(rejected) == 1
    assert "Optimistic concurrency violation" in str(rejected[0].payload.get("reason", ""))


# ------------------------------------------------------------------ dispatch


def test_executor_exception_fails_node_and_kernel_fail_closes(registry):
    """Raising executor: real traceback in node_failed, run driven to FAILED."""

    def raising_executor(node, run):
        raise RuntimeError("ledger backend down")

    registry.register("test.raise", raising_executor)
    kernel = _kernel_for(_single_definition("wf_kernel_boom", "test.raise", node_id="boom"))
    run = kernel.start_run("wf_kernel_boom")

    result = kernel.dispatch(run.run_id)

    # Engine would leave this RUNNING (reported gap); the kernel fail-closes.
    assert result.status == WorkflowRunStatus.FAILED
    assert result.failed_nodes == ["boom"]
    assert result.completed_nodes == []

    events = kernel.engine.events.get_events(run.run_id)
    node_failed = [e for e in events if e.event_type == "node_failed"]
    assert node_failed, "the executor failure must surface as an event"
    error = str(node_failed[-1].payload.get("error", ""))
    assert "executor 'test.raise' raised RuntimeError: ledger backend down" in error
    assert "Traceback (most recent call last)" in error
    assert 'File "' in error

    fail_closed = [e for e in events if e.event_type == "workflow_failed"]
    assert fail_closed, "the kernel must emit an honest fail-closed event"
    reason = str(fail_closed[-1].payload.get("reason", ""))
    assert "boom" in reason and "node_failed" in reason

    # A terminal FAILED run is not dispatched again: no new events, state intact.
    events_before = len(kernel.engine.events.get_events(run.run_id))
    second = kernel.dispatch(run.run_id)
    assert second.status == WorkflowRunStatus.FAILED
    assert second.failed_nodes == ["boom"]
    assert len(kernel.engine.events.get_events(run.run_id)) == events_before


def test_empty_registry_passes_engine_refusal_through(registry, monkeypatch):
    """No executors and no module seam: the engine's exact refusal surfaces."""
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)
    kernel = _kernel_for(_single_definition("wf_kernel_unbound", "alpha.tool", node_id="solo"))
    run = kernel.start_run("wf_kernel_unbound")

    result = kernel.dispatch(run.run_id)

    assert result.status == WorkflowRunStatus.FAILED
    assert result.completed_nodes == []
    assert result.failed_nodes == ["solo"]

    graph = kernel.engine.graphs["wf_kernel_unbound:v1"]
    node = graph.nodes["solo"]
    reason = str(node.output.get("reason", ""))
    assert "no node_runner bound to execute node 'solo' (kind=tool)" in reason
    assert node.evidence == []

    events = kernel.engine.events.get_events(run.run_id)
    node_failed = [e for e in events if e.event_type == "node_failed"]
    assert node_failed
    event_reason = str(node_failed[-1].payload.get("reason", ""))
    assert "no node_runner bound to execute node 'solo'" in event_reason
    assert not [k for k in result.state if k.startswith("solo_")]


# --------------------------------------------------------------------- modes


def test_run_turn_normal_mode_maps_executes_and_hands_off(registry):
    _bind_digest(registry)
    kernel = _bare_kernel()

    outcome = run_turn("summarize the incident", TurnContext(mode="normal", paradigm="direct_agent"), kernel=kernel)

    assert outcome.status == "completed"
    assert outcome.mode == "normal"
    assert outcome.paradigm == "direct_agent"
    assert outcome.run_id is not None and outcome.workflow_id is not None
    assert outcome.waves >= 1
    assert outcome.failed_nodes == ()

    run = kernel.engine.get_run(outcome.run_id)
    assert run is not None
    assert run.metrics["execution_mode"] == "normal"
    graph = kernel.engine.graphs[f"{outcome.workflow_id}:v{run.graph_version}"]
    assert [n.type for n in graph.nodes.values()] == [NodeType.AGENT]  # section 13 mapping
    assert run.node_states["direct"] == NodeStatus.SUCCEEDED

    handoff = outcome.handoff
    assert handoff is not None
    assert handoff.objective == "summarize the incident"
    assert handoff.status == "completed"
    assert handoff.mode_from == "normal"
    assert handoff.completed == ["direct"]
    assert len(handoff.findings) == 1 and handoff.findings[0].startswith("direct:")
    assert handoff.files == [] and handoff.decisions == []  # honest empties, never invented
    assert handoff.remaining == []


def test_run_turn_bot_mode_shares_the_kernel_with_bot_nodes(registry):
    _bind_digest(registry)
    kernel = _bare_kernel()

    outcome = run_turn("triage the support inbox", TurnContext(mode="bot", paradigm="direct_agent"), kernel=kernel)

    assert outcome.status == "completed"
    run = kernel.engine.get_run(outcome.run_id)
    assert run is not None
    assert run.metrics["execution_mode"] == "bot"
    graph = kernel.engine.graphs[f"{outcome.workflow_id}:v{run.graph_version}"]
    assert [n.type for n in graph.nodes.values()] == [NodeType.BOT]  # same kernel, bot kind
    assert outcome.handoff is not None
    assert outcome.handoff.mode_from == "bot"
    assert outcome.handoff.mode_to == "bot"

    # The mode is journaled so replay can reconstruct it.
    events = kernel.engine.events.get_events(outcome.run_id)
    mode_events = [e for e in events if e.event_type == "run_mode_selected"]
    assert mode_events and mode_events[-1].payload["mode"] == "bot"


def test_run_turn_refuses_non_expressible_paradigm_honestly(registry):
    kernel = _bare_kernel()

    outcome = run_turn("spin up a self-organizing swarm", TurnContext(paradigm="swarm"), kernel=kernel)

    assert outcome.status == "not_expressible"
    assert outcome.run_id is None
    assert "not expressible yet" in outcome.reason
    assert "P7" in outcome.reason
    assert kernel.engine.runs == {}, "a refused paradigm must not start a run"


# ------------------------------------------------------------------- handoff


def test_handoff_contract_reports_failed_and_remaining_work(registry):
    def failing_executor(node, run):
        return {"status": "failed", "output": "db unreachable", "tokens_used": 0}

    registry.register("test.fail", failing_executor)
    kernel = _kernel_for(_chain_definition("wf_handoff_failed", "test.fail"))
    run = kernel.start_run("wf_handoff_failed", initial_state={"objective": "ship the fix"})

    final, waves = kernel.run_to_completion(run.run_id)
    assert waves == 1  # wave 1 fails -> fail-closed terminal, no further dispatch
    assert final.status == WorkflowRunStatus.FAILED

    contract = kernel.handoff(run.run_id, to_mode="bot")

    assert contract.objective == "ship the fix"
    assert contract.status == "failed"
    assert contract.mode_from == "normal"
    assert contract.mode_to == "bot"
    assert contract.completed == []
    assert contract.findings == []
    assert contract.remaining == ["n1", "n2"]  # failed node + never-run dependent
    assert contract.files == [] and contract.decisions == []  # no real artifacts exist yet

    events = kernel.engine.events.get_events(run.run_id)
    handoff_events = [e for e in events if e.event_type == "handoff_created"]
    assert handoff_events, "the handoff must be journaled"
    payload = handoff_events[-1].payload
    assert payload["remaining"] == ["n1", "n2"]
    assert payload["status"] == "failed"
