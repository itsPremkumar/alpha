"""A version-incompatible store document must be refused, never quarantined.

The finding under test, measured from the tree before this change:

* All eleven registered memory stores write a schema marker into their
  on-disk document.
* No reader compared that marker against the version its own code
  implements, so an unknown marker reached the same ``except`` handler as
  torn bytes.
* That handler renames the file to ``<name>.corrupt-<stamp>`` and continues
  with an empty document, so the next write republishes the reader's own
  format at the live path and the newer build's data survives only in an
  unindexed sidecar.

Every test below therefore asserts the file is *still at its live path* with
its original bytes, that no ``.corrupt-`` sidecar was created, that the
refusal is disclosed, and that writes are refused rather than applied on top
of a document this build cannot read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alpha.memory._store_format import (
    DIRECTION_ABSENT,
    DIRECTION_CURRENT,
    DIRECTION_FUTURE,
    DIRECTION_LEGACY,
    DIRECTION_UNREADABLE,
    SCHEMA_MARKER_KEYS,
    STORE_FORMAT_UNSUPPORTED,
    StoreFormatAudit,
    StoreFormatUnsupported,
    StoreFormatVerdict,
    audit_store_formats,
    classify_store_format,
    find_quarantine_paths,
    format_disclosure,
    is_quarantine_path,
    read_schema_marker,
    require_store_format,
)

# ---------------------------------------------------------------------------
# The classifier: the whole decision, table-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "direction", "reason"),
    [
        ({"schema": 1, "events": []}, DIRECTION_CURRENT, "ok"),
        ({"schema_version": 1, "items": []}, DIRECTION_CURRENT, "ok"),
        ({"format_version": 1}, DIRECTION_CURRENT, "ok"),
        ({"schema": "1"}, DIRECTION_CURRENT, "ok"),
        ({"schema": 2, "events": []}, DIRECTION_FUTURE, "document_newer_than_reader"),
        ({"schema": 99}, DIRECTION_FUTURE, "document_newer_than_reader"),
        ({"schema": 0}, DIRECTION_LEGACY, "document_older_than_reader"),
        ({"events": []}, DIRECTION_ABSENT, "schema_marker_absent"),
        ({}, DIRECTION_ABSENT, "schema_marker_absent"),
        ({"schema": True}, DIRECTION_UNREADABLE, "schema_marker_not_an_integer:schema"),
        ({"schema": None}, DIRECTION_UNREADABLE, "schema_marker_not_an_integer:schema"),
        ({"schema": [1]}, DIRECTION_UNREADABLE, "schema_marker_not_an_integer:schema"),
        ({"schema": -1}, DIRECTION_UNREADABLE, "schema_marker_not_an_integer:schema"),
        ({"schema": "one"}, DIRECTION_UNREADABLE, "schema_marker_not_an_integer:schema"),
        ([], DIRECTION_UNREADABLE, "document_root_not_object"),
        ("text", DIRECTION_UNREADABLE, "document_root_not_object"),
        (None, DIRECTION_UNREADABLE, "document_root_not_object"),
    ],
)
def test_classification_covers_every_marker_outcome(raw: Any, direction: str, reason: str) -> None:
    """``True`` is not version 1, a string of digits is, and a bare array is not a document."""

    verdict = classify_store_format(store="s", path="p", raw=raw, supported_version=1)
    assert verdict.direction == direction
    assert verdict.reason == reason


def test_only_a_version_mismatch_is_a_refusal() -> None:
    """Corrupt bytes are not a refusal: quarantining them is correct behaviour.

    Collapsing the two is what let a version mismatch be quarantined.
    """

    assert classify_store_format(store="s", path="p", raw={"schema": 2}, supported_version=1).refusal is True
    assert classify_store_format(store="s", path="p", raw={"schema": 0}, supported_version=1).refusal is True
    assert classify_store_format(store="s", path="p", raw={}, supported_version=1).refusal is True
    assert classify_store_format(store="s", path="p", raw={"schema": 1}, supported_version=1).refusal is False
    assert classify_store_format(store="s", path="p", raw=[], supported_version=1).refusal is False


def test_future_versus_legacy_are_distinguishable_and_survive_a_round_trip() -> None:
    """A rolling deploy needs to tell 'too new' from 'too old'."""

    future = classify_store_format(store="s", path="p", raw={"schema": 7}, supported_version=2)
    legacy = classify_store_format(store="s", path="p", raw={"schema": 1}, supported_version=2)
    assert (future.direction, legacy.direction) == (DIRECTION_FUTURE, DIRECTION_LEGACY)
    assert (future.found_version, legacy.found_version) == (7, 1)
    for verdict in (future, legacy):
        assert StoreFormatVerdict.from_dict(verdict.to_dict()) == verdict


def test_read_schema_marker_prefers_the_first_key_that_exists() -> None:
    assert SCHEMA_MARKER_KEYS == ("schema", "schema_version", "format_version")
    assert read_schema_marker({"schema": 1, "format_version": 4}) == (1, "schema")
    assert read_schema_marker({"format_version": 4}) == (4, "format_version")
    assert read_schema_marker({"other": 1}) == (None, None)
    assert read_schema_marker("not a mapping") == (None, None)


def test_a_refusal_names_the_store_the_path_and_both_versions() -> None:
    """The disclosure is what an operator reads; it must carry the numbers."""

    verdict = classify_store_format(store="social.state", path="/x/state.json", raw={"schema": 4}, supported_version=1)
    message = format_disclosure(verdict)
    assert STORE_FORMAT_UNSUPPORTED in message
    assert "social.state" in message
    assert "/x/state.json" in message
    assert "4" in message and "1" in message
    assert verdict.status == STORE_FORMAT_UNSUPPORTED
    assert verdict.error


def test_supported_version_must_be_positive() -> None:
    with pytest.raises(ValueError, match="supported_version"):
        classify_store_format(store="s", path="p", raw={}, supported_version=0)


def test_require_raises_only_on_a_refusal() -> None:
    assert require_store_format(store="s", path="p", raw={"schema": 1}, supported_version=1).ok is True
    assert require_store_format(store="s", path="p", raw=[], supported_version=1).direction == DIRECTION_UNREADABLE
    with pytest.raises(StoreFormatUnsupported) as excinfo:
        require_store_format(store="s", path="p", raw={"schema": 2}, supported_version=1)
    error = excinfo.value
    assert error.store == "s"
    assert error.direction == DIRECTION_FUTURE
    assert error.found_version == 2
    assert error.supported_version == 1
    assert error.verdict.path == "p"


# ---------------------------------------------------------------------------
# Store-level behaviour: the file must survive
# ---------------------------------------------------------------------------


def _sidecars(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.corrupt-*"))


def _assert_untouched(path: Path, original: bytes) -> None:
    assert path.exists(), "the version-incompatible document was moved out of its live path"
    assert path.read_bytes() == original
    assert _sidecars(path) == [], "a version mismatch was quarantined, which is the data loss"


def _bump_marker(path: Path, *, current: int, to: int) -> bytes:
    """Rewrite a store-written document exactly as a newer build would publish it.

    The document starts life written by the store's own writer, so the payload
    is real and only the version marker (plus one field this build forbids)
    makes it unreadable.  That is the rolling-deploy case, reproduced exactly.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == current, f"writer must emit schema {current}, got {payload['schema']}"
    payload["schema"] = to
    payload["future_field"] = 1
    original = json.dumps(payload).encode()
    path.write_bytes(original)
    return original


