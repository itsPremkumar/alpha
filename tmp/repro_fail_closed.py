from alpha.workflow.events import WorkflowEventDispatcher
from alpha.workflow.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowGraph, WorkflowNode
from alpha.workflow.runtime import DynamicWorkflowEngine

cfg = WorkflowDefinition(
    id="wf",
    name="wf",
    graph=WorkflowGraph(
        version=1,
        nodes={
            "a": WorkflowNode(id="a", prompt="a"),
            "comp": WorkflowNode(id="comp", prompt="comp", type=NodeType.COMMAND),
        },
        edges=[WorkflowEdge(source="a", target="comp")],
    ),
)
engine = DynamicWorkflowEngine()
engine.events = WorkflowEventDispatcher(durable_sink=None)
engine.register_definition(cfg)
run = engine.start_run("wf")


def runner(node, r):
    if node.id == "a":
        return {"status": "failed", "output": "boom", "evidence": "", "tokens_used": 0}
    return {"status": "completed", "output": "compensation", "evidence": "", "tokens_used": 0}


# Mark comp as the compensation target of a
engine._run_graphs[run.run_id].nodes["a"].compensation_node_id = "comp"

engine.execute_step(run.run_id, node_runner=runner)
print("after engine step:")
print("  run.status =", run.status.value)
print("  run.failed_nodes =", run.failed_nodes)
for nid, st in run.node_states.items():
    print("  node", nid, "=", st.value)
print("  comp status in graph =", engine._run_graphs[run.run_id].nodes["comp"].status.value)
