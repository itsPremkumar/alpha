"""Group messaging features: roster presence, edit/delete/react/forward, intents.

Three real defects motivate this file.

1. **Every participant rendered as ``unknown``.** The client read presence from
   the *thread-scoped* agent roster (empty for any room the operator never
   opened as a thread) and from ``GET /company/attendance/roll-call``, which
   returns a markdown digest with no ``agents`` array. Presence is now resolved
   from the two systems that actually own it, and an unresolvable member is
   reported as ``unknown`` — a different claim from ``offline``.

2. **Seven of the composer's thirteen kinds were rejected with a 422.** The
   router validated its own six-value tuple while the client offered a
   different thirteen, so ``decision`` — sent by "Post as group decision" — never
   reached the room.

3. **Message editing/deletion/reactions had no route at all.**
"""

from __future__ import annotations

import pytest

from alpha.groups.presence import (
    PRESENCE_WINDOW_SECONDS,
    MemberPresence,
    _parse_epoch,
    resolve_room_presence,
)
from alpha.groups.room import REACTION_EMOJI, VALID_INTENTS, GroupMessage, GroupRoom
from alpha.groups.service import GroupChatService


# ---------------------------------------------------------------------------
# Message model: backward compatibility
# ---------------------------------------------------------------------------


def test_a_message_from_an_older_build_round_trips() -> None:
    """Every added field defaults, so a persisted room is not lost on upgrade."""
    legacy = {
        "id": "msg_1",
        "sender": "architect",
        "content": "hello",
        "intent": "discussion",
        "mentions": [],
        "metadata": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    msg = GroupMessage.from_dict(dict(legacy))
    assert msg.edited_at is None
    assert msg.deleted is False
    assert msg.reactions == {}
    assert msg.reply_to is None
    assert msg.forwarded_from is None
    # The original payload is not mutated by the round trip.
    assert set(legacy) == {"id", "sender", "content", "intent", "mentions", "metadata", "created_at"}


def test_room_from_dict_does_not_consume_the_callers_log() -> None:
    """`_load` hands `from_dict` the dict it just parsed; popping would empty it."""
    data = {"room_id": "r1", "name": "r", "log": [{"id": "m1", "sender": "s", "content": "c"}]}
    GroupRoom.from_dict(data)
    assert "log" in data, "the caller's mapping was mutated"
    assert len(data["log"]) == 1


def test_find_message_matches_a_whole_id_only() -> None:
    """A substring match would let `msg_ab` edit `msg_abcdef`."""
    room = GroupRoom(room_id="r", name="r", members=["coder"])
    room.append_message("coder", "first")
    target = room.append_message("coder", "second")

    assert room.find_message(target.id) is target
    assert room.find_message(target.id.upper()) is target
    assert room.find_message(target.id[:4]) is None
    assert room.find_message("") is None
    assert room.find_message("   ") is None


# ---------------------------------------------------------------------------
# Intent vocabulary: one source of truth
# ---------------------------------------------------------------------------


def test_the_typed_a2a_kinds_are_part_of_the_vocabulary() -> None:
    """The kinds the composer offers must be accepted by the room.

    `decision` is the regression: it is what the "Post as group decision"
    control sends, and it was rejected by a narrower router tuple.
    """
    for kind in ("decision", "handoff", "escalation", "blocker", "task_completion", "answer", "question"):
        assert kind in VALID_INTENTS, f"{kind} must be an accepted intent"


def test_the_harness_native_intents_survive_the_widening() -> None:
    """`alpha.kanban.bridge` writes card_update and `memory_bridge` folds these."""
    for kind in ("discussion", "proposal", "vote", "action", "pass", "card_update"):
        assert kind in VALID_INTENTS


def test_the_vocabulary_has_no_duplicates() -> None:
    assert len(set(VALID_INTENTS)) == len(VALID_INTENTS)


def test_reaction_emoji_is_a_small_bounded_set() -> None:
    assert 0 < len(REACTION_EMOJI) <= 12
    assert len(set(REACTION_EMOJI)) == len(REACTION_EMOJI)


# ---------------------------------------------------------------------------
# Edit / delete / react / forward
# ---------------------------------------------------------------------------


@pytest.fixture
def svc(tmp_path):
    service = GroupChatService(storage_path=tmp_path / "rooms.json")
    service.get_or_create_room("sprint-room", members=["architect", "coder"])
    return service


def test_edit_replaces_content_and_stamps_the_edit(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "typo tehre")
    original = svc.get_room("sprint-room").log[-1]

    edited = svc.edit_message("sprint-room", original.id, "typo there")
    assert edited.content == "typo there"
    assert edited.edited_at is not None
    assert edited.id == original.id, "an edit edits in place; it does not append a new row"
    assert len(svc.get_room("sprint-room").log) == 1


def test_edit_survives_a_reload(svc: GroupChatService, tmp_path) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "before")
    mid = svc.get_room("sprint-room").log[-1].id
    svc.edit_message("sprint-room", mid, "after")

    reloaded = GroupChatService(storage_path=tmp_path / "rooms.json")
    assert reloaded.get_room("sprint-room").find_message(mid).content == "after"


