"""Tests for DAG Task Workflow Engine."""

import pytest

from alpha.workflow.dag_engine import (
    DAGEngine,
    UnverifiedNodeCompletionError,
    WriteScopeCollisionError,
)


def test_topological_waves():
    engine = DAGEngine()
    wf = engine.create_workflow("refactor-auth", "Refactor Auth Pipeline")

    # Node dependencies:
    # audit (wave 0) -> [rewrite-auth, rewrite-tests] (wave 1) -> verify (wave 2)
    wf.add_node("audit", "Audit current auth tokens", write_scope=["docs/auth.md"])
    wf.add_node("rewrite-auth", "Update JWT logic", depends_on=["audit"], write_scope=["src/auth/jwt.py"])
    wf.add_node("rewrite-tests", "Update test fixtures", depends_on=["audit"], write_scope=["tests/test_jwt.py"])
    wf.add_node("verify", "Run full pytest suite", depends_on=["rewrite-auth", "rewrite-tests"], write_scope=["logs/"])

    waves = wf.get_executable_waves()
    assert len(waves) == 3
    assert waves[0] == ["audit"]
    assert waves[1] == ["rewrite-auth", "rewrite-tests"]
    assert waves[2] == ["verify"]


def test_cycle_detection():
    engine = DAGEngine()
    wf = engine.create_workflow("cyclic", "Cyclic Workflow")
    wf.add_node("A", "Task A", depends_on=["B"])
    wf.add_node("B", "Task B", depends_on=["A"])

    with pytest.raises(ValueError, match="cycle detected"):
        wf.get_executable_waves()


def test_disjoint_write_scopes_collision_detected():
    engine = DAGEngine()
    wf = engine.create_workflow("collision", "Collision Workflow")
    # Both nodes in wave 0 touch the same file
    wf.add_node("node1", "Edit settings", write_scope=["config/settings.json"])
    wf.add_node("node2", "Update port", write_scope=["config/settings.json"])

    with pytest.raises(WriteScopeCollisionError):
        wf.validate_wave_write_scopes()


def test_treat_as_false_until_proven_evidence_enforcement():
    engine = DAGEngine()
    wf = engine.create_workflow("evidence-check", "Evidence Workflow")
    wf.add_node("build", "Run build script")

    # Marking completed without evidence MUST fail
    with pytest.raises(UnverifiedNodeCompletionError):
        wf.mark_completed("build")

    # Provide real test evidence
    wf.record_evidence("build", "Evidence: 12 passing tests, 0 failures, exit 0")
    wf.mark_completed("build", output="Build completed successfully.")
    assert wf.nodes["build"].status == "completed"
    assert wf.is_all_completed()
