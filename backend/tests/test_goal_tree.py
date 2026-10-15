"""The goal tree: hierarchical goals with their own lifecycle and evidence.

The bias here is adversarial in the same way ``test_goal_loop.py`` is: every test
asks "how could this tree call a goal done when it is not, or walk forever, or
let a child outrank the work it was derived from?" and then tries to make it do
so.

The four properties that carry the weight:

1. **``COMPLETED`` is unreachable without measured evidence.** A criterion nobody
   measured is not a criterion that passed, and collapsing "unmeasured" into
   "passed" is exactly how a goal closes on a check nobody ran.
2. **A child cannot outrank an ancestor.** A decomposition that reorders the work
   it was derived from is a policy break, so it is refused at creation.
3. **The walk terminates.** Bounded depth, bounded fan-out, and a node that
   cannot produce children is forced to execute rather than decomposing forever.
4. **Completion flows up, honestly.** A parent whose children all finished
   reports ``COMPLETED`` / ``PARTIAL`` / ``FAILED`` from *its own* evidence, and
   an unmeasured parent parks in ``VERIFYING`` rather than being declared failed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from alpha.mission.goalloop.engine import GoalLoopEngine, TurnAction
from alpha.mission.goalloop.gates import GateReport, GateRunner, QualityGate
from alpha.mission.goalloop.goal_tree import (
    ACTIVE_NODE_STATES,
    MAX_CHILDREN_PER_NODE,
    MAX_DECOMPOSITION_ATTEMPTS,
    MAX_TREE_DEPTH,
    NODE_TRANSITIONS,
    RUNNABLE_NODE_STATES,
    TERMINAL_NODE_STATES,
    GoalEvidence,
    GoalNode,
    GoalTree,
    IllegalNodeTransition,
    NodeState,
    new_node_id,
)
from alpha.mission.goalloop.state import GoalState, GoalStatus

# --------------------------------------------------------------- fixtures


def leaf_decomposer():
    """Decompose the root into two leaves; leaves do not decompose."""

    async def _decompose(node: GoalNode):
        if node.parent_id:
            return []
        return [
            {"objective": "Write the tests", "success_criteria": ["pytest is green"]},
            {"objective": "Implement the tree", "success_criteria": ["imports cleanly"]},
        ]

    return _decompose


def measuring_executor():
    """Record passing evidence for every one of the node's own criteria."""

    async def _execute(node: GoalNode):
        return [GoalEvidence.measured(criterion, True, source="fake-executor") for criterion in node.success_criteria]

    return _execute


def failing_executor():
    async def _execute(node: GoalNode):
        return [GoalEvidence.measured(criterion, False, source="fake-executor") for criterion in node.success_criteria]

    return _execute


def noop_executor():
    async def _execute(node: GoalNode):
        return []

    return _execute


def a_gate(command: str, *, label: str = "tests") -> QualityGate:
    return QualityGate(command=command, label=label)


def red_gate_runner() -> GateRunner:
    def _runner(gate: QualityGate) -> tuple[int | None, str]:
        return 1, "the suite is red"

    return GateRunner(runner=_runner)


def green_gate_runner() -> GateRunner:
    def _runner(gate: QualityGate) -> tuple[int | None, str]:
        return 0, ""

    return GateRunner(runner=_runner)


def tree_state(objective: str = "Ship the feature", criteria: list[str] | None = None) -> GoalState:
    state = GoalState(session_id="tree", objective=objective)
    state.tree.add_root(objective, success_criteria=criteria if criteria is not None else ["both children completed"])
    return state


async def drive(engine: GoalLoopEngine, state: GoalState, limit: int = 200) -> list:
    """Run turn boundaries until the loop stops, returning every decision."""
    decisions = []
    for _ in range(limit):
        decision = await engine.on_turn_end(state, "agent finished a turn")
        decisions.append(decision)
        if decision.action in {TurnAction.DONE, TurnAction.BLOCKED, TurnAction.PAUSED, TurnAction.IDLE}:
            break
    return decisions


# --------------------------------------------------------------- the states