def test_edit_of_an_unknown_message_raises_rather_than_reporting_success(svc: GroupChatService) -> None:
    with pytest.raises(KeyError):
        svc.edit_message("sprint-room", "msg_nope", "anything")


def test_edit_rejects_empty_content(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "text")
    mid = svc.get_room("sprint-room").log[-1].id
    with pytest.raises(ValueError):
        svc.edit_message("sprint-room", mid, "   ")


def test_edit_of_a_deleted_message_is_refused(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "text")
    mid = svc.get_room("sprint-room").log[-1].id
    svc.delete_message("sprint-room", mid)
    with pytest.raises(ValueError):
        svc.edit_message("sprint-room", mid, "new text")


def test_delete_keeps_the_row_so_a_reply_still_resolves(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "original")
    mid = svc.get_room("sprint-room").log[-1].id

    deleted = svc.delete_message("sprint-room", mid)
    assert deleted.deleted is True
    assert deleted.content == ""
    room = svc.get_room("sprint-room")
    assert len(room.log) == 1
    assert room.find_message(mid) is not None, "the row is kept so replies keep a target"


def test_a_double_delete_is_an_error_not_a_silent_second_success(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "text")
    mid = svc.get_room("sprint-room").log[-1].id
    svc.delete_message("sprint-room", mid)
    with pytest.raises(ValueError, match="already deleted"):
        svc.delete_message("sprint-room", mid)


def test_delete_of_an_unknown_message_raises(svc: GroupChatService) -> None:
    with pytest.raises(KeyError):
        svc.delete_message("sprint-room", "msg_nope")


def test_reaction_toggles_off_on_the_second_click(svc: GroupChatService) -> None:
    """The same click twice has to remove the reaction, or it cannot be taken back."""
    _msg, _next = svc.post_message("sprint-room", "operator", "react to me")
    mid = svc.get_room("sprint-room").log[-1].id

    first = svc.toggle_reaction("sprint-room", mid, "🎉", "operator")
    assert first == {"🎉": ["operator"]}

    second = svc.toggle_reaction("sprint-room", mid, "🎉", "operator")
    assert second == {}, "the last actor leaving removes the key entirely"


def test_reactions_from_different_actors_are_counted_separately(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "operator", "react to me")
    mid = svc.get_room("sprint-room").log[-1].id

    svc.toggle_reaction("sprint-room", mid, "👍", "operator")
    reactions = svc.toggle_reaction("sprint-room", mid, "👍", "coder")
    assert reactions == {"👍": ["operator", "coder"]}

    # One actor leaving does not clear the other's reaction.
    after = svc.toggle_reaction("sprint-room", mid, "👍", "operator")
    assert after == {"👍": ["coder"]}


def test_reaction_to_an_unknown_message_raises(svc: GroupChatService) -> None:
    with pytest.raises(KeyError):
        svc.toggle_reaction("sprint-room", "msg_nope", "👍", "operator")


