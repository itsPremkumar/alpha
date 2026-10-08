"""APEX control changes must survive a restart before they are acknowledged."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from alpha.apex.store import ApexSessionState, ApexStore


@pytest.fixture()
def store(tmp_path: Path) -> ApexStore:
    return ApexStore(tmp_path / "sessions.json")


def _session(store: ApexStore):
    return store.create(owner="operator", objective="finish the job", profile="autonomous", contract_digest="contract")


@pytest.mark.parametrize("operation", ["create", "update", "pause", "constraint", "request", "approve", "reject", "delete"])
def test_failed_snapshot_rolls_back_control_changes(store: ApexStore, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="verification pending")
    approval = store.request_approval(session.session_id, note="verification pending") if operation in ("approve", "reject") else None
    before = session.to_dict()
    rows_before = [row.to_dict() for row in store.list()]
    disk_before = store.storage_path.read_bytes()
    events_before = store.events_path.read_bytes()

    def disk_full(*_args, **_kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr("alpha.apex.store.os.replace", disk_full)
    with pytest.raises(OSError, match="persist"):
        if operation == "create":
            _session(store)
        elif operation == "update":
            store.update(session.session_id, objective="lost edit")
        elif operation == "pause":
            store.set_state(session.session_id, ApexSessionState.PAUSED)
        elif operation == "constraint":
            store.record_constraint(session.session_id, "use local models")
        elif operation == "request":
            store.request_approval(session.session_id, note="please review")
        elif operation in ("approve", "reject"):
            store.decide_approval(approval.approval_id, verdict="approved" if operation == "approve" else "rejected", operator="admin")
        else:
            store.delete(session.session_id)

    assert session.to_dict() == before, "existing readers must not retain an uncommitted mutation"
    assert [row.to_dict() for row in store.list()] == rows_before
    assert store.storage_path.read_bytes() == disk_before
    assert store.events_path.read_bytes() == events_before, "a failed write must not publish a successful control event"
    assert not list(store.storage_path.parent.glob("*.tmp"))


def test_approval_and_resume_commit_in_one_snapshot(store: ApexStore, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="verification pending")
    approval = store.request_approval(session.session_id, note="verification pending")
    snapshots = []
    save = store._save

    def record_save():
        saved = save()
        snapshots.append(json.loads(store.storage_path.read_text(encoding="utf-8")))
        return saved

    monkeypatch.setattr(store, "_save", record_save)
    result = store.decide_approval(approval.approval_id, verdict="approved", operator="admin")

    assert result is not None
    assert len(snapshots) == 1, "there must be no crash window between consuming the approval and resuming"
    persisted = snapshots[0]["sessions"][0]
    assert persisted["approvals"][0]["status"] == "approved"
    assert persisted["state"] == "active"
    assert persisted["blocked_reason"] == ""
    restarted = ApexStore(store.storage_path)
    assert restarted.get(session.session_id).state is ApexSessionState.ACTIVE
    assert restarted.pending_approval(session.session_id) is None


def test_corrupt_store_cannot_be_replaced_by_a_new_session(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("{corrupt state", encoding="utf-8")
    store = ApexStore(path)
    with pytest.raises(OSError, match="unreadable"):
        _session(store)
    assert path.read_text(encoding="utf-8") == "{corrupt state"
    assert store.list() == []
    assert not store.events_path.exists()


def test_write_can_be_retried_after_storage_recovers(store: ApexStore, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(store)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_save", lambda: False)
        with pytest.raises(OSError):
            store.set_state(session.session_id, ApexSessionState.PAUSED)
    updated = store.set_state(session.session_id, ApexSessionState.PAUSED)
    assert updated.state is ApexSessionState.PAUSED
    assert ApexStore(store.storage_path).get(session.session_id).state is ApexSessionState.PAUSED


def test_tool_call_budget_reservations_are_atomic_and_durable(store: ApexStore) -> None:
    session = _session(store)

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(lambda _: store.consume_tool_call(session.session_id, limit=3), range(20)))

    admitted = [used for allowed, used in reservations if allowed]
    refused = [used for allowed, used in reservations if not allowed]
    assert sorted(admitted) == [1, 2, 3]
    assert refused == [3] * 17
    restarted = ApexStore(store.storage_path)
    assert restarted.get(session.session_id).usage.tool_calls == 3


def test_unlimited_tool_call_reservations_are_still_measured(store: ApexStore) -> None:
    session = store.create(owner="owner", objective="unlimited quota", profile="apex_max", contract_digest="contract")
    assert store.consume_tool_call(session.session_id, limit=None) == (True, 1)
    assert store.consume_tool_call(session.session_id, limit=None) == (True, 2)
    assert store.get(session.session_id).usage.tool_calls == 2


def test_approved_tool_action_is_exact_and_consumed_once(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="approval needed")
    action = {"tool_name": "git_commit", "action_class": "git_commit", "contract_digest": "contract"}
    approval = store.request_approval(session.session_id, note="commit", action=action)

    assert approval is not None
    assert approval.action == action
    assert store.decide_approval(approval.approval_id, verdict="approved", operator="admin") is not None
    assert store.consume_approved_action(session.session_id, action={**action, "tool_name": "git_push"}) is False
    assert store.consume_approved_action(session.session_id, action=action) is True
    assert store.consume_approved_action(session.session_id, action=action) is False
    restarted = ApexStore(store.storage_path)
    persisted = restarted.get(session.session_id).approvals[0]
    assert persisted["consumed_at"] is not None


def test_apex_dispatch_link_and_run_usage_are_durable_and_idempotent(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)

    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.claim_dispatch(session.session_id) == generation
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-1", status="running")
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-1", status="running")
    assert not store.record_dispatch_run(session.session_id, generation=generation, run_id="different-run", status="running")
    assert store.claim_dispatch(session.session_id) is None
    assert store.record_run_usage(session.session_id, run_id="run-1", input_tokens=100, output_tokens=20, llm_calls=2)
    assert store.record_run_usage(session.session_id, run_id="run-1", input_tokens=100, output_tokens=20, llm_calls=2)
    assert store.record_run_status(session.session_id, run_id="run-1", status="completed")
    assert store.claim_dispatch(session.session_id) is None

    restarted = ApexStore(store.storage_path).get(session.session_id)
    assert restarted.run_id == "run-1"
    assert restarted.run_status == "completed"
    assert restarted.dispatch_state == "awaiting_verification"
    assert restarted.usage.input_tokens == 100
    assert restarted.usage.output_tokens == 20
    assert restarted.usage.total_tokens == 120
    assert restarted.usage.llm_calls == 2
    assert restarted.usage.measured_run_ids == ["run-1"]


def test_apex_recovered_run_link_is_compare_and_set(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-1", status="error")

    assert not store.record_recovered_run(
        session.session_id,
        generation=generation + 1,
        source_run_id="run-1",
        run_id="stale-run",
        status="running",
    )
    assert store.record_recovered_run(
        session.session_id,
        generation=generation,
        source_run_id="run-1",
        run_id="run-2",
        status="running",
    )
    assert store.record_recovered_run(
        session.session_id,
        generation=generation,
        source_run_id="run-1",
        run_id="run-2",
        status="error",
    ), "concurrent recovery observers of the same idempotent run must not cancel it"

    persisted = ApexStore(store.storage_path).get(session.session_id)
    assert persisted.run_id == "run-2"
    assert persisted.run_status == "running"
    assert persisted.dispatch_state == "running"


def test_failure_retry_reservation_is_durable_idempotent_and_per_class(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-1", status="error")

    assert store.reserve_failure_retry(
        session.session_id,
        failure_class="model_timeout",
        limit=1,
        source_run_id="run-1",
        generation=generation,
    ) == (True, 1)
    assert store.reserve_failure_retry(
        session.session_id,
        failure_class="model_timeout",
        limit=1,
        source_run_id="run-1",
        generation=generation,
    ) == (True, 1), "replaying one recovery candidate must not spend a second retry"

    linked = ApexStore(store.storage_path).get(session.session_id)
    assert linked.usage.retries == 1
    assert linked.usage.retry_counts_by_failure_class == {"model_timeout": 1}
    assert linked.usage.retry_reservations == {"run-1": "model_timeout"}
    assert not store.reserve_failure_retry(
        session.session_id,
        failure_class="model_timeout",
        limit=1,
        source_run_id="run-2",
        generation=generation,
    )[0]


def test_apex_unadmitted_dispatch_failure_is_durable_and_not_retried(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)

    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.record_dispatch_failure(session.session_id, generation=generation, reason="missing thread")
    assert store.claim_dispatch(session.session_id) is None

    restarted = ApexStore(store.storage_path).get(session.session_id)
    assert restarted.dispatch_state == "failed"
    assert restarted.run_status == "dispatch_error"
    assert restarted.blocked_reason == "missing thread"


def test_run_event_usage_cursor_is_durable_and_prevents_summary_double_count(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-events", status="running")

    # A cumulative live snapshot can arrive before durable observer events are
    # queryable. The first event row must replace that snapshot contribution.
    assert store.record_run_usage(session.session_id, run_id="run-events", input_tokens=90, output_tokens=10, llm_calls=1)
    assert store.record_run_usage_event(session.session_id, run_id="run-events", seq=4, input_tokens=90, output_tokens=10, llm_call=True)
    assert store.record_run_usage_event(session.session_id, run_id="run-events", seq=4, input_tokens=90, output_tokens=10, llm_call=True)
    assert store.record_run_usage(session.session_id, run_id="run-events", input_tokens=90, output_tokens=10, llm_calls=1)

    usage = ApexStore(store.storage_path).get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (90, 10, 100, 1)
    assert usage.event_cursors == {"run-events": 4}


def test_live_run_usage_snapshots_replace_prior_observations(store: ApexStore) -> None:
    session = _session(store)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-live", status="running")

    assert store.record_run_usage(session.session_id, run_id="run-live", input_tokens=100, output_tokens=10, llm_calls=1)
    assert store.record_run_usage(session.session_id, run_id="run-live", input_tokens=250, output_tokens=20, llm_calls=2)

    usage = ApexStore(store.storage_path).get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (250, 20, 270, 2)
    assert usage.run_snapshots["run-live"] == {"input_tokens": 250, "output_tokens": 20, "llm_calls": 2}
