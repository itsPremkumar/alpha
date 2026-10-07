import sys, os, pathlib
os.chdir(pathlib.Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowRun, WorkflowRunStatus
from alpha.workflow.runtime import DynamicWorkflowEngine

def make_def():
    g = WorkflowGraph(id="w3", version=1)
    for i, nid in enumerate(["n1", "n2"]):
        n = WorkflowNode(id=nid, type=NodeType.COMPUTE, prompt=f"node {nid}")
        if i > 0:
            n.depends_on = [g.nodes[i-1].id]
        g.nodes[nid] = n
    g.edges = [WorkflowEdge(source="n1", target="n2")]
    return WorkflowDefinition(id="w3", name="w3", graph=g, variables={}, version=1)

def main():
    engine = DynamicWorkflowEngine()
    defn = make_def()
    engine.register_definition(defn)
    run = engine.start_run("w3")
    print("STARTED:", run.status.value, "id", run.run_id)
    run = engine.execute_step(run.run_id)
    print("STEP1:", run.status.value, run.node_states)

if __name__ == "__main__":
    main()
