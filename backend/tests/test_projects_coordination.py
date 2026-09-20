"""Context scoping, evidence gate, conflicts, workspace, handoffs."""

from __future__ import annotations

import pytest

from alpha.projects import conflicts as conflicts_mod
from alpha.projects.conflicts import raise_conflict, resolve_conflict
from alpha.projects.context import allowed_sections, build_context
from alpha.projects.evidence import Evidence, check_completion


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    yield


def test_role_sections_differ():
    assert "files_backend" in allowed_sections("backend engineer")
    assert "files_backend" not in allowed_sections("frontend engineer")
    assert "constitution" in allowed_sections("architect")
    assert allowed_sections("") == {"overview", "tasks", "decisions", "activity"}


def test_context_budget_and_filtering():
    ctx = build_context("proj-ctx", "backend engineer", files=["backend/api.py", "frontend/app.tsx"], max_chars=200)
    assert "files_backend" in ctx.sections
    assert "files_frontend" not in ctx.sections
    total = sum(len(str(v)) for v in ctx.sections.values())
    assert total <= 200 + 64


def test_evidence_gate_code_and_deploy():
    ok = check_completion([Evidence("commit", "abc"), Evidence("tests_passed", "r"), Evidence("lint", "r")])
    assert ok.passed and ok.missing == []
    short = check_completion([Evidence("commit", "abc")])
    assert not short.passed and "tests_passed" in short.missing
    deploy = check_completion([Evidence("commit", "a"), Evidence("tests_passed", "b"), Evidence("security", "c"), Evidence("integration", "d")], task_kind="deploy")
    assert deploy.passed


def test_conflict_raise_resolve_records_adr():
    raised = raise_conflict("proj-cf", "architecture", "REST vs GraphQL", ["coder", "researcher"], "disagree on API style")
    assert raised.status == "open"
    resolved = resolve_conflict("proj-cf", raised, "Use REST for v1; revisit at v2.", resolved_by="architect")
    assert resolved.status == "resolved"
    from alpha.projects.decisions import get_decision_log

    assert len(get_decision_log("proj-cf").search("REST vs GraphQL")) == 1


def test_lock_overlap_detection_same_scope(tmp_path):
    from alpha.projects.locks import LockManager

    manager = LockManager(storage_path=tmp_path / "locks.json")
    manager.acquire("p", "file", "a.py", "coder")
    # Same owner re-acquire is not a conflict; simulate a second owner by
    # direct insert (acquire would raise, which is the live behavior).
    from alpha.projects.locks import ResourceLock

    manager._locks["lk-other"] = ResourceLock(lock_id="lk-other", project_id="p", scope="file", path="a.py", owner_bot="frontend")
    found = conflicts_mod.detect_lock_conflicts("p", lock_manager=manager)
    assert len(found) == 1
    assert set(found[0].parties) == {"coder", "frontend"}


def test_workspace_layout_and_branch_names(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    from alpha.projects.workspace import WORKSPACE_DIRS, branch_name, ensure_workspace, project_root

    root = ensure_workspace("proj-ws")
    assert root == project_root("proj-ws")
    for sub in WORKSPACE_DIRS:
        assert (root / sub).is_dir()
    assert branch_name("Backend Agent", "proj-1", "task 42") == "agent/Backend-Agent/proj-1/task-42"
    assert branch_name("..", "..", "..") == "agent/x/x/x"
    assert ".." not in branch_name("a/b", "c", "d").split("/")


def test_handoff_create_accept_flow():
    from alpha.projects.handoffs import get_handoff_store

    store = get_handoff_store("proj-ho")
    rec = store.create("t-1", "architect", "coder", "Build auth", completed_work="spec done", files_modified=["auth.py"], recommended_next_action="implement")
    assert rec.status == "open"
    assert store.accept(rec.handoff_id, "reviewer") is None
    accepted = store.accept(rec.handoff_id, "coder")
    assert accepted is not None and accepted.status == "accepted"
    assert store.list(status="open") == []