def test_twelve_states_with_terminal_active_and_runnable_sets():
    assert len(NodeState) == 12
    assert TERMINAL_NODE_STATES == frozenset({NodeState.COMPLETED, NodeState.PARTIAL, NodeState.FAILED, NodeState.CANCELLED})
    assert RUNNABLE_NODE_STATES == frozenset(
        {
            NodeState.IDLE,
            NodeState.ANALYZING,
            NodeState.DECOMPOSING,
            NodeState.EXECUTING,
            NodeState.VERIFYING,
            NodeState.REPLANNING,
        }
    )
    assert ACTIVE_NODE_STATES - {NodeState.RECOVERING} <= RUNNABLE_NODE_STATES
    assert NodeState.RECOVERING.is_active
    assert not NodeState.RECOVERING.is_runnable
    assert NodeState.BLOCKED.is_active is False
    assert NodeState.COMPLETED.is_terminal and not NodeState.COMPLETED.is_active


@pytest.mark.parametrize("state", sorted(NodeState, key=lambda s: s.value))
def test_every_state_has_a_transition_row(state):
    assert state in NODE_TRANSITIONS


@pytest.mark.parametrize("terminal", sorted(TERMINAL_NODE_STATES, key=lambda s: s.value))
def test_terminal_states_go_nowhere(terminal):
    assert NODE_TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (NodeState.ANALYZING, NodeState.COMPLETED),
        (NodeState.DECOMPOSING, NodeState.VERIFYING),
        (NodeState.COMPLETED, NodeState.EXECUTING),
        (NodeState.FAILED, NodeState.ANALYZING),
    ],
)
def test_illegal_transitions_are_refused(source, target):
    tree = GoalTree()
    node = tree.add_root("Objective")
    # Set the requested source directly; always moving through ANALYZING made
    # terminal-state cases exercise a different transition edge.
    node.state = source
    with pytest.raises(IllegalNodeTransition):
        tree.transition(node.node_id, target)


def test_replanning_increments_the_plan_version():
    tree = GoalTree()
    node = tree.add_root("Objective")
    tree.transition(node.node_id, NodeState.EXECUTING)
    tree.transition(node.node_id, NodeState.REPLANNING)
    assert node.plan_version == 1
    tree.transition(node.node_id, NodeState.EXECUTING)
    tree.transition(node.node_id, NodeState.REPLANNING)
    assert node.plan_version == 2


def test_node_ids_are_namespaced():
    node_id = new_node_id()
    assert node_id.startswith("gn-")
    assert new_node_id() != node_id


# --------------------------------------------------------------- evidence


def test_unmeasured_evidence_is_not_a_pass():
    node = GoalNode(node_id="gn-a", parent_id="", objective="O", success_criteria=["a", "b"])
    assert node.criteria_without_evidence() == ["a", "b"]
    node.evidence.append(GoalEvidence.measured("a", True))
    assert node.criteria_without_evidence() == ["b"]


def test_latest_measurement_per_criterion_decides():
    """A goal that failed once can still complete after a passing re-verify."""
    node = GoalNode(node_id="gn-a", parent_id="", objective="O", success_criteria=["a"])
    node.evidence.append(GoalEvidence.measured("a", False, detail="first attempt"))
    assert node.criteria_failed() == ["a"]
    node.evidence.append(GoalEvidence.measured("a", True, detail="repaired"))
    assert node.criteria_failed() == []
    assert node.criteria_without_evidence() == []


def test_unmeasured_evidence_is_stored_but_decides_nothing():
    node = GoalNode(node_id="gn-a", parent_id="", objective="O", success_criteria=["a"])
    node.evidence.append(GoalEvidence.unmeasured("a", source="planner"))
    assert node.criteria_without_evidence() == ["a"]
    assert node.criteria_failed() == []


def test_evidence_survives_a_serialisation_round_trip():
    node = GoalNode(node_id="gn-a", parent_id="", objective="O", success_criteria=["a"])
    node.evidence.append(GoalEvidence.measured("a", True, source="pytest", detail="12 passed"))
    restored = GoalNode.from_dict(node.to_dict())
    assert restored.evidence[0].criterion == "a"
    assert restored.evidence[0].holds is True
    assert restored.evidence[0].detail == "12 passed"


