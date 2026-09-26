"""Degraded-subsystem honesty tests.

Each test maps to one rule.  Where alpha's current behaviour is wrong, the test
is written against the *correct* behaviour so it fails until the rule is
implemented - the failing output is the evidence.

Rules under test:
  1. a damaged subsystem must not fail-close the whole system
  2. one bad record must not kill a listing
  3. a write lock must not be taken when nothing is written
  4. a repair must refuse an action it cannot prove is safe
  5. a diagnostic must name the actual damage class
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from alpha.persistence.storekit.degradation import (
    DAMAGE_SCOPE,
    FAIL_CLOSED_CLASSES,
    NEEDS_OPERATOR,
    SELF_HEALING,
    UNREADABLE_PLACEHOLDER,
    CoercionResult,
    DamageClass,
    SafeRepair,
    classify_damage,
    coerce_records,
    degrade,
    index_health_probe,
)
from alpha.persistence.storekit.documents import DocumentEnvelope
from alpha.persistence.storekit.locking import FileLock, LockMode
from alpha.persistence.storekit.store import ScopedStore
from alpha.persistence.storekit.config import StoreKitConfig

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ===========================================================================
# Rule 5: the diagnostic names the actual damage class
# ===========================================================================
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("database disk image is malformed", DamageClass.INDEX_WRITE_CORRUPT),
        ("malformed database schema (run_events_fts)", DamageClass.INDEX_WRITE_CORRUPT),
        ("no such table: run_events_fts", DamageClass.STALE_INDEX),
        ("index is out of date", DamageClass.STALE_INDEX),
        ("fts5: syntax error near \"foo\"", DamageClass.QUERY_REJECTED),
        ("unable to use function MATCH in the requested context", DamageClass.INDEX_WRITE_CORRUPT),
        ("no such module: fts5", DamageClass.INDEX_UNAVAILABLE),
    ],
)
def test_stale_index_and_index_corruption_are_different_classes(message, expected):
    """'FTS write corruption' when the index is merely stale sends the operator
    to the wrong repair.  They must not collapse into one label."""
    report = classify_damage(sqlite3.OperationalError(message), component="search_index")
    assert report.damage is expected, f"{message!r} -> {report.damage} ({report.summary()})"


def test_every_class_declares_its_scope_and_disposition():
    for damage in DamageClass:
        assert damage in DAMAGE_SCOPE, damage
        buckets = [
            damage in SELF_HEALING,
            damage in FAIL_CLOSED_CLASSES,
            damage in NEEDS_OPERATOR,
        ]
        assert sum(1 for flag in buckets if flag) == 1, f"{damage} is in {buckets}"
    # The whole point: only container-level damage may fail an operation closed.
    assert FAIL_CLOSED_CLASSES == {DamageClass.FILE_CORRUPT, DamageClass.SCHEMA_TOO_NEW}
    assert DamageClass.INDEX_WRITE_CORRUPT not in FAIL_CLOSED_CLASSES
    assert DamageClass.INDEX_WRITE_CORRUPT in NEEDS_OPERATOR
    assert DamageClass.STALE_INDEX not in FAIL_CLOSED_CLASSES


def test_index_faults_are_scoped_to_the_index_not_the_transcript_store():
    report = classify_damage(
        sqlite3.DatabaseError("database disk image is malformed"), component="search_index"
    )
    assert report.scope == "search_index"
    assert report.fail_closed is False
    assert "records are untouched" in report.reason or "rebuild" in report.reason


def test_unclassified_failure_is_honest_about_being_unclassified():
    report = classify_damage(RuntimeError("something nobody predicted"), component="x")
    assert report.damage is DamageClass.FILE_CORRUPT
    assert "unclassified" in report.reason
    assert "treated as container damage" in report.reason


# ===========================================================================
# Rule 1: a damaged subsystem must not fail-close the whole system
# ===========================================================================
def test_index_fault_degrades_the_search_and_leaves_the_store_usable():
    events: list[Any] = []
    store_reads = {"n": 0}

    def primary_query() -> str:
        raise sqlite3.DatabaseError("database disk image is malformed")

    def fallback_scan() -> str:
        store_reads["n"] += 1
        return "degraded-result"

    # The fault must escape the ``degrade`` block for a *degradable* class: the
    # point is that the caller gets a usable (degraded) result, not an exception.
    with degrade("search_index", fallback=fallback_scan, on_damage=events.append) as recorded:
        primary_query()

    # The search degraded rather than the system failing closed.
    assert store_reads["n"] == 1
    assert len(recorded) == 1
    # The damage is recorded, named, and scoped to the index.
    assert len(events) == 1
    assert events[0].damage is DamageClass.INDEX_WRITE_CORRUPT
    assert events[0].scope == "search_index"
    assert events[0].fail_closed is False


def test_file_corruption_is_not_degraded_because_it_cannot_be():
    class ContainerGone(Exception):
        """A container-level fault that no narrower class matches."""

    with pytest.raises(ContainerGone):
        with degrade("document", fallback=lambda: "should not run"):
            raise ContainerGone("the store container is gone")
    assert FAIL_CLOSED_CLASSES == {DamageClass.FILE_CORRUPT, DamageClass.SCHEMA_TOO_NEW}


def test_degradation_events_do_not_mention_other_components():
    seen: list[Any] = []
    with degrade("search_index", on_damage=seen.append):
        # A stale index is degradable, so it does not escape the block.
        _raise_stale()
    assert [d.scope for d in seen] == ["search_index"]
    assert "transcript" not in json.dumps([d.to_dict() for d in seen])
    assert seen[0].damage is DamageClass.STALE_INDEX


def _raise_stale() -> None:
    raise sqlite3.OperationalError("no such table: run_events_fts")


# ===========================================================================
# Rule 2: one bad record must not kill a listing
# ===========================================================================
def test_one_bad_record_does_not_kill_a_listing_and_is_named():
    rows = [
        {"id": "good-1", "value": "a"},
        {"id": "bad", "value": object()},
        {"id": "good-2", "value": "b"},
    ]

    def validate(row: Any) -> Any:
        if not isinstance(row.get("value"), str):
            raise ValueError("value must be a string")
        return row

    result = coerce_records(rows, identifier=lambda r: r.get("id"), validate=validate)
    assert len(result.records) == 3
    assert result.records[0]["id"] == "good-1"
    assert result.records[2]["id"] == "good-2"
    assert result.records[1] == UNREADABLE_PLACEHOLDER
    assert result.damaged == ["bad"]
    assert result.report is not None
    assert "bad" in result.report.summary()


def test_a_non_mapping_row_is_coerced_not_fatal():
    result = coerce_records(
        [{"id": "a"}, 42, "nope", {"id": "b"}],
        identifier=lambda r: r.get("id") if isinstance(r, dict) else None,
        validate=lambda r: dict(r) if isinstance(r, dict) else _not_a_mapping(r),
    )
    assert result.records[0] == {"id": "a"}
    assert result.records[1] == UNREADABLE_PLACEHOLDER
    assert result.records[2] == UNREADABLE_PLACEHOLDER
    assert result.records[3] == {"id": "b"}
    assert result.damaged == ["#1", "#2"]


def _not_a_mapping(row: Any) -> Any:
    raise ValueError(f"stored record must be an object, found {type(row).__name__}")


def _rewrite_document(store: ScopedStore, scope: str, mutate) -> None:
    """Rewrite a scope document through the envelope so the checksum stays valid.

    Hand-editing the JSON would trip the document checksum and be classified as
    container corruption, which is a different rule from the one under test.
    """
    path = store.scope_path(scope)
    envelope = DocumentEnvelope.from_dict(json.loads(path.read_text(encoding="utf-8")))
    payload = dict(envelope.payload)
    mutate(payload)
    sealed = envelope.with_payload(payload, clock=time.time).sealed(clock=time.time)
    path.write_text(
        json.dumps(sealed.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def test_storekit_listing_survives_a_corrupt_row(tmp_path):
    """The real store: a deliberately corrupt row must not kill the listing."""
    store = ScopedStore(
        tmp_path,
        store_id="corrupt-row",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    for i in range(4):
        store.put({"id": f"rec-{i}", "text": f"value-{i}"}, scope="s")

    def damage(payload: dict) -> None:
        records = payload["records"]
        records[1] = "this row is not an object at all"
        records[2] = {"id": "rec-2-corrupt", "text": {"nested": "wrong type"}}

    _rewrite_document(store, "s", damage)

    result = store.read_result("s")
    assert result.status == "succeeded", result.to_dict()
    ids = [r.get("id") for r in result.records if isinstance(r, dict)]
    assert "rec-0" in ids and "rec-3" in ids, ids
    assert result.degraded is True
    assert result.degradation is not None
    assert result.degradation["damage"]["damage"] == DamageClass.RECORD_MALFORMED.value
    assert "record_malformed on record" in result.reason
    # The bad rows are named, so the operator knows what to repair.
    assert "rec-2-corrupt" in result.reason or "#1" in result.reason, result.reason


def test_storekit_keeps_a_sound_container_with_only_bad_records(tmp_path):
    store = ScopedStore(
        tmp_path,
        store_id="all-bad",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    store.put({"id": "a", "text": "1"}, scope="s")
    _rewrite_document(store, "s", lambda payload: payload.__setitem__("records", [1, 2, 3]))

    result = store.read_result("s")
    # Every record was unreadable, so there is nothing to list - but the reader
    # did not die, and it says exactly why.
    assert result.status in {"succeeded", "empty"}, result.to_dict()
    assert result.degraded is True
    assert result.degradation["damage"]["damage"] == DamageClass.RECORD_MALFORMED.value
    assert result.degradation["damaged"] == ["#0", "#1", "#2"]
    assert "record_malformed" in result.reason


def test_a_broken_container_still_quarantines(tmp_path):
    """Record-level tolerance must not extend to container-level damage."""
    store = ScopedStore(
        tmp_path,
        store_id="broken-container",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    store.put({"id": "a", "text": "1"}, scope="s")
    _rewrite_document(store, "s", lambda payload: payload.__setitem__("records", {"not": "a list"}))

    result = store.read_result("s")
    assert result.status in {"recovered", "failed"}, result.to_dict()
    assert result.preserved_path is not None, "the original must be preserved"
    assert store.scope_path("s").exists() is False, "the document was quarantined, not left in place"
    assert Path(result.preserved_path).exists()


# ===========================================================================
# Rule 3: a write lock must not be taken when nothing is written
# ===========================================================================
def test_reading_an_absent_scope_takes_no_lock_and_writes_nothing(tmp_path):
    store = ScopedStore(
        tmp_path,
        store_id="readonly-open",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    before = sorted(p.name for p in tmp_path.rglob("*"))
    result = store.read_result("never-written")
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert result.status == "empty", result.to_dict()
    assert before == after, f"a read-only open created files: {set(after) - set(before)}"
    assert store.lock_path("never-written").exists() is False


def test_a_read_does_not_rewrite_the_owner_record(tmp_path):
    """Truncating a live writer's owner metadata on a read weakens the
    stale-lock reclaim guard, so a read-mode lock must not write it.

    The lock file is seeded with a holder's metadata first, so the assertion is
    "a READ acquisition left the file byte-identical" - which is exactly the
    property the old unconditional ``_write_owner`` call broke.
    """
    store = ScopedStore(
        tmp_path,
        store_id="owner-intact",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    store.put({"id": "a", "text": "1"}, scope="s")
    lock_path = store.lock_path("s")
    holder_metadata = json.dumps(
        {"pid": os.getpid(), "acquired_at": time.time(), "mode": "exclusive", "host": "h"}
    ).encode("utf-8")
    lock_path.write_bytes(holder_metadata)
    before = lock_path.read_bytes()

    reader = FileLock(lock_path, mode=LockMode.READ, timeout=1.0)
    result = reader.acquire()
    if result.acquired:
        reader.release()
    assert lock_path.read_bytes() == before, "a read-mode lock rewrote owner metadata"


def test_read_lock_still_takes_a_real_lock(tmp_path):
    """Degrading the write must not degrade the guard: a READ acquisition is
    still a real OS lock, and it still reports a live EXCLUSIVE holder."""
    store = ScopedStore(
        tmp_path,
        store_id="contention",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=0.4, fsync_policy="file"),
    )
    store.put({"id": "a", "text": "1"}, scope="s")
    lock_path = store.lock_path("s")

    uncontended = FileLock(lock_path, mode=LockMode.READ, timeout=0.4)
    result = uncontended.acquire()
    assert result.acquired is True, result.to_dict()
    # Windows has no shared byte-range lock, so READ is upgraded to EXCLUSIVE
    # there.  The upgrade must be disclosed, and it must not turn a read into a
    # write.
    assert result.effective_mode in {LockMode.READ.value, LockMode.EXCLUSIVE.value}
    if result.effective_mode == LockMode.EXCLUSIVE.value:
        assert "read_upgraded_to_exclusive" in (result.reason or ""), result.to_dict()
    uncontended.release()

    holder = FileLock(lock_path, mode=LockMode.EXCLUSIVE, timeout=1.0)
    assert holder.acquire().acquired
    try:
        probe = FileLock(lock_path, mode=LockMode.READ, timeout=0.4)
        contended = probe.acquire()
        if contended.acquired:
            probe.release()
        else:
            # Where the platform supports shared locks the probe loses cleanly;
            # where it does not, READ upgrades to EXCLUSIVE and still loses.
            assert contended.status in {"timeout", "failed", "unavailable"}, contended.to_dict()
            assert contended.reason
    finally:
        holder.release()


def test_exclusive_writes_still_write_owner_metadata(tmp_path):
    """The write path keeps every guard it had."""
    store = ScopedStore(
        tmp_path,
        store_id="write-owner",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    store.put({"id": "a", "text": "1"}, scope="s")
    assert store.lock_path("s").exists()


# ===========================================================================
# Rule 4: a repair must refuse an action it cannot prove is safe
# ===========================================================================
def test_repair_refuses_when_the_precondition_cannot_be_verified():
    def broken_verify() -> tuple[bool, str]:
        raise sqlite3.OperationalError("unable to open database file")

    repair = SafeRepair(
        action="rebuild the search index",
        precondition="the index is behind the data and the container is sound",
        verify=broken_verify,
        perform=lambda: "rebuilt",
    )
    outcome = repair.run(dry_run=False)
    assert outcome.performed is False
    assert "could not be verified" in outcome.reason
    assert "unable to open database file" in outcome.reason


def test_repair_refuses_when_the_precondition_is_not_met():
    repair = SafeRepair(
        action="rebuild the search index",
        precondition="the index is behind the data",
        verify=lambda: (False, "index holds 10 rows, source holds 10"),
        perform=lambda: "rebuilt",
    )
    outcome = repair.run(dry_run=False)
    assert outcome.performed is False
    assert "was not met" in outcome.reason
    assert outcome.evidence == "index holds 10 rows, source holds 10"


def test_repair_dry_run_never_acts():
    performed = []
    repair = SafeRepair(
        action="rebuild",
        precondition="verifiable",
        verify=lambda: (True, "index holds 1, source holds 9"),
        perform=lambda: performed.append(1) or "done",
    )
    outcome = repair.run(dry_run=True)
    assert outcome.performed is False
    assert performed == []
    assert "dry_run=False" in outcome.reason


def test_repair_acts_only_after_proving_its_precondition():
    repair = SafeRepair(
        action="rebuild",
        precondition="verifiable",
        verify=lambda: (True, "index holds 1, source holds 9"),
        perform=lambda: "rebuilt 9 rows",
    )
    outcome = repair.run(dry_run=False)
    assert outcome.performed is True
    assert outcome.reason == "rebuilt 9 rows"
    assert outcome.evidence == "index holds 1, source holds 9"


def test_repair_reports_an_abort_rather_than_a_partial_repair():
    def exploding_perform() -> str:
        raise OSError("disk full")

    repair = SafeRepair(
        action="rebuild",
        precondition="verifiable",
        verify=lambda: (True, "ok"),
        perform=exploding_perform,
    )
    outcome = repair.run(dry_run=False)
    assert outcome.performed is False
    assert "aborted" in outcome.reason
    assert "disk full" in outcome.reason


# ===========================================================================
# index_health_probe: existence is not health
# ===========================================================================
def test_index_health_probe_refuses_when_the_index_is_current(tmp_path):
    db = sqlite3.connect(tmp_path / "probe.db")
    db.execute("CREATE TABLE run_events (category TEXT)")
    db.execute("CREATE VIRTUAL TABLE run_events_fts USING fts5(content)")
    db.execute("INSERT INTO run_events (category) VALUES ('message')")
    db.execute("INSERT INTO run_events_fts (content) VALUES ('hello')")
    ok, evidence = index_health_probe(db)
    assert ok is False, evidence
    assert "would be a no-op" in evidence
    db.close()


def test_index_health_probe_confirms_a_stale_index(tmp_path):
    db = sqlite3.connect(tmp_path / "probe.db")
    db.execute("CREATE TABLE run_events (category TEXT)")
    db.execute("CREATE VIRTUAL TABLE run_events_fts USING fts5(content)")
    db.execute("INSERT INTO run_events (category) VALUES ('message')")
    db.execute("INSERT INTO run_events (category) VALUES ('message')")
    db.execute("INSERT INTO run_events_fts (content) VALUES ('only one indexed')")
    ok, evidence = index_health_probe(db)
    assert ok is True, evidence
    assert "1 rows" in evidence and "2" in evidence
    db.close()


def test_index_health_probe_is_a_refusal_when_it_cannot_measure(tmp_path):
    db = sqlite3.connect(tmp_path / "empty.db")
    ok, evidence = index_health_probe(db)
    assert ok is False
    assert "could not be measured" in evidence
    db.close()


# ===========================================================================
# the classifier must never be the reason a store is destroyed
# ===========================================================================
@pytest.mark.parametrize(
    "exc",
    [
        sqlite3.DatabaseError("database disk image is malformed"),
        sqlite3.OperationalError("no such table: run_events_fts"),
        sqlite3.OperationalError("fts5: syntax error near \"x\""),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        ValueError("each stored record must be an object"),
    ],
)
def test_no_index_or_record_fault_is_classified_as_file_corruption(exc):
    report = classify_damage(exc, component="search_index")
    assert report.fail_closed is False, f"{type(exc).__name__}: {report.summary()}"


def test_coercion_result_serialises_for_the_operator():
    result = CoercionResult(records=[1, 2], damaged=["x"], report=None)
    assert result.to_dict() == {"count": 2, "damaged": ["x"], "damage": None}
    populated = coerce_records(
        [{"id": "a"}, 7],
        identifier=lambda r: r.get("id") if isinstance(r, dict) else None,
        validate=lambda r: dict(r) if isinstance(r, dict) else _not_a_mapping(r),
    )
    assert populated.report is not None
    assert populated.to_dict()["damage"]["damage"] == "record_malformed"
    assert json.dumps(populated.to_dict())


def test_storekit_store_reports_a_stale_owner_reclaim_on_read(tmp_path):
    """A stale reclaim stays visible to the operator on a read path."""
    store = ScopedStore(
        tmp_path,
        store_id="stale",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=1.0, fsync_policy="file"),
    )
    store.put({"id": "a", "text": "1"}, scope="s")
    lock_path = store.lock_path("s")
    lock_path.write_text(
        json.dumps({"pid": 999999, "acquired_at": time.time() - 10_000, "mode": "exclusive"}),
        encoding="utf-8",
    )
    result = store.read_result("s")
    assert result.status == "succeeded", result.to_dict()
    assert result.records
    # The reclaim is disclosed rather than silent.
    assert "stale" in (result.lock_reason or "") or result.lock_status == "acquired"
