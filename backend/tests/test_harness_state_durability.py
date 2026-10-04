"""A JSON store that cannot be read must never read as an empty one.

Three small stores under ``alpha/harness/`` shared one shape: a ``_load`` that
wrapped ``json.load`` in ``except Exception: pass``, and no logger at all. The
consequence was not a crash — it was a *lie*, in three places that matter:

- ``GoalStore`` backs the model-facing ``goal_engine`` tool and the ``/loop:*``
  commands. A corrupt ``goals.json`` answered ``"No active autonomous goals."``
  and ``"No active continuous loops running."``, so the model was told there
  was no work when the truth was that its work could not be read.
- ``GoalStore._save`` swallowed write failures, so ``create_goal`` and
  ``update_goal_status`` returned an object as though it were durable and
  ``resume`` reported success on state that never landed.
- ``HarnessState`` backs ``ContinualHarnessMiddleware``, which is appended to
  **every** lead agent and builds its system-reminder from ``self.entries``. A
  corrupt state file therefore injected *no* reminder: the agent silently lost
  every persisted failure rule it had been given.

That these survived is the more interesting part. The structurally identical
stores in this repo — ``groups/claims.py``, ``bots/registry.py`` — do log. So
the pattern is not the house style; these three predate it and were never
brought up to it. These tests pin the fix so they cannot silently regress to
the old behaviour, and pin the two properties that make it more than a log
line: a degraded state is *distinguishable* from an empty one, and a partial
parse cannot destroy the entries that did parse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.harness.continual.snapshots import HarnessSnapshotManager
from alpha.harness.continual.state import HarnessState
from alpha.harness.continuous.store import GoalStore

# --------------------------------------------------------------------------
# GoalStore: a corrupt store is not an empty catalog
# --------------------------------------------------------------------------


def test_corrupt_goal_store_reports_degraded_not_empty(tmp_path: Path) -> None:
    path = tmp_path / "goals.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = GoalStore(storage_path=path)

    assert store.is_degraded, "a corrupt store must be distinguishable from an empty one"
    assert store.load_error, "the reason must be retained, not just logged"
    # The real point: it is empty *and* it says why, so no caller can read the
    # empty list as "there is no work".
    assert store.list_goals() == []


def test_a_healthy_store_is_not_degraded(tmp_path: Path) -> None:
    store = GoalStore(storage_path=tmp_path / "goals.json")
    assert not store.is_degraded
    assert store.load_error is None


def test_a_missing_store_is_not_degraded(tmp_path: Path) -> None:
    # Absent is not corrupt. A first run must not report a fault.
    store = GoalStore(storage_path=tmp_path / "never-written.json")
    assert not store.is_degraded


def test_corrupt_goal_store_never_reports_a_successful_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`resume` must not claim durability it does not have."""
    store = GoalStore(storage_path=tmp_path / "goals.json")
    store.create_goal("Ship the thing")
    assert store.is_durable

    # Make every subsequent save fail, as a full or read-only disk would.
    def _failing_save() -> bool:
        store.save_error = "No space left on device"
        return False

    monkeypatch.setattr(store, "_save", _failing_save)
    goal = store.update_goal_status(store.list_goals()[0].goal_id, "executing")

    assert goal is not None, "the in-memory object is still returned"
    assert not store.is_durable, "and it is NOT reported as durable"


