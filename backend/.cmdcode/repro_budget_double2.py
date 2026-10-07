import sys, os, pathlib
os.chdir(pathlib.Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode, WorkflowRun, WorkflowRunStatus
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.events import WorkflowEvent, get_event_dispatcher

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
    run.budget_limit = 10
    run.tokens_consumed = 20
    dispatcher = get_event_dispatcher()
    before = len(dispatcher.get_events(run.run_id))
    run = engine.execute_step(run.run_id)
    after = len(dispatcher.get_events(run.run_id))
    print("BEFORE EVENTS", before, "AFTER EVENTS", after, flush=True)
    print("STATUS", run.status.value, flush=True)
    budget_events = [e for e in dispatcher.get_events(run.run_id) if e.event_type == "workflow_budget_exhausted"]
    print("BUDGET_EVENT COUNT", len(budget_events), flush=True)
    for e in budget_events:
        print("  ->", e.payload, flush=True)

if __name__ == "__main__":
    main()
