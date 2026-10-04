"""Advanced group messaging: identity, links, goals, pinning, threads, receipts.

Every feature here had the same shape of bug available to it: a field that
silently blanks a neighbour, a count that reports a different truth than the
list beside it, or a route that is declared *after* the `/{name}` catch-all and
therefore unreachable. The assertions below are written against those three
failures rather than against the happy path.

Route-boundary coverage follows `test_group_nesting_routes.py`: handlers are
called directly, with `ALPHA_HOME` isolated, so a refusal's status code and
message are what is actually asserted.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from alpha.groups.room import GROUP_CATEGORIES, GROUP_LINK_TYPES, GroupLink, GroupRoom
from alpha.groups.service import GroupChatService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg
    import alpha.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    yield


# ---------------------------------------------------------------------------
# Model backwards compatibility
# ---------------------------------------------------------------------------


def test_a_room_from_an_older_build_still_loads() -> None:
    """Every profile field defaults, so an existing rooms file is not lost."""
    legacy = {
        "room_id": "room_1",
        "name": "legacy",
        "topic": "General Team Collaboration",
        "members": ["architect"],
        "mode": "mention",
        "log": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    room = GroupRoom.from_dict(dict(legacy))
    assert room.description is None
    assert room.purpose is None
    assert room.goals == []
    assert room.tags == []
    assert room.category is None
    assert room.avatar_url is None
    assert room.banner_url is None
    assert room.avatar_color is None
    assert room.created_by is None
    # Absent is not zero: the caller can tell "no description" from a default.
    assert "description" not in legacy


def test_a_message_from_an_older_build_still_loads() -> None:
    legacy = {
        "id": "msg_1",
        "sender": "architect",
        "content": "hello",
        "intent": "discussion",
        "mentions": [],
        "metadata": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    from alpha.groups.room import GroupMessage

    msg = GroupMessage.from_dict(dict(legacy))
    assert msg.pinned is False
    assert msg.pinned_at is None
    assert msg.pinned_by is None
    assert msg.reply_count == 0
    assert msg.last_reply_at is None
    assert msg.attachments == []


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def _service(tmp_path) -> GroupChatService:
    return GroupChatService(storage_path=tmp_path / "groups.json")


async def test_profile_fields_round_trip_through_the_service(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team", members=["architect"])
    svc.update_group_profile(
        "alpha-team",
        description="Builds the core product.",
        purpose="Ship the v2 API",
        goals=["Finish the API", "Write the docs"],
        tags=["api", "v2"],
        category="engineering",
        avatar_color="#3366ff",
        created_by="operator",
        set_fields={"description", "purpose", "goals", "tags", "category", "avatar_color", "created_by"},
    )
    room = svc.get_room("alpha-team")
    assert room is not None
    assert room.description == "Builds the core product."
    assert room.purpose == "Ship the v2 API"
    assert room.tags == ["api", "v2"]
    assert room.category == "engineering"
    assert room.avatar_color == "#3366ff"
    assert room.created_by == "operator"


async def test_a_partial_update_does_not_blank_the_fields_it_did_not_send(tmp_path) -> None:
    """The whole reason `set_fields` exists: `None` means "not provided"."""
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    svc.update_group_profile(
        "alpha-team",
        description="keep me",
        purpose="keep me too",
        set_fields={"description", "purpose"},
    )
    svc.update_group_profile("alpha-team", purpose=None, set_fields={"purpose"})
    room = svc.get_room("alpha-team")
    assert room.description == "keep me", "an unrelated field was blanked"
    assert room.purpose is None, "an explicit clear was ignored"


async def test_empty_tags_and_goals_are_dropped_not_kept_as_blanks(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    svc.update_group_profile("alpha-team", tags=["  ", "real"], goals=["", "ship"], set_fields={"tags", "goals"})
    room = svc.get_room("alpha-team")
    assert room.tags == ["real"]
    assert room.goals == ["ship"]


async def test_the_profile_route_reports_absent_fields_as_null(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    profile = await groups.get_group_profile("alpha-team")
    assert profile["description"] is None
    assert profile["avatar_url"] is None
    assert profile["valid_categories"] == list(GROUP_CATEGORIES)
    # The read surface is the profile route, not `_room_to_response`.
    assert profile["name"] == "alpha-team"


async def test_a_partial_profile_patch_leaves_other_fields_alone(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    await groups.update_group_profile("alpha-team", groups.GroupProfileUpdate(description="first", purpose="second"))
    await groups.update_group_profile("alpha-team", groups.GroupProfileUpdate(purpose="changed"))
    profile = await groups.get_group_profile("alpha-team")
    assert profile["description"] == "first"
    assert profile["purpose"] == "changed"


async def test_the_profile_of_a_missing_room_is_404(tmp_path) -> None:
    from app.gateway.routers import groups

    with pytest.raises(HTTPException) as excinfo:
        await groups.get_group_profile("no-such-group")
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


async def test_links_are_created_listed_and_removed(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    link = svc.add_link("alpha-team", label="Board", url="https://example.test/board", link_type="project")
    assert link.link_type == "project"
    assert svc.list_links("alpha-team") == [link]
    svc.remove_link("alpha-team", link.link_id)
    assert svc.list_links("alpha-team") == []


async def test_an_unknown_link_type_is_refused_at_the_boundary(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(ValueError):
        svc.add_link("alpha-team", label="x", url="https://example.test", link_type="not-a-type")
    assert svc.list_links("alpha-team") == []


async def test_a_link_without_a_label_or_url_is_refused(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(ValueError):
        svc.add_link("alpha-team", label="   ", url="https://example.test")
    with pytest.raises(ValueError):
        svc.add_link("alpha-team", label="ok", url="   ")


async def test_an_incomplete_reorder_never_drops_a_link(tmp_path) -> None:
    """A reorder naming only some ids keeps the rest, rather than deleting them."""
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    a = svc.add_link("alpha-team", label="A", url="https://a.test")
    b = svc.add_link("alpha-team", label="B", url="https://b.test")
    c = svc.add_link("alpha-team", label="C", url="https://c.test")
    ordered = svc.reorder_links("alpha-team", [c.link_id])
    assert {link.link_id for link in ordered} == {a.link_id, b.link_id, c.link_id}
    assert ordered[0].link_id == c.link_id
    assert [link.position for link in ordered] == [0, 1, 2]


async def test_links_and_goals_survive_a_reload(tmp_path) -> None:
    first = _service(tmp_path)
    first.get_or_create_room("alpha-team")
    first.add_link("alpha-team", label="Docs", url="https://docs.test", link_type="docs")
    first.add_goal("alpha-team", title="Ship it")

    second = _service(tmp_path)
    links = second.list_links("alpha-team")
    assert len(links) == 1
    assert links[0].label == "Docs"
    assert links[0].link_type in GROUP_LINK_TYPES
    goals = second.list_goals("alpha-team")
    assert [g["title"] for g in goals] == ["Ship it"]


async def test_the_link_routes_answer(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    created = await groups.add_group_link("alpha-team", groups.GroupLinkCreate(label="Board", url="https://example.test/board", link_type="project"))
    assert created["label"] == "Board"

    listing = await groups.list_group_links("alpha-team")
    assert listing["count"] == 1
    assert listing["valid_types"] == list(GROUP_LINK_TYPES)

    await groups.remove_group_link("alpha-team", created["link_id"])
    assert (await groups.list_group_links("alpha-team"))["count"] == 0


async def test_adding_a_link_to_a_missing_room_is_404(tmp_path) -> None:
    from app.gateway.routers import groups

    with pytest.raises(HTTPException) as excinfo:
        await groups.add_group_link("no-such-group", groups.GroupLinkCreate(label="Board", url="https://example.test/board"))
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Goals and project binding
# ---------------------------------------------------------------------------


async def test_a_goal_completes_at_100_and_stamps_its_completion(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    goal = svc.add_goal("alpha-team", title="Ship it", description="v2")
    assert goal["status"] == "pending"
    assert goal["progress"] == 0

    updated = svc.update_goal("alpha-team", goal["goal_id"], {"status": "completed"})
    assert updated["progress"] == 100
    assert updated["completed_at"] is not None

    # Re-completing does not move the original stamp.
    again = svc.update_goal("alpha-team", goal["goal_id"], {"status": "pending"})
    assert again["completed_at"] == updated["completed_at"]


async def test_updating_an_unknown_goal_is_a_keyerror(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(KeyError):
        svc.update_goal("alpha-team", "goal_missing", {"status": "completed"})


async def test_an_empty_goal_title_is_refused(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(ValueError):
        svc.add_goal("alpha-team", title="   ")


async def test_the_project_link_is_a_display_record_and_is_remoable(tmp_path) -> None:
    svc = _service(tmp_path)
    room = svc.get_or_create_room("alpha-team")
    link = svc.link_project("alpha-team", project_id="proj_1", project_name="Core")
    assert link["project_id"] == "proj_1"
    assert room.project_id == "proj_1", "the room's own project_id is the crew binding"
    assert svc.get_project_link("alpha-team") == link

    svc.unlink_project("alpha-team")
    assert svc.get_project_link("alpha-team") is None


async def test_a_project_link_needs_an_id(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(ValueError):
        svc.link_project("alpha-team", project_id="   ")


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------


async def test_a_clone_copies_the_charter_but_never_the_transcript(tmp_path) -> None:
    svc = _service(tmp_path)
    source = svc.get_or_create_room("alpha-team", members=["architect", "coder"])
    svc.update_group_profile(
        "alpha-team",
        description="source description",
        tags=["src"],
        set_fields={"description", "tags"},
    )
    svc.add_link("alpha-team", label="Docs", url="https://docs.test")
    svc.post_message("alpha-team", "architect", "hello world")

    clone = svc.clone_group("alpha-team", new_name="alpha-team-copy")
    assert clone.name == "alpha-team-copy"
    assert clone.log == [], "the transcript must not be copied"
    assert clone.description == "source description"
    assert clone.tags == ["src"]
    assert set(clone.members) == {"architect", "coder"}
    assert [link.label for link in svc.list_links("alpha-team-copy")] == ["Docs"]
    assert source.room_id != clone.room_id

    # The clone's links are fresh records: editing one leaves the source alone.
    copy_links = svc.list_links("alpha-team-copy")
    svc.remove_link("alpha-team-copy", copy_links[0].link_id)
    assert len(svc.list_links("alpha-team")) == 1


async def test_cloning_into_an_existing_name_is_refused(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    svc.get_or_create_room("taken")
    from alpha.groups.scope import ScopeError

    with pytest.raises(ScopeError):
        svc.clone_group("alpha-team", new_name="taken")


async def test_the_clone_route_copies_only_what_was_asked(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    await groups.update_group_profile("alpha-team", groups.GroupProfileUpdate(description="the description"))
    await groups.add_group_link("alpha-team", groups.GroupLinkCreate(label="Docs", url="https://docs.test"))

    room = await groups.clone_group(
        "alpha-team",
        groups.CloneRequest(new_name="copy-one", include_links=False, include_profile=False),
    )
    assert room["name"] == "copy-one"
    assert (await groups.list_group_links("copy-one"))["count"] == 0
    assert (await groups.get_group_profile("copy-one"))["description"] is None


async def test_the_clone_route_reports_a_duplicate_as_409(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team"))
    await groups.create_room(groups.RoomCreateRequest(name="taken"))
    with pytest.raises(HTTPException) as excinfo:
        await groups.clone_group("alpha-team", groups.CloneRequest(new_name="taken"))
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


async def test_pin_and_unpin_move_a_message_in_and_out_of_the_pinned_list(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    msg, _ = svc.post_message("alpha-team", "architect", "the decision")
    svc.post_message("alpha-team", "coder", "a comment")

    pinned = svc.pin_message("alpha-team", msg.id, "operator")
    assert pinned.pinned is True
    assert pinned.pinned_by == "operator"
    assert [m.id for m in svc.pinned_messages("alpha-team")] == [msg.id]

    svc.unpin_message("alpha-team", msg.id)
    assert svc.pinned_messages("alpha-team") == []
    assert svc.get_room("alpha-team").find_message(msg.id).pinned is False


async def test_pinning_a_deleted_message_is_refused(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    msg, _ = svc.post_message("alpha-team", "architect", "bye")
    svc.delete_message("alpha-team", msg.id)
    with pytest.raises(ValueError):
        svc.pin_message("alpha-team", msg.id, "operator")


async def test_pinning_an_unknown_message_is_a_keyerror(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(KeyError):
        svc.pin_message("alpha-team", "msg_missing", "operator")


async def test_the_pin_routes_answer(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    posted = await groups.post_room_message("alpha-team", groups.RoomMessageRequest(sender="architect", content="pin me"))
    mid = posted["message"]["id"]

    await groups.pin_room_message("alpha-team", mid, groups.ActorRequest(actor="operator"))
    pinned = await groups.list_pinned_messages("alpha-team")
    assert pinned["count"] == 1
    assert pinned["pinned"][0]["id"] == mid

    await groups.unpin_room_message("alpha-team", mid)
    assert (await groups.list_pinned_messages("alpha-team"))["count"] == 0


async def test_pinning_without_an_actor_is_a_422(tmp_path) -> None:
    """`ActorRequest` exists precisely so a pin does not need a fake emoji."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        groups_request = __import__("app.gateway.routers.groups", fromlist=["ActorRequest"])
        groups_request.ActorRequest()


