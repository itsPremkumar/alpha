"""Quarantine escalation policies.

A record that stays open too long is just... open. These tests pin the
``stale_records`` query and the ``escalate_stale`` policy that makes the
dead-letter queue a live work queue.
"""

from __future__ import annotations

import time

from alpha.workflow.quarantine import QuarantineStore, QuarantineTrigger


def _admit(store: QuarantineStore, node_id: str = "n1", *, quarantined_at: float | None = None):
    record = store.admit(
        run_id="run_1",
        workflow_id="wf_1",
        node_id=node_id,
        reason="transient upstream failure",
        trigger=QuarantineTrigger.RECOVERY_EXHAUSTED,
        attempts=3,
    )
    if quarantined_at is not None:
        record.quarantined_at = quarantined_at
    return record


def test_stale_records_finds_long_open_records():
    store = QuarantineStore()
    now = time.time()
    _admit(store, "n1", quarantined_at=now - 3600)  # 1 hour ago
    _admit(store, "n2", quarantined_at=now - 10)  # 10 seconds ago

    stale = store.stale_records(threshold_seconds=300, now=now)
    assert len(stale) == 1
    assert stale[0].node_id == "n1"


def test_stale_records_excludes_resolved():
    store = QuarantineStore()
    now = time.time()
    record = _admit(store, "n1", quarantined_at=now - 3600)
    store.discard(record.record_id, note="handled")

    stale = store.stale_records(threshold_seconds=300, now=now)
    assert len(stale) == 0


def test_escalate_stale_marks_records_and_returns_them():
    store = QuarantineStore()
    now = time.time()
    _admit(store, "n1", quarantined_at=now - 3600)
    _admit(store, "n2", quarantined_at=now - 10)

    escalated = store.escalate_stale(threshold_seconds=300, note="notify owner", now=now)
    assert len(escalated) == 1
    assert escalated[0].node_id == "n1"
    assert "escalated" in escalated[0].resolution_note
    assert "3600" in escalated[0].resolution_note


def test_escalate_stale_is_idempotent():
    store = QuarantineStore()
    now = time.time()
    _admit(store, "n1", quarantined_at=now - 3600)

    first = store.escalate_stale(threshold_seconds=300, now=now)
    assert len(first) == 1

    second = store.escalate_stale(threshold_seconds=300, now=now)
    assert len(second) == 1  # still returned, but already escalated


def test_escalate_stale_does_not_touch_resolved_records():
    store = QuarantineStore()
    now = time.time()
    record = _admit(store, "n1", quarantined_at=now - 3600)
    store.discard(record.record_id, note="handled")

    escalated = store.escalate_stale(threshold_seconds=300, now=now)
    assert len(escalated) == 0


def test_stale_records_empty_store():
    store = QuarantineStore()
    stale = store.stale_records(threshold_seconds=300)
    assert len(stale) == 0
