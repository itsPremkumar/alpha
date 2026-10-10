"""Probe: does replay_run fold every non-terminal non-dispatchable park?"""
import sys

sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha")

from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    WorkflowRun,
    WorkflowRunStatus,
    NodeStatus,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
)
from alpha.orchestrator.replay import replay_run

g = WorkflowGraph(
    nodes={"n0": WorkflowNode(id="n0", type="connectivity_wait", config={})},
    edges=[],
)
defn = WorkflowDefinition(id="wf1", name="wf1", graph_type="direct_agent", graph=g)

start = WorkflowEvent(
    workflow_run_id="r1",
    event_type="workflow_started",
    timestamp="2026-10-10T00:00:00+00:00",
    payload={"workflow_id": "wf1", "state": {}, "owner_id": None},
)
park = WorkflowEvent(
    workflow_run_id="r1",
    event_type="connectivity_wait_parked",
    timestamp="2026-10-10T00:00:01+00:00",
    payload={
        "node_id": "n0",
        "target": "the network",
        "event": "connectivity.restored",
        "deadline_seconds": 30.0,
        "node_status": NodeStatus.WAITING.value,
        "reason": "Node 'n0' is waiting for connectivity to the network.",
    },
    idempotency_key=None,
)

fresh, run2 = replay_run([start, park], defn)

print("run.status =", run2.status)
print("node_states =", run2.node_states)
print("waiting_nodes =", run2.waiting_nodes)
print("waiting_reason =", repr(run2.waiting_reason))
print("metrics keys =", sorted(run.metrics.keys()))
