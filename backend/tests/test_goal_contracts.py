import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier
from unittest.mock import patch

import pytest

from alpha.goals import GoalContract, GoalStore, InvalidTransitionError, PlanVersion, RecordNotFoundError, StoreCorruptionError, TaskAttempt


@pytest.fixture
def store(tmp_path):
    return GoalStore(tmp_path / "goals")


def approved_plan(store, owner_id="alice"):
    contract = store.create_contract("Deliver the objective", owner_id=owner_id)
    plan = store.create_plan(contract.id, {"steps": ["verify"]}, owner_id=owner_id)
    return contract, store.approve_plan(plan.id, owner_id=owner_id)


def test_full_lifecycle(store):
    contract = store.create_contract("Original objective", owner_id="alice")
    assert isinstance(contract, GoalContract)
    assert contract.owner_id == "alice"
    assert contract.status == "active"
    assert contract.created_at == contract.updated_at
    plan = store.create_plan(contract.id, {"steps": ["build", "verify"]}, owner_id="alice")
    assert isinstance(plan, PlanVersion)
    assert (plan.contract_id, plan.version, plan.status) == (contract.id, 1, "draft")
    plan = store.approve_plan(plan.id, owner_id="alice")
    attempt = store.create_attempt(plan.id, "Build and verify", owner_id="alice")
    assert isinstance(attempt, TaskAttempt)
    assert (attempt.plan_id, attempt.intent, attempt.status) == (plan.id, "Build and verify", "pending")
    running = store.transition_attempt(attempt.id, "running", owner_id="alice")
    assert running.updated_at >= attempt.updated_at
    done = store.transition_attempt(attempt.id, "succeeded", owner_id="alice")
    assert done.status == "succeeded"
    achieved = store.set_contract_status(contract.id, "achieved", owner_id="alice")
    assert achieved.objective == "Original objective"
    assert achieved.created_at == contract.created_at
    assert achieved.updated_at >= contract.updated_at
    assert store.list_contracts(owner_id="alice") == [achieved]
    assert store.list_plans(contract.id, owner_id="alice") == [plan]
    assert store.list_attempts(plan.id, owner_id="alice") == [done]


