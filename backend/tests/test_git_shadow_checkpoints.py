"""Tests for Git Shadow Checkpoints and Rollback Engine."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from agent_workspace.tools.builtins.code_agentic_core import (
    CodeCheckpoint,
    create_shadow_checkpoint,
    get_all_checkpoints,
    rollback_to_checkpoint,
    get_checkpoint_diff,
    manage_code_checkpoint,
    _ACTIVE_CHECKPOINTS,
)


def test_create_and_list_checkpoints(tmp_path):
    # Setup sample files
    f1 = tmp_path / "app.py"
    f1.write_text("print('hello v1')\n", encoding="utf-8")

    cp = create_shadow_checkpoint(
        label="test initial checkpoint",
        root_path=str(tmp_path),
        target_files=["app.py"],
    )

    assert cp.checkpoint_id.startswith("chk_")
    assert cp.label == "test initial checkpoint"
    assert "app.py" in cp.files_snapshot
    assert cp.files_snapshot["app.py"] == "print('hello v1')\n"

    all_cps = get_all_checkpoints()
    assert any(c["checkpoint_id"] == cp.checkpoint_id for c in all_cps)


def test_rollback_restores_file_state(tmp_path):
    f1 = tmp_path / "service.py"
    f1.write_text("def run():\n    return 'clean'\n", encoding="utf-8")

    cp = create_shadow_checkpoint(
        label="clean state",
        root_path=str(tmp_path),
        target_files=["service.py"],
    )

    # Mutate file
    f1.write_text("def run():\n    return 'corrupted'\n", encoding="utf-8")
    assert "corrupted" in f1.read_text(encoding="utf-8")

    # Rollback
    res = rollback_to_checkpoint(checkpoint_id=cp.checkpoint_id, root_path=str(tmp_path))
    assert res["status"] == "rolled_back"
    assert "service.py" in res["restored_files"]

    # Verify restored state
    restored = f1.read_text(encoding="utf-8")
    assert "clean" in restored
    assert "corrupted" not in restored


def test_diff_shows_workspace_modifications(tmp_path):
    f1 = tmp_path / "router.py"
    f1.write_text("line 1\nline 2\n", encoding="utf-8")

    cp = create_shadow_checkpoint(
        label="before edits",
        root_path=str(tmp_path),
        target_files=["router.py"],
    )

    # Modify
    f1.write_text("line 1\nmodified line 2\nline 3\n", encoding="utf-8")

    diff_data = get_checkpoint_diff(checkpoint_id=cp.checkpoint_id, root_path=str(tmp_path))
    assert diff_data["checkpoint_id"] == cp.checkpoint_id
    assert "router.py" in diff_data["diffs"]
    diff_text = diff_data["diffs"]["router.py"]
    assert "+modified line 2" in diff_text
    assert "+line 3" in diff_text


def test_manage_code_checkpoint_tool_integration(tmp_path):
    f1 = tmp_path / "core.py"
    f1.write_text("x = 10\n", encoding="utf-8")

    # 1. Create via tool
    res_str = manage_code_checkpoint.func(
        action="create",
        label="tool test cp",
        target_files=["core.py"],
        root_path=str(tmp_path),
    )
    res = json.loads(res_str)
    assert res["status"] == "created"
    cid = res["checkpoint_id"]

    # 2. Mutate file
    f1.write_text("x = 999\n", encoding="utf-8")

    # 3. Diff via tool
    diff_str = manage_code_checkpoint.func(
        action="diff",
        checkpoint_id=cid,
        root_path=str(tmp_path),
    )
    diff_res = json.loads(diff_str)
    assert "core.py" in diff_res["diffs"]

    # 4. Rollback via tool
    rb_str = manage_code_checkpoint.func(
        action="rollback",
        checkpoint_id=cid,
        root_path=str(tmp_path),
    )
    rb_res = json.loads(rb_str)
    assert rb_res["status"] == "rolled_back"
    assert f1.read_text(encoding="utf-8") == "x = 10\n"
