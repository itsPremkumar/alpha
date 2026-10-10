"""DecisionRecords journaling: ``decision_recorded`` events at planning seams.

Before this change the engine journalled no DecisionRecords, so ``HandoffContract``
and the ``HANDOFF`` node both reported ``decisions: []`` even after a run made
real routing decisions. These tests pin that CONDITION and ROUTER nodes emit
``decision_recorded`` events, and that the handoff surfaces them.
"""

from __future__ import annotations

from alpha.orchestrator.loop import ExecutionKernel, HandoffContract
from alpha.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


def _collect(dwe: DynamicWorkflowEngine, events: list[dict]) -> None:
    def listener(event):
        if event.event_type == "decision_recorded":
            events.append(dict(event.payload))

    dwe.events.subscribe(listener)


def test_condition_node_emits_decision_recorded_event():
    dwe = DynamicWorkflowEngine()
    events: list[dict] = []
    _collect(dwe, events)

    node_eval = WorkflowNode(id="eval", type=NodeType.CONDITION, condition="state.score >= 0.8")
    node_pass = WorkflowNode(id="pass_step", prompt="ok", depends_on=["eval"])
    graph = WorkflowGraph(
        version=1,
        nodes={"eval": node_eval, "pass_step": node_pass},
        edges=[WorkflowEdge(source="eval", target="pass_step", condition="state.eval_result == true")],
    )
    dwe.register_definition(WorkflowDefinition(id="wf_dec", name="dec", graph=graph))
    run = dwe.start_run("wf_dec", initial_state={"score": 0.9})
    dwe.execute_step(run.run_id)

    decision_events = [e for e in events if e.get("kind") == "condition"]
    assert len(decision_events) == 1
    assert decision_events[0]["node_id"] == "eval"
    assert "state.score >= 0.8" in decision_events[0]["decision"]
    assert "-> True" in decision_events[0]["decision"]


def test_router_node_emits_decision_recorded_event():
    dwe = DynamicWorkflowEngine()
    events: list[dict] = []
    _collect(dwe, events)

    router = WorkflowNode(id="route", type=NodeType.ROUTER)
    a = WorkflowNode(id="a", prompt="a", depends_on=["route"])
    b = WorkflowNode(id="b", prompt="b", depends_on=["route"])
    graph = WorkflowGraph(
        version=1,
        nodes={"route": router, "a": a, "b": b},
        edges=[
            WorkflowEdge(source="route", target="a"),
            WorkflowEdge(source="route", target="b"),
        ],
    )
    dwe.register_definition(WorkflowDefinition(id="wf_router", name="r", graph=graph))
    run = dwe.start_run("wf_router")
    dwe.execute_step(run.run_id)

    decision_events = [e for e in events if e.get("kind") == "router"]
    assert len(decision_events) == 1
    assert decision_events[0]["node_id"] == "route"
    assert "router selected" in decision_events[0]["decision"]


def test_handoff_contract_surfaces_journalled_decisions():
    dwe = DynamicWorkflowEngine()
    node_eval = WorkflowNode(id="eval", type=NodeType.CONDITION, condition="state.score >= 0.5")
    node_next = WorkflowNode(id="next", prompt="next", depends_on=["eval"])
    graph = WorkflowGraph(
        version=1,
        nodes={"eval": node_eval, "next": node_next},
        edges=[WorkflowEdge(source="eval", target="next", condition="state.eval_result == true")],
    )
    dwe.register_definition(WorkflowDefinition(id="wf_ho", name="ho", graph=graph))
    run = dwe.start_run("wf_ho", initial_state={"score": 0.9})
    dwe.execute_step(run.run_id)  # eval runs, emits decision_recorded
    dwe.execute_step(run.run_id)  # next runs

    kernel = ExecutionKernel(engine=dwe)
    contract = kernel.handoff(run.run_id, to_mode="review", emit=False)
    assert isinstance(contract, HandoffContract)
    assert len(contract.decisions) == 1
    assert "state.score >= 0.5" in contract.decisions[0]


def test_handoff_contract_empty_when_no_decisions_recorded():
    dwe = DynamicWorkflowEngine()
    node_a = WorkflowNode(id="a", prompt="a")
    graph = WorkflowGraph(version=1, nodes={"a": node_a}, edges=[])
    dwe.register_definition(WorkflowDefinition(id="wf_none", name="none", graph=graph))
    run = dwe.start_run("wf_none")

    kernel = ExecutionKernel(engine=dwe)
    contract = kernel.handoff(run.run_id, to_mode="review", emit=False)
    assert contract.decisions == []


def test_handoff_node_surfaces_journalled_decisions():
    dwe = DynamicWorkflowEngine()
    node_eval = WorkflowNode(id="eval", type=NodeType.CONDITION, condition="state.ok == true")
    ho_node = WorkflowNode(id="ho", type=NodeType.HANDOFF, depends_on=["eval"])
    graph = WorkflowGraph(
        version=1,
        nodes={"eval": node_eval, "ho": ho_node},
        edges=[WorkflowEdge(source="eval", target="ho")],
    )
    dwe.register_definition(WorkflowDefinition(id="wf_hn", name="hn", graph=graph))
    run = dwe.start_run("wf_hn", initial_state={"ok": True})
    dwe.execute_step(run.run_id)  # eval runs
    dwe.execute_step(run.run_id)  # handoff runs

    handoff = run.state.get("ho_handoff")
    assert handoff is not None
    assert len(handoff["decisions"]) == 1
    assert "state.ok == true" in handoff["decisions"][0]