@pytest.mark.parametrize("source", ["pending", "running", "succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("target", ["pending", "running", "succeeded", "failed", "cancelled", "unknown"])
def test_attempt_transition_matrix(store, source, target):
    _, plan = approved_plan(store)
    attempt = store.create_attempt(plan.id, "Execute", owner_id="alice")
    if source in {"running", "succeeded", "failed"}:
        store.transition_attempt(attempt.id, "running", owner_id="alice")
    if source in {"succeeded", "failed", "cancelled"}:
        store.transition_attempt(attempt.id, source, owner_id="alice")
    allowed = {"pending": {"running", "cancelled"}, "running": {"succeeded", "failed", "cancelled"}}
    if target in allowed.get(source, set()):
        assert store.transition_attempt(attempt.id, target, owner_id="alice").status == target
    else:
        before = store.get_attempt(attempt.id, owner_id="alice")
        with pytest.raises(InvalidTransitionError):
            store.transition_attempt(attempt.id, target, owner_id="alice")
        assert store.get_attempt(attempt.id, owner_id="alice") == before


@pytest.mark.parametrize("terminal", ["achieved", "abandoned"])
def test_contract_terminal_states(store, terminal):
    contract, plan = approved_plan(store)
    store.set_contract_status(contract.id, terminal, owner_id="alice")
    for target in ["active", "achieved", "abandoned", "unknown"]:
        with pytest.raises(InvalidTransitionError):
            store.set_contract_status(contract.id, target, owner_id="alice")
    with pytest.raises(InvalidTransitionError):
        store.create_plan(contract.id, {}, owner_id="alice")
    with pytest.raises(InvalidTransitionError):
        store.create_attempt(plan.id, "Execute", owner_id="alice")


def test_supersede_chain_and_contract_independence(store):
    contract, first = approved_plan(store)
    other_contract, other_plan = approved_plan(store)
    plans = [first]
    for version in range(2, 5):
        draft = store.create_plan(contract.id, {"revision": version}, owner_id="alice")
        assert draft.version == version
        assert store.get_plan(plans[-1].id, owner_id="alice").status == "approved"
        plans.append(store.approve_plan(draft.id, owner_id="alice"))
    loaded = store.list_plans(contract.id, owner_id="alice")
    assert [p.status for p in loaded] == ["superseded"] * 3 + ["approved"]
    assert [p.version for p in loaded] == [1, 2, 3, 4]
    for plan in loaded:
        with pytest.raises(InvalidTransitionError):
            store.approve_plan(plan.id, owner_id="alice")
    assert store.list_plans(other_contract.id, owner_id="alice") == [other_plan]


def test_attempt_admission_and_old_running_result(store):
    contract = store.create_contract("Objective", owner_id="alice")
    plan = store.create_plan(contract.id, {}, owner_id="alice")
    with pytest.raises(InvalidTransitionError):
        store.create_attempt(plan.id, "Execute", owner_id="alice")
    store.approve_plan(plan.id, owner_id="alice")
    pending = store.create_attempt(plan.id, "Pending", owner_id="alice")
    running = store.create_attempt(plan.id, "Running", owner_id="alice")
    store.transition_attempt(running.id, "running", owner_id="alice")
    replacement = store.create_plan(contract.id, {}, owner_id="alice")
    store.approve_plan(replacement.id, owner_id="alice")
    with pytest.raises(InvalidTransitionError):
        store.transition_attempt(pending.id, "running", owner_id="alice")
    assert store.transition_attempt(pending.id, "cancelled", owner_id="alice").status == "cancelled"
    assert store.transition_attempt(running.id, "failed", owner_id="alice").status == "failed"
    assert store.get_contract(contract.id, owner_id="alice").status == "active"


def scoped_operations(store, contract, plan, attempt):
    return [
        lambda owner: store.get_contract(contract.id, owner_id=owner),
        lambda owner: store.set_contract_status(contract.id, "abandoned", owner_id=owner),
        lambda owner: store.create_plan(contract.id, {}, owner_id=owner),
        lambda owner: store.list_plans(contract.id, owner_id=owner),
        lambda owner: store.get_plan(plan.id, owner_id=owner),
        lambda owner: store.approve_plan(plan.id, owner_id=owner),
        lambda owner: store.create_attempt(plan.id, "Execute", owner_id=owner),
        lambda owner: store.list_attempts(plan.id, owner_id=owner),
        lambda owner: store.get_attempt(attempt.id, owner_id=owner),
        lambda owner: store.transition_attempt(attempt.id, "running", owner_id=owner),
    ]


def test_owner_isolation_all_reads_and_writes(store):
    records = []
    for owner in ["alice", "bob"]:
        contract, plan = approved_plan(store, owner)
        attempt = store.create_attempt(plan.id, "Private intent", owner_id=owner)
        records.append((owner, contract, plan, attempt))
    for owner, contract, plan, attempt in records:
        other = "bob" if owner == "alice" else "alice"
        assert store.list_contracts(owner_id=owner) == [contract]
        for operation in scoped_operations(store, contract, plan, attempt):
            with pytest.raises(RecordNotFoundError):
                operation(other)
        assert store.get_contract(contract.id, owner_id=owner) == contract
        assert store.get_plan(plan.id, owner_id=owner) == plan
        assert store.get_attempt(attempt.id, owner_id=owner) == attempt


@pytest.mark.parametrize("owner", [None, "", " ", "\t\n", 0, False])
def test_every_operation_rejects_empty_owner(store, owner):
    contract, plan = approved_plan(store)
    attempt = store.create_attempt(plan.id, "Execute", owner_id="alice")
    operations = scoped_operations(store, contract, plan, attempt)
    operations.extend([lambda owner: store.create_contract("Objective", owner_id=owner), lambda owner: store.list_contracts(owner_id=owner)])
    for operation in operations:
        with pytest.raises(ValueError, match="owner_id"):
            operation(owner)


def test_owner_is_mandatory(store):
    with pytest.raises(TypeError):
        store.list_contracts()
    with pytest.raises(TypeError):
        store.create_contract("Objective")


def test_persistence_across_instances(tmp_path):
    first = GoalStore(tmp_path)
    second = GoalStore(tmp_path)
    contract, plan = approved_plan(first)
    attempt = first.create_attempt(plan.id, "Persist me", owner_id="alice")
    assert second.get_contract(contract.id, owner_id="alice") == contract
    assert second.get_plan(plan.id, owner_id="alice") == plan
    second.transition_attempt(attempt.id, "running", owner_id="alice")
    assert first.get_attempt(attempt.id, owner_id="alice").status == "running"
    third = GoalStore(tmp_path)
    assert third.list_attempts(plan.id, owner_id="alice")[0].status == "running"


@pytest.mark.parametrize("payload", ["{", "", "null", "[]", "{}", '{"schema_version": 1, "schema_version": 1}', "\ud800"])
def test_corrupt_file_fails_closed(store, tmp_path, payload):
    contract, _ = approved_plan(store)
    path = next((tmp_path / "goals").glob("*.json"))
    path.write_bytes(payload.encode("utf-8", errors="surrogatepass"))
    original = path.read_bytes()
    for instance in [store, GoalStore(tmp_path / "goals")]:
        with pytest.raises(StoreCorruptionError):
            instance.get_contract(contract.id, owner_id="alice")
        with pytest.raises(StoreCorruptionError):
            instance.create_contract("Do not overwrite", owner_id="alice")
    assert path.read_bytes() == original
    assert store.list_contracts(owner_id="bob") == []


@pytest.mark.parametrize("damage", ["owner", "status", "reference", "version", "duplicate_approval", "missing_field", "duplicate_id"])
def test_invalid_snapshot_fails_closed(store, tmp_path, damage):
    contract, plan = approved_plan(store)
    store.create_plan(contract.id, {}, owner_id="alice")
    path = next((tmp_path / "goals").glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    if damage == "owner":
        data["contracts"][0]["owner_id"] = "bob"
    elif damage == "status":
        data["contracts"][0]["status"] = "unknown"
    elif damage == "reference":
        data["plans"][0]["contract_id"] = "missing"
    elif damage == "version":
        data["plans"][1]["version"] = 1
    elif damage == "duplicate_approval":
        data["plans"][1]["status"] = "approved"
    elif damage == "missing_field":
        del data["contracts"][0]["objective"]
    elif damage == "duplicate_id":
        data["plans"][1]["id"] = plan.id
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(StoreCorruptionError):
        store.list_contracts(owner_id="alice")
    with pytest.raises(StoreCorruptionError):
        store.create_contract("Do not overwrite", owner_id="alice")
    assert path.read_bytes() == before


def test_returned_objects_and_input_cannot_mutate_store(store):
    contract = store.create_contract("Original", owner_id="alice")
    with pytest.raises((ValueError, FrozenInstanceError, AttributeError)):
        contract.objective = "Changed"
    content = {"steps": [{"name": "Original"}]}
    plan = store.create_plan(contract.id, content, owner_id="alice")
    content["steps"][0]["name"] = "Changed input"
    plan.content["steps"][0]["name"] = "Changed output"
    loaded = store.get_plan(plan.id, owner_id="alice")
    assert loaded.content == {"steps": [{"name": "Original"}]}
    loaded.content.clear()
    assert store.get_plan(plan.id, owner_id="alice").content == {"steps": [{"name": "Original"}]}


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_atomic_failure_preserves_prior_approval(store, tmp_path, failure):
    contract, first = approved_plan(store)
    second = store.create_plan(contract.id, {}, owner_id="alice")
    path = next((tmp_path / "goals").glob("*.json"))
    before = path.read_bytes()
    with patch(f"alpha.goals.store.os.{failure}", side_effect=OSError("injected failure")):
        with pytest.raises(OSError, match="injected failure"):
            store.approve_plan(second.id, owner_id="alice")
    assert path.read_bytes() == before
    assert store.get_plan(first.id, owner_id="alice").status == "approved"
    assert store.get_plan(second.id, owner_id="alice").status == "draft"
    assert not list(path.parent.glob("*.tmp"))
    assert store.approve_plan(second.id, owner_id="alice").status == "approved"


def test_concurrent_mutations_do_not_lose_records_or_duplicate_versions(store):
    contract = store.create_contract("Concurrent", owner_id="alice")
    barrier = Barrier(8)

    def create(index):
        barrier.wait(timeout=10)
        plan = store.create_plan(contract.id, {"index": index}, owner_id="alice")
        store.approve_plan(plan.id, owner_id="alice")
        return plan

    with ThreadPoolExecutor(max_workers=8) as executor:
        plans = list(executor.map(create, range(8)))
    assert sorted(plan.version for plan in plans) == list(range(1, 9))
    loaded = store.list_plans(contract.id, owner_id="alice")
    assert len(loaded) == 8
    assert sum(plan.status == "approved" for plan in loaded) == 1
    assert sum(plan.status == "superseded" for plan in loaded) == 7


def test_concurrent_attempt_start_has_one_winner(store):
    _, plan = approved_plan(store)
    attempt = store.create_attempt(plan.id, "Only once", owner_id="alice")
    barrier = Barrier(8)

    def start(_):
        barrier.wait(timeout=10)
        try:
            store.transition_attempt(attempt.id, "running", owner_id="alice")
            return True
        except InvalidTransitionError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(start, range(8))) == 1


def test_invalid_content_does_not_write(store):
    contract = store.create_contract("Objective", owner_id="alice")
    for content in [[], {"bad": float("nan")}, {"bad": object()}]:
        with pytest.raises((ValueError, TypeError)):
            store.create_plan(contract.id, content, owner_id="alice")
    assert store.list_plans(contract.id, owner_id="alice") == []


def test_repository_export():
    from alpha.goals import GoalsRepository

    assert GoalsRepository is GoalStore


def test_atomic_write_flushes_before_replace(store, tmp_path):
    import os
    from pathlib import Path

    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(descriptor):
        events.append("fsync")
        real_fsync(descriptor)

    def replace(source, destination):
        assert events == ["fsync"]
        assert Path(source).parent == Path(destination).parent
        snapshot = json.loads(Path(source).read_text(encoding="utf-8"))
        assert snapshot["contracts"][0]["objective"] == "Durable"
        assert not Path(destination).exists()
        events.append("replace")
        real_replace(source, destination)

    with patch("alpha.goals.store.os.fsync", side_effect=fsync), patch("alpha.goals.store.os.replace", side_effect=replace):
        store.create_contract("Durable", owner_id="alice")
    assert events == ["fsync", "replace"]
    assert len(list((tmp_path / "goals").iterdir())) == 1


def test_owner_ids_are_not_paths_or_normalized(store, tmp_path):
    owners = ["../alice", "..\\bob", "ALICE", "alice", " alice ", "用户"]
    for owner in owners:
        contract = store.create_contract("Private", owner_id=owner)
        assert store.list_contracts(owner_id=owner) == [contract]
    paths = list((tmp_path / "goals").iterdir())
    assert len(paths) == len(owners)
    assert all(path.suffix == ".json" and len(path.stem) == 64 for path in paths)
    assert list(tmp_path.iterdir()) == [tmp_path / "goals"]


@pytest.mark.parametrize("terminal", ["achieved", "abandoned"])
def test_terminal_contract_blocks_approval_and_start(store, terminal):
    contract, plan = approved_plan(store)
    draft = store.create_plan(contract.id, {}, owner_id="alice")
    attempt = store.create_attempt(plan.id, "Pending", owner_id="alice")
    store.set_contract_status(contract.id, terminal, owner_id="alice")
    with pytest.raises(InvalidTransitionError):
        store.approve_plan(draft.id, owner_id="alice")
    with pytest.raises(InvalidTransitionError):
        store.transition_attempt(attempt.id, "running", owner_id="alice")
    assert store.transition_attempt(attempt.id, "cancelled", owner_id="alice").status == "cancelled"
