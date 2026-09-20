"""Project crew: membership + group room + shared memory reconciled as one unit."""

from __future__ import annotations

import pytest

from agent_workspace.bots import registry as registry_mod
from agent_workspace.groups import service as groups_service_mod
from agent_workspace.projects import context_router as context_router_mod
from agent_workspace.projects import events as events_mod
from agent_workspace.projects import locks as locks_mod
from agent_workspace.projects import membership as membership_mod
from agent_workspace.projects.crew import ProjectCrewService


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point every workforce singleton at a throwaway runtime home."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(h))

    monkeypatch.setattr(membership_mod, "_store", None)
    monkeypatch.setattr(membership_mod, "_store_path", None)
    monkeypatch.setattr(groups_service_mod, "_global_groups", None)
    monkeypatch.setattr(groups_service_mod, "_global_groups_path", None)
    monkeypatch.setattr(events_mod, "_buses", {})
    monkeypatch.setattr(locks_mod, "_manager", None)
    monkeypatch.setattr(locks_mod, "_manager_path", None)
    monkeypatch.setattr(context_router_mod, "_DEFAULT_ROUTER", None)
    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)
    return h


@pytest.fixture()
def crew(home):
    return ProjectCrewService(base_dir=home)


def test_solo_project_gets_no_room(crew):
    view = crew.attach("proj-1", ["coder"])
    assert [m.bot_name for m in view.members] == ["coder"]
    assert view.room is None


def test_room_is_provisioned_at_the_second_agent(crew):
    crew.attach("proj-1", ["coder"])
    view = crew.attach("proj-1", ["reviewer"])
    assert view.room is not None
    assert set(view.room.members) == {"coder", "reviewer"}
    assert view.room.mode == "moderated"


def test_attach_accepts_name_role_pairs(crew):
    view = crew.attach("proj-1", [("coder", "lead"), ("reviewer", "qa")])
    roles = {m.bot_name: m.role_in_project for m in view.members}
    assert roles == {"coder": "lead", "reviewer": "qa"}


def test_attach_is_idempotent(crew):
    crew.attach("proj-1", ["coder", "reviewer"])
    again = crew.attach("proj-1", ["coder"])
    assert len(again.members) == 2
    assert again.room is not None
    assert len(again.room.members) == 2


def test_membership_is_authoritative_over_the_room(crew):
    """A bot present in the room but not on the crew is stale and gets removed."""
    crew.attach("proj-1", ["coder", "reviewer"])
    svc = groups_service_mod.get_group_chat_service()
    room = svc.rooms_for_project("proj-1")[0]
    room.members.append("ghost")
    svc.persist()

    view = crew.ensure_crew("proj-1")
    assert "ghost" not in view.room.members
    assert set(view.room.members) == {"coder", "reviewer"}


def test_crew_read_backfills_the_room_for_legacy_membership(crew, home):
    """Membership written without the crew layer still produces a room on read."""
    membership_mod.get_membership_store().join("proj-1", "coder")
    membership_mod.get_membership_store().join("proj-1", "reviewer")

    view = crew.ensure_crew("proj-1")
    assert view.room is not None
    assert set(view.room.members) == {"coder", "reviewer"}


def test_detach_parks_the_room_without_losing_history(crew):
    crew.attach("proj-1", ["coder", "reviewer", "tester"])
    svc = groups_service_mod.get_group_chat_service()
    room = svc.rooms_for_project("proj-1")[0]
    svc.post_message(room.name, "coder", "@reviewer please check this", intent="proposal")

    view = crew.detach("proj-1", "tester")
    assert [m.bot_name for m in view.members] == ["coder", "reviewer"]
    assert view.room is not None
    assert view.room.message_count == 1

    solo = crew.detach("proj-1", "reviewer")
    # Dropping to one agent parks the room; history is preserved, not deleted.
    assert solo.room is not None
    assert solo.room.parked is True
    assert solo.room.message_count == 1


def test_detach_releases_the_departing_agents_locks(crew):
    crew.attach("proj-1", ["coder", "reviewer"])
    locks = locks_mod.get_lock_manager()
    locks.acquire("proj-1", "file", "src/app.py", "coder")

    view = crew.detach("proj-1", "coder")
    remaining = [lock["owner_bot"] for lock in view.active_locks]
    assert remaining == []


def test_moderator_prefers_architect_then_first_member(crew):
    view = crew.attach("proj-1", ["coder", "architect"])
    assert view.room.moderator == "architect"

    crew2 = ProjectCrewService(base_dir=crew.base_dir)
    other = crew2.attach("proj-2", ["coder", "reviewer"])
    assert other.room.moderator == "coder"


def test_collaboration_settings_reach_the_room(crew):
    crew.attach("proj-1", ["coder", "reviewer"])
    view = crew.set_collaboration("proj-1", orchestration_mode="quorum", moderator="reviewer")
    assert view.orchestration_mode == "quorum"

    synced = crew.ensure_crew("proj-1")
    assert synced.room.mode == "quorum"
    assert synced.room.moderator == "reviewer"


def test_collaboration_defaults_match_legacy_behaviour(crew):
    cfg = crew.get_collaboration("proj-1")
    assert cfg.orchestration_mode == "moderated"
    assert cfg.moderator is None
    assert cfg.mention_policy == "strict"


def test_invalid_collaboration_patches_are_rejected(crew):
    with pytest.raises(ValueError, match="Unknown collaboration setting"):
        crew.set_collaboration("proj-1", nope=True)
    with pytest.raises(ValueError, match="orchestration_mode"):
        crew.set_collaboration("proj-1", orchestration_mode="chaos")
    with pytest.raises(ValueError, match="mention_policy"):
        crew.set_collaboration("proj-1", mention_policy="maybe")
    with pytest.raises(ValueError, match="positive integer"):
        crew.set_collaboration("proj-1", max_concurrent_speakers=0)


def test_ensure_crew_settles_after_one_reconciliation(crew):
    """Second read must not re-emit room_synced — otherwise every poll writes events."""
    crew.attach("proj-1", ["coder", "reviewer"])
    events = events_mod.get_event_bus("proj-1")

    synced = [e for e in events.read() if e.type == "room_synced"]
    assert synced, "attach should emit room_synced when the room is created"

    crew.ensure_crew("proj-1")
    crew.ensure_crew("proj-1")
    after = [e for e in events.read() if e.type == "room_synced"]
    assert len(after) == len(synced)


def test_join_emits_agent_joined_for_each_agent(crew):
    crew.attach("proj-1", ["coder", "reviewer"])
    events = events_mod.get_event_bus("proj-1")
    actors = {e.actor for e in events.read() if e.type == "agent_joined"}
    assert actors == {"coder", "reviewer"}


def test_crew_view_exposes_one_complete_payload(crew):
    view = crew.attach("proj-1", ["coder", "reviewer"]).to_dict()
    for key in ("project_id", "members", "room", "collaboration", "shared_memory", "state", "active_locks", "recent_events"):
        assert key in view
    assert view["room"]["members"]
    assert view["shared_memory"]["project_id"] == "proj-1"
