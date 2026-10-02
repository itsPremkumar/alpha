"""Gateway route coverage for the group roster and message-feature endpoints.

Everything here is at the router boundary, because that is where the real bugs
were: the presence route did not exist (the client read a markdown digest
instead), the intent vocabulary was narrower than the composer's, and
edit/delete/react/forward had no route at all.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gateway.app import create_app
from app.gateway.routers import groups

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
# Route mounting
# ---------------------------------------------------------------------------


async def test_gateway_mounts_the_message_feature_routes() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/api/groups/{name}/members" in paths
    assert "/api/groups/{name}/messages/{message_id}" in paths
    assert "/api/groups/{name}/messages/{message_id}/reactions" in paths
    assert "/api/groups/{name}/messages/{message_id}/forward" in paths


# ---------------------------------------------------------------------------
# Intent vocabulary at the route boundary
# ---------------------------------------------------------------------------


async def test_every_typed_a2a_kind_is_accepted_by_the_post_route() -> None:
    """The composer's thirteen kinds must all reach the room.

    `decision` is the regression: it is what "Post as group decision" sends, and
    the route used to answer 422 because it validated its own six-value tuple.
    """
    await groups.create_room(groups.RoomCreateRequest(name="kinds", members=["architect", "coder"]))
    for kind in ("decision", "handoff", "escalation", "blocker", "task_completion", "question", "answer"):
        posted = await groups.post_room_message("kinds", groups.RoomMessageRequest(sender="operator", content=f"a {kind}", intent=kind))
        assert posted["message"]["intent"] == kind


async def test_an_intent_outside_the_vocabulary_is_still_refused() -> None:
    """Widening the vocabulary must not make it accept anything."""
    await groups.create_room(groups.RoomCreateRequest(name="bad-kind", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.post_room_message("bad-kind", groups.RoomMessageRequest(sender="operator", content="x", intent="nonsense"))
    assert excinfo.value.status_code == 422


# ---------------------------------------------------------------------------
# Presence roster
# ---------------------------------------------------------------------------


async def test_room_members_reports_a_state_per_member() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="sprint-room", members=["architect", "coder"]))
    response = await groups.list_room_members("sprint-room")

    assert response["room"] == "sprint-room"
    assert response["count"] == 2
    assert {m["name"] for m in response["members"]} == {"architect", "coder"}
    for member in response["members"]:
        assert member["state"] in ("online", "busy", "idle", "offline", "unknown")
        assert member["source"], "every member carries the provenance of its state"
        assert "detail" in member


async def test_room_members_keeps_the_rooms_member_order() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="ordered", members=["tester", "architect", "coder"]))
    response = await groups.list_room_members("ordered")
    assert [m["name"] for m in response["members"]] == ["tester", "architect", "coder"]


async def test_room_members_counts_states_by_bucket() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="bucketed", members=["architect", "coder"]))
    response = await groups.list_room_members("bucketed")
    assert sum(response["by_state"].values()) == response["count"]


async def test_room_members_404s_for_an_unknown_room() -> None:
    """A missing room is a 404, not an empty roster."""
    with pytest.raises(HTTPException) as excinfo:
        await groups.list_room_members("no-such-room")
    assert excinfo.value.status_code == 404


async def test_room_members_rejects_an_invalid_room_name() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.list_room_members("bad name!")
    assert excinfo.value.status_code == 422


# ---------------------------------------------------------------------------
# Edit / delete
# ---------------------------------------------------------------------------


async def _post(room: str, content: str, **kwargs):
    return await groups.post_room_message(room, groups.RoomMessageRequest(sender="operator", content=content, **kwargs))


async def test_edit_then_delete_round_trip() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="crud", members=["architect"]))
    posted = await _post("crud", "typo tehre")
    mid = posted["message"]["id"]

    edited = await groups.edit_room_message("crud", mid, groups.MessageEditRequest(content="typo there"))
    assert edited["message"]["content"] == "typo there"
    assert edited["message"]["edited_at"] is not None
    assert edited["message"]["id"] == mid

    deleted = await groups.delete_room_message("crud", mid)
    assert deleted["message"]["deleted"] is True
    assert deleted["message"]["content"] == ""


async def test_edit_of_an_unknown_message_is_404() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="ghost-edit", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.edit_room_message("ghost-edit", "msg_missing", groups.MessageEditRequest(content="x"))
    assert excinfo.value.status_code == 404


async def test_delete_of_an_unknown_message_is_404() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="ghost-del", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.delete_room_message("ghost-del", "msg_missing")
    assert excinfo.value.status_code == 404


async def test_a_double_delete_is_a_conflict_not_a_second_success() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="dd", members=["architect"]))
    posted = await _post("dd", "text")
    mid = posted["message"]["id"]
    await groups.delete_room_message("dd", mid)
    with pytest.raises(HTTPException) as excinfo:
        await groups.delete_room_message("dd", mid)
    assert excinfo.value.status_code == 409


async def test_a_malformed_message_id_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="bad-id", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.delete_room_message("bad-id", "msg with spaces")
    assert excinfo.value.status_code == 422


async def test_editing_a_deleted_message_is_409() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="ed", members=["architect"]))
    posted = await _post("ed", "text")
    mid = posted["message"]["id"]
    await groups.delete_room_message("ed", mid)
    with pytest.raises(HTTPException) as excinfo:
        await groups.edit_room_message("ed", mid, groups.MessageEditRequest(content="new"))
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


async def test_a_reaction_toggles_and_returns_the_server_map() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="react", members=["architect"]))
    posted = await _post("react", "react to me")
    mid = posted["message"]["id"]

    first = await groups.react_to_room_message("react", mid, groups.ReactionRequest(actor="operator", emoji="🎉"))
    assert first["reactions"] == {"🎉": ["operator"]}
    assert first["message_id"] == mid

    second = await groups.react_to_room_message("react", mid, groups.ReactionRequest(actor="operator", emoji="🎉"))
    assert second["reactions"] == {}


async def test_an_emoji_outside_the_bounded_set_is_refused() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="bad-react", members=["architect"]))
    posted = await _post("bad-react", "text")
    mid = posted["message"]["id"]
    with pytest.raises(HTTPException) as excinfo:
        await groups.react_to_room_message("bad-react", mid, groups.ReactionRequest(actor="operator", emoji="n"))
    assert excinfo.value.status_code == 422


async def test_a_reaction_on_an_unknown_message_is_404() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="react-404", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.react_to_room_message("react-404", "msg_missing", groups.ReactionRequest(actor="operator", emoji="👍"))
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Forward
# ---------------------------------------------------------------------------


async def test_forward_posts_a_fresh_copy_into_the_target_room() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="source-room", members=["architect"]))
    posted = await _post("source-room", "the finding")

    result = await groups.forward_room_message(
        "source-room",
        posted["message"]["id"],
        groups.ForwardRequest(sender="operator", target_room="target-room"),
    )
    assert result["target_room"] == "target-room"
    assert result["message"]["id"] != posted["message"]["id"]
    assert result["message"]["content"] == "the finding"
    assert result["message"]["forwarded_from"]["room"] == "source-room"

    # The source room keeps its own original, untouched.
    source = await groups.get_room("source-room")
    assert source["messages"][-1]["id"] == posted["message"]["id"]


async def test_forward_carries_edited_content_and_intent() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="fr-src", members=["architect"]))
    posted = await _post("fr-src", "raw text")
    result = await groups.forward_room_message(
        "fr-src",
        posted["message"]["id"],
        groups.ForwardRequest(sender="operator", target_room="fr-dst", content="trimmed", intent="decision"),
    )
    assert result["message"]["content"] == "trimmed"
    assert result["message"]["intent"] == "decision"


async def test_forward_with_a_bad_target_intent_is_422() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="fr-bad", members=["architect"]))
    posted = await _post("fr-bad", "text")
    with pytest.raises(HTTPException) as excinfo:
        await groups.forward_room_message(
            "fr-bad",
            posted["message"]["id"],
            groups.ForwardRequest(sender="operator", target_room="fr-bad-dst", intent="nonsense"),
        )
    assert excinfo.value.status_code == 422


async def test_forward_of_an_unknown_message_is_404() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="fr-404", members=["architect"]))
    with pytest.raises(HTTPException) as excinfo:
        await groups.forward_room_message("fr-404", "msg_missing", groups.ForwardRequest(sender="operator", target_room="fr-404-dst"))
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


async def test_a_posted_reply_carries_its_target_and_the_room_read_returns_it() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="replies", members=["architect", "coder"]))
    first = await _post("replies", "the question")
    reply = await _post("replies", "the answer", reply_to=first["message"]["id"])
    assert reply["message"]["reply_to"] == first["message"]["id"]

    room = await groups.get_room("replies")
    assert room["messages"][-1]["reply_to"] == first["message"]["id"]


async def test_a_reply_to_a_deleted_message_is_still_stored() -> None:
    """Losing the reply would be worse than keeping a dangling reference."""
    await groups.create_room(groups.RoomCreateRequest(name="orphan", members=["architect"]))
    first = await _post("orphan", "original")
    await groups.delete_room_message("orphan", first["message"]["id"])
    reply = await _post("orphan", "answering a deleted row", reply_to=first["message"]["id"])
    assert reply["message"]["reply_to"] == first["message"]["id"]
