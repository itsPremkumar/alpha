"""Probe: event id uniqueness + durable resume with real runner."""
import sys, os, pathlib
os.chdir(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowRun, WorkflowRunStatus
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.events import WorkflowEvent, get_event_dispatcher

def make_def():
    g = WorkflowGraph(id="w2", version=1)
    for i, nid in enumerate(["n1", "n2"]):
        n = WorkflowNode(id=nid, type=NodeType.COMPUTE, prompt=f"node {nid}", timeout_seconds=1.0)
        if i > 0:
            n.depends_on = [g.nodes[i-1].id]
        g.nodes[nid] = n
    g.edges = [WorkflowEdge(source="n1", target="n2")]
    return WorkflowDefinition(id="w2", name="w2", graph=g, variables={}, version=1)

def main():
    engine = DynamicWorkflowEngine()
    defn = make_def()
    engine.register_definition(defn)
    run = engine.start_run("w2")
    # single wave: n1, n2 both ready (edges? n2 depends on n1, but wave computes order)
    # just run
    run = engine.execute_step(run.run_id)
    print("RUN STATUS", run.status.value)
    print("n1 state", run.node_states.get("n1"), "n2 state", run.node_states.get("n2"))

    # Check idempotency dedupe for node n1 with declared idempotency key
    n1 = defn.graph.nodes["n1"]
    n1.idempotency_key = "card-charge"
    # The key should dedupe: run n1 again, it should NOT re-execute
    run2 = engine.execute_step(run.run_id)
    print("AFTER STEP 1:", run2.status.value)
    print("n1 state2", run2.node_states.get("n1"), "completed_nodes", run2.completed_nodes)
    print("n1 idempotency key:", n1.idempotency_key)

if __name__ == "__main__":
    main()
