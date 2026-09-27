"""Persistence audit: a failed save must never destroy the state already on disk.

Every case here is a *failure injection*, not a shape check. The injected
failures are the two that actually happen in production and that a bare
``write_text``/``open(path, "w") + json.dump`` cannot survive:

* **ENOSPC / crash / ``KeyboardInterrupt`` partway through the write.** The
  file has already been truncated by the time the write fails, so the
  pre-existing bytes are gone. The ``_enospc_after_truncation_open`` /
  ``_truncating_write_text`` faults model exactly that order of events.
* **A payload the serializer rejects.** The serialization error is raised
  while streaming into an already-truncated handle.

Both share one property, and it is the property these stores are built
around: a save that does not complete must leave the previous durable document
byte-for-byte intact, and the next load must still see the earlier state.
Silently answering "no state" instead is worse than a crash, because the
caller is then told there is nothing to resume.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from alpha.memory.contrastive_trajectory_replay import ContrastiveTrajectoryReplay
from alpha.state.boulder import BoulderState, ChecklistItem, create_boulder, load_boulder, save_boulder, update_checklist_item

_REAL_OPEN = open


def _truncating_write_text(self: Path, data, **kwargs) -> None:
    """``Path.write_text`` that truncates the target and *then* fails.

    This is the real ENOSPC/crash ordering: ``open(..., "w")`` empties the file
    before a single payload byte is written, and only afterwards does the write
    fail. A fault raised *before* opening the file would not exercise anything,
    because that is the one ordering a reader never observes.
    """
    with open(self, "w", **kwargs) as handle:
        handle.flush()
    raise OSError(errno.ENOSPC, "No space left on device")


def _enospc_after_truncation_open(file, mode="r", *args, **kwargs):
    """``open`` that truncates the target and *then* fails, as ENOSPC does.

    The truncation is performed by the OS at open time (``O_TRUNC``), so this
    models the real ordering precisely: the pre-existing bytes are gone *before*
    a single payload byte is written, and only afterwards does the write fail.

    Injected at ``builtins.open`` *and* ``io.open`` on purpose -- those are two
    separate module bindings, and the two write paths reach them differently:
    a pre-fix ``open(target, "w") + json.dump`` resolves ``builtins.open``,
    while a staging ``Path.write_text`` goes through ``Path.open`` ->
    ``io.open``. Patching only one would leave the other shape unexercised and
    the test would pass for the wrong reason.
    """
    handle = _REAL_OPEN(file, mode, *args, **kwargs)
    if any(flag in mode for flag in ("w", "a", "x", "+")):
        handle.close()
        raise OSError(errno.ENOSPC, "No space left on device")
    return handle


def _seed_boulder(bp: Path) -> BoulderState:
    """Create a boulder and complete its first step, so there is state worth losing."""
    create_boulder("Ship the audit", ["Explore", "Fix", "Verify"], path=bp, session_id="sess_1")
    update_checklist_item(0, True, evidence="milestone-1", path=bp)
    return load_boulder(bp)


# ---------------------------------------------------------------------------
# alpha.state.boulder
# ---------------------------------------------------------------------------


def test_boulder_a_serialisation_failure_never_opens_the_target(tmp_path: Path) -> None:
    """A payload the serializer rejects must not reach the filesystem at all.

    ``BoulderState`` is a plain dataclass, so nothing validates the field
    types; ``asdict()`` happily carries a non-JSON value into the serializer.
    With a pre-fix ``open(target, "w") + json.dump`` the target was already
    truncated when the ``TypeError`` was raised, so the whole checkpoint was
    destroyed by a save that never succeeded.
    """
    bp = tmp_path / "boulder.json"
    _seed_boulder(bp)
    before = bp.read_bytes()
    assert before, "the seeded boulder must exist on disk before the failed save"

    poisoned = BoulderState(
        work_id="work_poisoned",
        top_level_task="Ship the audit",
        checklist=[ChecklistItem(item=object())],  # type: ignore[arg-type]
    )
    with pytest.raises(TypeError):
        save_boulder(poisoned, path=bp)

    assert bp.read_bytes() == before, "a failed save must leave the saved boulder byte-for-byte intact"
    survived = load_boulder(bp)
    assert survived is not None, "the loader must still find the boulder that was already on disk"
    assert survived.checklist[0].evidence == "milestone-1"


def test_boulder_an_interrupted_write_never_leaves_a_partial_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that dies after truncation must not cost us the prior checkpoint."""
    bp = tmp_path / "boulder.json"
    _seed_boulder(bp)
    before = bp.read_bytes()

    monkeypatch.setattr("builtins.open", _enospc_after_truncation_open)
    monkeypatch.setattr("io.open", _enospc_after_truncation_open)
    with pytest.raises(OSError):
        save_boulder(load_boulder(bp), path=bp)
    monkeypatch.undo()

    assert bp.read_bytes() == before, "an interrupted save must leave the saved boulder byte-for-byte intact"
    survived = load_boulder(bp)
    assert survived is not None
    assert survived.checklist[0].evidence == "milestone-1"
    assert not list(tmp_path.glob("*.tmp")), "the staging file must be cleaned up, not left as debris"


