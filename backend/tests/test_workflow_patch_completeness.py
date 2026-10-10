"""Patch engine completeness: all 19 declared operations are supported.

Before this change, 6 of the 19 declared patch operations were refused as
"unsupported" and ``update_edge_condition`` was a deferred no-op. These tests
pin every new operation's apply + validate behaviour, and the honest refusal
paths for malformed arguments.
"""

from __future__ import annotations

from alpha.workflow.models import (
    LoopPolicy,
    NodeStatus,
    PatchOperation,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.patch import WorkflowPatchEngine


def _make_run() -> WorkflowRun:
    return WorkflowRun(
        run_id="run_test",
        workflow_id="wf_test",
        status=WorkflowRunStatus.RUNNING,
        graph_version=1,
    )


def _make_graph() -> WorkflowGraph:
    return WorkflowGraph(
        version=1,
        nodes={
            "a": WorkflowNode(id="a", type="tool"),
            "b": WorkflowNode(id="b", type="tool"),
            "c": WorkflowNode(id="c", type="tool"),
        },
        edges=[WorkflowEdge(source="a", target="b")],
    )


def _patch(run_id: str, base_version: int, ops: list[tuple[str, dict]]) -> WorkflowPatch:
    return WorkflowPatch(
        workflow_run_id=run_id,
        base_graph_version=base_version,
        reason="test",
        operations=[PatchOperation(op=op, args=args) for op, args in ops],
    )


# ------------------------------------------------------------------ fan_out


def test_fan_out_creates_edges_to_all_targets():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_out", {"source": "a", "targets": ["b", "c"]})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    targets = {e.target for e in new_graph.outgoing_edges("a")}
    assert targets == {"b", "c"}


def test_fan_out_refuses_unknown_source():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_out", {"source": "missing", "targets": ["b"]})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


def test_fan_out_refuses_unknown_target():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_out", {"source": "a", "targets": ["missing"]})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


def test_fan_out_refuses_empty_targets():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_out", {"source": "a", "targets": []})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "non-empty" in validation.reason


# ------------------------------------------------------------------- fan_in


def test_fan_in_creates_edges_from_all_sources():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_in", {"sources": ["a", "b"], "target": "c"})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    sources = {e.source for e in new_graph.incoming_edges("c")}
    assert sources == {"a", "b"}


def test_fan_in_refuses_unknown_target():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_in", {"sources": ["a"], "target": "missing"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


# ------------------------------------------------------------ set_loop_limit


def test_set_loop_limit_updates_existing_policy():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    graph.nodes["a"].loop_policy = LoopPolicy(max_iterations=5)
    patch = _patch("run_test", 1, [("set_loop_limit", {"node_id": "a", "max_iterations": 3})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    assert new_graph.nodes["a"].loop_policy.max_iterations == 3


def test_set_loop_limit_creates_policy_when_absent():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("set_loop_limit", {"node_id": "a", "max_iterations": 7})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    assert new_graph.nodes["a"].loop_policy is not None
    assert new_graph.nodes["a"].loop_policy.max_iterations == 7


def test_set_loop_limit_refuses_zero():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("set_loop_limit", {"node_id": "a", "max_iterations": 0})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert ">= 1" in validation.reason


def test_set_loop_limit_refuses_unknown_node():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("set_loop_limit", {"node_id": "missing", "max_iterations": 3})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


# --------------------------------------------------------------- skip_node


def test_skip_node_marks_node_skipped():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("skip_node", {"node_id": "a"})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    assert new_graph.nodes["a"].status == NodeStatus.SKIPPED
    assert run.node_states["a"] == NodeStatus.SKIPPED


def test_skip_node_refuses_goal_gate():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    graph.nodes["a"].type = "goal_gate"
    patch = _patch("run_test", 1, [("skip_node", {"node_id": "a"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "goal gate" in validation.reason


def test_skip_node_refuses_unknown_node():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("skip_node", {"node_id": "missing"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


# ------------------------------------------------------------ request_human


def test_request_human_sets_approval_flag():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("request_human", {"node_id": "a"})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    assert new_graph.nodes["a"].requires_approval is True
    assert new_graph.nodes["a"].approval_requested_at is not None


def test_request_human_refuses_unknown_node():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("request_human", {"node_id": "missing"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


# ------------------------------------------------------------ request_review


def test_request_review_sets_reviewer_config():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("request_review", {"node_id": "a", "reviewer": "senior"})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    assert new_graph.nodes["a"].config["reviewer"] == "senior"
    assert "review_requested_at" in new_graph.nodes["a"].config


def test_request_review_refuses_unknown_node():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("request_review", {"node_id": "missing"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


# ------------------------------------------------------ update_edge_condition


def test_update_edge_condition_applies_condition():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("update_edge_condition", {"source": "a", "target": "b", "condition": "state.score > 0.5"})])
    new_graph, validation = engine.apply(run, graph, patch)
    assert validation.allowed
    edge = next(e for e in new_graph.edges if e.source == "a" and e.target == "b")
    assert edge.condition == "state.score > 0.5"


def test_update_edge_condition_refuses_missing_edge():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("update_edge_condition", {"source": "b", "target": "a", "condition": "x"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "not found" in validation.reason


def test_update_edge_condition_refuses_missing_args():
    engine = WorkflowPatchEngine()
    run = _make_run()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("update_edge_condition", {"source": "a"})])
    _, validation = engine.apply(run, graph, patch)
    assert not validation.allowed
    assert "requires" in validation.reason


# ------------------------------------------------------- simulate (dry-run)


def test_simulate_fan_out_does_not_mutate_original():
    engine = WorkflowPatchEngine()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("fan_out", {"source": "a", "targets": ["c"]})])
    result = engine.simulate(graph, patch)
    assert result.allowed
    # Original graph is unchanged
    assert len(graph.outgoing_edges("a")) == 1


def test_simulate_skip_node_does_not_mutate_original():
    engine = WorkflowPatchEngine()
    graph = _make_graph()
    patch = _patch("run_test", 1, [("skip_node", {"node_id": "a"})])
    result = engine.simulate(graph, patch)
    assert result.allowed
    assert graph.nodes["a"].status == NodeStatus.PENDING