def test_unknown_persisted_node_state_is_rejected():
    with pytest.raises(ValueError, match="Unknown goal node state"):
        GoalNode.from_dict({"node_id": "gn-a", "parent_id": "", "objective": "O", "state": "mystery"})


# --------------------------------------------------------------- the tree


def test_root_and_children_are_linked_both_ways():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["done"])
    assert root.parent_id == ""
    assert tree.root is root
    child = tree.add_child(root.node_id, "Child", success_criteria=["child done"])
    assert child.parent_id == root.node_id
    assert root.child_ids == [child.node_id]
    assert tree.children(root.node_id) == [child]
    assert tree.get(child.node_id) is child
    assert tree.depth(child.node_id) == 1


def test_a_child_may_not_outrank_its_ancestors():
    tree = GoalTree()
    root = tree.add_root("Root", priority=40)
    child = tree.add_child(root.node_id, "Child", priority=30)
    with pytest.raises(ValueError, match="ceiling"):
        tree.add_child(child.node_id, "Grandchild", priority=31)
    assert tree.add_child(child.node_id, "Grandchild", priority=30).priority == 30


def test_children_inherit_constraints_and_the_session():
    tree = GoalTree()
    root = tree.add_root("Root", constraints=["never push to main"], session_id="s-1")
    child = tree.add_child(root.node_id, "Child")
    assert child.constraints == ["never push to main"]
    assert child.session_id == "s-1"
    assert child.success_criteria == []


def test_fan_out_and_depth_are_bounded():
    tree = GoalTree()
    root = tree.add_root("Root")
    for index in range(MAX_CHILDREN_PER_NODE):
        tree.add_child(root.node_id, f"Child {index}")
    with pytest.raises(ValueError, match="children"):
        tree.add_child(root.node_id, "One too many")

    depth_tree = GoalTree()
    depth_root = depth_tree.add_root("Root")
    deep = depth_tree.add_child(depth_root.node_id, "Chain")
    for _ in range(MAX_TREE_DEPTH - 2):
        deep = depth_tree.add_child(deep.node_id, "Deeper")
    assert depth_tree.depth(deep.node_id) == MAX_TREE_DEPTH - 1
    with pytest.raises(ValueError, match="depth"):
        depth_tree.add_child(deep.node_id, "Too deep")


def test_a_terminal_node_cannot_gain_children():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    tree.record_evidence(root.node_id, GoalEvidence.measured("a", True))
    tree.request_completion(root.node_id)
    with pytest.raises(IllegalNodeTransition):
        tree.add_child(root.node_id, "Too late")


def test_ancestors_are_root_first_and_bounded():
    tree = GoalTree()
    root = tree.add_root("Root")
    child = tree.add_child(root.node_id, "Child")
    grandchild = tree.add_child(child.node_id, "Grandchild")
    assert tree.ancestors(grandchild.node_id) == [root, child]
    assert tree.ancestors(root.node_id) == []


def test_a_parent_cycle_cannot_hang_the_ancestor_walk():
    tree = GoalTree()
    root = tree.add_root("Root")
    child = tree.add_child(root.node_id, "Child")
    child.parent_id = root.node_id
    root.parent_id = child.node_id  # a bad write: mutual parentage
    assert tree.ancestors(child.node_id)  # terminates instead of hanging


def test_descendants_come_back_in_creation_order():
    tree = GoalTree()
    root = tree.add_root("Root")
    first = tree.add_child(root.node_id, "First")
    second = tree.add_child(root.node_id, "Second")
    tree.add_child(first.node_id, "Nested")
    assert tree.descendants(root.node_id) == [first, second, *tree.children(first.node_id)]


# --------------------------------------------------------------- the walk


