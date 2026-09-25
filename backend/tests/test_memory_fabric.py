"""Hermetic contract tests for Alpha's canonical memory fabric."""

from __future__ import annotations

import ast
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from alpha.memory.fabric import (
    FabricConfig,
    FabricStore,
    ForgetReport,
    Lifecycle,
    LifecycleStatus,
    MemoryEnvelope,
    MemoryEnvelopeStore,
    MemoryScope,
    Representations,
    Security,
    SecurityClassification,
    SourceRef,
    TransitionResult,
    archive,
    as_of,
    assert_temporal_integrity,
    contradict,
    demote,
    due_transitions,
    fabric_enabled,
    fabric_root,
    forget,
    forget_scope,
    latest,
    load_fabric_config,
    promote,
    read_entries,
    restore,
    supersede,
    transition,
    transition_allowed,
)
from alpha.memory.fabric.forget import forget as module_forget
from alpha.memory.fabric.store import scope_document_path

DAY = 86_400.0


def make_scope(user: str = "u1", project: str = "p1") -> MemoryScope:
    return MemoryScope(tenant_id="tenant-a", user_id=user, project_id=project)


def make_config(tmp_path: Path, **overrides: Any) -> FabricConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "storage_path": str(tmp_path / "fabric"),
        "default_decay_rate": 0.05,
        "default_ttl_seconds": None,
        "max_tags": 8,
        "compress_after_days": 1.0,
        "archive_after_days": 2.0,
        "purge_after_days": 3.0,
    }
    values.update(overrides)
    return FabricConfig.model_validate(values)


def make_record(
    *,
    record_id: str = "mem-1",
    scope: MemoryScope | None = None,
    now: float = 100.0,
    valid_from: float | None = None,
    valid_to: float | None = None,
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
    pinned: bool = False,
    content: str = "The project uses PostgreSQL.",
    quality: dict[str, Any] | None = None,
    security: dict[str, Any] | None = None,
) -> MemoryEnvelope:
    return MemoryEnvelope.create(
        content,
        id=record_id,
        scope=scope or make_scope(),
        types=["semantic", "decision"],
        subtype="architecture",
        summary="PostgreSQL decision",
        source=SourceRef(kind="conversation", event_id="event-1", uri="session://s1/e1"),
        timestamps={
            "created_at": now,
            "observed_at": now,
            "valid_from": valid_from if valid_from is not None else now,
            "valid_to": valid_to,
        },
        quality=quality,
        lifecycle=Lifecycle(status=status, pinned=pinned),
        representations=Representations(raw=True, text=True, graph=False),
        security=security,
        now=now,
    )


def make_store(tmp_path: Path, **overrides: Any) -> MemoryEnvelopeStore:
    max_records = overrides.pop("max_records", 100)
    return MemoryEnvelopeStore(make_config(tmp_path, **overrides), max_records_per_scope=max_records)


# ---------------------------------------------------------------------------
# Canonical model, scope, security, and configuration contracts
# ---------------------------------------------------------------------------


def test_envelope_round_trip_preserves_quality_disclosure_and_json_shape(tmp_path: Path) -> None:
    scope = make_scope()
    record = make_record(
        scope=scope,
        quality={"importance": 4.0, "confidence": -1.0, "salience": 0.4, "novelty": 0.8},
    )

    assert record.quality.importance == 1.0
    assert record.quality.confidence == 0.0
    assert set(record.quality.clamped_fields) == {"importance", "confidence"}
    assert "importance" in record.quality.clamping_disclosure
    assert record.quality.clamp_disclosure == record.quality.clamping_disclosure
    assert record.quality.was_clamped is True
    assert record.representations.raw is True
    assert record.representations.embedding is False
    assert record.source.content_hash is not None

    payload = record.to_dict()
    assert payload["types"] == ["semantic", "decision"]
    assert payload["scope"] == scope.model_dump()
    assert json.loads(json.dumps(payload)) == payload
    restored = MemoryEnvelope.model_validate(payload)
    assert restored.to_dict() == payload


