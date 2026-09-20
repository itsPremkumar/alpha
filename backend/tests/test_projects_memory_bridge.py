"""Memory bridge: the group conversation becoming durable shared memory."""

from __future__ import annotations

import pytest

from agent_workspace.bots import registry as registry_mod
from agent_workspace.groups import service as groups_service_mod
from agent_workspace.projects import context_router as context_router_mod
from agent_workspace.projects import events as events_mod
from agent_workspace.projects import locks as locks_mod
from agent_workspace.projects import membership as membership_mod
from agent_workspace.projects.crew import ProjectCrewService
from agent_workspace.projects.memory_bridge import (
    build_transcript_digest,
    maybe_compact,
    member_briefs,
    open_questions,
    pending_mentions,
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
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


@pytest.fixture()
def room(crew):
    """A two-agent crew with its group room provisioned."""
    view = crew.attach("proj-1", ["coder", "reviewer"])
    assert view.room is not None
    return groups_service_mod.get_group_chat_service().get_room(view.room.name)


def post(room, sender, content, intent="discussion"):
    return groups_service_mod.get_group_chat_service().post_message(room.name, sender, content, intent=intent)[0]


def test_solo_project_yields_empty_structures(crew):
    crew.attach("proj-1", ["coder"])
    assert build_transcript_digest("proj-1")["messages"] == []
    assert member_briefs("proj-1") == {}
    assert open_questions("proj-1") == []
    assert pending_mentions("proj-1") == {}


def test_digest_captures_recent_conversation(room):
    post(room, "coder", "I will refactor the auth module")
    post(room, "reviewer", "please split it into two commits")

    digest = build_transcript_digest("proj-1")
    assert [m["from"] for m in digest["messages"]] == ["coder", "reviewer"]
    assert "refactor the auth module" in digest["messages"][0]["text"]
    assert digest["total"] == 2


def test_digest_respects_the_budget(crew, room):
    crew.set_collaboration("proj-1", transcript_digest_n=2)
    for i in range(5):
        post(room, "coder", f"message {i}")

    digest = build_transcript_digest("proj-1")
    assert len(digest["messages"]) == 2
    assert digest["messages"][-1]["text"] == "message 4"
    assert digest["total"] == 5


def test_mention_creates_pending_work_for_the_target(room):
    post(room, "coder", "@reviewer can you check the auth diff")

    pending = pending_mentions("proj-1")
    assert list(pending) == ["reviewer"]
    assert "auth diff" in pending["reviewer"][0]["text"]


def test_replying_clears_the_pending_queue(room):
    post(room, "coder", "@reviewer can you check the auth diff")
    assert pending_mentions("proj-1") != {}

    post(room, "reviewer", "looks good, ship it")
    assert pending_mentions("proj-1") == {}


def test_pending_queue_keeps_only_unanswered_mentions(room):
    post(room, "coder", "@reviewer first question")
    post(room, "reviewer", "answering the first")
    post(room, "coder", "@reviewer second question")

    pending = pending_mentions("proj-1")
    assert len(pending["reviewer"]) == 1
    assert "second question" in pending["reviewer"][0]["text"]


def test_mentioning_all_reaches_every_member(room):
    post(room, "coder", "@all standup in five minutes")

    pending = pending_mentions("proj-1")
    # The sender is excluded — you cannot be waiting on yourself.
    assert set(pending) == {"reviewer"}


def test_unknown_handles_are_ignored(room):
    post(room, "coder", "@ghostbot please review")
    assert pending_mentions("proj-1") == {}


def test_member_briefs_track_the_latest_contribution(room):
    post(room, "coder", "starting on the auth module")
    post(room, "coder", "auth module done")

    briefs = member_briefs("proj-1")
    assert briefs["coder"] == "auth module done"


def test_open_questions_are_proposals_after_the_last_action(room):
    post(room, "coder", "proposal: extract a token service", intent="proposal")
    post(room, "reviewer", "agreed", intent="action")
    post(room, "coder", "proposal: rename the config keys", intent="proposal")

    questions = open_questions("proj-1")
    assert len(questions) == 1
    assert "rename the config keys" in questions[0]["text"]


def test_shared_memory_exposes_the_transcript_keys(crew, room):
    """Level-2 memory must remember the conversation, not just the state."""
    post(room, "coder", "@reviewer please check the auth diff")

    memory = context_router_mod.get_three_level_router().get_project_memory("proj-1")
    for key in ("transcript_digest", "member_briefs", "open_questions", "pending_mentions"):
        assert key in memory
    assert "reviewer" in memory["pending_mentions"]
    assert memory["transcript_digest"]["total"] == 1


def test_compaction_summarises_overflow_but_keeps_history(crew, room):
    crew.set_collaboration("proj-1", transcript_digest_n=3)
    for i in range(8):
        post(room, "coder", f"work item {i}")

    report = maybe_compact("proj-1")
    assert report is not None
    assert report["compacted"] == 5
    assert report["compacted_upto"] == 5

    # Non-destructive: the room log is the record, compaction never truncates it.
    assert len(room.log) == 8

    digest = build_transcript_digest("proj-1")
    assert len(digest["messages"]) == 3
    assert digest["summary"] and "work item" in digest["summary"]


def test_compaction_is_idempotent_within_budget(crew, room):
    crew.set_collaboration("proj-1", transcript_digest_n=3)
    for i in range(8):
        post(room, "coder", f"work item {i}")

    assert maybe_compact("proj-1") is not None
    assert maybe_compact("proj-1") is None


def test_compaction_emits_an_event(crew, room):
    crew.set_collaboration("proj-1", transcript_digest_n=2)
    for i in range(5):
        post(room, "coder", f"work item {i}")

    maybe_compact("proj-1")
    events = events_mod.get_event_bus("proj-1")
    assert any(e.type == "memory_compacted" for e in events.read())


def test_crew_read_triggers_compaction(crew, room):
    crew.set_collaboration("proj-1", transcript_digest_n=2)
    for i in range(6):
        post(room, "coder", f"work item {i}")

    crew.ensure_crew("proj-1")
    assert build_transcript_digest("proj-1")["compacted_upto"] == 4
