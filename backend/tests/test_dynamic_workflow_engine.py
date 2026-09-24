"""Tests for Alpha Dynamic Workflow Engine (DWE).

Validates:
- Safe expression evaluation and AST security.
- Topological waves and bounded cycle loop policies.
- Dynamic graph patch operations and optimistic concurrency.
- Conditional branching, dynamic routing, Map/Reduce fan-out/in, Race, Quorum.
- Human-in-the-loop approval pause/resume.
- Saga compensation triggers.
- Evidence verification.
- DY-R1 honesty contract: every execution flows through the node_runner seam;
  unbound runners fail with the real reason; outputs and evidence are never
  fabricated (banned-string regression pin included).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import alpha.workflow.runtime as runtime_module
from alpha.workflow.expressions import ExpressionSecurityError, SafeExpressionEvaluator
from alpha.workflow.models import (
    LoopPolicy,
    NodeStatus,
    NodeType,
    PatchOperation,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.patch import WorkflowPatchEngine
from alpha.workflow.patch_validator import PatchValidator
from alpha.workflow.runtime import DynamicWorkflowEngine


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """Point AGENT_WORKSPACE_HOME at a per-test temp dir (the env is process-global)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def test_safe_expression_evaluator_basic():
    evaluator = SafeExpressionEvaluator()
    ctx = {"state": {"score": 0.85, "active": True, "name": "alpha"}, "metrics": {"cost": 1.5}}

    assert evaluator.evaluate("state.score > 0.8", ctx) is True
    assert evaluator.evaluate("state.score < 0.5", ctx) is False
    assert evaluator.evaluate("state.active == true && metrics.cost <= 2.0", ctx) is True
    assert evaluator.evaluate("state.name == 'beta' || state.score >= 0.8", ctx) is True


def test_safe_expression_evaluator_security_blocks():
    evaluator = SafeExpressionEvaluator()
    ctx = {"state": {}}

    with pytest.raises(ExpressionSecurityError):
        evaluator.evaluate("__import__('os').system('dir')", ctx)

    with pytest.raises(ExpressionSecurityError):
        evaluator.evaluate("state.__class__.__bases__", ctx)


def test_patch_engine_optimistic_concurrency():
    graph = WorkflowGraph(version=2, nodes={}, edges=[])
    patch = WorkflowPatch(
        workflow_run_id="run_1",
        base_graph_version=1,  # Out of date!
        reason="Test stale patch",
        operations=[],
    )
    validator = PatchValidator()
    res = validator.validate(graph, patch)
    assert res.allowed is False
    assert "Optimistic concurrency violation" in res.reason


def test_dynamic_patch_add_and_insert():
    engine = WorkflowPatchEngine()
    run = WorkflowRun(run_id="run_test", workflow_id="wf_test", graph_version=1)

    node_a = WorkflowNode(id="node_a", prompt="Step A")
    node_b = WorkflowNode(id="node_b", prompt="Step B")
    graph = WorkflowGraph(
        version=1,
        nodes={"node_a": node_a, "node_b": node_b},
        edges=[WorkflowEdge(source="node_a", target="node_b")],
    )

    # Insert a review node between A and B
    review_node = WorkflowNode(id="review_ab", prompt="Review after A before B")
    patch = WorkflowPatch(
        workflow_run_id="run_test",
        base_graph_version=1,
        reason="Insert review gate",
        operations=[
            PatchOperation(op="insert_before", args={"target_node_id": "node_b", "node": review_node.model_dump()}),
        ],
    )

    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed is True
    assert new_graph.version == 2
    assert "review_ab" in new_graph.nodes
    # Check edges redirected: node_a -> review_ab, review_ab -> node_b
    assert any(e.source == "node_a" and e.target == "review_ab" for e in new_graph.edges)
    assert any(e.source == "review_ab" and e.target == "node_b" for e in new_graph.edges)


