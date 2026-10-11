"""Tests for the durable capability passport store.

The store follows the arena/``GoalStore`` house rule: an unreadable document
is an error state that callers can see, never an empty-but-valid store. Every
test here uses ``tmp_path``; nothing touches the network or a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel
from alpha.capability_mesh.store import STORE_FILE_NAME, PassportStore, PassportStoreError


def _passport(entity_id: str, *, entity_type: EntityKind = EntityKind.AGENT, version: str = "v1") -> CapabilityPassport:
    return CapabilityPassport(entity_id=entity_id, entity_type=entity_type, version=version)


def _measured(entity_id: str) -> CapabilityPassport:
    estimate = CapabilityEstimate(
        capability="implement",
        task_category="coding",
        quality_mean=0.8,
        sample_count=3,
        evidence_level=EvidenceLevel.OBSERVED,
        profile_version="p1",
    )
    return CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=(estimate,))


class TestStoreLifecycle:
    def test_fresh_store_creates_nothing(self, tmp_path: Path) -> None:
        PassportStore(tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_missing_file_is_empty_and_readable(self, tmp_path: Path) -> None:
        state = PassportStore(tmp_path).read_state()
        assert state.passports == ()
        assert state.unreadable is False
        assert state.error is None

    def test_get_unknown_is_none(self, tmp_path: Path) -> None:
        assert PassportStore(tmp_path).get("agent:ghost") is None

    def test_roundtrip(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        original = _measured("agent:a")
        store.upsert(original)
        loaded = PassportStore(tmp_path).get("agent:a")
        assert loaded == original

    def test_written_document_is_utf8_json(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:café"))
        raw = (tmp_path / STORE_FILE_NAME).read_text(encoding="utf-8")
        assert "café" in raw
        document = json.loads(raw)
        assert document["version"] == 1
        assert document["passports"][0]["entity_id"] == "agent:café"

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a"))
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != STORE_FILE_NAME]
        assert leftovers == []


class TestUpsertAndRemove:
    def test_upsert_appends(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a"))
        store.upsert(_passport("agent:b"))
        assert [p.entity_id for p in store.list()] == ["agent:a", "agent:b"]

    def test_upsert_overwrites_by_entity_id(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a", version="v1"))
        store.upsert(_passport("agent:a", version="v2"))
        loaded = store.get("agent:a")
        assert loaded is not None
        assert loaded.version == "v2"
        assert store.list() and len(store.list()) == 1

    def test_remove_existing_returns_true(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a"))
        assert store.remove("agent:a") is True
        assert store.get("agent:a") is None
        assert store.list() == []

    def test_remove_missing_returns_false(self, tmp_path: Path) -> None:
        assert PassportStore(tmp_path).remove("agent:ghost") is False

    def test_remove_missing_does_not_create_the_file(self, tmp_path: Path) -> None:
        PassportStore(tmp_path).remove("agent:ghost")
        assert list(tmp_path.iterdir()) == []


class TestList:
    def test_sorted_by_entity_id(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:zeta"))
        store.upsert(_passport("agent:alpha"))
        store.upsert(_passport("agent:mid"))
        assert [p.entity_id for p in store.list()] == ["agent:alpha", "agent:mid", "agent:zeta"]

    def test_type_filter(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a", entity_type=EntityKind.AGENT))
        store.upsert(_passport("tool:b", entity_type=EntityKind.TOOL))
        assert [p.entity_id for p in store.list(EntityKind.TOOL)] == ["tool:b"]

    def test_type_filter_accepts_the_string_form(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("tool:b", entity_type=EntityKind.TOOL))
        assert [p.entity_id for p in store.list("tool")] == ["tool:b"]

    def test_unknown_type_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="entity_type"):
            PassportStore(tmp_path).list("spaceship")

    def test_empty_store_lists_empty(self, tmp_path: Path) -> None:
        assert PassportStore(tmp_path).list() == []


class TestReplaceAll:
    def test_replaces_the_whole_document(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a"))
        store.replace_all([_passport("agent:b"), _passport("agent:c")])
        assert [p.entity_id for p in store.list()] == ["agent:b", "agent:c"]

    def test_replace_all_with_nothing_empties_the_store(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a"))
        store.replace_all([])
        assert store.list() == []

    def test_duplicate_entity_id_in_input_is_refused(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.upsert(_passport("agent:a"))
        with pytest.raises(ValueError, match="duplicate"):
            store.replace_all([_passport("agent:b"), _passport("agent:b", version="v2")])
        # The refusal changed nothing.
        assert [p.entity_id for p in store.list()] == ["agent:a"]

    def test_replace_all_persists_in_sorted_order(self, tmp_path: Path) -> None:
        store = PassportStore(tmp_path)
        store.replace_all([_passport("agent:z"), _passport("agent:a")])
        assert [p.entity_id for p in store.list()] == ["agent:a", "agent:z"]


class TestCorruptAndDuplicateFiles:
    def _write(self, tmp_path: Path, payload: str) -> PassportStore:
        (tmp_path / STORE_FILE_NAME).write_text(payload, encoding="utf-8")
        return PassportStore(tmp_path)

    def test_corrupt_file_is_unreadable_and_never_empty_valid(self, tmp_path: Path) -> None:
        store = self._write(tmp_path, "{not json")
        state = store.read_state()
        assert state.unreadable is True
        assert state.passports == ()
        assert state.error is not None
        assert "unreadable" in state.error

    def test_corrupt_file_makes_reads_raise(self, tmp_path: Path) -> None:
        store = self._write(tmp_path, "{not json")
        with pytest.raises(PassportStoreError, match="unreadable"):
            store.get("agent:a")
        with pytest.raises(PassportStoreError, match="unreadable"):
            store.list()

    def test_corrupt_file_is_never_silently_overwritten(self, tmp_path: Path) -> None:
        """The refusal that keeps one bad sector from becoming amnesia."""
        store = self._write(tmp_path, "{not json")
        with pytest.raises(PassportStoreError, match="unreadable"):
            store.upsert(_passport("agent:new"))
        with pytest.raises(PassportStoreError, match="unreadable"):
            store.replace_all([_passport("agent:new")])
        with pytest.raises(PassportStoreError, match="unreadable"):
            store.remove("agent:ghost")
        assert (tmp_path / STORE_FILE_NAME).read_text(encoding="utf-8") == "{not json"

    def test_wrong_top_level_shape_is_unreadable(self, tmp_path: Path) -> None:
        store = self._write(tmp_path, json.dumps(["not", "a", "document"]))
        state = store.read_state()
        assert state.unreadable is True
        assert state.passports == ()

    def test_missing_passports_key_is_unreadable(self, tmp_path: Path) -> None:
        store = self._write(tmp_path, json.dumps({"version": 1}))
        assert store.read_state().unreadable is True

    def test_malformed_entry_is_unreadable_not_dropped(self, tmp_path: Path) -> None:
        payload = json.dumps({"version": 1, "passports": [{"entity_id": "agent:a", "entity_type": "agent", "version": "v1"}, {"entity_id": "agent:broken"}]})
        store = self._write(tmp_path, payload)
        state = store.read_state()
        assert state.unreadable is True
        assert state.passports == ()

    def test_fabricated_statistic_on_disk_is_unreadable(self, tmp_path: Path) -> None:
        """A hand-edited file cannot claim a mean with zero observations."""
        payload = {
            "version": 1,
            "passports": [
                {
                    "entity_id": "agent:a",
                    "entity_type": "agent",
                    "version": "v1",
                    "capabilities": [{"capability": "x", "task_category": "*", "quality_mean": 0.99, "sample_count": 0}],
                }
            ],
        }
        store = self._write(tmp_path, json.dumps(payload))
        state = store.read_state()
        assert state.unreadable is True
        assert "sample_count" in (state.error or "")

    def test_duplicate_entity_id_keeps_first_and_records_the_error(self, tmp_path: Path) -> None:
        payload = {
            "version": 1,
            "passports": [
                {"entity_id": "agent:a", "entity_type": "agent", "version": "first"},
                {"entity_id": "agent:b", "entity_type": "agent", "version": "v1"},
                {"entity_id": "agent:a", "entity_type": "agent", "version": "second"},
            ],
        }
        store = self._write(tmp_path, json.dumps(payload))
        state = store.read_state()
        assert state.unreadable is False
        kept = {p.entity_id: p.version for p in state.passports}
        assert kept == {"agent:a": "first", "agent:b": "v1"}
        assert state.error is not None
        assert "duplicate" in state.error
        assert "agent:a" in state.error

    def test_reads_still_work_when_only_duplicates_are_reported(self, tmp_path: Path) -> None:
        payload = {
            "version": 1,
            "passports": [
                {"entity_id": "agent:a", "entity_type": "agent", "version": "first"},
                {"entity_id": "agent:a", "entity_type": "agent", "version": "second"},
            ],
        }
        store = self._write(tmp_path, json.dumps(payload))
        assert store.get("agent:a") is not None
        assert store.get("agent:a").version == "first"
        assert len(store.list()) == 1