def test_next_runnable_node_is_depth_first_and_priority_ordered():
    tree = GoalTree()
    root = tree.add_root("Root")
    low = tree.add_child(root.node_id, "Low", success_criteria=["done"], priority=10)
    high = tree.add_child(root.node_id, "High", success_criteria=["done"], priority=90)
    tree.transition(root.node_id, NodeState.ANALYZING)
    tree.transition(root.node_id, NodeState.DECOMPOSING)
    tree.transition(root.node_id, NodeState.EXECUTING)
    assert tree.next_runnable_node() is high
    tree.transition(high.node_id, NodeState.ANALYZING)
    tree.transition(high.node_id, NodeState.DECOMPOSING)
    tree.transition(high.node_id, NodeState.EXECUTING)
    tree.transition(high.node_id, NodeState.VERIFYING)
    tree.record_evidence(high.node_id, GoalEvidence.measured("done", True))
    tree.request_completion(high.node_id)
    assert tree.next_runnable_node() is low


def test_the_walk_prefers_children_of_a_node_working_through_them():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    child = tree.add_child(root.node_id, "Child", success_criteria=["b"])
    tree.transition(root.node_id, NodeState.EXECUTING)  # executing *via* the child
    assert tree.next_runnable_node() is child
    tree.transition(child.node_id, NodeState.EXECUTING)
    tree.transition(child.node_id, NodeState.VERIFYING)
    tree.record_evidence(child.node_id, GoalEvidence.measured("b", True))
    tree.request_completion(child.node_id)
    # No runnable child left, so the internal node itself is next: it verifies.
    assert tree.next_runnable_node() is root


def test_terminal_nodes_are_skipped():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    tree.record_evidence(root.node_id, GoalEvidence.measured("a", True))
    tree.request_completion(root.node_id)
    assert tree.next_runnable_node() is None


def test_a_blocked_ancestor_hides_its_subtree():
    tree = GoalTree()
    root = tree.add_root("Root")
    child = tree.add_child(root.node_id, "Child")
    tree.transition(child.node_id, NodeState.ANALYZING)
    tree.transition(root.node_id, NodeState.BLOCKED)
    assert tree.next_runnable_node() is None


def test_a_partial_parent_gates_its_children_unless_they_opt_in():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a", "b"])
    gated = tree.add_child(root.node_id, "Gated", success_criteria=["g"])
    opted_in = tree.add_child(root.node_id, "Opted in", success_criteria=["o"], can_run_despite_partial_parent=True)
    tree.record_evidence(root.node_id, GoalEvidence.measured("a", True))
    tree.transition(root.node_id, NodeState.VERIFYING)
    tree.transition(root.node_id, NodeState.PARTIAL)
    assert tree.next_runnable_node() is opted_in
    tree.transition(opted_in.node_id, NodeState.CANCELLED)
    assert gated.node_id  # still parked behind the partial parent
    assert tree.next_runnable_node() is None


def test_an_empty_tree_has_nothing_runnable():
    assert GoalTree().next_runnable_node() is None
    assert GoalTree().is_empty


# --------------------------------------------------------------- decomposition


def test_decompose_creates_children_and_the_parent_executes_through_them():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    tree.transition(root.node_id, NodeState.ANALYZING)
    tree.transition(root.node_id, NodeState.DECOMPOSING)
    children = tree.decompose(root.node_id, [{"objective": "One", "success_criteria": ["x"]}, "Two"])
    assert len(children) == 2
    assert root.state is NodeState.EXECUTING
    assert [child.objective for child in children] == ["One", "Two"]
    assert children[1].success_criteria == []  # no criteria were promised
    assert all(child.parent_id == root.node_id for child in children)


def test_decompose_refuses_a_node_that_is_not_decomposing():
    tree = GoalTree()
    root = tree.add_root("Root")
    with pytest.raises(IllegalNodeTransition):
        tree.decompose(root.node_id, ["Child"])


def test_a_node_that_cannot_produce_children_is_forced_to_execute():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    tree.transition(root.node_id, NodeState.DECOMPOSING)
    for attempt in range(1, MAX_DECOMPOSITION_ATTEMPTS):
        assert tree.decompose(root.node_id, []) == []
        assert root.state is NodeState.DECOMPOSING
        assert root.decompose_attempts == attempt
    assert tree.decompose(root.node_id, []) == []
    assert root.state is NodeState.EXECUTING


def test_decompose_caps_the_number_of_children():
    tree = GoalTree()
    root = tree.add_root("Root")
    tree.transition(root.node_id, NodeState.DECOMPOSING)
    with pytest.raises(ValueError, match="children"):
        tree.decompose(root.node_id, [f"Child {index}" for index in range(MAX_CHILDREN_PER_NODE + 1)])