def test_scope_qualification_is_stable_and_descendant_visibility_is_fail_closed() -> None:
    record_scope = MemoryScope(tenant_id="tenant-a", user_id="u1", project_id="p1")
    assert MemoryScope.from_qualified(record_scope.qualified()) == record_scope
    assert record_scope.qualified() == MemoryScope(tenant_id="tenant-a", user_id="u1", project_id="p1").qualified()
    assert record_scope.permits(record_scope) is True
    assert record_scope.permits(MemoryScope(tenant_id="tenant-a", user_id="u1", project_id="p1", task_id="t")) is True
    assert record_scope.permits(MemoryScope(tenant_id="tenant-a", user_id="u2", project_id="p1")) is False
    assert record_scope.permits(MemoryScope(tenant_id="tenant-a", project_id="p1")) is False
    assert record_scope.permits(MemoryScope(tenant_id="tenant-a", user_id="u1", project_id="p2")) is False
    assert MemoryScope(project_id="p1").permits(MemoryScope(user_id="u1", project_id="p1")) is True
    assert MemoryScope(user_id="u1").is_descendant_of(MemoryScope(user_id="u1", project_id="p1")) is False


def test_scope_uri_escapes_components_and_round_trips_acl_shorthand() -> None:
    scope = MemoryScope(user_id="user/one", project_id="project blue")
    assert MemoryScope.from_qualified(scope.qualified()) == scope
    assert MemoryScope.from_qualified("project:project blue") == MemoryScope(project_id="project blue")


def test_secret_classification_is_rejected_by_default_and_never_leaked(tmp_path: Path) -> None:
    scope = make_scope()
    secret_security = {"classification": "secret", "acl": ["project:p1"]}
    with pytest.raises(ValueError, match="secret classification"):
        MemoryEnvelope.create("private", scope=scope, types="semantic", security=secret_security)

    default_store = MemoryEnvelopeStore(make_config(tmp_path / "default"))
    refused = default_store.create(
        "private",
        scope=scope,
        types="semantic",
        id="secret-1",
        security=secret_security,
        now=100.0,
    )
    assert refused.status == "refused"
    assert refused.reason == "secret_classification_rejected"
    assert default_store.get("secret-1") is None

    allowed_store = MemoryEnvelopeStore(
        make_config(tmp_path / "allowed", allow_secret_classification=True),
    )
    admitted = allowed_store.create(
        "private",
        scope=scope,
        types="semantic",
        id="secret-2",
        security=secret_security,
        now=100.0,
    )
    assert admitted.status == "succeeded"
    assert allowed_store.get("secret-2", reader_scope=scope, now=100.0) is not None
    assert (
        allowed_store.get(
            "secret-2",
            reader_scope=MemoryScope(tenant_id="tenant-a", user_id="u1", project_id="p2"),
            now=100.0,
        )
        is None
    )
    assert allowed_store.visible_to(MemoryScope(tenant_id="tenant-a", user_id="u2", project_id="p1"), now=100.0) == []
    assert Security(classification=SecurityClassification.SECRET).permits(scope) is False


def test_representations_default_to_no_unbuilt_indexes(tmp_path: Path) -> None:
    record = MemoryEnvelope.create("raw fact", scope=make_scope(), types="semantic", now=1.0)
    assert record.representations.model_dump() == {
        "raw": True,
        "text": True,
        "embedding": False,
        "graph": False,
        "summary": False,
    }
    assert record.source.content_hash is not None
    assert record.timestamps.valid_from == 1.0
    assert record.is_expired(1.0) is False


def test_current_and_as_of_return_only_valid_versions() -> None:
    record = make_record(now=100.0, valid_from=100.0, valid_to=200.0)
    assert record.as_of(100.0) is record
    assert record.as_of(200.0) is record
    assert record.as_of(200.001) is None
    assert record.current() is None


