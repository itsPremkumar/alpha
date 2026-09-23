"""DAG workflow budget + human-in-the-loop (HITL) gate tests.

Covers: per-node and per-workflow token budgets ending the run with an
explicit BUDGET_EXHAUSTED status (dependents skipped, no fabricated evidence),
and human-gated nodes pausing in WAITING_APPROVAL through the existing
approval queue, then resuming on approval or failing honestly on
rejection/timeout - including a snapshot/restore resume across engine
instances.
"""

import json
from pathlib import Path

from alpha.config.token_budget_config import get_current_budget_scope
from alpha.projects.approval_queue import ApprovalQueue
from alpha.workflow.dag_engine import (
    DAGEngine,
    NodeRunResult,
    WorkflowRunResult,
)


def _runner(calls, tokens=0, evidence=True, token_by_node=None):
    """Build a node runner that records calls and asserts the node scope binding."""

    def run(node):
        calls.append(node.id)
        scope = get_current_budget_scope()
        assert scope is not None and scope.startswith("node:") and scope.endswith(f":{node.id}"), f"unexpected scope {scope!r}"
        tokens_used = tokens if token_by_node is None else token_by_node.get(node.id, 0)
        return NodeRunResult(
            status="completed",
            output=f"{node.id} done",
            evidence=f"Evidence: {node.id} tests passed, exit 0" if evidence else None,
            tokens_used=tokens_used,
        )

    return run


def _queue(tmp_path: Path, name: str = "queue") -> ApprovalQueue:
    return ApprovalQueue("dag-test", storage_path=tmp_path / f"{name}.json")


# ---------------------------------------------------------------------------
# scoped budgets in the DAG
# ---------------------------------------------------------------------------


