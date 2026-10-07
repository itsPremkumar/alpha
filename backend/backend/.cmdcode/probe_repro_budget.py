"""Repro: budget exhaustion double-emission. Absolute-path safe."""
import os, sys
# Force project root as cwd + sys.path
os.chdir("/c/Users/PREM KUMAR/Videos/alpha")
sys.path.insert(0, "/c/Users/PREM KUMAR/Videos/alpha/backend")
import alpha  # noqa: E402
print("alpha imported from:", alpha.__file__, flush=True)

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowRun, WorkflowRunStatus
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.events import get_event_dispatcher

def make_def():
    g = WorkflowGraph(id="w_bud", version=1)
    n = WorkflowNode(id="n1", type=NodeType.COMPUTE, prompt="node n1", budget=100)
    g.nodes["n1"] = n
    return WorkflowDefinition(id="w_bud", name="w_bud", graph=g, variables={}, version=1)

def main():
    engine = DynamicWorkflowEngine()
    defn = make_def()
    engine.register_definition(defn)
    run = engine.start_run("w_bud")
    # Pre-exhaust: already in BUDGET_EXHAUSTED before any step.
    run.budget_limit = 10
    run.tokens_consumed = 20

    dispatcher = get_event_dispatcher()
    before = len(dispatcher.get_events(run.run_id))
    run = engine.execute_step(run.run_id)
    after = len(dispatcher.get_events(run.run_id))

    print("run.status:", run.status.value, flush=True)
    print("BEFORE events:", before, "AFTER events:", after, flush=True)
    budget_events = [e for e in dispatcher.get_events(run.run_id) if e.event_type == "workflow_budget_exhausted"]
    print("workflow_budget_exhausted count:", len(budget_events), flush=True)
    for e in budget_events:
        print("  payload:", e.payload, flush=True)
    assert len(budget_events) == 1, "BUG STILL PRESENT: duplicate budget event!"
    print("PASS: exactly one workflow_budget_exhausted event", flush=True)

if __name__ == "__main__":
    main()