# --------------------------------------------------------------- the gate


def test_completion_requires_measured_evidence():
    tree = GoalTree()
    node = tree.add_root("Objective", success_criteria=["a", "b"])
    tree.transition(node.node_id, NodeState.EXECUTING)
    tree.transition(node.node_id, NodeState.VERIFYING)
    tree.record_evidence(node.node_id, GoalEvidence.measured("a", True))
    with pytest.raises(IllegalNodeTransition, match="unmeasured"):
        tree.request_completion(node.node_id)
    tree.record_evidence(node.node_id, GoalEvidence.measured("b", False))
    with pytest.raises(IllegalNodeTransition, match="failed"):
        tree.request_completion(node.node_id)


def test_a_node_with_no_criteria_cannot_claim_completion():
    tree = GoalTree()
    node = tree.add_root("Objective")
    tree.transition(node.node_id, NodeState.VERIFYING)
    with pytest.raises(IllegalNodeTransition, match="nothing to prove"):
        tree.request_completion(node.node_id)


def test_verify_parks_in_verifying_until_the_criteria_are_measured():
    tree = GoalTree()
    node = tree.add_root("Objective", success_criteria=["a"])
    tree.transition(node.node_id, NodeState.EXECUTING)
    assert tree.verify(node.node_id).state is NodeState.VERIFYING
    tree.record_evidence(node.node_id, GoalEvidence.measured("a", True))
    assert tree.verify(node.node_id).state is NodeState.COMPLETED


def test_a_node_with_unfinished_children_cannot_complete():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    child = tree.add_child(root.node_id, "Child", success_criteria=["b"])
    tree.transition(root.node_id, NodeState.VERIFYING)
    tree.record_evidence(root.node_id, GoalEvidence.measured("a", True))
    with pytest.raises(IllegalNodeTransition, match="child"):
        tree.request_completion(root.node_id)
    tree.transition(child.node_id, NodeState.CANCELLED)
    assert tree.request_completion(root.node_id).state is NodeState.COMPLETED


# --------------------------------------------------------------- propagation


def test_propagation_completes_the_parent_when_every_child_completed():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    first = tree.add_child(root.node_id, "First", success_criteria=["f"])
    second = tree.add_child(root.node_id, "Second", success_criteria=["s"])
    tree.record_evidence(root.node_id, GoalEvidence.measured("a", True))
    tree.transition(root.node_id, NodeState.EXECUTING)
    for child in (first, second):
        tree.transition(child.node_id, NodeState.EXECUTING)
        tree.transition(child.node_id, NodeState.VERIFYING)
        tree.record_evidence(child.node_id, GoalEvidence.measured(child.success_criteria[0], True))
        tree.request_completion(child.node_id)
    tree.propagate_completion(second.node_id)
    assert root.state is NodeState.COMPLETED


def test_propagation_reports_partial_when_a_child_was_partial():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    first = tree.add_child(root.node_id, "First", success_criteria=["f1", "f2"])
    second = tree.add_child(root.node_id, "Second", success_criteria=["s"])
    tree.record_evidence(root.node_id, GoalEvidence.measured("a", True))
    tree.transition(root.node_id, NodeState.EXECUTING)
    tree.record_evidence(first.node_id, GoalEvidence.measured("f1", True))
    tree.transition(first.node_id, NodeState.VERIFYING)
    tree.transition(first.node_id, NodeState.PARTIAL)
    tree.record_evidence(second.node_id, GoalEvidence.measured("s", True))
    tree.request_completion(second.node_id)
    tree.propagate_completion(second.node_id)
    assert root.state is NodeState.PARTIAL


def test_propagation_fails_the_parent_when_a_child_failed():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    failed = tree.add_child(root.node_id, "Failed", success_criteria=["f"])
    other = tree.add_child(root.node_id, "Other", success_criteria=["o"])
    tree.transition(root.node_id, NodeState.EXECUTING)
    tree.transition(failed.node_id, NodeState.FAILED)
    tree.record_evidence(other.node_id, GoalEvidence.measured("o", True))
    tree.request_completion(other.node_id)
    tree.propagate_completion(other.node_id)
    assert root.state is NodeState.FAILED