def test_conditional_branching_execution():
    dwe = DynamicWorkflowEngine()

    node_eval = WorkflowNode(id="eval", type=NodeType.CONDITION, condition="state.score >= 0.8")
    node_pass = WorkflowNode(id="pass_step", prompt="High quality output", depends_on=["eval"])
    node_fail = WorkflowNode(id="fail_step", prompt="Low quality remediation", depends_on=["eval"])

    # Edge from eval to pass_step when true, to fail_step when false
    edge_pass = WorkflowEdge(source="eval", target="pass_step", condition="state.eval_result == true")
    edge_fail = WorkflowEdge(source="eval", target="fail_step", condition="state.eval_result == false")

    graph = WorkflowGraph(
        version=1,
        nodes={"eval": node_eval, "pass_step": node_pass, "fail_step": node_fail},
        edges=[edge_pass, edge_fail],
    )

    defn = WorkflowDefinition(id="wf_branch", name="Branching Test", graph=graph)
    dwe.register_definition(defn)

    calls: list[str] = []

    def runner(node, run):
        calls.append(node.id)
        return {"status": "completed", "output": f"ran:{node.id}", "evidence": f"evidence:{node.id}"}

    # Run with score = 0.9 (should route to pass_step)
    run = dwe.start_run("wf_branch", initial_state={"score": 0.9})
    dwe.execute_step(run.run_id)  # Executes eval (condition nodes need no executor)
    assert run.state.get("eval_result") is True

    dwe.execute_step(run.run_id, node_runner=runner)  # Executes pass_step (fail_step is filtered out by condition)
    assert "pass_step" in run.completed_nodes
    assert "fail_step" not in run.completed_nodes

    # Only the conditionally-selected node really executed through the seam
    assert calls == ["pass_step"]
    assert graph.nodes["pass_step"].evidence == ["evidence:pass_step"]