def test_forward_creates_a_fresh_id_in_the_target_room(svc: GroupChatService) -> None:
    """Reusing the source id would make reactions and edits ambiguous."""
    _msg, _next = svc.post_message("sprint-room", "coder", "the finding")
    mid = svc.get_room("sprint-room").log[-1].id

    forwarded, _next = svc.forward_message("sprint-room", mid, "test-board", sender="operator")

    assert forwarded.id != mid
    assert forwarded.content == "the finding"
    assert forwarded.sender == "operator"
    assert forwarded.forwarded_from["room"] == "sprint-room"
    assert forwarded.forwarded_from["sender"] == "coder"
    assert svc.get_room("sprint-room").log[-1].id == mid, "the source room is unchanged"


def test_forward_can_carry_edited_content_and_an_explicit_intent(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "coder", "raw text")
    mid = svc.get_room("sprint-room").log[-1].id

    forwarded, _next = svc.forward_message(
        "sprint-room",
        mid,
        "test-board",
        sender="operator",
        content="trimmed for the other room",
        intent="decision",
    )
    assert forwarded.content == "trimmed for the other room"
    assert forwarded.intent == "decision"


def test_forward_of_a_deleted_message_is_refused(svc: GroupChatService) -> None:
    _msg, _next = svc.post_message("sprint-room", "coder", "text")
    mid = svc.get_room("sprint-room").log[-1].id
    svc.delete_message("sprint-room", mid)
    with pytest.raises(ValueError):
        svc.forward_message("sprint-room", mid, "test-board", sender="operator")


def test_forward_to_an_unknown_source_room_raises(svc: GroupChatService) -> None:
    with pytest.raises(KeyError):
        svc.forward_message("no-such-room", "msg_1", "test-board", sender="operator")


def test_a_reply_stores_its_target_without_validating_it(svc: GroupChatService) -> None:
    """A dangling reference is a real state; refusing the reply would lose it."""
    _msg, _next = svc.post_message("sprint-room", "operator", "first", reply_to=None)
    first = svc.get_room("sprint-room").log[-1]

    reply, _next = svc.post_message("sprint-room", "coder", "answering", reply_to=first.id)
    assert reply.reply_to == first.id

    orphan, _next = svc.post_message("sprint-room", "coder", "answering a ghost", reply_to="msg_gone")
    assert orphan.reply_to == "msg_gone"


# ---------------------------------------------------------------------------
# Presence resolution
# ---------------------------------------------------------------------------


def test_parse_epoch_accepts_both_wire_shapes() -> None:
    """BotProfile.last_active is ISO; bots/inbox writes time.time() floats."""
    assert _parse_epoch(1700000000.0) == 1700000000.0
    assert _parse_epoch("1700000000") == 1700000000.0
    assert _parse_epoch("2026-01-01T00:00:00+00:00") is not None


def test_parse_epoch_returns_none_rather_than_now_for_unreadable_values() -> None:
    """A missing stamp must never become a fresh heartbeat."""
    for value in (None, "", "   ", "not-a-time", 0, -5, True, [], {}):
        assert _parse_epoch(value) is None, f"{value!r} must not parse"


def _presence_with(registry: dict, attendance: dict, now: float, members: list[str]):
    """Resolve against explicit sources, bypassing the real registries."""
    from alpha.groups import presence as presence_module

    original_registry = presence_module._registry_rows
    original_attendance = presence_module._attendance_rows
    presence_module._registry_rows = lambda: registry
    presence_module._attendance_rows = lambda: attendance
    try:
        return resolve_room_presence(members, now=now)
    finally:
        presence_module._registry_rows = original_registry
        presence_module._attendance_rows = original_attendance


def test_a_recently_active_bot_reads_as_working() -> None:
    now = 1_700_000_000.0
    entries = _presence_with(
        {"coder": {"status": "active", "last_active": now - 5, "role": "Engineer"}},
        {},
        now,
        ["coder"],
    )
    assert entries[0].state == "busy"


def test_an_active_bot_with_no_recent_activity_reads_as_idle_not_offline() -> None:
    now = 1_700_000_000.0
    entries = _presence_with(
        {"coder": {"status": "active", "last_active": now - (PRESENCE_WINDOW_SECONDS + 600), "role": "Engineer"}},
        {},
        now,
        ["coder"],
    )
    assert entries[0].state == "idle"