def test_boulder_a_legal_save_still_round_trips(tmp_path: Path) -> None:
    """Positive control: the atomic path must not stop ordinary checkpoints."""
    bp = tmp_path / "boulder.json"
    state = create_boulder("Ordinary task", ["One", "Two"], path=bp, session_id="sess_1")
    state = update_checklist_item(1, True, evidence="done-two", path=bp)

    reloaded = load_boulder(bp)
    assert reloaded is not None
    assert reloaded.work_id == state.work_id
    assert reloaded.session_ids == ["sess_1"]
    assert reloaded.checklist[1].evidence == "done-two"
    assert json.loads(bp.read_text(encoding="utf-8"))["top_level_task"] == "Ordinary task"


# ---------------------------------------------------------------------------
# alpha.memory.contrastive_trajectory_replay -- whole-file rewrite, silent reload
# ---------------------------------------------------------------------------

_SIGNATURE = "ZeroDivisionError: division by zero"
_HYPOTHESIS = "Multiply the numerator by 10 to avoid dividing by zero"
_PATCH = "return (n * 10) / d"


def _seed_contrastive(storage: Path) -> ContrastiveTrajectoryReplay:
    mem = ContrastiveTrajectoryReplay(persistence_path=storage)
    mem.record_outcome(
        task_id="task_1",
        failure_signature=_SIGNATURE,
        erroneous_hypothesis=_HYPOTHESIS,
        failed_patch=_PATCH,
        winning_resolution="if d == 0: return 0.0",
    )
    return mem


def test_contrastive_an_interrupted_save_keeps_the_records_already_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_save_to_disk`` rewrites the *whole* file on every recorded outcome.

    A partial write therefore costs every prior record, not just the new one.
    """
    storage = tmp_path / "contrastive_memory.json"
    _seed_contrastive(storage)
    before = storage.read_bytes()
    assert before, "the seeded contrastive memory must exist on disk before the failed save"

    monkeypatch.setattr(Path, "write_text", _truncating_write_text)
    ContrastiveTrajectoryReplay(persistence_path=storage).record_outcome(
        task_id="task_2",
        failure_signature="TypeError: unsupported operand type",
        erroneous_hypothesis="Cast the string straight to an integer",
        failed_patch="x = int(val)",
    )
    monkeypatch.undo()

    assert storage.read_bytes() == before, "a failed save must leave every prior record on disk"
    assert not list(tmp_path.glob("*.tmp")), "the staging file must be cleaned up, not left as debris"


def test_contrastive_a_failed_save_does_not_erase_the_cyclic_trap_detector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The consequence that makes the empty reload dangerous, not merely lossy.

    ``_load_from_disk`` swallows a parse error and leaves ``records`` empty, so
    a truncated file does not raise -- ``detect_cyclic_trap`` answers
    ``is_cyclic_trap: False`` for a hypothesis that previously *was* recorded
    as a failed path. The agent is then told, confidently, that repeating the
    mistake is safe.
    """
    storage = tmp_path / "contrastive_memory.json"
    _seed_contrastive(storage)

    monkeypatch.setattr(Path, "write_text", _truncating_write_text)
    ContrastiveTrajectoryReplay(persistence_path=storage).record_outcome(
        task_id="task_2",
        failure_signature="TypeError: unsupported operand type",
        erroneous_hypothesis="Cast the string straight to an integer",
        failed_patch="x = int(val)",
    )
    monkeypatch.undo()

    reopened = ContrastiveTrajectoryReplay(persistence_path=storage)
    trap = reopened.detect_cyclic_trap(proposed_hypothesis=_HYPOTHESIS, proposed_patch=_PATCH)
    assert trap["is_cyclic_trap"] is True, "a failed save must not make a known failed path look safe"
    assert reopened.records, "the recorded failure must still be present after a failed save"
    assert len(reopened.query_negative_constraints(query=_HYPOTHESIS)) == 1


def test_contrastive_a_legal_save_still_persists_every_record(tmp_path: Path) -> None:
    """Positive control: the atomic path must not stop ordinary recording."""
    storage = tmp_path / "contrastive_memory.json"
    mem = _seed_contrastive(storage)
    mem.record_outcome(
        task_id="task_2",
        failure_signature="TypeError: unsupported operand type",
        erroneous_hypothesis="Cast the string straight to an integer",
        failed_patch="x = int(val)",
    )

    reopened = ContrastiveTrajectoryReplay(persistence_path=storage)
    assert len(reopened.records) == 2
    assert reopened.detect_cyclic_trap(proposed_hypothesis=_HYPOTHESIS)["is_cyclic_trap"] is True
    assert not list(tmp_path.glob("*.tmp"))