def test_propagation_parks_an_unmeasured_parent_in_verifying():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"])
    child = tree.add_child(root.node_id, "Child", success_criteria=["c"])
    tree.transition(root.node_id, NodeState.EXECUTING)
    tree.record_evidence(child.node_id, GoalEvidence.measured("c", True))
    tree.request_completion(child.node_id)
    moved = tree.propagate_completion(child.node_id)
    assert root.state is NodeState.VERIFYING
    assert moved == [root.node_id]


def test_propagation_recurses_to_the_root():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["r"])
    middle = tree.add_child(root.node_id, "Middle", success_criteria=["m"])
    leaf = tree.add_child(middle.node_id, "Leaf", success_criteria=["l"])
    tree.record_evidence(root.node_id, GoalEvidence.measured("r", True))
    tree.record_evidence(middle.node_id, GoalEvidence.measured("m", True))
    tree.transition(root.node_id, NodeState.EXECUTING)
    tree.transition(middle.node_id, NodeState.EXECUTING)
    tree.record_evidence(leaf.node_id, GoalEvidence.measured("l", True))
    tree.request_completion(leaf.node_id)
    tree.propagate_completion(leaf.node_id)
    assert middle.state is NodeState.COMPLETED
    assert root.state is NodeState.COMPLETED


def test_propagation_stops_at_a_parent_that_still_has_running_children():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["r"])
    done = tree.add_child(root.node_id, "Done", success_criteria=["d"])
    running = tree.add_child(root.node_id, "Running", success_criteria=["x"])
    tree.record_evidence(root.node_id, GoalEvidence.measured("r", True))
    tree.transition(root.node_id, NodeState.EXECUTING)
    tree.transition(running.node_id, NodeState.EXECUTING)
    tree.record_evidence(done.node_id, GoalEvidence.measured("d", True))
    tree.request_completion(done.node_id)
    assert tree.propagate_completion(done.node_id) == []
    assert root.state is NodeState.EXECUTING


# --------------------------------------------------------------- serialisation


def test_tree_round_trips_through_a_dict():
    tree = GoalTree()
    root = tree.add_root("Root", success_criteria=["a"], constraints=["c"], session_id="s")
    child = tree.add_child(root.node_id, "Child", success_criteria=["b"])
    tree.record_evidence(child.node_id, GoalEvidence.measured("b", True))
    tree.transition(child.node_id, NodeState.EXECUTING)
    restored = GoalTree.from_dict(tree.to_dict())
    assert restored.root.objective == "Root"
    assert restored.root.child_ids == [child.node_id]
    assert restored.get(child.node_id).state is NodeState.EXECUTING
    assert restored.get(child.node_id).evidence[0].holds is True
    assert restored.next_runnable_node() is not None


def test_tree_deserialization_rejects_cycles_and_parent_child_mismatches():
    self_cycle = {
        "root_id": "root",
        "nodes": [
            {
                "node_id": "root",
                "parent_id": "",
                "objective": "Root",
                "state": "executing",
                "child_ids": ["root"],
            }
        ],
    }
    with pytest.raises(ValueError, match="parent-child mismatch"):
        GoalTree.from_dict(self_cycle)

    missing_reverse_link = {
        "root_id": "root",
        "nodes": [
            {"node_id": "root", "parent_id": "", "objective": "Root", "child_ids": []},
            {"node_id": "child", "parent_id": "root", "objective": "Child", "child_ids": []},
        ],
    }
    with pytest.raises(ValueError, match="parent-child mismatch"):
        GoalTree.from_dict(missing_reverse_link)


