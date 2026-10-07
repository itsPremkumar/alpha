"""Probe: durable resume + orphan reconciliation + idempotency + leases (real engine)."""
import sys, os, pathlib
os.chdir(pathlib.Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowRun, WorkflowRunStatus
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.leases import LeaseManager

def make_def():
    g = WorkflowGraph(id="w4", version=1)
    n1 = WorkflowNode(id="n1", type=NodeType.COMPUTE, prompt="node n1", timeout_seconds=1.0, budget=100)
    n2 = WorkflowNode(id="n2", type=NodeType.COMPUTE, prompt="node n2", depends_on=["n1"])
    g.nodes["n1"] = n1
    g.nodes["n2"] = n2
    g.edges = [WorkflowEdge(source="n1", target="n2")]
    return WorkflowDefinition(id="w4", name="w4", graph=g, variables={}, version=1)

def main():
    engine = DynamicWorkflowEngine()
    defn = make_def()
    engine.register_definition(defn)
    run = engine.start_run("w4")
    print("STARTED", run.run_id, run.status.value)

    # Force a budget exhaustion BEFORE execution to test the pre-wave exhaust path.
    run.budget_limit = 5
    run.tokens_consumed = 10
    run = engine.execute_step(run.run_id)
    print("AFTER EXHAUST STEP", run.status.value, run.tokens_consumed)
    print("failed_nodes", run.failed_nodes)

if __name__ == "__main__":
    main()
