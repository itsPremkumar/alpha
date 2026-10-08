"""APEX control changes must survive a restart before they are acknowledged."""

from __future__ import annotations

import json
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