def _seed_affective(tmp_path: Path):
    from alpha.memory.affective.models import AffectEvent, AffectSubject
    from alpha.memory.affective.store import AffectiveEventStore

    store = AffectiveEventStore(tmp_path)
    store.append(
        [
            AffectEvent(
                id="e1",
                user_id="u1",
                agent_name="alpha",
                subject=AffectSubject.INTERACTION,
                content="the deploy failed again",
                valence=-0.8,
                arousal=0.7,
                intensity=0.8,
                confidence=0.9,
                source="caller",
                created_at=100.0,
            )
        ],
        user_id="u1",
        max_events=20,
    )
    assert AffectiveEventStore(tmp_path).list_events("u1"), "seed write must round-trip before the marker is bumped"
    return store


def test_affective_store_refuses_a_future_document_without_touching_it(tmp_path: Path) -> None:
    from alpha.memory.affective.store import AffectiveEventStore, StoreUnavailableError

    seed = _seed_affective(tmp_path)
    path = seed._path("u1")
    original = _bump_marker(path, current=1, to=2)

    fresh = AffectiveEventStore(tmp_path)
    assert fresh.list_events("u1") == []
    _assert_untouched(path, original)

    assert fresh.read_status("u1") == STORE_FORMAT_UNSUPPORTED
    refusal = fresh.format_refusal("u1")
    assert refusal is not None
    assert refusal.direction == DIRECTION_FUTURE
    assert refusal.found_version == 2

    with pytest.raises(StoreUnavailableError, match=STORE_FORMAT_UNSUPPORTED):
        fresh.delete(["e1"], user_id="u1")
    _assert_untouched(path, original)


