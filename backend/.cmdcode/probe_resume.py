"""Probe: durable-resume correctness. Run with the project venv python."""
import sys, os, tempfile, pathlib
os.chdir(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from alpha.workflow.models import (
    NodeStatus, NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowPatch, WorkflowRun, WorkflowRunStatus
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.events import WorkflowEvent, get_event_dispatcher

def make_def():
    g = WorkflowGraph(id="w", version=1)
    for i, nid in enumerate(["a", "b", "c"]):
        n = WorkflowNode(id=nid, type=NodeType.COMPUTE, prompt=f"node {nid}")
        n.depends_on = [g.nodes[i-1].id] if i > 0 else []
        g.nodes[nid] = n
    g.edges = [WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c")]
    return WorkflowDefinition(id="w", name="w", graph=g, variables={}, version=1)

def main():
    engine = DynamicWorkflowEngine()
    defn = make_def()
    engine.register_definition(defn)
    run = engine.start_run("w")
    print("STARTED:", run.status.value, "run.id", run.run_id)

    # Step 0 executes node 'a'
    run = engine.execute_step(run.run_id)
    print("AFTER STEP 0:", run.status.value, run.node_states)

    # Manually emit 'b' and 'c' completed to simulate a crash where the engine
    # never wrote those events, then restart with a fresh engine + replay.
    dispatcher = get_event_dispatcher()
    dispatcher.emit("node_completed", run.run_id, node_id="b", output={"ok": True})
    dispatcher.emit("node_completed", run.run_id, node_id="c", output={"ok": True})
    print("LOG LEN after manual events:", len(dispatcher.get_events(run.run_id)))

    from alpha.orchestrator.replay import replay_run
    events = dispatcher.get_events(run.run_id)
    fresh = DynamicWorkflowEngine()
    fresh, replayed = replay_run(events, defn)
    print("REPLAYED STATUS:", replayed.status.value, "node_states", replayed.node_states)
    print("REPLAYED completed_nodes", replayed.completed_nodes)

    fresh.execute_step(replayed.run_id)
    final = fresh.get_run(replayed.run_id)
    print("FRESH STEP 1:", final.status.value if final else None)

    # Check idempotency: does 'a' get re-executed (double side effect)?
    events2 = dispatcher.get_events(run.run_id)
    print("NODES COMPLETED in log:", [e.payload.get('node_id') for e in events2])

if __name__ == "__main__":
    main()