def test_a_suspended_bot_reads_as_offline() -> None:
    now = 1_700_000_000.0
    entries = _presence_with({"coder": {"status": "suspended", "last_active": now - 5}}, {}, now, ["coder"])
    assert entries[0].state == "offline"


def test_an_archived_bot_reads_as_offline_even_with_a_fresh_stamp() -> None:
    """`archived` is administrative removal; a recent stamp must not revive it."""
    now = 1_700_000_000.0
    entries = _presence_with(
        {"coder": {"status": "active", "last_active": now - 5, "archived": True}},
        {},
        now,
        ["coder"],
    )
    assert entries[0].state == "offline"


def test_a_sleeping_bot_reads_as_idle() -> None:
    now = 1_700_000_000.0
    entries = _presence_with({"coder": {"status": "sleeping", "last_active": now - 5}}, {}, now, ["coder"])
    assert entries[0].state == "idle"


def test_attendance_presence_wins_over_the_registry() -> None:
    """Attendance is the only source that knows a run is executing right now."""
    now = 1_700_000_000.0
    entries = _presence_with(
        {"coder": {"status": "sleeping", "last_active": now - 5}},
        {"coder": {"status": "present", "active_task_id": "run_42"}},
        now,
        ["coder"],
    )
    assert entries[0].state == "busy"
    assert "run_42" in entries[0].detail
    assert "company_attendance" in entries[0].source


def test_a_stuck_agent_is_not_reported_as_offline() -> None:
    """It is enrolled and reporting; flattening this to offline would hide a stall."""
    now = 1_700_000_000.0
    entries = _presence_with({}, {"coder": {"status": "stuck_loop", "active_task_id": None}}, now, ["coder"])
    assert entries[0].state == "busy"
    assert entries[0].detail == "stuck in a loop"


def test_an_absent_agent_reads_as_offline() -> None:
    now = 1_700_000_000.0
    entries = _presence_with({}, {"coder": {"status": "absent", "active_task_id": None}}, now, ["coder"])
    assert entries[0].state == "offline"


def test_a_member_no_system_knows_is_unknown_not_offline() -> None:
    """No record and a downed bot are opposite claims."""
    entries = _presence_with({}, {}, 1_700_000_000.0, ["ghost"])
    assert entries[0].state == "unknown"
    assert entries[0].source == "unresolved"


def test_an_unrecognised_registry_status_stays_unknown() -> None:
    """Never defaulted to online from a status word we do not understand."""
    now = 1_700_000_000.0
    entries = _presence_with({"coder": {"status": "levitating", "last_active": now - 5}}, {}, now, ["coder"])
    assert entries[0].state == "unknown"


def test_presence_keeps_the_rooms_member_order() -> None:
    """The room is what the operator is looking at; a sorted list would reorder it."""
    entries = _presence_with({}, {}, 1_700_000_000.0, ["tester", "architect", "coder"])
    assert [e.name for e in entries] == ["tester", "architect", "coder"]


def test_presence_deduplicates_a_repeated_member() -> None:
    entries = _presence_with({}, {}, 1_700_000_000.0, ["coder", "coder", "coder"])
    assert len(entries) == 1


def test_presence_survives_an_unreadable_registry() -> None:
    """A broken registry must degrade to unknown, not take the roster down."""
    from alpha.groups import presence as presence_module

    original = presence_module._registry_rows

    def boom() -> dict:
        raise RuntimeError("registry unreadable")

    presence_module._registry_rows = boom
    try:
        entries = resolve_room_presence(["coder"], now=1_700_000_000.0)
    finally:
        presence_module._registry_rows = original
    assert entries[0].state == "unknown"


def test_member_presence_serializes_its_provenance() -> None:
    payload = MemberPresence(
        name="coder", state="busy", source="company_attendance", activity_at=None, detail="working now"
    ).to_dict()
    assert payload["state"] == "busy"
    assert payload["source"] == "company_attendance"
    assert payload["activity_at"] is None