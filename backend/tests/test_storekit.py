"""Regression tests for the StoreKit persistence and migration framework."""

from __future__ import annotations

import ast
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import pytest

from alpha.persistence.storekit.atomic import (
    ATOMIC_WRITE_STEPS,
    AtomicWriteResult,
    InjectedCrash,
    atomic_write_json,
    atomic_write_text,
    directory_fsync_supported,
)
from alpha.persistence.storekit.config import StoreKitConfig, load_store_kit_config
from alpha.persistence.storekit.documents import (
    DocumentEnvelope,
    checksum_for,
    load_document,
    make_document,
)
from alpha.persistence.storekit.locking import (
    FileLock,
    LockMode,
    file_lock_path,
    inspect_lock,
    lock_backend,
)
from alpha.persistence.storekit.migrations import (
    Migration,
    MigrationError,
    MigrationRegistry,
    migrate_document,
    plan_migrations,
)
from alpha.persistence.storekit.registry import build_default_registry
from alpha.persistence.storekit.retention import (
    GlobalRetentionBudget,
    RetentionBudget,
    RetentionCandidate,
    plan_global_retention,
    plan_retention,
)
from alpha.persistence.storekit.store import ScopedStore


# ----------------------------------------------------------------------
# Process targets.  They are module-level so Windows' spawn context can
# import them without pickling a local closure.
# ----------------------------------------------------------------------
def _increment_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        if record.get("id") == "counter":
            record["value"] = int(record.get("value", 0)) + 1
            return records
    records.append({"id": "counter", "value": 0})
    return records


def _child_increment(root: str, worker: str, iterations: int, start: Any, done: Any) -> None:
    from alpha.persistence.storekit.store import ScopedStore

    store = ScopedStore(
        root,
        store_id="concurrent",
        enabled=True,
        config=StoreKitConfig(enabled=True, lock_timeout_seconds=2.0, fsync_policy="file"),
    )
    if not start.wait(5.0):
        done.put((worker, "start_timeout"))
        return
    for _ in range(iterations):
        result = store.mutate("shared", _increment_records)
        if not result.ok:
            done.put((worker, result.status))
            return
    done.put((worker, "ok"))


def _child_hold_lock(path: str, ready: Any, release: Any) -> None:
    lock = FileLock(path, mode=LockMode.EXCLUSIVE, timeout=1.0)
    result = lock.acquire()
    ready.set()
    release.wait(5.0)
    lock.release()
    if not result.acquired:
        raise RuntimeError(result.reason or "child could not acquire lock")


