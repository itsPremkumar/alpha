"""Project membership: join/leave/heartbeat/presence across projects."""

from __future__ import annotations

import pytest

from agent_workspace.projects import membership as membership_mod
from agent_workspace.projects.membership import MembershipStore


@pytest.fixture()
def store(tmp_path):
    return MembershipStore(storage_path=tmp_path / "membership.json")


def test_join_is_idempotent_and_reactivates(store):
    first = store.join("proj-a", "Coder", "backend")
    assert first.bot_name == "coder"
    assert first.role_in_project == "backend"
    second = store.join("proj-a", "coder", "backend")
    assert second.joined_at == first.joined_at
    assert second.status == "active"


def test_agent_can_belong_to_many_projects(store):
    store.join("proj-a", "coder")
    store.join("proj-b", "coder")
    store.join("proj-c", "coder")
    assert {m.project_id for m in store.projects_for_bot("coder")} == {"proj-a", "proj-b", "proj-c"}


def test_leave_removes_only_that_project(store):
    store.join("proj-a", "coder")
    store.join("proj-b", "coder")
    assert store.leave("proj-a", "coder") is True
    assert store.leave("proj-a", "coder") is False
    assert [m.project_id for m in store.projects_for_bot("coder")] == ["proj-b"]


def test_heartbeat_tracks_task_and_blocker(store):
    store.join("proj-a", "coder")
    m = store.heartbeat("proj-a", "coder", status="idle", current_task_id="t-1", blocked_reason="waiting on review")
    assert m is not None
    assert m.current_task_id == "t-1"
    assert m.blocked_reason == "waiting on review"
    assert store.heartbeat("proj-a", "ghost") is None


def test_presence_lists_project_members(store):
    store.join("proj-a", "coder")
    store.join("proj-a", "reviewer")
    store.join("proj-b", "coder")
    rows = store.presence("proj-a")
    assert {m.bot_name for m in rows} == {"coder", "reviewer"}
    assert store.member_count("proj-a") == 2


def test_singleton_rebuilds_when_home_moves(tmp_path, monkeypatch):
    first_home = tmp_path / "home1"
    second_home = tmp_path / "home2"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(first_home))
    monkeypatch.setattr(membership_mod, "_store", None)
    monkeypatch.setattr(membership_mod, "_store_path", None)
    s1 = membership_mod.get_membership_store()
    s1.join("proj-a", "coder")
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(second_home))
    s2 = membership_mod.get_membership_store()
    assert s2 is not s1
    assert s2.member_count("proj-a") == 0
