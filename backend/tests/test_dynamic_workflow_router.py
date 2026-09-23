"""Tests for the Dynamic Workflow Gateway API router.

DY-R1 honesty contract at the REST layer: ``step_workflow_run`` executes
through the ``node_runner`` module seam — with an executor bound, completion
carries that executor's REAL evidence; with no executor bound, the step fails
honestly (empty ``completed_nodes``, the node in ``failed_nodes``, and the
real ``no node_runner bound ...`` reason visible through the events endpoint).
The engine never fabricates a completed node to satisfy an API response.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import alpha.workflow.runtime as runtime_module
from app.gateway.routers.workflows import (
    WorkflowApprovalRequest,
    WorkflowCreateRequest,
    WorkflowRunCreateRequest,
    get_workflow,
    get_workflow_engine,
    get_workflow_events,
    get_workflow_run,
    list_workflows,
    patch_workflow_run,
    register_workflow,
    resolve_approval,
    start_workflow_run,
    step_workflow_run,
)


def _runner(node, run):
    """A real executor stand-in: per-node output + evidence, nothing canned."""
    return {
        "status": "completed",
        "output": f"executed:{node.id}",
        "evidence": f"router-runner-evidence:{node.id}",
        "tokens_used": 0,
    }


@pytest.mark.asyncio
async def test_workflows_router_crud_and_execution(monkeypatch):
    req = MagicMock()

    # 1. Register workflow
    create_body = WorkflowCreateRequest(
        id="api_wf_1",
        name="API Test Workflow",
        description="Testing DWE via FastAPI router",
        graph={
            "version": 1,
            "nodes": {
                "step1": {"id": "step1", "prompt": "Initial task"},
                "step2": {"id": "step2", "prompt": "Followup task", "depends_on": ["step1"]},
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

    # 4. Start run
    run_res = await start_workflow_run("api_wf_1", WorkflowRunCreateRequest(initial_state={"env": "test"}), req)
    run_id = run_res["run_id"]
    assert run_res["status"] == "running"

    # 5. Step run — execution only happens through a bound node_runner: the
    # module seam carries a real executor for this test and the node's evidence
    # must BE that executor's output, not engine-authored text. (The unbound
    # path has its own pin in test_step_without_runner_fails_honestly below.)
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", _runner)
    step1_res = await step_workflow_run(run_id, req)
    assert "step1" in step1_res["completed_nodes"]
    assert "step2" not in step1_res["completed_nodes"]  # one wave per step: step2 depends on step1
    step1_node = get_workflow_engine().get_definition("api_wf_1").graph.nodes["step1"]
    assert step1_node.evidence == ["router-runner-evidence:step1"]

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
    assert "patch_committed" in event_types


@pytest.mark.asyncio
async def test_step_without_runner_fails_honestly(monkeypatch):
    """No executor bound at REST: nothing completes, and the real reason shows."""
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)
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

    # Honest failure: no fabricated completion, the node failed, run not completed.
    assert step_res["completed_nodes"] == []
    assert "solo1" in step_res["failed_nodes"]
    assert step_res["status"] != "completed"

    # The real refusal reason is visible through the REST events endpoint.
    events_res = await get_workflow_events(run_id, req)
    failed = [e for e in events_res["events"] if e["event_type"] == "node_failed"]
    assert failed, "the honest node failure must surface as an event"
    reasons = [str(e["payload"].get("reason", "")) for e in failed]
    assert any("no node_runner bound to execute node 'solo1'" in r for r in reasons)

    # No state artifact was invented for the unexecuted node.
    assert not [k for k in step_res["state"] if k.startswith("solo1_")]


@pytest.mark.asyncio
async def test_approval_gate_roundtrip_through_router(monkeypatch):
    """HITL REST flow: approval-gated node waits, human approves, then executes."""
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", _runner)
    req = MagicMock()

    create_body = WorkflowCreateRequest(
        id="api_wf_approval",
        name="Approval Gate Workflow",
        graph={
            "version": 1,
            "nodes": {"gate": {"id": "gate", "prompt": "Deploy to production", "requires_approval": True}},
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

    # The next step executes through the bound seam and completes.
    done_res = await step_workflow_run(run_id, req)
    assert done_res["completed_nodes"] == ["gate"]
    run = await get_workflow_run(run_id, req)
    assert run["status"] == "completed"
    gate_node = get_workflow_engine().get_definition("api_wf_approval").graph.nodes["gate"]
    assert gate_node.evidence == ["router-runner-evidence:gate"]