# ----------------------------------------------------------------------
# Atomic durability
# ----------------------------------------------------------------------
def test_atomic_crash_injection_never_exposes_partial_target(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    atomic_write_text(target, "previous", fsync="file_and_directory")

    for step in ATOMIC_WRITE_STEPS:
        atomic_write_text(target, "previous", fsync="file_and_directory")

        def crash(at: str, expected: str = step) -> None:
            if at == expected:
                raise InjectedCrash(expected)

        with pytest.raises(InjectedCrash):
            atomic_write_text(target, "replacement", fsync="file_and_directory", fault=crash)

        content = target.read_text(encoding="utf-8")
        assert content == ("replacement" if step in {"after_replace", "after_directory_fsync"} else "previous")
        assert list(tmp_path.glob("*.tmp")) == []
        assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_result_reports_platform_durability(tmp_path: Path) -> None:
    result: AtomicWriteResult = atomic_write_text(tmp_path / "value", "x", fsync="file_and_directory")
    assert result.replaced is True
    assert result.file_synced is True
    assert result.directory_synced is directory_fsync_supported()
    assert result.durable_rename is directory_fsync_supported()


def test_atomic_json_rejects_non_portable_nan(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        atomic_write_json(tmp_path / "bad.json", {"value": float("nan")})


# ----------------------------------------------------------------------
# Locking
# ----------------------------------------------------------------------
def test_lock_timeout_and_always_release_on_exception(tmp_path: Path) -> None:
    path = file_lock_path(tmp_path / "doc.json")
    lock = FileLock(path, timeout=0.2, poll_interval_seconds=0.005)
    with pytest.raises(RuntimeError):
        with lock:
            assert lock.acquired is True
            raise RuntimeError("callback failed")
    assert lock.acquired is False
    second = FileLock(path, timeout=0.2)
    assert second.acquire().acquired is True
    second.release()


def test_lock_disabled_is_disclosed_unavailable(tmp_path: Path) -> None:
    result = FileLock(tmp_path / "doc.lock", enabled=False).acquire()
    assert result.status == "unavailable"
    assert result.acquired is False
    assert result.reason == "locking_disabled"


def test_lock_shared_mode_is_explicitly_disclosed(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "doc.lock", mode=LockMode.SHARED, timeout=0.1)
    result = lock.acquire()
    backend = lock_backend()
    if backend.shared_supported:
        assert result.acquired is True
        lock.release()
    else:
        assert result.status == "unavailable"
        assert result.reason == "shared_locks_unsupported"


def test_stale_lock_owner_is_detected_and_dead_lock_can_be_reused(tmp_path: Path) -> None:
    path = file_lock_path(tmp_path / "doc.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 999_999_999, "host": os.uname().nodename if hasattr(os, "uname") else "", "started_at": 0}), encoding="utf-8")
    old = time.time() - 120.0
    os.utime(path, (old, old))
    info = inspect_lock(path, stale_after_seconds=1.0)
    assert info.stale is True
    lock = FileLock(path, timeout=0.2, stale_after_seconds=1.0)
    assert lock.acquire().acquired is True
    lock.release()


def test_two_processes_serialize_read_modify_write(tmp_path: Path) -> None:
    seed = ScopedStore(tmp_path, store_id="concurrent", enabled=True)
    assert seed.put({"id": "counter", "value": 0}, scope="shared").ok
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    done = context.Queue()
    processes = [context.Process(target=_child_increment, args=(str(tmp_path), name, 8, start, done)) for name in ("a", "b")]
    for process in processes:
        process.start()
    start.set()
    outcomes = [done.get(timeout=60.0) for _ in processes]
    for process in processes:
        process.join(timeout=60.0)
        assert process.exitcode == 0
    assert sorted(outcomes) == [("a", "ok"), ("b", "ok")]
    store = ScopedStore(tmp_path, store_id="concurrent", enabled=True)
    assert store.get("counter", scope="shared") == {"id": "counter", "value": 16}


def test_two_processes_block_on_one_file_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    path = str(file_lock_path(tmp_path / "doc.json"))
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_child_hold_lock, args=(path, ready, release))
    process.start()
    try:
        assert ready.wait(60.0)
        blocked = FileLock(path, timeout=0.15, poll_interval_seconds=0.005).acquire()
        assert blocked.status == "timeout"
        assert blocked.acquired is False
    finally:
        release.set()
        process.join(timeout=60.0)
    assert process.exitcode == 0
    final_lock = FileLock(path, timeout=0.5)
    assert final_lock.acquire().acquired is True
    final_lock.release()


# ----------------------------------------------------------------------
# Documents and migrations
# ----------------------------------------------------------------------
def test_checksum_mismatch_is_quarantined_and_read_discloses_empty(tmp_path: Path) -> None:
    store = ScopedStore(tmp_path, store_id="checksum", enabled=True)
    assert store.put({"id": "a", "value": 1}).ok
    path = store.scope_path("default")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["checksum"] = "sha256:wrong"
    path.write_text(json.dumps(raw), encoding="utf-8")

    result = store.read_result()
    assert result.status == "recovered"
    assert result.records == []
    assert result.reason == "corrupt_document_preserved"
    assert result.preserved_path is not None
    assert result.preserved_path.name.startswith(path.name + ".corrupt-")
    assert not path.exists()
    assert load_document(result.preserved_path, expected_schema_id=store.schema_id, quarantine=False).status == "checksum_mismatch"


def test_document_envelope_uses_injected_clock_and_checksum() -> None:
    document = make_document({"value": 1}, format_version=2, schema_id="test", clock=lambda: 42.0)
    assert document.created_at == 42.0
    assert document.updated_at == 42.0
    assert document.checksum == checksum_for({"value": 1})
    assert DocumentEnvelope.from_dict(document.to_dict()).payload == {"value": 1}


def _add_marker(payload: Any) -> Any:
    value = dict(payload)
    value["marked"] = True
    return value


def _rename_marker(payload: Any) -> Any:
    value = dict(payload)
    if "marked" in value:
        value["renamed"] = value.pop("marked")
    return value


def test_migration_chain_is_stepwise_and_dry_run_changes_nothing(tmp_path: Path) -> None:
    registry = MigrationRegistry(
        [
            Migration(1, 2, "add marker", _add_marker),
            Migration(2, 3, "rename marker", _rename_marker),
        ]
    )
    assert [step.identifier for step in plan_migrations(1, 3, registry)] == ["v1->v2", "v2->v3"]
    document = make_document({"value": 1}, format_version=1, schema_id="test", clock=lambda: 1.0)
    dry = migrate_document(document, registry, target_version=3, dry_run=True, clock=lambda: 2.0)
    assert dry.status == "dry_run"
    assert dry.document is not None and dry.document.payload == {"value": 1, "renamed": True}
    assert document.payload == {"value": 1}
    applied = migrate_document(document, registry, target_version=3, clock=lambda: 2.0)
    assert applied.status == "succeeded"
    assert applied.document is not None and applied.document.format_version == 3
    assert applied.document.migration_chain == ("v1->v2", "v2->v3")


def test_migration_refusals_have_distinct_reasons() -> None:
    with pytest.raises(MigrationError) as duplicate:
        MigrationRegistry([Migration(1, 2, "one", _add_marker), Migration(1, 2, "two", _add_marker)])
    assert duplicate.value.code == "duplicate_migration"

    with pytest.raises(MigrationError) as cycle:
        MigrationRegistry([Migration(1, 2, "up", _add_marker), Migration(2, 1, "down", _add_marker)])
    assert cycle.value.code == "migration_cycle"

    gapped = MigrationRegistry([Migration(1, 2, "one", _add_marker), Migration(3, 4, "three", _add_marker)])
    with pytest.raises(MigrationError) as gap:
        plan_migrations(1, 4, gapped)
    assert gap.value.code == "migration_gap"

    future = make_document({}, format_version=99, schema_id="test", clock=lambda: 1.0)
    refused = migrate_document(future, MigrationRegistry([Migration(1, 2, "one", _add_marker)]), target_version=2)
    assert refused.status == "refused"
    assert refused.reason == "unsupported_future_version"
    assert refused.document == future


def test_failed_migration_leaves_source_document_byte_identical(tmp_path: Path) -> None:
    def fail(payload: Any) -> Any:
        raise ValueError("deliberate migration failure")

    registry = MigrationRegistry([Migration(1, 2, "fails", fail)])
    store = ScopedStore(
        tmp_path,
        store_id="migration",
        format_version=1,
        target_version=2,
        migration_registry=registry,
        enabled=True,
        config=StoreKitConfig(enabled=True, dry_run=False),
    )
    assert store.put({"id": "a"}).ok
    path = store.scope_path("default")
    before = path.read_bytes()
    result = store.migrate(dry_run=False)
    assert result.status == "failed"
    assert result.reason == "migration_step_failed"
    assert path.read_bytes() == before


# ----------------------------------------------------------------------
# ScopedStore
# ----------------------------------------------------------------------
def test_scoped_store_round_trip_retention_and_rebuilt_index(tmp_path: Path) -> None:
    clock = {"value": 100.0}
    store = ScopedStore(
        tmp_path,
        store_id="scoped",
        enabled=True,
        max_documents=2,
        clock=lambda: clock["value"],
    )
    assert store.enabled is True
    assert store.put({"id": "old", "value": 1, "created_at": 1.0}).ok
    clock["value"] = 101.0
    assert store.put({"id": "middle", "value": 2, "created_at": 2.0}).ok
    clock["value"] = 102.0
    assert store.put({"id": "new", "value": 3, "created_at": 3.0}).ok
    result = store.read_result()
    assert [record["id"] for record in result.records] == ["middle", "new"]
    assert store.count() == 2
    assert store.get("new") == {"id": "new", "value": 3, "created_at": 3.0}

    # Change the document behind the store's back; index() must rebuild from
    # documents rather than return a separate drifting index.
    path = store.scope_path("default")
    document = load_document(path, expected_schema_id=store.schema_id).document
    assert document is not None
    payload = dict(document.payload)
    payload["records"] = [*payload["records"], {"id": "external", "value": 4}]
    atomic_write_json(path, document.with_payload(payload, clock=lambda: 103.0).to_dict(), fsync="file")
    assert store.index().id_to_scope["external"] == "default"
    assert store.id_index()["middle"] == "default"


def test_scoped_store_exposes_generic_payload_and_legacy_loader_seams(tmp_path: Path) -> None:
    def legacy_loader(raw: dict[str, Any]) -> DocumentEnvelope:
        return make_document(raw, format_version=1, schema_id="storekit.generic", clock=lambda: 1.0)

    path = ScopedStore(
        tmp_path,
        store_id="generic",
        record_key=None,
        enabled=True,
        legacy_loader=legacy_loader,
    ).scope_path("scope")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "entities": {}}), encoding="utf-8")
    store = ScopedStore(
        tmp_path,
        store_id="generic",
        record_key=None,
        enabled=True,
        legacy_loader=legacy_loader,
    )
    assert store.read_payload("scope") == {"schema": 1, "entities": {}}
    assert store.mutate_payload("scope", lambda payload: payload.update({"entities": {"e": 1}}) or None).ok
    assert store.read_payload("scope") == {"schema": 1, "entities": {"e": 1}}
    assert store.read_result("scope").status == "succeeded"


