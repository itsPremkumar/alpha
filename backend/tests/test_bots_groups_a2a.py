"""Production wiring for bots, group chat, and agent-to-agent messaging.

Covers: BotRegistry defaults + AGENT_WORKSPACE_HOME-aware persistence, GroupChatService
room auto-provisioning + orchestration modes, AgentRoster send/inbox, and Gateway
route mounting for /api/bots, /api/groups, /api/threads/{id}/agent-messages.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.app import create_app
from app.gateway.routers import agent_messages, bots, groups

pytestmark = pytest.mark.asyncio


def _admin_request():
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))


def _user_request():
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="user")))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    # Reset process singletons so each test gets a fresh HOME-bound instance.
    import agent_workspace.bots.registry as bot_reg
    import agent_workspace.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    yield


async def test_gateway_mounts_coordination_routes() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/api/bots" in paths
    assert "/api/bots/{name}" in paths
    assert "/api/groups" in paths
    assert "/api/groups/{name}" in paths
    assert "/api/threads/{thread_id}/agent-messages/roster" in paths
    assert "/api/threads/{thread_id}/agent-messages/messages" in paths


async def test_bots_list_contains_defaults_and_epoch() -> None:
    response = await bots.list_bots()
    names = {b["name"] for b in response["bots"]}
    assert {"architect", "coder", "reviewer", "tester", "researcher"} <= names
    for b in response["bots"]:
        assert b["epoch"] and len(b["epoch"]) == 12


async def test_bots_ensure_and_update_roundtrip() -> None:
    created = await bots.ensure_bot("secops", _admin_request(), bots.BotEnsureRequest(role="Security"))
    assert created["name"] == "secops"
    fetched = await bots.get_bot("secops")
    assert fetched["role"] == "Security"
    updated = await bots.update_bot("secops", _admin_request(), bots.BotUpdateRequest(role="Sec Lead"))
    assert updated["role"] == "Sec Lead"
    with pytest.raises(HTTPException) as excinfo:
        await bots.get_bot("missing-bot-xyz")
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        await bots.ensure_bot("secops", _user_request(), bots.BotEnsureRequest())
    assert excinfo.value.status_code == 403


async def test_bots_reject_bad_names() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await bots.get_bot("bad name!")
    assert excinfo.value.status_code == 422


async def test_groups_create_post_and_next_speakers() -> None:
    room = await groups.create_room(groups.RoomCreateRequest(name="alpha", members=["architect", "coder"], mode="mention"))
    assert room["name"] == "alpha"
    assert set(room["members"]) == {"architect", "coder"}
    posted = await groups.post_room_message("alpha", groups.RoomMessageRequest(sender="user", content="Hello @coder please review"))
    assert posted["message"]["sender"] == "user"
    assert "coder" in posted["next_speakers"]
    fetched = await groups.get_room("alpha")
    assert fetched["message_count"] >= 1
    assert fetched["messages"][-1]["content"].startswith("Hello")


async def test_groups_moderated_and_round_robin_modes() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="mod", members=["architect", "coder"], mode="moderated", moderator="architect"))
    posted = await groups.post_room_message("mod", groups.RoomMessageRequest(sender="coder", content="draft ready"))
    # Moderated mode returns to moderator after a specialist speaks.
    assert posted["next_speakers"] == ["architect"]
    await groups.create_room(groups.RoomCreateRequest(name="rr", members=["architect", "coder"], mode="round_robin"))
    first = await groups.post_room_message("rr", groups.RoomMessageRequest(sender="architect", content="kickoff"))
    assert first["next_speakers"] == ["coder"]


async def test_agent_roster_send_inbox_and_broadcast() -> None:
    from agent_workspace.subagents.messaging import get_agent_roster

    roster = get_agent_roster("thread-test-1")
    roster.register_agent("coder", role="worker", status="idle")
    roster.register_agent("reviewer", role="worker", status="busy")
    ok = roster.send_message("lead_agent", "coder", "start work", mode="auto")
    assert ok["status"] == "ok"
    assert ok["receipts"][0]["delivery_status"] == "delivered"
    queued = roster.send_message("lead_agent", "reviewer", "review later", mode="auto")
    assert queued["receipts"][0]["delivery_status"] == "queued"
    inbox = roster.get_inbox("coder")
    assert len(inbox) == 1 and inbox[0].content == "start work"
    # Second fetch marks read.
    assert roster.get_inbox("coder") == []
    broadcast = roster.send_message("lead_agent", "all", "standup", mode="steer")
    assert {r["receiver"] for r in broadcast["receipts"]} >= {"coder", "reviewer"}
    missing = roster.send_message("lead_agent", "ghost", "hi")
    assert missing["status"] == "error"


async def test_agent_messages_router_validation() -> None:
    # Unwrapped to bypass the threads owner-check decorator (covered elsewhere);
    # here we pin request validation + 404 mapping.
    roster = await agent_messages.list_roster.__wrapped__(thread_id="thread-x")
    assert roster["thread_id"] == "thread-x"
    await agent_messages.register_agent.__wrapped__(
        thread_id="thread-x",
        body=agent_messages.RegisterAgentRequest(name="coder", role="worker"),
    )
    sent = await agent_messages.send_agent_message.__wrapped__(
        thread_id="thread-x",
        body=agent_messages.SendMessageRequest(sender_name="lead_agent", receiver_name="coder", content="go"),
    )
    assert sent["status"] == "ok"
    inbox = await agent_messages.get_inbox.__wrapped__(thread_id="thread-x", agent_name="coder")
    assert inbox["count"] >= 1
    with pytest.raises(HTTPException) as excinfo:
        await agent_messages.send_agent_message.__wrapped__(
            thread_id="thread-x",
            body=agent_messages.SendMessageRequest(sender_name="lead_agent", receiver_name="ghost-1", content="hi"),
        )
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        await agent_messages.send_agent_message.__wrapped__(
            thread_id="thread-x",
            body=agent_messages.SendMessageRequest(sender_name="lead_agent", receiver_name="coder", content="hi", mode="bogus"),
        )
    assert excinfo.value.status_code == 422