def test_affective_store_refuses_a_document_with_no_marker(tmp_path: Path) -> None:
    """A pre-versioning file is the 'old on-disk format' case, not corruption."""

    from alpha.memory.affective.store import AffectiveEventStore

    seed = _seed_affective(tmp_path)
    path = seed._path("u1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("schema")
    original = json.dumps(payload).encode()
    path.write_bytes(original)

    fresh = AffectiveEventStore(tmp_path)
    assert fresh.list_events("u1") == []
    _assert_untouched(path, original)
    assert fresh.read_status("u1") == STORE_FORMAT_UNSUPPORTED
    assert fresh.format_refusal("u1").direction == DIRECTION_ABSENT


def test_affective_store_still_quarantines_genuinely_corrupt_bytes(tmp_path: Path) -> None:
    """The refusal must not swallow the corruption case it replaced."""

    from alpha.memory.affective.store import AffectiveEventStore

    store = AffectiveEventStore(tmp_path)
    path = store._path("u1")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"{ definitely not valid json"
    path.write_bytes(original)

    fresh = AffectiveEventStore(tmp_path)
    assert fresh.list_events("u1") == []
    assert fresh.read_status("u1") == "corrupt_document_preserved"
    assert fresh.format_refusal("u1") is None
    backups = _sidecars(path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert not path.exists()


def test_affective_store_still_reads_its_own_format(tmp_path: Path) -> None:
    from alpha.memory.affective.store import AffectiveEventStore

    _seed_affective(tmp_path)
    fresh = AffectiveEventStore(tmp_path)
    assert [item.id for item in fresh.list_events("u1")] == ["e1"]
    assert fresh.read_status("u1") == "ok"
    assert fresh.format_refusal("u1") is None


def test_social_store_refuses_instead_of_quarantining_an_unknown_schema(tmp_path: Path) -> None:
    """social.store already compared the marker -- and treated the answer as corruption."""

    from alpha.memory.social.config import SocialConfig
    from alpha.memory.social.store import SocialStore, SocialStoreUnavailable

    config = SocialConfig.model_validate({"enabled": True, "storage_path": str(tmp_path / "social")})
    seed = SocialStore(config)
    seed.update_scope("user:a", lambda doc: doc.facts.update({"f1": _shared_fact("user:a", "kept by a newer build")}), now=100.0)
    assert seed.list_facts("user:a")

    path = seed.scope_path("user:a")
    original = _bump_marker(path, current=1, to=2)

    fresh = SocialStore(SocialConfig.model_validate({"enabled": True, "storage_path": str(tmp_path / "social")}))
    assert fresh.list_facts("user:a") == []
    _assert_untouched(path, original)
    assert fresh.read_status("user:a") == STORE_FORMAT_UNSUPPORTED
    assert fresh.format_refusal("user:a").direction == DIRECTION_FUTURE

    # A write must be refused, not applied on top of a document this build
    # cannot read.
    with pytest.raises(SocialStoreUnavailable, match=STORE_FORMAT_UNSUPPORTED):
        fresh.update_scope("user:a", lambda doc: doc.facts.update({"f2": _shared_fact("user:a", "x")}), now=200.0)
    _assert_untouched(path, original)


def _shared_fact(owner_scope: str, content: str):
    from alpha.memory.social.models import SharedFact

    return SharedFact(
        id="f1" if content != "x" else "f2",
        owner_scope=owner_scope,
        content=content,
        sensitivity="private",
        created_by="agent:alpha",
        created_at=100.0,
    )


def test_fabric_store_refuses_instead_of_quarantining_an_unknown_schema(tmp_path: Path) -> None:
    from alpha.memory.fabric.models import MemoryScope
    from alpha.memory.fabric.store import MemoryEnvelopeStore

    scope = MemoryScope.model_validate({"user_id": "u1"})
    config = {"enabled": True, "storage_path": str(tmp_path)}
    seed = MemoryEnvelopeStore(config=config)
    assert seed.create("written before the bump", scope=scope, types="semantic", id="safe", now=1.0).ok
    assert MemoryEnvelopeStore(config=config).list_result(scope).records

    path = seed._path(scope)
    original = _bump_marker(path, current=2, to=99)

    fresh = MemoryEnvelopeStore(config=config)
    result = fresh.list_result(scope)
    assert result.records == []
    assert result.status == STORE_FORMAT_UNSUPPORTED
    _assert_untouched(path, original)
    assert fresh.format_refusal(scope).direction == DIRECTION_FUTURE

    # Every mutation funnels through one persist choke point; refuse there so
    # nothing can republish this build's older format over the newer document.
    # The public surface discloses it as a refusal result rather than an
    # exception, matching the vocabulary the storage-failure path already uses.
    refusal = fresh.create("a new record", scope=scope, types="semantic", id="new", now=2.0)
    assert refusal.status == "refused"
    assert refusal.reason == "document_format_unsupported"
    assert refusal.storage_status == STORE_FORMAT_UNSUPPORTED
    assert STORE_FORMAT_UNSUPPORTED in refusal.error
    _assert_untouched(path, original)


def test_codebase_store_refuses_instead_of_quarantining_an_unknown_schema(tmp_path: Path) -> None:
    from alpha.memory.codebase.models import CodebaseSnapshot
    from alpha.memory.codebase.store import CodebaseStore, CodebaseStoreUnavailable

    snapshot = CodebaseSnapshot(repo_id="repo1", modules=[], edges=[], coverage=1.0)
    seed = CodebaseStore(storage_path=str(tmp_path), repo_id="repo1")
    seed.save(snapshot)
    assert CodebaseStore(storage_path=str(tmp_path), repo_id="repo1").load() is not None

    path = seed.path_for("repo1")
    original = _bump_marker(path, current=1, to=4)

    fresh = CodebaseStore(storage_path=str(tmp_path), repo_id="repo1")
    assert fresh.load() is None
    assert fresh.read_status("repo1") == STORE_FORMAT_UNSUPPORTED
    _assert_untouched(path, original)
    assert fresh.format_refusal("repo1").direction == DIRECTION_FUTURE
    with pytest.raises(CodebaseStoreUnavailable, match=STORE_FORMAT_UNSUPPORTED):
        fresh.save(snapshot)
    _assert_untouched(path, original)


def test_entities_store_refuses_instead_of_quarantining_an_unknown_schema(tmp_path: Path) -> None:
    from alpha.memory.entities.models import EntityCandidate
    from alpha.memory.entities.store import EntityStore, StoreUnavailableError

    store = EntityStore(storage_path=str(tmp_path))
    path = store._path("u1")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {"schema": 2, "entities": [], "mentions": [], "links": [], "evictions": [], "future_field": 1}
    ).encode()
    path.write_bytes(original)

    fresh = EntityStore(storage_path=str(tmp_path))
    assert fresh.list_entities("u1") == []
    _assert_untouched(path, original)
    assert fresh.read_status("u1") == STORE_FORMAT_UNSUPPORTED
    assert fresh.format_refusal("u1").direction == DIRECTION_FUTURE
    with pytest.raises(StoreUnavailableError, match=STORE_FORMAT_UNSUPPORTED):
        fresh.ingest_candidates([EntityCandidate(record_id="r1", canonical_name="Acme")], user_id="u1")
    _assert_untouched(path, original)


def test_utility_store_refuses_instead_of_quarantining_an_unknown_schema(tmp_path: Path) -> None:
    from alpha.memory.utility.config import UtilityConfig
    from alpha.memory.utility.store import UtilityStore, UtilityStoreUnavailable

    config = UtilityConfig(storage_path=str(tmp_path / "utility"), enabled=True)
    seed = UtilityStore(config=config)
    path = seed._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {"schema": 5, "records": [], "evictions": [], "corruption_events": [], "future_field": 1}
    ).encode()
    path.write_bytes(original)

    fresh = UtilityStore(config=UtilityConfig(storage_path=str(tmp_path / "utility"), enabled=True))
    assert fresh.count() == 0
    _assert_untouched(path, original)
    events = fresh.corruption_events()
    assert any(STORE_FORMAT_UNSUPPORTED in event for event in events), events
    assert fresh.format_refusal().direction == DIRECTION_FUTURE
    with pytest.raises(UtilityStoreUnavailable, match=STORE_FORMAT_UNSUPPORTED):
        fresh.put({"record_id": "r1", "score": 0.5})
    _assert_untouched(path, original)


