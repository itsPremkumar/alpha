"""Proof: external_wait_parked fold in orchestrator/replay.py.
Revert fix -> run -> expect FAIL (status=running). Restore fix -> run -> expect PASS.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(
    "C:/Users/PREM KUMAR/Videos/alpha/backend"))

from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.events import WorkflowEvent


def build_run(workflow_id="wf", graph_version=1):
    graph = WorkflowGraph(version=graph_version)
    node = WorkflowNode(id="db", type=NodeType.TOOL, executor="alpha.tool")
    graph.nodes["db"] = node
    definition = WorkflowDefinition(id="wf", name="wf", graph=graph)
    run = WorkflowRun(
        run_id="run_1",
        workflow_id="wf",
        graph_version=graph_version,
        status=WorkflowRunStatus.RUNNING,
        state={"objective": "train"},
        node_states={"db": NodeStatus.PENDING},
    )
    return run, definition, graph


def fresh_event(event_type, node_id, reason):
    return WorkflowEvent(
        workflow_run_id="run_1",
        event_type=event_type,
        timestamp="2026-01-01T00:00:00+00:00",
        payload={"node_id": node_id, "reason": reason},
    )


def replay_events(events):
    import alpha.orchestrator.replay as replay_mod
    print("REPLAY_FILE:", replay_mod.__file__)
    print("HAS_FIX_IN_MODULE:", "external_wait_parked" in inspect.getsource(replay_mod.replay_run))
    from alpha.orchestrator.replay import replay_run
    run, definition, _ = build_run()
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    _, run = replay_run(events, definition, engine)
    return run


def main():
    import alpha
    import alpha.orchestrator.replay as replay_mod
    print("ALPHA_FILE:", getattr(alpha, "__file__", None))
    print("REPLAY_FILE:", getattr(replay_mod, "__file__", None))
    print("HAS_FIX_IN_MODULE:", "external_wait_parked" in inspect.getsource(replay_mod.replay_run))
    started = WorkflowEvent(
        workflow_run_id="run_1",
        event_type="workflow_started",
        timestamp="2026-01-01T00:00:00+00:00",
        payload={"workflow_id": "wf", "owner_id": "u"},
    )
    parked = fresh_event("external_wait_parked", "db", "waiting for signal")

    run = replay_events([started, parked])
    print("status           =", run.status.value)
    print("node_states      =", run.node_states)
    print("waiting_reason   =", run.waiting_reason)
    ok = run.status is WorkflowRunStatus.WAITING_EVENT
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
