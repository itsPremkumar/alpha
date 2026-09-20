"""Tests for Boulder State Machine & Checkpointing."""

from pathlib import Path

from alpha.state.boulder import (
    append_session_id,
    clear_boulder,
    create_boulder,
    load_boulder,
    update_checklist_item,
)


def test_create_and_load_boulder(tmp_path: Path):
    bp = tmp_path / "test_boulder.json"
    checklist = ["Explore codebase", "Implement feature", "Run verification tests"]

    state = create_boulder("Integrate OmO Features", checklist, path=bp, session_id="sess_001")
    assert state.work_id.startswith("work_")
    assert len(state.checklist) == 3
    assert state.status == "in_progress"

    loaded = load_boulder(bp)
    assert loaded is not None
    assert loaded.work_id == state.work_id
    assert loaded.session_ids == ["sess_001"]


def test_update_checklist_and_auto_completion(tmp_path: Path):
    bp = tmp_path / "test_boulder.json"
    create_boulder("Short Task", ["Step 1", "Step 2"], path=bp)

    s1 = update_checklist_item(0, True, evidence="Evidence 1", path=bp)
    assert s1.checklist[0].completed is True
    assert s1.checklist[0].evidence == "Evidence 1"
    assert s1.status == "in_progress"

    s2 = update_checklist_item(1, True, evidence="Evidence 2", path=bp)
    assert s2.checklist[1].completed is True
    assert s2.status == "completed"


def test_append_session_and_clear(tmp_path: Path):
    bp = tmp_path / "test_boulder.json"
    create_boulder("Long Running", ["Step 1"], path=bp, session_id="sess_01")
    
    append_session_id("sess_02", path=bp)
    loaded = load_boulder(bp)
    assert loaded.session_ids == ["sess_01", "sess_02"]

    clear_boulder(bp)
    assert load_boulder(bp) is None