def test_narrative_store_refuses_the_events_document_it_versioned(tmp_path: Path) -> None:
    from alpha.memory.narrative.config import NarrativeConfig
    from alpha.memory.narrative.store import NarrativeStore, StoreUnavailableError

    config = NarrativeConfig(enabled=True, storage_path=str(tmp_path / "narrative"))
    seed = NarrativeStore(config)
    path = seed._event_path("user", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"schema": 7, "events": [], "future_field": 1}).encode()
    path.write_bytes(original)

    fresh = NarrativeStore(NarrativeConfig(enabled=True, storage_path=str(tmp_path / "narrative")))
    assert fresh.list_events("user") == []
    _assert_untouched(path, original)
    assert fresh.read_status("user") == STORE_FORMAT_UNSUPPORTED
    assert fresh.format_refusal("user").direction == DIRECTION_FUTURE
    with pytest.raises(StoreUnavailableError, match=STORE_FORMAT_UNSUPPORTED):
        fresh.append_events([_narrative_event()], scope="user")
    _assert_untouched(path, original)


def _narrative_event():
    from alpha.memory.narrative.models import NarrativeEvent, NarrativeScope

    return NarrativeEvent(
        id="n1",
        scope=NarrativeScope.USER,
        scope_id="default",
        period_start=1.0,
        period_end=1.0,
        title="written before the bump",
        summary="a seeded event",
    )


