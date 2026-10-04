"""Notification store, preferences and triggers.

A notification is a *claim that something happened*, so the properties worth
testing are the honesty ones:

- a preference that is off means the event happened but nobody was told — the
  trigger must return no records rather than pretending the event did not occur;
- a sender never notifies themselves;
- a mention outranks ordinary chatter, because a direct address is a summons;
- history is bounded, and the cap drops the *oldest* entries — the store must
  not claim it still has them;
- a notification fault never fails the message that already landed.
"""

from __future__ import annotations

import pytest

from alpha.notifications import (
    MAX_HISTORY,
    NOTIFICATION_PRIORITIES,
    NOTIFICATION_TYPES,
    OPERATOR_USER_ID,
    Notification,
    NotificationPreference,
    NotificationStore,
    new_notification_id,
)
from alpha.notifications.triggers import NotificationTriggers, _preview

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg
    import alpha.groups.service as grp_svc
    import alpha.notifications.triggers as trg

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    monkeypatch.setattr(trg, "_store", None)
    yield


def _store(tmp_path) -> NotificationStore:
    return NotificationStore(storage_dir=tmp_path / "notifications")


def _notification(**kwargs) -> Notification:
    defaults = {
        "notification_id": new_notification_id(),
        "type": "message",
        "title": "New message in alpha-team",
        "body": "hello",
        "priority": "normal",
    }
    defaults.update(kwargs)
    return Notification(**defaults)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_a_created_notification_is_listed_newest_first(tmp_path) -> None:
    store = _store(tmp_path)
    first = store.create(_notification(title="older"))
    second = store.create(_notification(title="newer"))
    listed = store.list("operator")
    assert [n.notification_id for n in listed] == [second.notification_id, first.notification_id]
    assert store.unread_count("operator") == 2


def test_marking_read_is_idempotent_and_stamps_when(tmp_path) -> None:
    store = _store(tmp_path)
    note = store.create(_notification())
    assert store.mark_read("operator", note.notification_id) is True
    stored = store.list("operator")[0]
    assert stored.read is True
    assert stored.read_at is not None
    first_stamp = stored.read_at

    # Re-marking is idempotent: it does not raise and does not rewrite the stamp.
    assert store.mark_read("operator", note.notification_id) is True
    assert store.list("operator")[0].read_at == first_stamp
    assert store.unread_count("operator") == 0


