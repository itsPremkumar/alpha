"""Unit tests for Continual Harness state, snapshots, refinement, and middleware."""

from pathlib import Path

import pytest
from langchain_core.messages import SystemMessage

from alpha.agents.middlewares.continual_harness_middleware import (
    CONTINUAL_HARNESS_REMINDER_KEY,
    ContinualHarnessMiddleware,
)
from alpha.harness.continual.refine import ContinualRefinementEngine
from alpha.harness.continual.snapshots import HarnessSnapshotManager
from alpha.harness.continual.state import HarnessState
from alpha.tools.builtins.harness_refine_tool import harness_refine_tool


def test_harness_state_crud(tmp_path: Path):
    state_file = tmp_path / "harness_state.json"
    state = HarnessState(file_path=state_file, scope="local")

    # Add entries
    e1 = state.add_entry("prompt", "Strict Linting", "Always run ruff check before commits.")
    e2 = state.add_entry("memory", "DB Port", "Local postgres runs on port 5433.", path="database")

    assert e1.id.startswith("prompt_")
    assert state.get_entry(e1.id) is not None
    assert len(state.list_entries()) == 2

    # Update entry
    updated = state.update_entry(e1.id, content="Always run ruff format and ruff check.")
    assert updated is not None
    assert updated.version == 2
    assert "format" in state.get_entry(e1.id).content

    # Format for prompt
    prompt_text = state.format_for_prompt()
    assert "Strict Linting" in prompt_text
    assert "DB Port" in prompt_text

    # Reload from disk in a fresh instance
    state2 = HarnessState(file_path=state_file, scope="local")
    assert len(state2.list_entries()) == 2
    assert state2.get_entry(e1.id).version == 2

    # Remove entry
    assert state2.remove_entry(e2.id) is True
    assert len(state2.list_entries()) == 1


def test_harness_snapshots_and_rollback(tmp_path: Path):
    state_file = tmp_path / "harness_state.json"
    state = HarnessState(file_path=state_file, scope="local")
    mgr = HarnessSnapshotManager(state)

    state.add_entry("memory", "Initial Rule", "Rule 1")
    snap1 = mgr.create_snapshot(description="Base state")
    assert snap1 is not None

    state.add_entry("memory", "Second Rule", "Rule 2")
    assert len(state.list_entries()) == 2

    snapshots = mgr.list_snapshots()
    assert len(snapshots) >= 1
    assert snapshots[0]["snapshot_id"] == snap1

    # Rollback to snap1
    assert mgr.restore_snapshot(snap1) is True
    assert len(state.list_entries()) == 1
    assert state.list_entries()[0].title == "Initial Rule"


def test_continual_refinement_engine(tmp_path: Path):
    state_file = tmp_path / "harness_state.json"
    state = HarnessState(file_path=state_file, scope="local")
    engine = ContinualRefinementEngine(state)

    events = [
        {
            "type": "tool_result",
            "payload": {
                "tool_name": "read_file",
                "output": "Error: FileNotFoundError: [Errno 2] No such file or directory: 'missing.py'",
            },
        },
        {
            "type": "tool_result",
            "payload": {
                "tool_name": "write_file",
                "output": "Error: write_file content (95000 bytes) exceeds the single-call limit.",
            },
        },
    ]

    refine_event = engine.refine_from_events(events, trigger="test_errors")
    assert refine_event is not None
    assert len(refine_event.changes) == 2

    entries = state.list_entries()
    titles = [e.title for e in entries]
    assert any("Path Verification Rule" in t for t in titles)
    assert any("Size Limit Policy" in t for t in titles)


def test_continual_harness_middleware(tmp_path: Path):
    local_file = tmp_path / "local.json"
    global_file = tmp_path / "global.json"

    local_state = HarnessState(file_path=local_file, scope="local")
    global_state = HarnessState(file_path=global_file, scope="global")

    local_state.add_entry("prompt", "Local Note", "Focus on backend tests.")
    global_state.add_entry("memory", "Global Habit", "Operator prefers pytest verbose output.")

    middleware = ContinualHarnessMiddleware(local_state=local_state, global_state=global_state)
    result = middleware.before_agent(state={}, runtime=None)

    assert result is not None
    assert "messages" in result
    msg = result["messages"][0]
    assert isinstance(msg, SystemMessage)
    assert msg.additional_kwargs.get(CONTINUAL_HARNESS_REMINDER_KEY) is True
    assert "Local Note" in msg.content
    assert "Global Habit" in msg.content


def test_harness_refine_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    # Add entry
    add_res = harness_refine_tool.invoke({
        "action": "add",
        "kind": "prompt",
        "title": "TDD Rule",
        "content": "Write unit test first before editing core code.",
        "scope": "local",
    })
    assert "Successfully recorded" in add_res

    # List entries
    list_res = harness_refine_tool.invoke({
        "action": "list",
        "scope": "local",
    })
    assert "TDD Rule" in list_res

    # Refine
    refine_res = harness_refine_tool.invoke({
        "action": "refine",
        "scope": "local",
    })
    assert "Refinement" in refine_res