def test_scoped_store_is_default_off_and_discloses_lock_unavailability(tmp_path: Path) -> None:
    default_store = ScopedStore(tmp_path, store_id="off")
    assert default_store.enabled is False
    assert default_store.put({"id": "a"}).status == "skipped"
    unavailable = ScopedStore(
        tmp_path,
        store_id="unavailable",
        enabled=True,
        config=StoreKitConfig(enabled=True, locking_enabled=False),
    )
    result = unavailable.put({"id": "a"})
    assert result.status == "unavailable"
    assert result.reason == "lock_unavailable"
    assert not unavailable.scope_path("default").exists()


# ----------------------------------------------------------------------
# Retention and registry
# ----------------------------------------------------------------------
def test_retention_is_deterministic_and_proposes_rather_than_deletes() -> None:
    candidates = [
        RetentionCandidate("b", size_bytes=40, importance=0.5, age_seconds=10.0),
        RetentionCandidate("a", size_bytes=40, importance=0.1, age_seconds=10.0),
        RetentionCandidate("pinned", size_bytes=10, importance=9.0, pinned=True),
    ]
    first = plan_retention(candidates, RetentionBudget(max_bytes=60, max_count=10))
    second = plan_retention(list(reversed(candidates)), RetentionBudget(max_bytes=60, max_count=10))
    assert first.to_dict() == second.to_dict()
    assert "a" in first.keys_to_evict
    assert "pinned" in first.keys_to_keep
    assert all(decision.reason for decision in first.decisions)
    assert first.over_budget is False
    assert first.evicted_bytes == 40


