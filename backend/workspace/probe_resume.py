import threading, time, sys
sys.path.insert(0, '.')
from alpha.workflow.runtime import DynamicWorkflowEngine, NodeStatus
from alpha.workflow.models import WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowEdge
from alpha.orchestrator.replay import replay_run

def rec(node, run):
    return {"status": "completed", "output": f"ran:{node.id}", "evidence": f"ev:{node.id}", "tokens_used": 0}

eng = DynamicWorkflowEngine()
g = WorkflowGraph(version=1, nodes={"a": WorkflowNode(id="a"), "b": WorkflowNode(id="b", depends_on=["a"])}, edges=[WorkflowEdge(source="a", target="b")])
eng.register_definition(WorkflowDefinition(id="wf", name="wf", graph=g))
run = eng.start_run("wf")
eng.execute_step(run.run_id, node_runner=rec)
print("after step a:", run.node_states, run.status)

# Crash-mid-node: break the lease, don't hold engine state lock.
events = eng.events.get_events(run.run_id)
fresh = DynamicWorkflowEngine()
replayed_engine, replayed = replay_run(events, eng.get_definition("wf"), engine=fresh)
print("replayed status:", replayed.status, "node_states:", replayed.node_states)

fresh.execute_step(run.run_id, node_runner=rec)
print("after re-dispatch:", fresh.runs.get(run.run_id).node_states, fresh.runs.get(run.run_id).status)
