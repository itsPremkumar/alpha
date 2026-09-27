"""The model-facing boulder tool must not lose a checkpoint on a failed save.

`test_persist_audit_atomic_state_writes.py` already covers the durability
contract of `alpha.state.boulder.save_boulder` at the module boundary. This file
covers the gap it does not: the *tool* boundary.

`boulder_checkpoint_manage` is a model-facing builtin tool
(`BUILTIN_TOOLS` in `alpha.tools.tools`), and three of its actions --
`update_step`, `append_session`, `complete` -- route through `save_boulder`.
The tool wraps each call in `except Exception as e: return f"Error: {e}"`, so
the model is told the save failed *after* the checkpoint file has already been
destroyed, and is free to retry against a file that no longer holds anything.

That is the shape the durability fix exists to prevent, and it is only
observable through the tool, so it is pinned here.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from alpha.state.boulder import load_boulder
from alpha.tools.builtins.boulder_checkpoint_tool import boulder_checkpoint_manage

_REAL_OPEN = open


def _enospc_after_truncation_open(file, mode="r", *args, **kwargs):
    """``open`` that truncates the target and *then* fails, as ENOSPC does.

    ``O_TRUNC`` empties the file at open time, so this models the real
    ordering: the pre-existing bytes are gone *before* a single payload byte is
    written. Patched at ``builtins.open`` *and* ``io.open`` because a pre-fix
    ``open(target, "w") + json.dump`` resolves ``builtins.open`` while a
    staging ``Path.write_text`` goes through ``Path.open`` -> ``io.open``.
    """
    handle = _REAL_OPEN(file, mode, *args, **kwargs)
    if any(flag in mode for flag in ("w", "a", "x", "+")):
        handle.close()
        raise OSError(errno.ENOSPC, "No space left on device")
    return handle


def _seed(bp: Path) -> None:
    reply = boulder_checkpoint_manage.invoke(
        {
            "action": "create",
            "task": "Ship the durable resume path",
            "checklist": ["Explore", "Fix", "Verify"],
            "session_id": "sess_1",
            "custom_path": str(bp),
        }
    )
    assert "Created Boulder checkpoint" in reply, reply
    reply = boulder_checkpoint_manage.invoke({"action": "update_step", "step_index": 0, "completed": True, "evidence": "milestone-1", "custom_path": str(bp)})
    assert "Current boulder status" in reply, reply


def _assert_checkpoint_intact(bp: Path, before: bytes) -> None:
    assert bp.read_bytes() == before, "the checkpoint already on disk was destroyed by a failed save through the tool"
    survived = load_boulder(bp)
    assert survived is not None, "the checkpoint became unreachable after a failed save through the tool"
    assert survived.checklist[0].evidence == "milestone-1"


@pytest.mark.parametrize("action,extra", [("update_step", {"step_index": 1, "completed": True, "evidence": "milestone-2"}), ("append_session", {"session_id": "sess_2"}), ("complete", {})])
def test_a_failing_tool_save_leaves_the_checkpoint_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, extra: dict) -> None:
    """ENOSPC on a tool write must cost the tool only an error string, not the checkpoint."""
    bp = tmp_path / "boulder.json"
    _seed(bp)
    before = bp.read_bytes()

    monkeypatch.setattr("builtins.open", _enospc_after_truncation_open)
    monkeypatch.setattr("io.open", _enospc_after_truncation_open)
    try:
        reply = boulder_checkpoint_manage.invoke({"action": action, "custom_path": str(bp), **extra})
    finally:
        monkeypatch.undo()

    assert "Error" in reply, f"the tool must report the failure honestly, got: {reply!r}"
    _assert_checkpoint_intact(bp, before)
    assert not list(tmp_path.glob("*.tmp")), "a failed tool save left staging debris behind"


def test_the_tool_still_saves_ordinary_checkpoints(tmp_path: Path) -> None:
    """Positive control: the durability fix must not stop the tool working."""
    bp = tmp_path / "boulder.json"
    _seed(bp)

    reply = boulder_checkpoint_manage.invoke({"action": "update_step", "step_index": 1, "completed": True, "evidence": "milestone-2", "custom_path": str(bp)})
    assert "Current boulder status" in reply, reply

    saved = load_boulder(bp)
    assert saved is not None
    # the third checklist step is still outstanding, so its evidence stays empty
    assert [item.evidence for item in saved.checklist] == ["milestone-1", "milestone-2", ""]
    assert [item.completed for item in saved.checklist] == [True, True, False]

    reply = boulder_checkpoint_manage.invoke({"action": "append_session", "session_id": "sess_2", "custom_path": str(bp)})
    assert "sess_2" in reply, reply
    saved = load_boulder(bp)
    assert saved is not None
    assert saved.session_ids == ["sess_1", "sess_2"]
    assert not list(tmp_path.glob("*.tmp"))