def test_global_budget_prevents_collective_disk_fill() -> None:
    report = plan_global_retention({"fabric": 80, "l1": 80, "narrative": 10}, GlobalRetentionBudget(100))
    assert report.total_allocated == 100
    assert report.unfilled["narrative"] == 10
    assert report.over_budget is True
    assert report.remaining == 0


def test_registry_answers_without_importing_sibling_stores(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "packages" / "harness" / "alpha" / "persistence" / "storekit" / "registry.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not (node.module or "").startswith("alpha.memory") for node in imports)
    root = tmp_path / "home"
    path = root / "users" / "u" / "l1" / "records" / "a.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    registry = build_default_registry()
    report = registry.report(root, target_version=3)
    assert any(item.store == "l1.records" and item.reason == "migration_required" for item in report.migration_required)
    assert report.total_bytes == 2
    assert report.total_files == 1


def test_storekit_config_reader_reads_every_declared_key() -> None:
    config = StoreKitConfig()
    assert config.enabled is False
    assert config.dry_run is True
    declared = set(StoreKitConfig.model_fields)
    assert set(config.read_all_keys()) == declared
    for name in declared:
        assert config.read_key(name) == getattr(config, name)
    with pytest.raises(KeyError):
        config.read_key("not_a_key")
    assert load_store_kit_config({"enabled": True}).enabled is True
