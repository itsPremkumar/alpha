"""Unit tests for Multi-Agent Group Chat orchestration, speaker strategies, and quorum voting."""

from pathlib import Path

from alpha.groups.orchestration import GroupOrchestrator
from alpha.groups.quorum import QuorumEngine
from alpha.groups.room import GroupRoom
from alpha.groups.service import GroupChatService


def test_orchestrator_parse_mentions():
    members = ["architect", "coder", "reviewer"]

    # Single and multiple mentions
    m1 = GroupOrchestrator.parse_mentions("Hey @coder and @Architect can you check this?", members)
    assert set(m1) == {"coder", "architect"}

    # @everyone or @all mentions
    m_all = GroupOrchestrator.parse_mentions("Attention @everyone please sync up", members)
    assert set(m_all) == {"architect", "coder", "reviewer"}

    # Unrecognized mention ignored
    m_unknown = GroupOrchestrator.parse_mentions("Ping @random_bot for help", members)
    assert m_unknown == []


def test_speaker_selection_mention_mode():
    orchestrator = GroupOrchestrator()
    room = GroupRoom(
        room_id="room_1",
        name="backend-team",
        members=["lead", "coder", "reviewer"],
        mode="mention",
        moderator="lead",
    )

    # User message with mention
    msg1 = room.append_message(sender="user", content="Please build feature @coder", mentions=["coder"])
    assert orchestrator.resolve_next_speakers(room, msg1) == ["coder"]

    # User message with no mention -> defaults to moderator
    msg2 = room.append_message(sender="user", content="Hello team")
    assert orchestrator.resolve_next_speakers(room, msg2) == ["lead"]

    # Bot message with no mention -> settles discussion
    msg3 = room.append_message(sender="coder", content="Feature complete.")
    assert orchestrator.resolve_next_speakers(room, msg3) == []


def test_speaker_selection_moderated_mode():
    orchestrator = GroupOrchestrator()
    room = GroupRoom(
        room_id="room_2",
        name="review-board",
        members=["lead", "coder", "reviewer"],
        mode="moderated",
        moderator="lead",
    )

    # When specialist speaks, moderator is next
    msg_spec = room.append_message(sender="coder", content="Draft PR submitted.")
    assert orchestrator.resolve_next_speakers(room, msg_spec) == ["lead"]

    # When moderator speaks mentioning reviewer, reviewer is next
    msg_mod = room.append_message(sender="lead", content="Please review @reviewer", mentions=["reviewer"])
    assert orchestrator.resolve_next_speakers(room, msg_mod) == ["reviewer"]

    # When moderator speaks with no mention, defaults to first specialist
    msg_mod2 = room.append_message(sender="lead", content="Who can take this?")
    assert orchestrator.resolve_next_speakers(room, msg_mod2) == ["coder"]


def test_speaker_selection_round_robin_and_parallel():
    orchestrator = GroupOrchestrator()
    room = GroupRoom(
        room_id="room_3",
        name="round-room",
        members=["bot_a", "bot_b", "bot_c"],
        mode="round_robin",
    )

    # Round robin progression
    msg_a = room.append_message(sender="bot_a", content="Point 1")
    assert orchestrator.resolve_next_speakers(room, msg_a) == ["bot_b"]

    msg_b = room.append_message(sender="bot_b", content="Point 2")
    assert orchestrator.resolve_next_speakers(room, msg_b) == ["bot_c"]

    msg_c = room.append_message(sender="bot_c", content="Point 3")
    assert orchestrator.resolve_next_speakers(room, msg_c) == ["bot_a"]

    # Parallel mode
    room.mode = "parallel"
    msg_parallel = room.append_message(sender="bot_a", content="Brainstorm now!")
    speakers = orchestrator.resolve_next_speakers(room, msg_parallel)
    assert set(speakers) == {"bot_b", "bot_c"}


def test_quorum_engine_consensus():
    quorum = QuorumEngine()
    prop = quorum.create_proposal(
        room_id="room_1",
        proposer="architect",
        question="Should we migrate to SQLite WAL mode?",
        threshold=0.6,
    )
    assert prop.status == "in_progress"

    # Vote 1: agree
    res1 = quorum.cast_vote(prop.proposal_id, voter="architect", choice="agree", comment="Better concurrency.")
    assert res1["status"] == "ok"

    # Tally with 3 total eligible voters (1/3 = 33% < 60%)
    t1 = quorum.tally(prop.proposal_id, total_eligible_voters=3)
    assert t1["status"] == "in_progress"

    # Vote 2: agree (2/3 = 66% >= 60%)
    res2 = quorum.cast_vote(prop.proposal_id, voter="coder", choice="agree")
    assert res2["status"] == "ok"

    t2 = quorum.tally(prop.proposal_id, total_eligible_voters=3)
    assert t2["status"] == "approved"
    assert prop.status == "approved"

    # Further votes rejected on closed proposal
    res3 = quorum.cast_vote(prop.proposal_id, voter="reviewer", choice="disagree")
    assert res3["status"] == "error"


def test_group_chat_service_auto_provisioning(tmp_path: Path):
    storage = tmp_path / "rooms.json"
    service = GroupChatService(storage_path=storage)

    # Post message to non-existent room -> auto provisions room and default members
    msg, next_speakers = service.post_message(
        room_name="dev-squad",
        sender="user",
        content="Welcome team! @coder please start.",
    )
    assert msg.sender == "user"
    assert next_speakers == ["coder"]

    # Verify room was created
    room = service.get_room("dev-squad")
    assert room is not None
    assert "coder" in room.members
    assert len(room.log) == 1

    # Reload in fresh service instance
    service2 = GroupChatService(storage_path=storage)
    reloaded_room = service2.get_room("dev-squad")
    assert reloaded_room is not None
    assert len(reloaded_room.log) == 1
    assert reloaded_room.log[0].content == msg.content