def test_prospective_store_refuses_instead_of_quarantining_an_unknown_schema(tmp_path: Path) -> None:
    from alpha.memory.prospective.config import ProspectiveConfig
    from alpha.memory.prospective.store import ProspectiveStore, ProspectiveStoreUnavailable

    config = ProspectiveConfig(enabled=True, storage_path=str(tmp_path / "prospective"))
    seed = ProspectiveStore(config=config)
    path = seed._path_for("u1")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"schema": 3, "items": [], "future_field": 1}).encode()
    path.write_bytes(original)

    fresh = ProspectiveStore(config=ProspectiveConfig(enabled=True, storage_path=str(tmp_path / "prospective")))
    document, status, error = fresh._load_unlocked("u1")
    assert document["items"] == []
    assert status == STORE_FORMAT_UNSUPPORTED
    _assert_untouched(path, original)
    assert fresh.format_refusal("u1").direction == DIRECTION_FUTURE
    assert STORE_FORMAT_UNSUPPORTED in error

    # This module's `_blocked` set is maintained but never read, so the refusal
    # has to be enforced at the persist choke point to mean anything.
    with pytest.raises(ProspectiveStoreUnavailable, match=STORE_FORMAT_UNSUPPORTED):
        fresh._persist_unlocked("u1", {"schema": 1, "items": []})
    _assert_untouched(path, original)


# ---------------------------------------------------------------------------
# Preflight: answer "what would this build refuse?" before deploying
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Preflight: answer "what would this build refuse?" before deploying
# ---------------------------------------------------------------------------


def test_audit_names_every_document_the_declared_stores_cannot_read(tmp_path: Path) -> None:
    good = tmp_path / "users" / "u1" / "affective" / "events.json"
    legacy = tmp_path / "users" / "u2" / "affective" / "events.json"
    future = tmp_path / "users" / "u3" / "affective" / "events.json"
    absent = tmp_path / "users" / "u4" / "affective" / "events.json"
    torn = tmp_path / "users" / "u5" / "affective" / "events.json"
    for path, body in (
        (good, {"schema": 1, "events": []}),
        (legacy, {"schema": 0, "events": []}),
        (future, {"schema": 9, "events": []}),
        (absent, {"events": []}),
        (torn, None),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body) if body is not None else "{torn", encoding="utf-8")
    # A preserved sidecar must never be reported as a live document.
    sidecar = tmp_path / "users" / "u6" / "affective" / "events.json.corrupt-123"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text("{torn", encoding="utf-8")

    entries = [{"name": "affective.events", "format_version": 1, "document_glob": "users/*/affective/events.json"}]
    audit = audit_store_formats(tmp_path, entries)

    assert isinstance(audit, StoreFormatAudit)
    assert audit.ok is False
    assert audit.scanned == 5
    assert sorted(item.direction for item in audit.findings) == [
        DIRECTION_ABSENT,
        DIRECTION_FUTURE,
        DIRECTION_LEGACY,
        DIRECTION_UNREADABLE,
    ]
    assert audit.unreadable
    assert audit.owned_by_a_newer_build
    assert audit.stores_needing_migration() == ("affective.events",)
    assert all("u6" not in item.path for item in audit.findings)
    payload = audit.to_dict()
    assert payload["ok"] is False
    assert payload["refusals"] == 3
    assert payload["unreadable"] == 1
    assert payload["needs_migration"] == ["affective.events"]
    assert StoreFormatAudit.from_dict(payload) is not None