def test_every_fabric_config_key_has_a_real_reader_and_no_shared_config_import() -> None:
    assert set(FabricConfig.model_fields) == {
        "enabled",
        "default_decay_rate",
        "default_ttl_seconds",
        "max_tags",
        "purge_after_days",
        "archive_after_days",
        "compress_after_days",
        "allow_secret_classification",
        "storage_path",
    }
    config = FabricConfig()
    assert FabricStore is MemoryEnvelopeStore
    assert config.enabled is False
    assert fabric_enabled(config) is False
    assert config.read_all_keys()["storage_path"] is None
    assert load_fabric_config({"enabled": True}).enabled is True
    assert fabric_root(config.storage_path) == fabric_root(None)

    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "fabric"
    expected_readers = {
        "enabled": "store.py",
        "default_decay_rate": "store.py",
        "default_ttl_seconds": "store.py",
        "max_tags": "store.py",
        "purge_after_days": "lifecycle.py",
        "archive_after_days": "lifecycle.py",
        "compress_after_days": "lifecycle.py",
        "allow_secret_classification": "store.py",
        "storage_path": "config.py",
    }
    for key, filename in expected_readers.items():
        assert key in (package / filename).read_text(encoding="utf-8"), key
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("memory_config")
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("memory_config") for alias in node.names)


# ---------------------------------------------------------------------------
# Lifecycle state machine and age policy
# ---------------------------------------------------------------------------


def test_every_legal_adjacent_transition_and_restore_is_pure_and_disclosed() -> None:
    legal = {
        (LifecycleStatus.ACTIVE, LifecycleStatus.COMPRESSED),
        (LifecycleStatus.COMPRESSED, LifecycleStatus.ACTIVE),
        (LifecycleStatus.COMPRESSED, LifecycleStatus.ARCHIVED),
        (LifecycleStatus.ARCHIVED, LifecycleStatus.COMPRESSED),
        (LifecycleStatus.ARCHIVED, LifecycleStatus.PURGED),
        (LifecycleStatus.PURGED, LifecycleStatus.ARCHIVED),
    }
    for current, target in legal:
        record = make_record(status=current)
        result = transition(record, target, now=200.0, reason="matrix")
        assert result.ok is True
        assert result.envelope is not None
        assert result.envelope.lifecycle.status is target
        assert record.lifecycle.status is current
        assert result.reason == "matrix"

    assert promote(make_record(), now=200.0).envelope.lifecycle.status is LifecycleStatus.COMPRESSED
    assert demote(make_record(status=LifecycleStatus.COMPRESSED), now=200.0).envelope.lifecycle.status is LifecycleStatus.ACTIVE
    assert transition_allowed(LifecycleStatus.ACTIVE, LifecycleStatus.ARCHIVED) is False
    assert transition_allowed(LifecycleStatus.ACTIVE, LifecycleStatus.COMPRESSED) is True

    for current in LifecycleStatus:
        record = make_record(status=current)
        assert transition(record, current, now=200.0).status == "skipped"
        for target in LifecycleStatus:
            if target is not current and (current, target) not in legal:
                result = transition(record, target, now=200.0)
                assert result.status == "illegal_transition"
                assert "adjacent" in result.reason or "cannot transition" in result.reason
                assert result.envelope is record

    for status in (LifecycleStatus.COMPRESSED, LifecycleStatus.ARCHIVED, LifecycleStatus.PURGED):
        record = make_record(status=status)
        if status is LifecycleStatus.COMPRESSED:
            assert transition(record, LifecycleStatus.ACTIVE, now=200.0).ok is True
        else:
            result = transition(record, LifecycleStatus.ACTIVE, now=200.0)
            assert result.status == "illegal_transition"
        restored = restore_envelope_for_test(record)
        assert restored.ok is True
        assert restored.envelope is not None
        assert restored.envelope.lifecycle.status is LifecycleStatus.ACTIVE


def restore_envelope_for_test(record: MemoryEnvelope) -> TransitionResult:
    from alpha.memory.fabric.lifecycle import restore_envelope

    return restore_envelope(record, now=300.0, reason="explicit restore")


def test_pin_protects_transitions_and_restore_unless_force_is_explicit() -> None:
    record = make_record(pinned=True)
    assert transition(record, LifecycleStatus.COMPRESSED, now=200.0).status == "refused"
    assert "pinned" in transition(record, LifecycleStatus.COMPRESSED, now=200.0).reason
    forced = transition(record, LifecycleStatus.COMPRESSED, now=200.0, force=True)
    assert forced.ok is True
    assert forced.forced is True
    compressed = make_record(status=LifecycleStatus.COMPRESSED, pinned=True)
    assert restore_envelope_for_test(compressed).status == "refused"
    assert restore_envelope_for_test(compressed).reason.startswith("pinned_record_protected")