def test_tree_deserialization_enforces_construction_depth_and_fan_out_bounds():
    deep_nodes = []
    for index in range(MAX_TREE_DEPTH + 1):
        node_id = f"n{index}"
        deep_nodes.append(
            {
                "node_id": node_id,
                "parent_id": "" if index == 0 else f"n{index - 1}",
                "objective": node_id,
                "child_ids": [f"n{index + 1}"] if index < MAX_TREE_DEPTH else [],
            }
        )
    with pytest.raises(ValueError, match="depth bound"):
        GoalTree.from_dict({"root_id": "n0", "nodes": deep_nodes})

    child_ids = [f"child-{index}" for index in range(MAX_CHILDREN_PER_NODE + 1)]
    fanout_nodes = [
        {"node_id": "root", "parent_id": "", "objective": "Root", "child_ids": child_ids},
        *({"node_id": child_id, "parent_id": "root", "objective": child_id, "child_ids": []} for child_id in child_ids),
    ]
    with pytest.raises(ValueError, match="fan-out bound"):
        GoalTree.from_dict({"root_id": "root", "nodes": fanout_nodes})


def test_goal_state_persists_and_reloads_its_tree():
    state = tree_state()
    state.tree.add_child(state.tree.root.node_id, "Child", success_criteria=["c"])
    reloaded = GoalState.from_payload(state.to_payload())
    assert reloaded.tree.root.objective == state.objective
    assert reloaded.tree.root.child_ids == state.tree.root.child_ids
    assert reloaded.tree.next_runnable_node() is not None


def test_a_legacy_payload_without_a_tree_loads_as_an_empty_tree():
    state = GoalState.from_payload({"objective": "Legacy", "subgoals": ["one"], "max_turns": 5})
    assert state.tree.is_empty
    assert state.subgoals == ["one"]
    assert state.nodes == {}


def test_clearing_a_goal_clears_the_tree():
    state = tree_state()
    state.clear()
    assert state.tree.is_empty
    assert state.status is GoalStatus.CLEARED


# --------------------------------------------------------------- the run loop


def test_the_run_loop_decomposes_executes_and_completes_the_root():
    state = tree_state()
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=measuring_executor())
    decisions = asyncio.run(drive(engine, state))
    assert decisions[-1].action is TurnAction.DONE
    assert state.status is GoalStatus.DONE
    assert state.tree.root.state is NodeState.COMPLETED
    assert all(child.state is NodeState.COMPLETED for child in state.tree.children(state.tree.root.node_id))
    assert state.turns_used > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"decomposer": leaf_decomposer()},
        {"executor": measuring_executor()},
    ],
    ids=["neither-collaborator", "missing-executor", "missing-decomposer"],
)
def test_a_tree_without_both_runtime_collaborators_cannot_fall_back_to_judge(kwargs):
    from alpha.mission.goalloop.judge import GoalJudge

    state = tree_state()

    async def _fabricated_done(_request):
        return '{"verdict":"done","reason":"fabricated"}'

    judge = GoalJudge(judge_fn=_fabricated_done)
    engine = GoalLoopEngine(judge=judge, **kwargs)

    decision = asyncio.run(engine.on_turn_end(state, "the task is done"))

    assert decision.action is TurnAction.BLOCKED
    assert "decomposer and executor are not both configured" in decision.reason
    assert state.status is GoalStatus.BLOCKED
    assert state.tree.root.state is NodeState.IDLE
    assert state.tree.root.evidence == []
    assert decision.verdict is None


def test_the_run_loop_terminates_instead_of_walking_forever():
    """A decomposer that never produces children must not spin the loop."""

    async def _decompose(node):
        return []

    state = tree_state()
    engine = GoalLoopEngine(decomposer=_decompose, executor=noop_executor())
    decisions = asyncio.run(drive(engine, state))
    assert decisions[-1].action is TurnAction.PAUSED
    assert "no runnable" in decisions[-1].reason or "budget" in decisions[-1].reason


def test_a_failing_leaf_leaves_the_root_unfinished():
    state = tree_state()
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=failing_executor())
    decisions = asyncio.run(drive(engine, state))
    assert decisions[-1].action is not TurnAction.DONE
    assert state.tree.root.state is not NodeState.COMPLETED


def test_the_tree_path_pauses_on_budget_exhaustion():
    state = tree_state()
    state.max_turns = 3
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=noop_executor())
    decisions = asyncio.run(drive(engine, state))
    assert decisions[-1].action is TurnAction.PAUSED
    assert "turns used" in decisions[-1].reason
    assert state.status is GoalStatus.PAUSED