def test_audit_reports_nothing_for_a_disk_every_store_can_read(tmp_path: Path) -> None:
    path = tmp_path / "users" / "u1" / "affective" / "events.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "events": []}), encoding="utf-8")
    audit = audit_store_formats(tmp_path, [{"name": "a", "format_version": 1, "document_glob": "users/*/affective/events.json"}])
    assert audit.ok is True
    assert audit.findings == ()
    assert audit.to_dict()["ok"] is True


def test_audit_refuses_a_traversing_or_absolute_glob_and_a_missing_root(tmp_path: Path) -> None:
    bad = audit_store_formats(
        tmp_path,
        [
            {"name": "up", "format_version": 1, "document_glob": "../outside/*.json"},
            {"name": "abs", "format_version": 1, "document_glob": str((tmp_path / "*.json").resolve())},
            {"name": "bad-version", "format_version": "x", "document_glob": "*.json"},
            "not a registration",
        ],
    )
    assert bad.findings == ()
    assert len(bad.errors) == 4
    assert audit_store_formats(tmp_path / "nope", []).scanned == 0


def test_audit_works_from_the_shipped_default_registrations(tmp_path: Path) -> None:
    """The registry in persistence.storekit is the source of truth, not a copy."""

    from alpha.persistence.storekit.registry import build_default_registry

    path = tmp_path / "users" / "u1" / "affective" / "events.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 3, "events": []}), encoding="utf-8")

    audit = audit_store_formats(tmp_path, build_default_registry().entries)
    assert [item.store for item in audit.owned_by_a_newer_build] == ["affective.events"]
    assert audit.scanned == 1


def test_the_registry_declarations_say_nothing_about_the_disk_and_are_still_asked(tmp_path: Path) -> None:
    """Declarations answer "is my code current?"; the audit answers "will this deploy strand data?".

    A registry with every entry at its target version reports no migration work
    even when the disk holds a document that build cannot read.  That gap is
    exactly what the audit exists to close, so it is pinned here.  The audit
    lives in the stdlib leaf rather than in ``storekit.registry`` precisely
    because that module is required to stay a pure-data inventory: composing
    the two from the outside keeps both properties true.
    """

    from alpha.persistence.storekit.registry import build_default_registry

    path = tmp_path / "users" / "u1" / "affective" / "events.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "events": []}), encoding="utf-8")

    registry = build_default_registry()
    assert registry.what_needs_migration() == ()
    assert audit_store_formats(tmp_path, registry.entries).ok is True

    path.write_text(json.dumps({"schema": 9, "events": []}), encoding="utf-8")
    assert registry.what_needs_migration() == ()
    stranded = audit_store_formats(tmp_path, registry.entries)
    assert stranded.ok is False
    assert [item.store for item in stranded.owned_by_a_newer_build] == ["affective.events"]


def test_quarantine_paths_are_discoverable_by_an_operator(tmp_path: Path) -> None:
    (tmp_path / "a.json.corrupt-1").write_text("x", encoding="utf-8")
    (tmp_path / "a.json").write_text("x", encoding="utf-8")
    assert is_quarantine_path(tmp_path / "a.json.corrupt-1") is True
    assert is_quarantine_path(tmp_path / "a.json") is False
    assert [path.name for path in find_quarantine_paths(tmp_path)] == ["a.json.corrupt-1"]
    assert find_quarantine_paths(tmp_path / "missing") == ()