# ---------------------------------------------------------------------------
# Threading
# ---------------------------------------------------------------------------


async def test_thread_replies_and_counts_track_the_live_transcript(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    root, _ = svc.post_message("alpha-team", "architect", "root question")
    first, _ = svc.post_message("alpha-team", "coder", "answer one", reply_to=root.id)
    svc.post_message("alpha-team", "architect", "answer two", reply_to=root.id)

    replies = svc.thread_replies("alpha-team", root.id)
    assert [r.id for r in replies] == [first.id, replies[1].id]
    assert root.reply_count == 2, "the derived count must match the list beside it"
    assert root.last_reply_at == replies[-1].created_at
    assert [r.id for r in svc.thread_roots("alpha-team")] == [root.id]


async def test_a_reply_to_a_missing_message_is_a_keyerror(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(KeyError):
        svc.thread_replies("alpha-team", "msg_missing")


async def test_the_thread_route_returns_root_and_replies(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    root = await groups.post_room_message("alpha-team", groups.RoomMessageRequest(sender="architect", content="root"))
    root_id = root["message"]["id"]
    await groups.post_room_message("alpha-team", groups.RoomMessageRequest(sender="architect", content="reply", reply_to=root_id))

    thread = await groups.get_message_thread("alpha-team", root_id)
    assert thread["root"]["id"] == root_id
    assert len(thread["replies"]) == 1

    roots = await groups.list_threads("alpha-team")
    assert roots["count"] == 1
    assert roots["threads"][0]["reply_count"] == 1


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def test_search_is_case_insensitive_bounded_and_skips_deleted(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    svc.post_message("alpha-team", "architect", "The DEPLOY plan is ready")
    doomed, _ = svc.post_message("alpha-team", "architect", "deploy the secret thing")
    svc.post_message("alpha-team", "coder", "unrelated")
    svc.delete_message("alpha-team", doomed.id)

    hits = svc.search_messages("alpha-team", "deploy")
    assert len(hits) == 1
    assert hits[0].id != doomed.id, "searching must not re-disclose a deleted message"

    assert svc.search_messages("alpha-team", "   ") == [], "an empty query is not a wildcard"
    assert svc.search_messages("alpha-team", "deploy", sender="coder") == []


async def test_a_bounded_search_never_returns_the_whole_log(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    for i in range(10):
        svc.post_message("alpha-team", "architect", f"deploy {i}")
    assert len(svc.search_messages("alpha-team", "deploy", limit=3)) == 3


async def test_the_search_route_reports_the_query_it_ran(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    await groups.post_room_message("alpha-team", groups.RoomMessageRequest(sender="architect", content="deploy plan"))
    # Every query parameter is passed explicitly: outside FastAPI a `Query`
    # default is a sentinel object, not the value, and in-process callers are
    # expected to supply the same injection the framework would.
    found = await groups.search_group_messages("alpha-team", q="deploy", sender=None, intent=None, limit=50)
    assert found["query"] == "deploy"
    assert found["count"] == 1

    empty = await groups.search_group_messages("alpha-team", q="nothing-matches", sender=None, intent=None, limit=50)
    assert empty["count"] == 0, "no hits is a count of 0, not a failed read"


# ---------------------------------------------------------------------------
# Read receipts
# ---------------------------------------------------------------------------


async def test_a_message_with_no_receipt_is_unread(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    msg, _ = svc.post_message("alpha-team", "architect", "did you see this?")

    unread = svc.unread_messages("alpha-team", "coder")
    assert [m.id for m in unread] == [msg.id], "absence of a receipt is not a read"

    svc.mark_read("alpha-team", msg.id, "coder")
    assert svc.unread_messages("alpha-team", "coder") == []
    assert svc.message_readers("alpha-team", msg.id) == ["coder"]
    # The other member still has not read it.
    assert [m.id for m in svc.unread_messages("alpha-team", "architect")] == [msg.id]


async def test_marking_a_missing_message_read_is_a_keyerror(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.get_or_create_room("alpha-team")
    with pytest.raises(KeyError):
        svc.mark_read("alpha-team", "msg_missing", "coder")


async def test_receipts_survive_a_reload(tmp_path) -> None:
    first = _service(tmp_path)
    first.get_or_create_room("alpha-team")
    msg, _ = first.post_message("alpha-team", "architect", "hello")
    first.mark_read("alpha-team", msg.id, "coder")

    second = _service(tmp_path)
    assert second.unread_messages("alpha-team", "coder") == []
    assert second.message_readers("alpha-team", msg.id) == ["coder"]


async def test_the_unread_route_names_the_reader_it_answered_for(tmp_path) -> None:
    from app.gateway.routers import groups

    await groups.create_room(groups.RoomCreateRequest(name="alpha-team", members=["architect"]))
    posted = await groups.post_room_message("alpha-team", groups.RoomMessageRequest(sender="architect", content="hello"))
    mid = posted["message"]["id"]

    before = await groups.list_unread_messages("alpha-team", reader="coder")
    assert before["count"] == 1
    assert before["reader"] == "coder"

    await groups.mark_message_read("alpha-team", mid, groups.ActorRequest(actor="coder"))
    after = await groups.list_unread_messages("alpha-team", reader="coder")
    assert after["count"] == 0
    assert (await groups.list_message_readers("alpha-team", mid))["readers"] == ["coder"]


# ---------------------------------------------------------------------------
# Typing indicators
# ---------------------------------------------------------------------------


async def test_a_typing_indicator_is_volatile_and_never_persisted(tmp_path) -> None:
    """A typing state that survives a restart would claim someone still types."""
    first = _service(tmp_path)
    first.get_or_create_room("alpha-team")
    first.set_typing("alpha-team", "coder", True)
    assert [t["bot_name"] for t in first.typing_indicators("alpha-team")] == ["coder"]

    second = _service(tmp_path)
    assert second.typing_indicators("alpha-team") == []

    first.set_typing("alpha-team", "coder", False)
    assert first.typing_indicators("alpha-team") == []


async def test_typing_in_a_missing_room_is_404(tmp_path) -> None:
    from app.gateway.routers import groups

    with pytest.raises(HTTPException) as excinfo:
        await groups.set_typing_indicator("no-such-group", groups.TypingRequest(bot_name="coder", is_typing=True))
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Route mounting and ordering
# ---------------------------------------------------------------------------


def _group_paths() -> list[str]:
    from app.gateway.app import create_app

    return [route.path for route in create_app().routes if route.path.startswith("/api/groups")]


def test_every_advanced_route_is_mounted() -> None:
    paths = _group_paths()
    for expected in (
        "/api/groups/{name}/profile",
        "/api/groups/{name}/links",
        "/api/groups/{name}/goals",
        "/api/groups/{name}/project-link",
        "/api/groups/{name}/clone",
        "/api/groups/{name}/pinned",
        "/api/groups/{name}/threads",
        "/api/groups/{name}/search",
        "/api/groups/{name}/unread",
        "/api/groups/{name}/typing",
        "/api/groups/{name}/events",
        "/api/groups/{name}/messages/{message_id}/pin",
        "/api/groups/{name}/messages/{message_id}/read",
        "/api/groups/{name}/messages/{message_id}/thread",
        "/api/groups/{name}/messages/{message_id}/readers",
    ):
        assert expected in paths, f"{expected} is not mounted"


def test_every_literal_route_precedes_the_name_catchall() -> None:
    """Starlette matches in registration order.

    A literal declared after `GET /{name}` answers `Room 'profile' not found`,
    which is indistinguishable from an absent route — the same trap as
    `skills/{skill_name}` and `workflows/{workflow_id}`.
    """
    paths = _group_paths()
    catchall = paths.index("/api/groups/{name}")
    for literal in (
        "/api/groups/{name}/profile",
        "/api/groups/{name}/links",
        "/api/groups/{name}/goals",
        "/api/groups/{name}/project-link",
        "/api/groups/{name}/clone",
        "/api/groups/{name}/pinned",
        "/api/groups/{name}/threads",
        "/api/groups/{name}/search",
        "/api/groups/{name}/unread",
        "/api/groups/{name}/typing",
        "/api/groups/{name}/events",
    ):
        assert paths.index(literal) < catchall, f"{literal} is swallowed by the {{name}} catch-all"


def test_the_pin_route_precedes_the_message_id_catchall() -> None:
    paths = _group_paths()
    assert paths.index("/api/groups/{name}/messages/{message_id}/pin") < paths.index("/api/groups/{name}/messages/{message_id}")


async def test_the_events_route_of_a_missing_room_is_404(tmp_path) -> None:
    from app.gateway.routers import groups

    with pytest.raises(HTTPException) as excinfo:
        await groups.group_events("no-such-group")
    assert excinfo.value.status_code == 404


async def test_a_subscriber_receives_the_events_a_real_room_publishes(tmp_path, monkeypatch) -> None:
    """Wire the route to a live publisher, not to an invented bus key.

    The two tests below this one drive `GroupEventBus` with strings like
    `"room_x"`, so they only prove the bus matches a key to itself. Nothing
    connected the *route* to a *real* publisher, and the route subscribed with
    the room's name while every `post_message`/pin/link/goal write publishes
    under `room.room_id`. The stream then opened, answered 200 with
    `text/event-stream`, and delivered nothing for the life of the connection
    — a healthy-looking dead stream.
    """
    import asyncio

    import alpha.groups.service as grp_svc
    from app.gateway.group_events import get_group_event_bus, publish_room_event
    from app.gateway.routers import groups

    bus = get_group_event_bus()
    # The bus is process-global; swap its containers rather than clearing the
    # originals, so this test cannot leak state into the ones beside it.
    monkeypatch.setattr(bus, "_subscribers", {})
    monkeypatch.setattr(bus, "_history", [])

    svc = _service(tmp_path)
    # The router resolves the process-wide service through
    # `get_group_chat_service()`, which *rebuilds* it whenever the recorded
    # path no longer matches the live default. Pin both globals so the route
    # reaches this instance instead of a freshly constructed empty one.
    monkeypatch.setattr(grp_svc, "_global_groups", svc)
    monkeypatch.setattr(grp_svc, "_global_groups_path", str(grp_svc._default_storage_path().resolve()))
    monkeypatch.setattr(grp_svc, "_group_event_observer", publish_room_event)

    stream = None
    try:
        svc.get_or_create_room("alpha-team", members=["architect"])

        # The handler subscribes eagerly, before the body iterator is drained.
        response = await groups.group_events("alpha-team")
        stream = response.body_iterator
        assert response.media_type == "text/event-stream"

        svc.post_message("alpha-team", "architect", "hello over the stream")

        frame = await asyncio.wait_for(stream.__anext__(), timeout=5)
        assert "event: message" in frame
        assert "hello over the stream" not in frame, "the frame carries ids, not the transcript"
        assert '"room_name": "alpha-team"' in frame
    finally:
        if stream is not None:
            await stream.aclose()


async def test_the_group_event_bus_delivers_to_a_subscriber(tmp_path) -> None:
    from app.gateway.group_events import get_group_event_bus

    bus = get_group_event_bus()
    queue = bus.subscribe("room_x")
    bus.publish("room_x", "message", {"message_id": "msg_1"})
    event = queue.get_nowait()
    assert event["event"] == "message"
    assert event["room_id"] == "room_x"
    assert event["data"]["message_id"] == "msg_1"
    bus.unsubscribe("room_x", queue)


async def test_a_slow_subscriber_drops_the_oldest_event_rather_than_growing(tmp_path) -> None:
    from app.gateway.group_events import RETAINED_EVENTS, get_group_event_bus

    bus = get_group_event_bus()
    queue = bus.subscribe("room_y")
    for i in range(RETAINED_EVENTS + 5):
        bus.publish("room_y", "message", {"i": i})
    assert queue.qsize() <= RETAINED_EVENTS
    # The newest event is the one that survived.
    assert queue.get_nowait()["data"]["i"] == 5
    bus.unsubscribe("room_y", queue)


async def test_posting_a_room_message_publishes_an_event(tmp_path) -> None:
    from alpha.groups.service import set_group_event_observer

    seen: list[tuple[str, str, dict]] = []
    set_group_event_observer(lambda room_id, event, data: seen.append((room_id, event, data)))
    try:
        svc = _service(tmp_path)
        svc.get_or_create_room("alpha-team")
        svc.post_message("alpha-team", "architect", "hello")
        assert [e for _, e, _ in seen] == ["message"]
        assert seen[0][2]["room_name"] == "alpha-team"
    finally:
        set_group_event_observer(None)


async def test_a_faulty_event_observer_cannot_fail_a_message_that_landed(tmp_path) -> None:
    from alpha.groups.service import set_group_event_observer

    def _boom(room_id, event, data):
        raise RuntimeError("display bug")

    set_group_event_observer(_boom)
    try:
        svc = _service(tmp_path)
        svc.get_or_create_room("alpha-team")
        msg, _ = svc.post_message("alpha-team", "architect", "hello")
        assert svc.get_room("alpha-team").find_message(msg.id) is not None
    finally:
        set_group_event_observer(None)


def test_group_link_defaults_are_serialisable() -> None:
    link = GroupLink(link_id="link_1", room_id="room_1", label="Docs", url="https://docs.test")
    assert GroupLink.from_dict(link.to_dict()) == link
    assert link.created_at
    assert link.position == 0