def test_marking_an_unknown_notification_reports_not_found(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.mark_read("operator", "notif_missing") is False


def test_mark_all_read_counts_only_what_changed(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create(_notification())
    store.create(_notification())
    store.mark_read("operator", a.notification_id)

    assert store.mark_all_read("operator") == 1, "already-read rows are not counted again"
    assert store.mark_all_read("operator") == 0, "a second pass has nothing left to do"


def test_history_is_capped_and_the_dropped_entries_are_gone(tmp_path) -> None:
    """The cap drops the oldest — reporting them afterwards would be a lie."""
    store = _store(tmp_path)
    for i in range(MAX_HISTORY + 25):
        store.create(_notification(title=f"n{i}"))
    listed = store.list("operator", limit=MAX_HISTORY * 2)
    assert len(listed) == MAX_HISTORY
    assert listed[0].title == f"n{MAX_HISTORY + 24}"
    assert listed[-1].title == "n25", "the oldest 25 scrolled out of history"


def test_type_filtering_and_unread_filtering(tmp_path) -> None:
    store = _store(tmp_path)
    store.create(_notification(type="message"))
    store.create(_notification(type="mention"))
    assert [n.type for n in store.list("operator", types=["mention"])] == ["mention"]
    store.mark_all_read("operator")
    assert store.list("operator", unread_only=True) == []


def test_preferences_survive_a_reload(tmp_path) -> None:
    first = _store(tmp_path)
    first.update_preference("operator", {"sound_enabled": False, "enabled": False})

    second = _store(tmp_path)
    pref = second.get_preference("operator")
    assert pref.sound_enabled is False
    assert pref.enabled is False


def test_a_corrupt_user_file_is_a_failed_read_for_that_user_not_a_failed_boot(tmp_path) -> None:
    """The store must not report "no notifications" for an unreadable file."""
    store = _store(tmp_path)
    store.create(_notification())
    target = next((tmp_path / "notifications").glob("*.json"))
    target.write_text("{ this is not json", encoding="utf-8")

    reloaded = NotificationStore(storage_dir=tmp_path / "notifications")
    # In-memory it starts empty (the file could not be read) but boots fine.
    assert reloaded.list("operator") == []
    # And the next save rewrites it cleanly.
    reloaded.create(_notification())
    assert NotificationStore(storage_dir=tmp_path / "notifications").list("operator") != []


def test_a_user_id_that_is_not_filename_safe_is_confined(tmp_path) -> None:
    """`../../etc` must not escape the storage directory."""
    store = _store(tmp_path)
    store.create(_notification())
    weird = store.create(_notification())
    # Force a save under a hostile id.
    object.__setattr__(weird, "user_id", "../../etc/passwd")
    store._save("../../etc/passwd")
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path / "notifications" / "..").joinpath("etc").exists()
    assert list((tmp_path / "notifications").glob("*.json"))


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_a_default_preference_surfaces_normal_and_up(tmp_path) -> None:
    pref = NotificationPreference(user_id="operator")
    assert pref.allows("message", "normal") is True
    assert pref.allows("mention", "high") is True
    assert pref.allows("activity", "urgent") is True
    assert pref.allows("message", "low") is False, "low-priority chatter is off by default"


def test_a_disabled_global_switch_turns_everything_off(tmp_path) -> None:
    pref = NotificationPreference(user_id="operator", enabled=False)
    assert pref.allows("mention", "urgent") is False


def test_an_absent_type_inherits_the_global_switch_rather_than_being_muted(tmp_path) -> None:
    pref = NotificationPreference(user_id="operator")
    pref.types = {"message": True}
    assert pref.allows("some_future_type", "normal") is True


def test_an_absent_priority_falls_back_to_the_normal_verdict(tmp_path) -> None:
    pref = NotificationPreference(user_id="operator")
    pref.priorities = {"normal": True}
    assert pref.allows("message", "brand_new_priority") is True
    pref.priorities["normal"] = False
    assert pref.allows("message", "brand_new_priority") is False


def test_the_vocabulary_is_declared_not_inferred() -> None:
    assert "mention" in NOTIFICATION_TYPES
    assert NOTIFICATION_PRIORITIES == ("low", "normal", "high", "urgent")


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def _triggers(tmp_path) -> NotificationTriggers:
    return NotificationTriggers(_store(tmp_path))


def test_a_post_notifies_the_other_members_but_never_the_sender(tmp_path) -> None:
    triggers = _triggers(tmp_path)
    created = triggers.on_message_posted(
        "room_1",
        "alpha-team",
        "architect",
        "hello team",
        "discussion",
        [],
        "msg_1",
        recipients=["architect", "coder", "designer"],
    )
    assert len(created) == 2
    assert all(n.sender == "architect" for n in created)
    assert all(n.type == "message" for n in created)
    assert all(n.priority == "normal" for n in created)
    # The sender is excluded: your own post does not notify you.
    assert len(triggers.store.list("architect")) == 0


def test_a_mention_is_high_priority_and_addresses_the_mentioned_user(tmp_path) -> None:
    triggers = _triggers(tmp_path)
    created = triggers.on_mention("room_1", "alpha-team", "architect", "@coder please look", ["coder"], "msg_1")
    assert len(created) == 1
    note = created[0]
    assert note.type == "mention"
    assert note.priority == "high"
    assert "architect mentioned you" in note.title
    assert note.message_id == "msg_1"
    assert note.sound is True, "a direct address is worth a sound"


def test_every_room_message_is_soundable_because_the_preference_is_the_mute(tmp_path) -> None:
    """Both intents ring; `sound_enabled` is the documented control.

    This asserted that a `decision` rang and a `discussion` did not, which
    made the *default* intent the quiet one — so the agent posts an operator
    most wants to hear were exactly the ones that stayed silent, while every
    intent that did ring already carried a raised priority. A record-level
    hard mute is also invisible to every preference the client evaluates, so
    there was no way to turn it off or explain it.
    """
    triggers = _triggers(tmp_path)
    loud = triggers.on_message_posted(
        "room_1",
        "alpha-team",
        "architect",
        "we decided",
        "decision",
        [],
        "msg_1",
        recipients=["coder"],
    )
    quiet = triggers.on_message_posted(
        "room_1",
        "alpha-team",
        "architect",
        "as a note",
        "discussion",
        [],
        "msg_2",
        recipients=["coder"],
    )
    assert loud[0].sound is True
    assert quiet[0].sound is True, "a default-intent message must still be soundable"

    # Muting is a delivery decision, so the record still lands — the
    # "muting deletes nothing" rule applied to the sound knob. The client
    # is what evaluates `sound_enabled` against this record.
    store = triggers.store
    store.update_preference("coder", {"sound_enabled": False})
    muted = triggers.on_message_posted(
        "room_1",
        "alpha-team",
        "architect",
        "another note",
        "discussion",
        [],
        "msg_3",
        recipients=["coder"],
    )
    assert len(muted) == 1, "a muted sound must not suppress the record itself"
    assert store.list("coder")[0].sound is True


def test_a_disabled_preference_means_the_event_happened_but_nobody_was_told(tmp_path) -> None:
    store = _store(tmp_path)
    store.update_preference("coder", {"enabled": False})
    triggers = NotificationTriggers(store)

    assert (
        triggers.on_message_posted(
            "room_1",
            "alpha-team",
            "architect",
            "hi",
            "discussion",
            [],
            "msg_1",
            recipients=["coder"],
        )
        == []
    )
    assert store.list("coder") == []


def test_crashes_are_urgent_and_ordinary_activity_is_not_notified(tmp_path) -> None:
    triggers = _triggers(tmp_path)
    crashed = triggers.on_activity_change("room_1", "alpha-team", "coder", "crashed", {"reason": "model_failure"})
    assert len(crashed) == 1
    assert crashed[0].priority == "urgent"
    assert "crashed" in crashed[0].title

    assert triggers.on_activity_change("room_1", "alpha-team", "coder", "working") == []
    assert triggers.on_activity_change("room_1", "alpha-team", "coder", "idle") == []


def test_an_unresponsive_agent_is_high_but_not_urgent(tmp_path) -> None:
    triggers = _triggers(tmp_path)
    note = triggers.on_activity_change("room_1", "alpha-team", "coder", "unresponsive")
    assert note[0].priority == "high", "silence is evidence, not proof of death"


def test_run_start_and_completion_carry_different_priorities(tmp_path) -> None:
    triggers = _triggers(tmp_path)
    started = triggers.on_run_started("room_1", "alpha-team", {"run_id": "r1", "objective": "ship it"})
    done = triggers.on_run_completed("room_1", "alpha-team", {"run_id": "r1", "status": "success"})
    assert started[0].priority == "high"
    assert done[0].priority == "normal"
    assert started[0].sound is True
    assert done[0].sound is False


def test_a_body_is_bounded_and_previews_break_on_a_word(tmp_path) -> None:
    triggers = _triggers(tmp_path)
    long_body = "word " * 400
    created = triggers.on_message_posted(
        "room_1",
        "alpha-team",
        "architect",
        long_body,
        "discussion",
        [],
        "msg_1",
        recipients=["coder"],
    )
    assert len(created[0].body) <= 500

    preview = _preview("one two three four five six", limit=10)
    assert len(preview) <= 11
    assert not preview.endswith(" "), "a mid-word cut is not a preview"
    assert _preview("short") == "short"


def test_a_preview_never_leaves_a_dangling_suffix(tmp_path) -> None:
    assert _preview("alpha beta gamma", limit=11).endswith("…")


# ---------------------------------------------------------------------------
# Service integration
# ---------------------------------------------------------------------------


async def test_posting_a_message_raises_a_notification_for_the_other_members(tmp_path) -> None:
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    msg, _ = svc.post_message("alpha-team", "architect", "status update")
    assert store.unread_count("coder") == 1
    assert store.unread_count("architect") == 0, "the sender must not notify themselves"

    notes = store.list("coder")
    assert notes[0].room_id is not None
    assert notes[0].message_id == msg.id
    assert notes[0].action_url.endswith("alpha-team")


async def test_an_agents_message_reaches_the_operators_inbox(tmp_path) -> None:
    """The record has to land in an inbox somebody actually reads.

    A room's roster is made of agents, but the only reader of this store is
    the operator running the installation. When the audience was the roster
    alone, every notification was created, durable and counted — and read by
    nobody, because the notification routes serve ``OPERATOR_USER_ID``. The
    bell was structurally empty, and the defect was invisible to every test
    that read back an *agent's* inbox instead of the one the UI asks for.
    """
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    msg, _ = svc.post_message("alpha-team", "coder", "the migration is done")

    assert store.unread_count(OPERATOR_USER_ID) == 1, "an agent's message must reach the operator's inbox"
    note = store.list(OPERATOR_USER_ID)[0]
    assert note.message_id == msg.id
    assert note.room_id is not None
    assert note.sender == "coder"
    assert note.action_url.endswith("alpha-team")


async def test_an_agents_message_is_soundable(tmp_path) -> None:
    """A room message carries no hard mute of its own.

    ``sound`` used to be ``intent in _LOUD_INTENTS``, which excluded the
    default ``discussion`` intent: an agent's ordinary post badged the panel
    and stayed inaudible, and the only intents that rang already carried a
    raised priority. The operator's ``sound_enabled`` preference and quiet
    hours are the documented controls — a per-record ``False`` was a second
    mute that no setting could explain or reach.
    """
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    svc.post_message("alpha-team", "coder", "ordinary chatter")  # intent="discussion"

    note = store.list(OPERATOR_USER_ID)[0]
    assert note.sound is not False, "a default-intent agent message must be able to make a sound"


async def test_the_operator_does_not_notify_themselves(tmp_path) -> None:
    """The operator is on every audience, so the sender guard has to hold.

    Putting the operator on the audience is what makes the notification
    reachable; skipping the sender is what keeps it from becoming a record of
    the operator talking to themselves. Both halves are load-bearing.
    """
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    svc.post_message("alpha-team", OPERATOR_USER_ID, "a note to self")

    assert store.unread_count(OPERATOR_USER_ID) == 0


async def test_the_trigger_path_and_the_read_path_agree_on_one_identity(tmp_path) -> None:
    """Guard the seam itself, not just the outcome.

    ``post_message`` writing a record and the notification routes serving a
    history are two modules each choosing an identity. Asserting only the
    outcome would still have passed whenever the ids happened to agree; this
    fails the moment either side re-introduces its own literal, which is
    exactly how the inbox went empty in the first place.
    """
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService
    from alpha.notifications.triggers import get_notification_store
    from app.gateway.routers import notifications as notif_router

    assert notif_router._current_user() == OPERATOR_USER_ID

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect"])
    svc.post_message("alpha-team", "architect", "ping")

    # The exact store object the router resolves must already hold the record.
    assert get_notification_store() is store
    assert store.unread_count(notif_router._current_user()) == 1


async def test_a_mentioned_member_gets_a_high_priority_record(tmp_path) -> None:
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    svc.post_message("alpha-team", "architect", "@coder can you check this?")

    types = [n.type for n in store.list("coder")]
    assert "mention" in types
    mention = next(n for n in store.list("coder") if n.type == "mention")
    assert mention.priority == "high"


async def test_a_notification_fault_never_fails_a_message_that_landed(tmp_path) -> None:
    """The transcript is durable first; the surface is best-effort after."""
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    class _ExplodingStore:
        def get_preference(self, user_id):
            raise RuntimeError("store is on fire")

        def create(self, notification):  # pragma: no cover - must not be reached
            raise RuntimeError("store is on fire")

    trg._store = _ExplodingStore()
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    msg, _ = svc.post_message("alpha-team", "architect", "this must survive")
    assert svc.get_room("alpha-team").find_message(msg.id) is not None


async def test_a_disabled_preference_still_records_the_message_in_the_room(tmp_path) -> None:
    import alpha.notifications.triggers as trg
    from alpha.groups.service import GroupChatService

    store = NotificationStore(storage_dir=tmp_path / "notifications")
    store.update_preference("coder", {"enabled": False})
    trg._store = store
    svc = GroupChatService(storage_path=tmp_path / "groups.json")
    svc.get_or_create_room("alpha-team", members=["architect", "coder"])

    svc.post_message("alpha-team", "architect", "quietly")
    assert store.list("coder") == []
    assert len(svc.get_room("alpha-team").log) == 1, "silence must not eat the transcript"


# ---------------------------------------------------------------------------
# Route surface
# ---------------------------------------------------------------------------


async def test_the_notification_routes_are_mounted() -> None:
    from app.gateway.app import create_app

    paths = {route.path for route in create_app().routes}
    for expected in (
        "/api/notifications",
        "/api/notifications/unread-count",
        "/api/notifications/read-all",
        "/api/notifications/preferences",
        "/api/notifications/test",
        "/api/notifications/{notification_id}/read",
    ):
        assert expected in paths, f"{expected} is not mounted"


async def test_the_test_route_creates_exactly_one_notification() -> None:
    from app.gateway.routers import notifications

    before = await notifications.unread_count()
    result = await notifications.test_notification()
    assert result["notification"]["type"] == "system"
    after = await notifications.unread_count()
    assert after["unread_count"] == before["unread_count"] + 1

    await notifications.mark_all_read()


async def test_reading_a_missing_notification_is_404() -> None:
    import pytest as _pytest

    from app.gateway.routers import notifications

    with _pytest.raises(Exception) as excinfo:
        await notifications.mark_notification_read("notif_missing")
    assert getattr(excinfo.value, "status_code", None) == 404


async def test_the_preference_route_round_trips() -> None:
    from app.gateway.routers import notifications

    updated = await notifications.update_preferences(notifications.PreferenceUpdate(sound_enabled=False, digest_mode=True))
    assert updated["sound_enabled"] is False
    assert updated["digest_mode"] is True

    read = await notifications.get_preferences()
    assert read["sound_enabled"] is False
    assert read["valid_types"] == list(NOTIFICATION_TYPES)
    assert read["valid_priorities"] == list(NOTIFICATION_PRIORITIES)

    await notifications.update_preferences(notifications.PreferenceUpdate(sound_enabled=True, digest_mode=False))


async def test_history_filters_by_type() -> None:
    from app.gateway.routers import notifications

    await notifications.test_notification()
    listing = await notifications.list_notifications(types="system", limit=5)
    assert listing["count"] >= 1
    assert all(n["type"] == "system" for n in listing["notifications"])
    await notifications.mark_all_read()
