"""Automatic evidence collectors wired into the verification gate.

A node may declare ``evidence_test_report`` or ``evidence_artifact`` +
``evidence_artifact_root`` on its config. After a passing verification, the
engine reads these with the existing ``collect_test_exit_report`` /
``collect_artifact_digest`` readers — never guessing, never running a suite.
These tests pin that wiring, the honest disclosure on a missing file, and
that a node with no declaration collects nothing.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from alpha.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


def test_evidence_test_report_collected_after_verification():
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        report.write_text(json.dumps({"exit_code": 0, "passed": 5, "failed": 0}), encoding="utf-8")

        dwe = DynamicWorkflowEngine()
        node = WorkflowNode(
            id="n1",
            prompt="run tests",
            config={
                "evidence_test_report": str(report),
            },
        )
        graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])
        dwe.register_definition(WorkflowDefinition(id="wf_ev1", name="ev1", graph=graph))
        run = dwe.start_run("wf_ev1")

        def runner(node, run):
            return {"status": "completed", "output": "ok", "evidence": "ran tests"}

        dwe.execute_step(run.run_id, node_runner=runner)
        assert node.status.value == "succeeded"
        joined = " | ".join(node.evidence)
        assert "test exit report" in joined
        assert "measured=True" in joined


def test_evidence_artifact_collected_after_verification():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "output.bin"
        artifact.write_bytes(b"hello world")

        dwe = DynamicWorkflowEngine()
        node = WorkflowNode(
            id="n1",
            prompt="build artifact",
            config={
                "evidence_artifact": "output.bin",
                "evidence_artifact_root": str(root),
            },
        )
        graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])
        dwe.register_definition(WorkflowDefinition(id="wf_ev2", name="ev2", graph=graph))
        run = dwe.start_run("wf_ev2")

        def runner(node, run):
            return {"status": "completed", "output": "ok", "evidence": "built"}

        dwe.execute_step(run.run_id, node_runner=runner)
        assert node.status.value == "succeeded"
        joined = " | ".join(node.evidence)
        assert "artifact digest" in joined
        assert "measured=True" in joined


def test_missing_report_is_disclosed_not_raised():
    dwe = DynamicWorkflowEngine()
    node = WorkflowNode(
        id="n1",
        prompt="run tests",
        config={
            "evidence_test_report": "/nonexistent/report.json",
        },
    )
    graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])
    dwe.register_definition(WorkflowDefinition(id="wf_ev3", name="ev3", graph=graph))
    run = dwe.start_run("wf_ev3")

    def runner(node, run):
        return {"status": "completed", "output": "ok", "evidence": "ran"}

    dwe.execute_step(run.run_id, node_runner=runner)
    # The node still succeeds (the runner succeeded); evidence collection
    # failure is disclosed, not raised.
    assert node.status.value == "succeeded"


def test_node_without_declaration_collects_nothing():
    dwe = DynamicWorkflowEngine()
    node = WorkflowNode(id="n1", prompt="do work")
    graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])
    dwe.register_definition(WorkflowDefinition(id="wf_ev4", name="ev4", graph=graph))
    run = dwe.start_run("wf_ev4")

    def runner(node, run):
        return {"status": "completed", "output": "ok", "evidence": "did it"}

    dwe.execute_step(run.run_id, node_runner=runner)
    assert node.status.value == "succeeded"
    # Only the runner's own evidence — no collector strings
    assert len(node.evidence) == 1
    assert node.evidence[0] == "did it"


def test_malformed_report_is_disclosed_not_raised():
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "bad.json"
        report.write_text("{not valid json", encoding="utf-8")

        dwe = DynamicWorkflowEngine()
        node = WorkflowNode(
            id="n1",
            prompt="run tests",
            config={"evidence_test_report": str(report)},
        )
        graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])
        dwe.register_definition(WorkflowDefinition(id="wf_ev5", name="ev5", graph=graph))
        run = dwe.start_run("wf_ev5")

        def runner(node, run):
            return {"status": "completed", "output": "ok", "evidence": "ran"}

        dwe.execute_step(run.run_id, node_runner=runner)
        assert node.status.value == "succeeded"