def test_map_reduce_fan_out_fan_in():
    dwe = DynamicWorkflowEngine()

    map_node = WorkflowNode(
        id="map_items",
        type=NodeType.MAP,
        config={"items_key": "data_chunks"},
    )
    reduce_node = WorkflowNode(
        id="reduce_results",
        type=NodeType.REDUCE,
        config={"input_key": "map_items_mapped"},
        depends_on=["map_items"],
    )

    graph = WorkflowGraph(
        version=1,
        nodes={"map_items": map_node, "reduce_results": reduce_node},
        edges=[WorkflowEdge(source="map_items", target="reduce_results")],
    )

    defn = WorkflowDefinition(id="wf_map_reduce", name="Map Reduce Test", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_map_reduce", initial_state={"data_chunks": ["doc1", "doc2", "doc3"]})

    calls: list[dict] = []

    def runner(node, run):
        calls.append({"id": node.id, "item": node.config.get("item")})
        item = node.config.get("item")
        if "accumulator" in node.config:
            # Reduce fold step: combine the previous accumulator with this child result
            acc = node.config["accumulator"]
            folded = item if acc is None else f"{acc}+{item}"
            return {"status": "completed", "output": folded, "evidence": f"reduce-evidence:{item}"}
        return {"status": "completed", "output": f"executed:{item}", "evidence": f"map-evidence:{item}"}

    dwe.execute_step(run.run_id, node_runner=runner)  # Map step: fan-out through the real seam
    assert run.state.get("map_items_mapped") == ["executed:doc1", "executed:doc2", "executed:doc3"]
    map_calls = [c for c in calls if c["id"].startswith("map_items[")]
    assert [c["item"] for c in map_calls] == ["doc1", "doc2", "doc3"]

    dwe.execute_step(run.run_id, node_runner=runner)  # Reduce step: folds executor-produced child results
    assert "reduce_results" in run.completed_nodes
    assert run.state.get("reduce_results_reduced") == "executed:doc1+executed:doc2+executed:doc3"
    reduce_calls = [c for c in calls if c["id"].startswith("reduce_results[")]
    assert len(reduce_calls) == 3

    # Evidence derives from the actual runner results - nothing invented
    assert graph.nodes["map_items"].evidence == [
        "map[0] item='doc1': map-evidence:doc1",
        "map[1] item='doc2': map-evidence:doc2",
        "map[2] item='doc3': map-evidence:doc3",
    ]
    assert graph.nodes["reduce_results"].evidence == [
        "reduce[0]: reduce-evidence:executed:doc1",
        "reduce[1]: reduce-evidence:executed:doc2",
        "reduce[2]: reduce-evidence:executed:doc3",
    ]


def test_reduce_refuses_non_executor_child_inputs():
    """A reduce must only fold results that were produced through the executor seam."""
    dwe = DynamicWorkflowEngine()

    reduce_node = WorkflowNode(id="seed_reduce", type=NodeType.REDUCE, config={"input_key": "seeded"})
    graph = WorkflowGraph(version=1, nodes={"seed_reduce": reduce_node}, edges=[])

    defn = WorkflowDefinition(id="wf_reduce_provenance", name="Reduce Provenance", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_reduce_provenance", initial_state={"seeded": ["a", "b"]})

    def runner(node, run):
        return {"status": "completed", "output": "x", "evidence": "e"}

    dwe.execute_step(run.run_id, node_runner=runner)

    node = graph.nodes["seed_reduce"]
    assert run.node_states["seed_reduce"] == NodeStatus.FAILED
    assert "not recorded as executor-produced" in str(node.output.get("reason", ""))
    assert "seed_reduce" not in run.completed_nodes


def test_race_and_quorum_execution():
    dwe = DynamicWorkflowEngine()

    race_node = WorkflowNode(
        id="race_step",
        type=NodeType.RACE,
        config={"candidates": ["fast_provider", "slow_provider"]},
    )
    quorum_node = WorkflowNode(
        id="quorum_step",
        type=NodeType.QUORUM,
        config={"required_votes": 2, "voters": ["agent_a", "agent_b", "agent_c"]},
        depends_on=["race_step"],
    )

    graph = WorkflowGraph(
        version=1,
        nodes={"race_step": race_node, "quorum_step": quorum_node},
        edges=[WorkflowEdge(source="race_step", target="quorum_step")],
    )

    defn = WorkflowDefinition(id="wf_consensus", name="Race and Quorum Test", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_consensus")

    calls: list[str] = []

    def runner(node, run):
        calls.append(node.id)
        if "candidate" in node.config:
            if node.config["candidate"] == "fast_provider":
                return {"status": "failed", "output": "fast_provider timed out"}
            return {"status": "completed", "output": {"served": node.config["candidate"]}, "evidence": "slow_provider responded"}
        voter = node.config.get("voter")
        vote = {"agent_a": "agree", "agent_b": "agree", "agent_c": "disagree"}[voter]
        return {"status": "completed", "output": vote, "evidence": f"vote evidence for {voter}"}

    dwe.execute_step(run.run_id, node_runner=runner)  # Race: every candidate actually run
    assert run.node_states["race_step"] == NodeStatus.SUCCEEDED
    # The first candidate genuinely failed, so the winner must be the second one -
    # not a blind pick of candidates[0].
    assert graph.nodes["race_step"].output["winner"] == "slow_provider"
    race_attempts = graph.nodes["race_step"].output["attempts"]
    assert [(a["candidate"], a["status"]) for a in race_attempts] == [
        ("fast_provider", "failed"),
        ("slow_provider", "completed"),
    ]

    dwe.execute_step(run.run_id, node_runner=runner)  # Quorum: votes come from executor results
    assert run.node_states["quorum_step"] == NodeStatus.SUCCEEDED
    quorum_output = graph.nodes["quorum_step"].output
    assert quorum_output["vote_source"] == "executor"
    assert quorum_output["votes"] == ["agree", "agree", "disagree"]
    assert quorum_output["agreed"] is True
    assert run.status == WorkflowRunStatus.COMPLETED

    # Real per-candidate/per-voter execution happened through the seam, in order
    assert calls == ["race_step[0]", "race_step[1]", "quorum_step[0]", "quorum_step[1]", "quorum_step[2]"]
    quorum_evidence = graph.nodes["quorum_step"].evidence
    assert any("vote evidence for agent_a" in e for e in quorum_evidence)


def test_quorum_config_votes_are_disclosed_as_config():
    """Config-supplied votes may pass, but must be labeled vote_source=config."""
    dwe = DynamicWorkflowEngine()

    quorum_node = WorkflowNode(
        id="cfg_quorum",
        type=NodeType.QUORUM,
        config={"required_votes": 2, "votes": ["agree", "agree", "disagree"]},
    )
    graph = WorkflowGraph(version=1, nodes={"cfg_quorum": quorum_node}, edges=[])

    defn = WorkflowDefinition(id="wf_quorum_config", name="Quorum Config Votes", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_quorum_config")
    dwe.execute_step(run.run_id)  # No runner bound: the config path needs no executor

    node = graph.nodes["cfg_quorum"]
    assert run.node_states["cfg_quorum"] == NodeStatus.SUCCEEDED
    assert node.output["vote_source"] == "config"
    assert node.output["votes"] == ["agree", "agree", "disagree"]
    # Disclosure is explicit: evidence never implies these are agent votes
    assert any("vote_source=config" in e and "not agent votes" in e for e in node.evidence)


def test_quorum_unreachable_fails_honestly():
    dwe = DynamicWorkflowEngine()

    quorum_node = WorkflowNode(
        id="vote_quorum",
        type=NodeType.QUORUM,
        config={"required_votes": 2, "voters": ["agent_a", "agent_b"]},
    )
    graph = WorkflowGraph(version=1, nodes={"vote_quorum": quorum_node}, edges=[])

    defn = WorkflowDefinition(id="wf_quorum_unreachable", name="Quorum Unreachable", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_quorum_unreachable")

    def runner(node, run):
        voter = node.config.get("voter")
        return {"status": "completed", "output": "disagree", "evidence": f"vote evidence for {voter}"}

    dwe.execute_step(run.run_id, node_runner=runner)

    node = graph.nodes["vote_quorum"]
    assert run.node_states["vote_quorum"] == NodeStatus.FAILED
    assert node.output["agreed"] is False
    assert node.output["vote_source"] == "executor"
    assert "quorum not reached: 0/2" in str(node.output["reason"])


UNBOUND_NODE_CASES = [
    pytest.param(NodeType.MAP, {"items_key": "items"}, id="map"),
    pytest.param(NodeType.REDUCE, {"input_key": "child_outputs"}, id="reduce"),
    pytest.param(NodeType.RACE, {"candidates": ["a", "b"]}, id="race"),
    pytest.param(NodeType.QUORUM, {"required_votes": 1, "voters": ["voter_1"]}, id="quorum"),
    pytest.param(NodeType.COMPENSATION, {"target_rollback_node": "failed_step"}, id="compensation"),
    pytest.param(NodeType.BOT, {"bot_name": "unregistered_bot"}, id="bot"),
    pytest.param(NodeType.TOOL, {}, id="default"),
]


@pytest.mark.parametrize("node_type,config", UNBOUND_NODE_CASES)
def test_unbound_runner_fails_honestly(node_type, config, monkeypatch):
    """Every fabrication site fails with the real reason when no runner is bound."""
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)  # seam explicitly unbound

    dwe = DynamicWorkflowEngine()
    node = WorkflowNode(id="n1", type=node_type, config=config, prompt="Run the thing")
    graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])

    defn = WorkflowDefinition(id=f"wf_unbound_{node_type.value}", name=f"Unbound {node_type.value}", graph=graph)
    dwe.register_definition(defn)
    run = dwe.start_run(f"wf_unbound_{node_type.value}", initial_state={"items": ["x"], "child_outputs": ["y"]})
    if node_type is NodeType.COMPENSATION:
        # Compensation nodes only ever execute once a saga has triggered them
        run.node_states["n1"] = NodeStatus.COMPENSATING

    dwe.execute_step(run.run_id)  # no per-call runner and no module seam

    assert run.node_states["n1"] == NodeStatus.FAILED
    assert "n1" in run.failed_nodes
    reason = str(node.output.get("reason", ""))
    assert f"no node_runner bound to execute node 'n1' (kind={node_type.value})" in reason
    assert node.evidence == []  # zero fabricated evidence
    # Gap 1 root-cause fix: the engine fail-closes the run itself in the SAME
    # wave (no saga compensation wave is pending here), so EVERY unbound
    # failure now ends terminal FAILED - including the compensation-only
    # graph, which the old completion gate used to mark COMPLETED.
    assert run.status == WorkflowRunStatus.FAILED
    fail_close_events = [
        e for e in dwe.events.get_events(run.run_id) if e.event_type == "workflow_failed"
    ]
    assert len(fail_close_events) == 1, "exactly one fail-closed event per run"
    # No fabricated state artifacts were written either
    assert not [key for key in run.state if key.startswith("n1_")]
    if node_type is NodeType.COMPENSATION:
        assert node.output.get("compensated") is False
        assert run.node_states["n1"] == NodeStatus.FAILED
        assert "n1" in run.failed_nodes


def test_compensation_executes_real_callback_when_runner_bound():
    """A triggered saga compensation runs its real callback through the seam."""
    dwe = DynamicWorkflowEngine()

    comp_node = WorkflowNode(
        id="undo_provision",
        type=NodeType.COMPENSATION,
        config={"target_rollback_node": "bad_provision"},
    )
    fail_node = WorkflowNode(
        id="bad_provision",
        prompt="Step that will fail",
        compensation_node_id="undo_provision",
    )

    graph = WorkflowGraph(
        version=1,
        nodes={"bad_provision": fail_node, "undo_provision": comp_node},
        edges=[],
    )

    defn = WorkflowDefinition(id="wf_saga_callback", name="Saga Callback", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_saga_callback")

    def failing_runner(node, run):
        return {"status": "failed", "output": "Infrastructure deployment error"}

    dwe.execute_step(run.run_id, node_runner=failing_runner)
    assert graph.nodes["undo_provision"].status == NodeStatus.COMPENSATING

    def compensating_runner(node, run):
        assert node.id == "undo_provision"
        return {"status": "completed", "output": "side-effects reverted", "evidence": "rollback receipt #42"}

    dwe.execute_step(run.run_id, node_runner=compensating_runner)

    undo = graph.nodes["undo_provision"]
    assert undo.status == NodeStatus.SUCCEEDED
    assert undo.output == {"compensated": True, "target": "bad_provision", "result": "side-effects reverted"}
    assert any("rollback receipt #42" in e for e in undo.evidence)
    assert "undo_provision" in run.completed_nodes


def test_bounded_loop_execution():
    dwe = DynamicWorkflowEngine()

    loop_node = WorkflowNode(
        id="iteration_step",
        prompt="Iterative refiner",
        loop_policy=LoopPolicy(max_iterations=3, stop_condition="state.accuracy >= 0.95"),
    )

    graph = WorkflowGraph(
        version=1,
        nodes={"iteration_step": loop_node},
    )

    defn = WorkflowDefinition(id="wf_loop", name="Loop Test", graph=graph)
    dwe.register_definition(defn)

    calls: list[str] = []

    def runner(node, run):
        calls.append(node.id)
        return {"status": "completed", "output": "refined", "evidence": "refinement evidence"}

    run = dwe.start_run("wf_loop", initial_state={"accuracy": 0.5})

    # Step 1: count=1
    dwe.execute_step(run.run_id, node_runner=runner)
    assert run.iteration_counts.get("iteration_step") == 1

    # Step 2: count=2
    dwe.execute_step(run.run_id, node_runner=runner)
    assert run.iteration_counts.get("iteration_step") == 2

    # Update accuracy to trigger early termination
    run.state["accuracy"] = 0.98
    dwe.execute_step(run.run_id, node_runner=runner)
    assert "iteration_step" in run.completed_nodes

    # Two iterations really executed; the stopped iteration ran no executor call
    assert calls == ["iteration_step", "iteration_step"]


def test_human_in_the_loop_suspension_and_resume():
    dwe = DynamicWorkflowEngine()

    deploy_node = WorkflowNode(
        id="deploy_prod",
        prompt="Deploy to production environment",
        requires_approval=True,
    )

    graph = WorkflowGraph(
        version=1,
        nodes={"deploy_prod": deploy_node},
    )

    defn = WorkflowDefinition(id="wf_deploy", name="Deploy HITL Test", graph=graph)
    dwe.register_definition(defn)

    calls: list[str] = []

    def runner(node, run):
        calls.append(node.id)
        return {"status": "completed", "output": "deployed", "evidence": "deploy log"}

    run = dwe.start_run("wf_deploy")
    dwe.execute_step(run.run_id, node_runner=runner)

    # Should be suspended waiting for approval, with no execution yet
    assert run.status == WorkflowRunStatus.WAITING_APPROVAL
    assert run.node_states["deploy_prod"] == NodeStatus.WAITING
    assert calls == []

    # Human approves
    dwe.resolve_approval(run.run_id, "deploy_prod", approved=True, feedback="Approved by SRE lead")
    assert run.status == WorkflowRunStatus.RUNNING

    # Step executes and completes through the seam
    dwe.execute_step(run.run_id, node_runner=runner)
    assert run.status == WorkflowRunStatus.COMPLETED
    assert "deploy_prod" in run.completed_nodes
    assert calls == ["deploy_prod"]


def test_saga_compensation_on_failure():
    dwe = DynamicWorkflowEngine()

    comp_node = WorkflowNode(
        id="undo_provision",
        type=NodeType.COMPENSATION,
        config={"target_rollback_node": "bad_provision"},
    )
    fail_node = WorkflowNode(
        id="bad_provision",
        prompt="Step that will fail",
        compensation_node_id="undo_provision",
    )

    graph = WorkflowGraph(
        version=1,
        nodes={"bad_provision": fail_node, "undo_provision": comp_node},
    )

    defn = WorkflowDefinition(id="wf_saga", name="Saga Test", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_saga")

    # Runner that deliberately fails
    def faulty_runner(node, run):
        return {"status": "failed", "output": "Infrastructure deployment error"}

    dwe.execute_step(run.run_id, node_runner=faulty_runner)

    assert run.node_states["bad_provision"] == NodeStatus.FAILED
    assert graph.nodes["undo_provision"].status == NodeStatus.COMPENSATING


def test_completion_without_evidence_is_refused():
    """A runner reporting success with no evidence must not complete the node."""
    dwe = DynamicWorkflowEngine()

    node = WorkflowNode(id="trust_me", prompt="Claim success without proof")
    graph = WorkflowGraph(version=1, nodes={"trust_me": node}, edges=[])

    defn = WorkflowDefinition(id="wf_evidence_gate", name="Evidence Gate", graph=graph)
    dwe.register_definition(defn)

    run = dwe.start_run("wf_evidence_gate")

    def runner(node, run):
        return {"status": "completed", "output": "done", "evidence": None}

    dwe.execute_step(run.run_id, node_runner=runner)

    assert run.node_states["trust_me"] == NodeStatus.FAILED
    assert "trust_me" not in run.completed_nodes
    failed_events = [e for e in dwe.events.get_events(run.run_id) if e.event_type == "node_failed"]
    assert any("completed without evidence" in str(e.payload.get("reason", "")) for e in failed_events)
    assert all("error" not in e.payload for e in failed_events), "gap 2: unified reason key"


def test_runtime_source_has_no_fabricated_execution_strings():
    """Static regression pin: banned fabrication strings are absent from runtime.py."""
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    banned = (
        "Processed item",
        "Default verification evidence",
        "verified output with capability epoch",
        "Executed node",
        "Executed by bot",
    )
    for phrase in banned:
        assert phrase not in source, f"banned fabrication string present in runtime.py: {phrase!r}"
