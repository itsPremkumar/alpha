"""The goal tree's link into the versioned plan store (``alpha.goals``).

The tree in ``mission/goalloop/goal_tree.py`` is the in-memory *what and why*.
The store in ``alpha/goals`` is the durable *who approved what, and what was
attempted*. Neither is useful alone: a tree with no contract history cannot
answer "which plan did this node run, and did that attempt succeed?", and a
contract with no node link cannot answer "which part of the goal does this
belong to?".

The bias here is adversarial in the same way as the rest of the suite: every
test asks "how could this link point at something that does not exist, or form
a cycle, or let a closed parent gain children?" and then tries to make it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from alpha.goals import (
    GoalStore,
    InvalidTransitionError,
    PlanVersion,
    RecordNotFoundError,
    StoreCorruptionError,
)


@pytest.fixture
def store(tmp_path):
    return GoalStore(tmp_path / "goals")


OWNER = "alice"


# --------------------------------------------------------------- the link fields


def test_a_contract_carries_its_tree_links(store):
    contract = store.create_contract("Ship the feature", owner_id=OWNER)
    # A root contract has no parent and no node until a tree links one.
    assert contract.parent_contract_id == ""
    assert contract.node_id == ""


def test_a_child_contract_is_linked_to_its_parent(store):
    root = store.create_contract("Ship the feature", owner_id=OWNER)
    child = store.create_child_contract(root.id, "Write the tests", owner_id=OWNER, node_id="gn-child1")

    assert child.parent_contract_id == root.id
    assert child.node_id == "gn-child1"
    assert child.owner_id == OWNER
    assert child.status == "active"
    assert store.get_contract(child.id, owner_id=OWNER).parent_contract_id == root.id


def test_child_contracts_are_listed_under_their_parent(store):
    root = store.create_contract("Ship the feature", owner_id=OWNER)
    first = store.create_child_contract(root.id, "First", owner_id=OWNER)
    second = store.create_child_contract(root.id, "Second", owner_id=OWNER)
    store.create_child_contract(second.id, "Nested", owner_id=OWNER)

    assert [c.objective for c in store.list_child_contracts(root.id, owner_id=OWNER)] == ["First", "Second"]
    assert [c.objective for c in store.list_child_contracts(second.id, owner_id=OWNER)] == ["Nested"]
    assert store.list_child_contracts(first.id, owner_id=OWNER) == []


def test_a_child_contract_cannot_name_a_missing_parent(store):
    with pytest.raises(RecordNotFoundError):
        store.create_child_contract("no-such-contract", "Orphan", owner_id=OWNER)


def test_a_closed_parent_cannot_gain_children(store):
    root = store.create_contract("Ship the feature", owner_id=OWNER)
    store.set_contract_status(root.id, "achieved", owner_id=OWNER)
    with pytest.raises(InvalidTransitionError):
        store.create_child_contract(root.id, "Too late", owner_id=OWNER)


def snapshot_path(store: GoalStore) -> Path:
    """The file the store reads for ``OWNER``, so a test can corrupt it on purpose."""
    return store._storage_dir / f"{hashlib.sha256(OWNER.encode('utf-8')).hexdigest()}.json"


def test_a_parent_chain_cannot_form_a_cycle(store):
    """A corrupt chain must be refused at load time, not discovered by a hang.

    The snapshot validator runs before any write, so a mutual-parentage pair is
    reported as corruption on the read that would have walked it. That is the
    fail-closed direction: the alternative is a reader that spins forever.
    """
    root = store.create_contract("Root", owner_id=OWNER)
    child = store.create_child_contract(root.id, "Child", owner_id=OWNER)
    # Hand-corrupt the chain so the two name each other as parent.
    path = snapshot_path(store)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for record in payload["contracts"]:
        if record["id"] == child.id:
            record["parent_contract_id"] = root.id
        if record["id"] == root.id:
            record["parent_contract_id"] = child.id
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StoreCorruptionError):
        store.create_child_contract(child.id, "Grandchild", owner_id=OWNER)


def test_a_dangling_parent_reference_is_refused_on_load(store):
    """The snapshot validator fails closed rather than reading a broken link."""
    root = store.create_contract("Root", owner_id=OWNER)
    store.create_child_contract(root.id, "Child", owner_id=OWNER)
    path = snapshot_path(store)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for record in payload["contracts"]:
        if record["objective"] == "Child":
            record["parent_contract_id"] = "vanished"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StoreCorruptionError):
        store.list_contracts(owner_id=OWNER)


def test_an_older_snapshot_without_the_link_fields_still_loads(tmp_path):
    """Backward compatibility: a pre-tree snapshot has no parent/node columns."""
    directory = tmp_path / "legacy"
    directory.mkdir()
    path = directory / f"{hashlib.sha256(OWNER.encode('utf-8')).hexdigest()}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner_id": OWNER,
                "contracts": [
                    {
                        "id": "c1",
                        "owner_id": OWNER,
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "objective": "Legacy objective",
                        "status": "active",
                    }
                ],
                "plans": [],
                "attempts": [],
            }
        ),
        encoding="utf-8",
    )
    loaded = GoalStore(directory).list_contracts(owner_id=OWNER)
    assert loaded[0].parent_contract_id == ""
    assert loaded[0].node_id == ""


# --------------------------------------------------------------- plan versions


def test_a_plan_version_is_linked_to_its_node_and_wave(store):
    contract = store.create_contract("Ship the feature", owner_id=OWNER)
    plan = store.create_plan(contract.id, {"waves": [1]}, owner_id=OWNER, node_id="gn-root", wave_index=2)

    assert isinstance(plan, PlanVersion)
    assert plan.node_id == "gn-root"
    assert plan.wave_index == 2
    assert store.get_plan(plan.id, owner_id=OWNER).node_id == "gn-root"


def test_plan_node_and_wave_default_to_unlinked(store):
    contract = store.create_contract("Ship the feature", owner_id=OWNER)
    plan = store.create_plan(contract.id, {}, owner_id=OWNER)
    assert plan.node_id == ""
    assert plan.wave_index == -1


def test_a_replan_is_a_new_version_on_the_same_node(store):
    contract = store.create_contract("Ship the feature", owner_id=OWNER)
    first = store.create_plan(contract.id, {"revision": 1}, owner_id=OWNER, node_id="gn-root", wave_index=1)
    second = store.create_plan(contract.id, {"revision": 2}, owner_id=OWNER, node_id="gn-root", wave_index=1)
    assert (first.version, second.version) == (1, 2)
    assert first.node_id == second.node_id == "gn-root"
    assert [p.version for p in store.list_plans(contract.id, owner_id=OWNER)] == [1, 2]


def test_plans_for_a_node_are_listable(store):
    contract = store.create_contract("Ship the feature", owner_id=OWNER)
    store.create_plan(contract.id, {"n": 1}, owner_id=OWNER, node_id="gn-a")
    store.create_plan(contract.id, {"n": 2}, owner_id=OWNER, node_id="gn-b")
    assert len(store.list_plans_for_node("gn-a", owner_id=OWNER)) == 1
    assert store.list_plans_for_node("gn-missing", owner_id=OWNER) == []