def test_due_transitions_use_exact_age_boundaries_and_never_mutate_pinned_records(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    record = make_record(now=0.0)
    assert due_transitions(record, DAY - 0.001, config) == []
    assert [item.target for item in due_transitions(record, DAY, config)] == [LifecycleStatus.COMPRESSED]
    assert [item.target for item in due_transitions(record, 2 * DAY, config)] == [
        LifecycleStatus.COMPRESSED,
        LifecycleStatus.ARCHIVED,
    ]
    assert [item.target for item in due_transitions(record, 3 * DAY, config)] == [
        LifecycleStatus.COMPRESSED,
        LifecycleStatus.ARCHIVED,
        LifecycleStatus.PURGED,
    ]
    assert record.lifecycle.status is LifecycleStatus.ACTIVE
    pinned = make_record(now=0.0, pinned=True)
    assert due_transitions(pinned, 4 * DAY, config) == []
    compressed = make_record(now=0.0, status=LifecycleStatus.COMPRESSED)
    assert [item.target for item in due_transitions(compressed, 3 * DAY, config)] == [
        LifecycleStatus.ARCHIVED,
        LifecycleStatus.PURGED,
    ]


def test_due_transitions_honor_ttl_before_age_thresholds(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        compress_after_days=2.0,
        archive_after_days=3.0,
        purge_after_days=4.0,
    )
    record = make_record(now=0.0)
    ttl_record = MemoryEnvelope.create(
        "ttl",
        scope=make_scope(),
        types="episodic",
        now=0.0,
        ttl_seconds=DAY,
        decay_rate=config.default_decay_rate,
    )
    assert due_transitions(record, DAY, config) == []
    assert [item.target for item in due_transitions(ttl_record, DAY, config)] == [
        LifecycleStatus.COMPRESSED,
        LifecycleStatus.ARCHIVED,
        LifecycleStatus.PURGED,
    ]


# ---------------------------------------------------------------------------
# Temporal integrity, contradictions, and deterministic point-in-time reads
# ---------------------------------------------------------------------------


def test_supersede_sets_shared_boundary_and_refuses_backdated_replacement() -> None:
    old = make_record(record_id="old", now=10.0)
    new = make_record(record_id="new", now=20.0)
    result = supersede(old, new, now=20.0)
    assert result.ok is True
    assert result.old is not None and result.new is not None
    assert result.old.timestamps.valid_to == result.new.timestamps.valid_from == 20.0
    assert old.id in result.new.lifecycle.supersedes
    assert new.id in result.old.lifecycle.superseded_by
    assert old.timestamps.valid_to == new.timestamps.valid_from == 20.0
    assert_temporal_integrity([result.old, result.new])

    backdated = make_record(record_id="backdated", now=5.0)
    refused = supersede(old, backdated, now=20.0)
    assert refused.status == "refused"
    assert refused.reason == "replacement_valid_from_precedes_existing"
    assert backdated.lifecycle.supersedes == []


def test_contradict_links_both_sides_without_resolving_either_record() -> None:
    first = make_record(record_id="first", now=1.0)
    second = make_record(record_id="second", now=1.0, content="The project uses SQLite.")
    result = contradict(first, second)
    assert result.ok is True
    assert result.old is not None and result.new is not None
    assert result.old.lifecycle.contradicts == ["second"]
    assert result.new.lifecycle.contradicts == ["first"]
    assert first.lifecycle.contradicts == ["second"]
    assert second.lifecycle.contradicts == ["first"]


def test_as_of_and_latest_are_deterministic_for_overlapping_intervals() -> None:
    broad = make_record(record_id="broad", now=0.0, valid_from=0.0, valid_to=100.0)
    newer = make_record(record_id="newer", now=50.0, valid_from=50.0, valid_to=150.0, content="newer")
    at_boundary = as_of([newer, broad], 50.0)
    assert [record.id for record in at_boundary] == ["newer", "broad"]
    assert as_of([broad, newer], 50.0) == at_boundary
    assert [record.id for record in as_of([broad, newer], 25.0)] == ["broad"]
    assert latest([broad, newer], when=75.0).id == "newer"
    assert latest([broad, newer], when=25.0).id == "broad"
    assert latest([broad, newer], when=200.0) is None


# ---------------------------------------------------------------------------
# Store admission, indexes, corruption, bounds, isolation, and concurrency
# ---------------------------------------------------------------------------


def test_store_is_disabled_by_default_and_reports_a_disclosed_skip(tmp_path: Path) -> None:
    store = MemoryEnvelopeStore(FabricConfig(storage_path=str(tmp_path / "disabled")))
    result = store.create("not written", scope=make_scope(), types="semantic", now=1.0)
    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert store.get("anything") is None
    assert store.list(make_scope()) == []
    path_style = MemoryEnvelopeStore(tmp_path / "path-style")
    assert path_style.root == (tmp_path / "path-style").resolve()


def test_store_keeps_scopes_isolated_and_exposes_id_and_scope_indexes(tmp_path: Path) -> None:
    store = MemoryEnvelopeStore(make_config(tmp_path))
    first_scope = make_scope("u1", "p1")
    second_scope = make_scope("u2", "p2")
    assert store.create("first", scope=first_scope, types="semantic", id="a", now=1.0).ok
    assert store.create("second", scope=second_scope, types="semantic", id="b", now=1.0).ok
    assert store.get("a", scope=second_scope) is None
    assert store.get("a", reader_scope=first_scope).id == "a"
    assert store.get("b", reader_scope=second_scope).id == "b"
    assert store.visible_to(first_scope, now=1.0)[0].id == "a"
    assert store.id_index() == {
        "a": first_scope.qualified(),
        "b": second_scope.qualified(),
    }
    assert store.scope_index()[first_scope.qualified()] == ("a",)
    assert store.scope_for_id("a") == first_scope


def test_store_preserves_corrupt_document_and_never_silently_overwrites_it(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    scope = make_scope()
    first_store = MemoryEnvelopeStore(config)
    assert first_store.create("before corruption", scope=scope, types="semantic", id="safe", now=1.0).ok
    path = scope_document_path(first_store.root, scope)
    path.write_text("{not json", encoding="utf-8")

    recovered_store = MemoryEnvelopeStore(config)
    read = recovered_store.list_result(scope, now=1.0)
    assert read.status == "recovered"
    assert read.records == []
    assert list(path.parent.glob("envelopes.json.corrupt-*")) or list(path.parent.glob("*.corrupt-*"))
    replacement = recovered_store.create("after recovery", scope=scope, types="semantic", id="new", now=2.0)
    assert replacement.ok is True
    assert [record.id for record in recovered_store.list(scope, now=2.0)] == ["new"]


def test_store_bounds_growth_with_deterministic_disclosed_eviction(tmp_path: Path) -> None:
    scope = make_scope()
    store = MemoryEnvelopeStore(make_config(tmp_path), max_records_per_scope=2)
    assert store.create("one", scope=scope, types="semantic", id="one", now=1.0).ok
    assert store.create("two", scope=scope, types="semantic", id="two", now=2.0).ok
    third = store.create("three", scope=scope, types="semantic", id="three", now=3.0)
    assert third.ok is True
    assert third.evicted_ids == ["one"]
    assert {record.id for record in store.list(scope, now=3.0)} == {"two", "three"}
    entries = read_entries("1970-01-01", config=store.config)
    assert any(entry["action"] == "forget" and entry["status"] == "evicted" for entry in entries)

    pinned_store = MemoryEnvelopeStore(make_config(tmp_path / "pinned"), max_records_per_scope=1)
    pinned = make_record(record_id="pinned", scope=scope, pinned=True)
    assert pinned_store.admit(pinned, now=1.0).ok is True
    refused = pinned_store.create("new", scope=scope, types="semantic", id="new", now=2.0)
    assert refused.status == "refused"
    assert refused.reason == "scope_capacity_has_no_eviction_candidate"
    assert pinned_store.get("pinned", now=1.0) is not None


def test_store_thread_safe_concurrent_writes_keep_every_distinct_record(tmp_path: Path) -> None:
    scope = make_scope()
    store = MemoryEnvelopeStore(make_config(tmp_path), max_records_per_scope=100)

    def write(index: int) -> str:
        result = store.create(
            f"concurrent-{index}",
            scope=scope,
            types="episodic",
            id=f"concurrent-{index}",
            now=float(index),
        )
        assert result.ok is True
        return result.record_ids[0]

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(write, range(24)))

    assert len(set(ids)) == 24
    assert len(store.list(scope, now=24.0)) == 24
    assert store.get("concurrent-17", reader_scope=scope, now=24.0) is not None


# ---------------------------------------------------------------------------
# Forget/archive/restore, pin refusal, counts, and provenance
# ---------------------------------------------------------------------------


def test_archive_restore_and_forget_return_counts_and_append_audit_entries(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    scope = make_scope()
    store = MemoryEnvelopeStore(config)
    created = store.create("archive me", scope=scope, types="semantic", id="audit", now=100.0)
    assert created.ok is True

    archived = archive("audit", store=store, actor_scope=scope, now=110.0)
    assert archived.status == "succeeded"
    assert archived.changed == 2
    assert len(archived.transitions) == 2
    assert store.get("audit", scope=scope, reader_scope=scope, now=110.0).lifecycle.status is LifecycleStatus.ARCHIVED

    restored = restore("audit", store=store, actor_scope=scope, now=120.0)
    assert restored.status == "succeeded"
    assert restored.changed == 1
    assert store.get("audit", scope=scope, reader_scope=scope, now=120.0).lifecycle.status is LifecycleStatus.ACTIVE

    forgotten = forget("audit", store=store, actor_scope=scope, now=130.0)
    assert isinstance(forgotten, ForgetReport)
    assert forgotten.status == "succeeded"
    assert forgotten.deleted == 1
    assert forgotten.changed == 1
    assert store.get("audit", scope=scope) is None
    entries = read_entries("1970-01-01", config=config)
    assert [entry["action"] for entry in entries] == ["create", "transition", "transition", "transition", "forget"]
    assert all(entry["actor_scope"] == scope.qualified() for entry in entries)


def test_forget_scope_handles_pinned_records_only_with_explicit_force(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    scope = make_scope()
    store = MemoryEnvelopeStore(config)
    assert store.create("ordinary", scope=scope, types="semantic", id="ordinary", now=1.0).ok
    assert store.admit(make_record(record_id="pinned", scope=scope, pinned=True), now=1.0).ok

    refused = forget_scope(scope, store=store, actor_scope=scope, now=2.0)
    assert refused.status == "partial"
    assert refused.deleted == 1
    assert refused.pinned_skipped == 1
    assert store.get("pinned", scope=scope) is not None

    forced = forget_scope(scope, store=store, actor_scope=scope, now=3.0, force=True)
    assert forced.status == "succeeded"
    assert forced.deleted == 1
    assert forced.forced is True
    assert store.get("pinned", scope=scope) is None
    entries = read_entries("1970-01-01", config=config)
    assert any(entry["action"] == "forget" and entry["forced"] is True for entry in entries)


def test_module_level_forget_is_the_same_explicit_api() -> None:
    assert callable(module_forget)
    assert callable(forget)


# ---------------------------------------------------------------------------
# Lazy package-root contract
# ---------------------------------------------------------------------------


def test_every_public_name_imports_from_the_lazy_package_root() -> None:
    import alpha.memory.fabric as fabric

    assert fabric.__all__
    for name in fabric.__all__:
        assert getattr(fabric, name) is not None, name
    assert callable(fabric.forget)
    assert fabric.forget_scope.__module__ == "alpha.memory.fabric.forget"
    assert fabric.restore.__module__ == "alpha.memory.fabric.forget"
    assert "alpha.memory.fabric" in __import__("sys").modules


def test_public_names_are_explicitly_reexported_for_type_checkers() -> None:
    source = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "fabric" / "__init__.py"
    text = source.read_text(encoding="utf-8")
    assert "from alpha.memory._lazy_exports import install_lazy_exports" in text
    assert "if TYPE_CHECKING" in text
    assert " as " in text
