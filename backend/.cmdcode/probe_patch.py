"""Probe: apply_patch OCC + cancel + approval resolution (real engine)."""
import sys, os, pathlib
os.chdir(pathlib.Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowRun, WorkflowRunStatus, WorkflowPatch, PatchOperation
from alpha.workflow.runtime import DynamicWorkflowEngine

def make_def():
    g = WorkflowGraph(id="w5", version=1)
    n1 = WorkflowNode(id="n1", type=NodeType.COMPUTE, prompt="node n1")
    g.nodes["n1"] = n1
    g.edges = []
    return WorkflowDefinition(id="w5", name="w5", graph=g, variables={}, version=1)

def main():
    engine = DynamicWorkflowEngine()
    defn = make_def()
    engine.register_definition(defn)
    run = engine.start_run("w5")
    print("RUN_ID", run.run_id)

    # apply_patch from another workflow id -> reject (patch targets run w5, engine executing w5)
    patch = WorkflowPatch(workflow_run_id="w5", base_graph_version=1, operations=[], proposed_by="x")
    g2, v = engine.apply_patch(run.run_id, patch)
    print("PATCH VERDICT", v.allowed, v.reason)

    # start a second workflow; apply patch aimed at the run from engine running w5
    run2 = engine.start_run("w6")
    patch2 = WorkflowPatch(workflow_run_id=run2.run_id, base_graph_version=1, operations=[], proposed_by="x")
    g3, v2 = engine.apply_patch(run2.run_id, patch2)
    print("PATCH2 ACCEPTED?", v2.allowed)

if __name__ == "__main__":
    main()