def test_node_budget_exhaustion_explicit_status_and_dependents_skipped(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("wf", "Node Budget Workflow")
    wf.add_node("build", "Build the project", budget=1_000)
    wf.add_node("verify", "Verify the build", depends_on=["build"])

    calls: list[str] = []
    result = engine.execute("wf", _runner(calls, token_by_node={"build": 1_500, "verify": 1}))

    assert isinstance(result, WorkflowRunResult)
    assert result.status == "BUDGET_EXHAUSTED"
    assert result.consumed_tokens == 1_500
    assert result.budget_limit == 1_000
    assert result.scope_id == "node:wf:build"
    assert "1500 of 1000" in (wf.nodes["build"].output or "")

    # dependent node never ran
    assert calls == ["build"]
    assert result.node_status["build"] == "BUDGET_EXHAUSTED"
    assert result.node_status["verify"] == "SKIPPED"
    assert result.skipped == ["verify"]

    # no fabricated completion: no evidence, not completed
    assert wf.nodes["build"].evidence == []
    assert wf.nodes["build"].status != "completed"
    assert not wf.is_all_completed()
    assert json.dumps(result.to_dict())


def test_workflow_budget_exhaustion_skips_remaining_nodes(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("wb", "Workflow Budget", budget=1_000)
    wf.add_node("a", "Step A")
    wf.add_node("b", "Step B", depends_on=["a"])
    wf.add_node("c", "Step C", depends_on=["b"])

    calls: list[str] = []
    result = engine.execute("wb", _runner(calls, tokens=600))

    # a fits (600 <= 1000); b pushes spend to 1200 > 1000 -> explicit stop
    assert result.status == "BUDGET_EXHAUSTED"
    assert result.scope_id == "workflow:wb"
    assert result.consumed_tokens == 1_200
    assert result.budget_limit == 1_000
    assert calls == ["a", "b"]  # c never ran
    assert wf.nodes["a"].status == "completed"
    assert wf.nodes["b"].status == "BUDGET_EXHAUSTED"
    assert wf.nodes["c"].status == "SKIPPED"
    assert wf.nodes["c"].evidence == []
    assert result.node_status["c"] == "SKIPPED"
    assert "1200 of 1000" in (wf.nodes["b"].output or "")


def test_budget_exhausted_run_cannot_be_resumed_without_headroom(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("wd", "Stale Budget", budget=100)
    wf.add_node("a", "Step A")

    calls: list[str] = []
    first = engine.execute("wd", _runner(calls, tokens=150))
    assert first.status == "BUDGET_EXHAUSTED"

    # Re-running with no fresh headroom stops again with the honest status -
    # it must not silently re-execute or claim completion.
    second = engine.execute("wd", _runner(calls, tokens=150))
    assert second.status == "BUDGET_EXHAUSTED"
    assert calls == ["a"]  # the exhausted node was not run a second time
    assert not wf.is_all_completed()


def test_runner_without_evidence_never_completes(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("we", "Evidence Workflow")
    wf.add_node("build", "Build")
    wf.add_node("verify", "Verify", depends_on=["build"])

    calls: list[str] = []
    result = engine.execute("we", _runner(calls, evidence=False))

    assert result.status == "FAILED"
    assert "evidence" in result.detail
    assert wf.nodes["build"].status == "failed"
    assert wf.nodes["build"].evidence == []
    assert wf.nodes["verify"].status == "SKIPPED"
    assert calls == ["build"]


# ---------------------------------------------------------------------------
# human-in-the-loop gates
# ---------------------------------------------------------------------------


def test_hitl_pause_approve_resume(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("hi", "HITL Workflow")
    wf.add_node("build", "Build")
    wf.add_node("deploy", "Deploy to production", depends_on=["build"], requires_approval=True)
    wf.add_node("notify", "Notify team", depends_on=["deploy"])
    queue = _queue(tmp_path)

    calls: list[str] = []
    runner = _runner(calls, tokens=50)

    paused = engine.execute("hi", runner, approval_queue=queue)
    assert paused.status == "WAITING_APPROVAL"
    assert paused.waiting_node == "deploy"
    assert paused.approval_request_id
    assert wf.nodes["deploy"].status == "WAITING_APPROVAL"
    # gated node and its dependents did NOT run
    assert calls == ["build"]
    assert wf.nodes["notify"].status == "pending"
    assert not wf.is_all_completed()

    # the pause is stable: still waiting until a human decides
    again = engine.execute("hi", runner, approval_queue=queue)
    assert again.status == "WAITING_APPROVAL"
    assert again.approval_request_id == paused.approval_request_id
    assert calls == ["build"]

    queue.resolve_request(paused.approval_request_id, approved=True, resolved_by="human_operator")

    resumed = engine.execute("hi", runner, approval_queue=queue)
    assert resumed.status == "completed"
    assert resumed.waiting_node is None
    assert calls == ["build", "deploy", "notify"]
    assert wf.is_all_completed()
    assert all(node.evidence for node in wf.nodes.values())
    assert wf.tokens_consumed == 150  # 3 nodes x 50 tokens, nothing double-charged


def test_hitl_rejection_fails_honestly(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("hr", "Rejected Workflow")
    wf.add_node("build", "Build")
    wf.add_node("deploy", "Deploy", depends_on=["build"], requires_approval=True)
    wf.add_node("notify", "Notify", depends_on=["deploy"])
    queue = _queue(tmp_path)

    calls: list[str] = []
    runner = _runner(calls, tokens=10)

    paused = engine.execute("hr", runner, approval_queue=queue)
    assert paused.status == "WAITING_APPROVAL"
    assert calls == ["build"]

    queue.resolve_request(paused.approval_request_id, approved=False, resolved_by="human_operator", comment="not safe")

    rejected = engine.execute("hr", runner, approval_queue=queue)
    assert rejected.status == "REJECTED"
    assert rejected.node_status["deploy"] == "REJECTED"
    assert rejected.node_status["notify"] == "SKIPPED"
    # the gated node never executed and produced no evidence
    assert calls == ["build"]
    assert wf.nodes["deploy"].evidence == []
    assert not wf.is_all_completed()


def test_hitl_timeout_fails_honestly(tmp_path):
    engine = DAGEngine()
    wf = engine.create_workflow("ht", "Timeout Workflow")
    wf.add_node("deploy", "Deploy", requires_approval=True, gate_timeout_seconds=0)
    wf.add_node("verify", "Verify", depends_on=["deploy"])
    queue = _queue(tmp_path)

    calls: list[str] = []
    runner = _runner(calls, tokens=10)

    paused = engine.execute("ht", runner, approval_queue=queue)
    assert paused.status == "WAITING_APPROVAL"
    assert paused.waiting_node == "deploy"

    # nobody decided and the gate deadline already elapsed -> honest timeout
    timed_out = engine.execute("ht", runner, approval_queue=queue)
    assert timed_out.status == "APPROVAL_TIMED_OUT"
    assert timed_out.node_status["deploy"] == "APPROVAL_TIMED_OUT"
    assert timed_out.node_status["verify"] == "SKIPPED"
    assert calls == []  # the gated node never ran
    assert not wf.is_all_completed()


def test_paused_run_snapshot_restore_resumes(tmp_path):
    """A paused run's state survives an engine restart: snapshot -> restore in
    a fresh engine + the (file-backed) approval queue decision -> resume."""
    engine1 = DAGEngine()
    wf = engine1.create_workflow("hs", "Snapshot Workflow")
    wf.add_node("build", "Build")
    wf.add_node("deploy", "Deploy", depends_on=["build"], requires_approval=True)
    queue = _queue(tmp_path, name="persisted")

    calls1: list[str] = []
    paused = engine1.execute("hs", _runner(calls1, tokens=70), approval_queue=queue)
    assert paused.status == "WAITING_APPROVAL"

    snapshot = engine1.snapshot("hs")
    assert json.dumps(snapshot)  # JSON-able persisted state, no new database

    # "restart": a brand-new engine rebuilds the paused run from the snapshot
    engine2 = DAGEngine()
    restored = engine2.restore(snapshot)
    assert restored.tokens_consumed == 70
    assert restored.nodes["build"].status == "completed"
    assert restored.nodes["deploy"].status == "WAITING_APPROVAL"
    assert restored.nodes["deploy"].approval_request_id == paused.approval_request_id

    queue.resolve_request(paused.approval_request_id, approved=True, resolved_by="human_operator")

    calls2: list[str] = []
    resumed = engine2.execute("hs", _runner(calls2, tokens=70), approval_queue=queue)
    assert resumed.status == "completed"
    assert restored.is_all_completed()
    # already-completed nodes were not re-run after the restore
    assert calls2 == ["deploy"]
    assert restored.tokens_consumed == 140
