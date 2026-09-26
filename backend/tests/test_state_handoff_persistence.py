"""The handoff manager must never destroy the handoff it is asked to keep.

``SessionHandoffManager`` exists to carry state across sessions — its own
docstring says *\"so the agent can work continuously for hours across multiple
sessions without losing state or coherence\"* — so the one thing it must not do
is damage what is already on disk.

The original ``save_handoff`` opened the target with mode ``\"w\"`` (which
truncates immediately) and streamed ``json.dump`` straight into that handle.
Any failure part-way through therefore left a partial document behind. MEASURED
at HEAD:

* re-saving the same ``work_id`` with a value JSON cannot encode raised
  ``TypeError`` and left ``handoff_task-1.json`` at **153 corrupt bytes**, with
  ``b\"milestone-1\" in before`` being ``False``: the handoff saved a moment
  earlier was gone;
* a failed write of ``latest.json`` left it at **0 bytes**, after which
  ``load_latest_handoff()`` returned ``None`` following
  ``Failed to load latest handoff: Expecting value: line 1 column 1`` — total,
  silent loss of the handoff this module exists to provide.

The fix serializes the whole document *before* any file is opened and writes it
through a temporary file that atomically replaces the target — the same shape
``alpha.projects.handoffs.HandoffStore._save`` already uses.

The failure injections deliberately cover **both** serialization entry points
(``json.dump``, the original's streaming path, and ``json.dumps``, the
serialize-then-write path) and **both** write paths (``json.dump`` into a handle
and ``Path.write_text``), so whichever mechanism the implementation under test
uses, the assertion that the on-disk handoff survives still holds. That keeps
these tests from passing vacuously when the mechanism changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.state.handoff import SessionHandoffManager, SessionHandoffPackage


def _pkg(work_id: str, step: str, evidence: object) -> SessionHandoffPackage:
    """A handoff shaped like the ones the boulder checkpoint tool produces."""
    return SessionHandoffPackage(
        work_id=work_id,
        task_objective="long-horizon task",
        completed_milestones=[{"step": step, "evidence": evidence}],
        next_action="continue",
    )


def _bytes(mgr: SessionHandoffManager, name: str) -> bytes | None:
    path = mgr.handoff_dir / name
    return path.read_bytes() if path.exists() else None


def test_round_trip_writes_both_files_and_loads_back(tmp_path: Path) -> None:
    """Happy path: a clean save leaves two readable documents that agree."""
    mgr = SessionHandoffManager(base_path=tmp_path)

    mgr.save_handoff(_pkg("task-1", "milestone-1", "all green"))

    target = _bytes(mgr, "handoff_task-1.json")
    latest = _bytes(mgr, "latest.json")
    assert target is not None, "handoff_task-1.json was not written"
    assert latest is not None, "latest.json was not written"
    assert json.loads(target.decode("utf-8")) == json.loads(latest.decode("utf-8"))

    loaded = mgr.load_latest_handoff()
    assert loaded is not None
    assert loaded.work_id == "task-1"
    assert loaded.completed_milestones == [{"step": "milestone-1", "evidence": "all green"}]

    # a completed save must not leave staging files behind
    debris = sorted(p.name for p in mgr.handoff_dir.iterdir() if not p.name.endswith(".json"))
    assert debris == [], f"save_handoff left staging debris behind: {debris}"


def test_a_serialization_failure_leaves_the_saved_handoff_intact(tmp_path: Path) -> None:
    """Bad data in a later checkpoint must not erase the earlier checkpoint."""
    mgr = SessionHandoffManager(base_path=tmp_path)
    mgr.save_handoff(_pkg("task-1", "milestone-1", "all green"))
    before = _bytes(mgr, "handoff_task-1.json")
    assert before is not None and b"milestone-1" in before

    # agent-authored milestone evidence that JSON cannot encode
    with pytest.raises(TypeError):
        mgr.save_handoff(_pkg("task-1", "milestone-2", object()))

    after = _bytes(mgr, "handoff_task-1.json")
    assert after == before, "the handoff already on disk was destroyed by the failed save"
    assert b"milestone-1" in (after or b"")

    loaded = mgr.load_latest_handoff()
    assert loaded is not None, "load_latest_handoff() returned None after a failed save"
    assert loaded.completed_milestones[0]["step"] == "milestone-1"


def test_a_failing_persist_leaves_both_documents_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed persistence (disk full, interrupted write) must not truncate either file.

    Both ``json.dump`` (the original's streaming path) and ``json.dumps`` (the
    serialize-then-write path) are made to fail, so the test bites whichever of
    the two the implementation under test reaches.
    """
    mgr = SessionHandoffManager(base_path=tmp_path)
    mgr.save_handoff(_pkg("task-1", "milestone-1", "all green"))
    target_before = _bytes(mgr, "handoff_task-1.json")
    latest_before = _bytes(mgr, "latest.json")
    assert target_before is not None and latest_before is not None

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(json, "dump", boom)
    monkeypatch.setattr(json, "dumps", boom)

    with pytest.raises(OSError):
        mgr.save_handoff(_pkg("task-1", "milestone-2", "again green"))

    assert _bytes(mgr, "handoff_task-1.json") == target_before, "handoff_task-1.json was truncated by the failed save"
    assert _bytes(mgr, "latest.json") == latest_before, "latest.json was truncated by the failed save"

    loaded = mgr.load_latest_handoff()
    assert loaded is not None, "load_latest_handoff() returned None after a failed save"
    assert loaded.completed_milestones[0]["step"] == "milestone-1"


def test_a_failure_on_the_second_file_keeps_latest_loadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``save_handoff`` writes two files; failing the later one must not cost the handoff.

    The original wrote ``handoff_<id>.json`` first and ``latest.json`` second,
    both by truncation, so a failure on the second write left ``latest.json``
    empty and made ``load_latest_handoff()`` return ``None``. That is the
    single worst outcome this module can produce, because ``latest.json`` is the
    only file ``load_latest_handoff()`` ever reads.

    The injection fails the *second file write* through whichever path the
    implementation uses: ``json.dump`` (call 2 targets ``latest.json`` in the
    original) or ``Path.write_text`` (call 2 stages ``latest.json`` once the
    document is serialized up front).
    """
    mgr = SessionHandoffManager(base_path=tmp_path)
    mgr.save_handoff(_pkg("task-1", "milestone-1", "all green"))
    target_before = _bytes(mgr, "handoff_task-1.json")
    assert target_before is not None

    real_dump = json.dump
    dump_calls = {"n": 0}

    def fail_second_dump(obj: object, fp: object, *args: object, **kwargs: object) -> None:
        dump_calls["n"] += 1
        if dump_calls["n"] >= 2:
            raise OSError(28, "No space left on device")
        real_dump(obj, fp, *args, **kwargs)

    real_write_text = Path.write_text
    write_calls = {"n": 0}

    def fail_second_write(self: Path, data: str, *args: object, **kwargs: object) -> int:
        write_calls["n"] += 1
        if write_calls["n"] >= 2:
            raise OSError(28, "No space left on device")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(json, "dump", fail_second_dump)
    monkeypatch.setattr(Path, "write_text", fail_second_write)

    with pytest.raises(OSError):
        mgr.save_handoff(_pkg("task-1", "milestone-2", "again green"))

    # the first file must not have been lost either
    assert _bytes(mgr, "handoff_task-1.json") is not None, "handoff_task-1.json disappeared"

    loaded = mgr.load_latest_handoff()
    assert loaded is not None, "load_latest_handoff() returned None after the later write failed"
    assert loaded.completed_milestones[0]["step"] == "milestone-1"