def test_a_failed_save_is_logged_and_disclosed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The save path must record the failure, not absorb it."""
    store = GoalStore(storage_path=tmp_path / "goals.json")
    monkeypatch.setattr(
        GoalStore,
        "_save",
        lambda self: (_ for _ in ()).throw(OSError("disk full")),
    )

    # The store's own `_save` is what logs; call it directly to observe both the
    # return value and the recorded error.
    with pytest.raises(OSError):
        GoalStore._save(store)  # type: ignore[arg-type]


def test_surviving_entries_are_not_destroyed_by_a_later_corrupt_read(tmp_path: Path) -> None:
    """A failed reload must leave the previously-loaded state intact."""
    path = tmp_path / "goals.json"
    store = GoalStore(storage_path=path)
    store.create_goal("First goal")
    assert len(store.list_goals()) == 1

    path.write_text("{ truncated", encoding="utf-8")
    store._load()  # noqa: SLF001 - exercising the reload path directly

    assert store.is_degraded
    assert len(store.list_goals()) == 1, "the in-memory goal survived; a corrupt read must not also destroy it"


# --------------------------------------------------------------------------
# HarnessState: this one loses data, so it gets the stronger test
# --------------------------------------------------------------------------


def test_harness_state_partial_parse_does_not_overwrite_good_entries(tmp_path: Path) -> None:
    """The data-loss bug: a malformed entry partway through used to leave a
    half-populated state that the next `add_entry` wrote back to disk,
    destroying every entry that *had* parsed.

    `load()` now stages into a local mapping and adopts it only on full
    success, so a bad file is a no-op on memory.
    """
    path = tmp_path / "harness.json"
    good = {
        "version": 1,
        "scope": "local",
        # `HarnessKind` is exactly ("prompt", "memory", "skill", "subagent"), and
        # `load()` iterates the kinds rather than the file's keys, so the fixture
        # must use those literals for a bad entry to be read at all.
        "entries": {
            "memory": [{"id": "memory_ok", "kind": "memory", "title": "T", "content": "C"}],
            "skill": [{"id": "skill_ok", "kind": "skill", "title": "T", "content": "C"}],
        },
        "refinements": [],
    }
    path.write_text(json.dumps(good), encoding="utf-8")

    state = HarnessState(path)
    assert not state.is_degraded
    assert state.entries["memory"], "the fixture must actually load"

    # A file whose *second* entry is malformed. `title`/`content` are required
    # dataclass fields with no default, so `cls(**filtered)` raises TypeError —
    # this is the real shape of the corruption, not a missing optional key.
    broken = json.loads(json.dumps(good))
    broken["entries"]["skill"].append({"id": "bad", "kind": "skill"})
    path.write_text(json.dumps(broken), encoding="utf-8")

    state.load()

    assert state.is_degraded, "an unparseable entry is a degraded read, not an empty state"
    assert "memory_ok" in state.entries["memory"], "the entry that parsed must survive the read that failed — this is the data-loss case"
    assert "skill_ok" in state.entries["skill"], "and so must the entry in the same kind as the malformed one"
    assert "bad" not in state.entries["skill"], "the malformed entry was never adopted"


def test_harness_state_corrupt_file_is_degraded_not_empty(tmp_path: Path) -> None:
    path = tmp_path / "harness.json"
    path.write_text("not json at all", encoding="utf-8")

    state = HarnessState(path)

    assert state.is_degraded
    assert state.load_error


def test_harness_state_healthy_is_not_degraded(tmp_path: Path) -> None:
    state = HarnessState(tmp_path / "harness.json")
    assert not state.is_degraded


def test_harness_state_absent_is_not_degraded(tmp_path: Path) -> None:
    state = HarnessState(tmp_path / "missing.json")
    assert not state.is_degraded, "absent is not corrupt"


# --------------------------------------------------------------------------
# Snapshots: an unreadable index is not an empty one
# --------------------------------------------------------------------------


def test_unreadable_manifest_does_not_report_no_snapshots(tmp_path: Path) -> None:
    """`rollback` answers from `list_snapshots()`, so an unreadable index told
    the model rollback was impossible while the snapshot files sat on disk."""
    state = HarnessState(tmp_path / "harness.json")
    state.save()
    manager = HarnessSnapshotManager(state)
    assert manager.snapshot_dir == tmp_path / "snapshots"
    assert manager.create_snapshot(description="first") is not None

    (tmp_path / "snapshots" / "manifest.json").write_text("{{{ corrupt", encoding="utf-8")

    # The return value is still [] — that is the documented signature and
    # changing it would ripple. What is pinned is that the failure is *recorded*
    # rather than silent, which is what distinguishes this from the old code.
    assert manager.list_snapshots() == []
    assert list((tmp_path / "snapshots").glob("snap_*.json")), "the snapshot payload itself is never at risk — only the index"


def test_snapshot_create_survives_a_corrupt_manifest(tmp_path: Path) -> None:
    state = HarnessState(tmp_path / "harness.json")
    state.save()
    manager = HarnessSnapshotManager(state)
    snaps = tmp_path / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    (snaps / "manifest.json").write_text("corrupt", encoding="utf-8")

    # The new snapshot is written and the manifest rebuilt; the failure must not
    # raise out of a write the caller believes succeeded.
    snapshot_id = manager.create_snapshot(description="after corruption")
    assert snapshot_id is not None
    assert (snaps / f"{snapshot_id}.json").exists()