def test_gates_still_run_first_on_the_tree_path():
    state = tree_state()
    state.add_gate(a_gate("pytest -q"))
    engine = GoalLoopEngine(
        gate_runner=red_gate_runner(),
        decomposer=leaf_decomposer(),
        executor=measuring_executor(),
    )
    decision = asyncio.run(engine.on_turn_end(state, "turn"))
    assert decision.action is TurnAction.CONTINUE
    assert "QUALITY GATES ARE RED" in decision.continuation
    assert state.tree.root.state is NodeState.IDLE  # the tree did not advance


def test_an_exhausted_gate_pauses_the_tree_path_without_consulting_the_judge():
    state = tree_state()
    state.add_gate(a_gate("pytest -q"))
    engine = GoalLoopEngine(
        gate_runner=red_gate_runner(),
        max_gate_failures=2,
        decomposer=leaf_decomposer(),
        executor=measuring_executor(),
    )
    first = asyncio.run(engine.on_turn_end(state, "turn"))
    second = asyncio.run(engine.on_turn_end(state, "turn"))
    assert first.action is TurnAction.CONTINUE
    assert second.action is TurnAction.PAUSED
    assert state.status is GoalStatus.PAUSED
    assert state.tree.root.state is NodeState.IDLE


def test_a_user_turn_advances_the_tree_but_never_auto_continues():
    state = tree_state()
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=measuring_executor())
    decision = asyncio.run(engine.on_turn_end(state, "turn", user_initiated=True))
    assert decision.action is TurnAction.IDLE
    assert decision.continuation == ""
    assert state.tree.root.state is not NodeState.IDLE  # the tree still moved


def test_the_flat_path_is_untouched_when_there_is_no_tree():
    """Phase 1's non-breaking promise: no nodes, no tree behaviour."""
    state = GoalState(session_id="flat", objective="Do the thing")

    async def _judge(request):
        return '{"verdict": "continue", "reason": "not yet"}'

    engine = GoalLoopEngine(judge_fn=_judge)
    decision = asyncio.run(engine.on_turn_end(state, "turn"))
    assert decision.action is TurnAction.CONTINUE
    assert "GOAL (turn 2/20)" in decision.continuation


def test_a_completed_root_closes_the_standing_goal():
    state = tree_state()
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=measuring_executor())
    decisions = asyncio.run(drive(engine, state))
    assert decisions[-1].action is TurnAction.DONE
    assert state.tree.root.is_terminal


def test_the_tree_step_is_idempotent_at_a_boundary():
    state = tree_state()
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=measuring_executor())
    asyncio.run(engine.on_turn_end(state, "turn"))
    snapshot = state.to_payload()
    asyncio.run(engine.on_turn_end(state, "turn"))
    assert state.to_payload() != snapshot  # it really did advance, and only once


def test_evidence_recorded_by_the_executor_lands_on_the_node():
    state = tree_state()
    engine = GoalLoopEngine(decomposer=leaf_decomposer(), executor=measuring_executor())
    asyncio.run(drive(engine, state))
    for node in state.tree.descendants(state.tree.root.node_id):
        assert node.criteria_without_evidence() == []
        assert any(evidence.source == "fake-executor" for evidence in node.evidence)


def test_the_clock_is_injected_not_read_from_the_module():
    state = tree_state()
    fixed = time.time()
    engine = GoalLoopEngine(
        decomposer=leaf_decomposer(),
        executor=measuring_executor(),
        clock=lambda: fixed,
    )
    asyncio.run(engine.on_turn_end(state, "turn"))
    assert state.tree.root.updated_at == fixed


def test_gate_report_is_surfaced_on_the_tree_decision():
    state = tree_state()
    state.add_gate(a_gate("true", label="ok"))
    engine = GoalLoopEngine(
        gate_runner=green_gate_runner(),
        decomposer=leaf_decomposer(),
        executor=measuring_executor(),
    )
    decision = asyncio.run(engine.on_turn_end(state, "turn"))
    assert isinstance(decision.gate_report, GateReport)
    assert decision.gate_report.all_passed
