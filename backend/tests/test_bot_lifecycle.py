"""Bot lifecycle layer (inventory #1/#23/#24/#25 + A2A kinds #12).

Covers: role templates, cloning (config without memory), lifecycle statuses
with team-run exclusion, avatars, and structured agent message kinds — all on
top of the BotRegistry / AgentRoster system parts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import agent_messages, bots

pytestmark = pytest.mark.asyncio


def _admin_request():
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg
    import alpha.groups.runner as runner
    import alpha.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    monkeypatch.setattr(runner, "_global_runner", None)
    monkeypatch.setattr(runner, "_global_runner_path", None)
    yield


async def test_template_catalog_lists_roles() -> None:
    response = await bots.list_bot_templates()
    slugs = {t["slug"] for t in response["templates"]}
    assert {"ceo", "security", "sre", "developer", "qa"} <= slugs
    assert response["count"] == len(response["templates"])
    for template in response["templates"]:
        assert template["display"] and template["role"]


async def test_ensure_from_template_applies_role_and_avatar() -> None:
    created = await bots.ensure_bot("sec-lead", _admin_request(), bots.BotEnsureRequest(template="security"))
    assert created["role"] == "Security & Vulnerability Analyst"
    assert created["avatar"] == "🛡️"
    assert created["status"] == "active"


async def test_ensure_rejects_unknown_template() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await bots.ensure_bot("x", _admin_request(), bots.BotEnsureRequest(template="nope"))
    assert excinfo.value.status_code == 422


async def test_ensure_explicit_fields_win_over_template() -> None:
    created = await bots.ensure_bot(
        "custom-sec",
        _admin_request(),
        bots.BotEnsureRequest(template="security", role="Custom Role", avatar="🔒"),
    )
    assert created["role"] == "Custom Role"
    assert created["avatar"] == "🔒"


async def test_clone_copies_config_not_memory() -> None:
    await bots.ensure_bot("orig", _admin_request(), bots.BotEnsureRequest(template="sre"))
    await bots.update_bot("orig", _admin_request(), bots.BotUpdateRequest(skills=["k8s"], model="model-x"))
    clone = await bots.clone_bot("orig-copy", _admin_request(), bots.BotCloneRequest(source="orig", display_name="SRE Two"))
    assert clone["name"] == "orig-copy"
    assert clone["display_name"] == "SRE Two"
    assert clone["role"] == "Site Reliability Engineer & On-Call Responder"
    assert clone["soul"] == (await bots.get_bot("orig"))["soul"]
    assert clone["skills"] == ["k8s"]
    assert clone["model"] == "model-x"
    assert clone["status"] == "active"


async def test_clone_missing_source_is_404_and_taken_name_is_409() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await bots.clone_bot("fresh", _admin_request(), bots.BotCloneRequest(source="ghost"))
    assert excinfo.value.status_code == 404
    await bots.ensure_bot("taken", _admin_request(), bots.BotEnsureRequest())
    with pytest.raises(HTTPException) as excinfo:
        await bots.clone_bot("taken", _admin_request(), bots.BotCloneRequest(source="architect"))
    assert excinfo.value.status_code == 422


async def test_lifecycle_status_roundtrip_and_filter() -> None:
    await bots.ensure_bot("napper", _admin_request(), bots.BotEnsureRequest())
    updated = await bots.update_bot("napper", _admin_request(), bots.BotUpdateRequest(status="sleeping", avatar="😴"))
    assert updated["status"] == "sleeping"
    assert updated["avatar"] == "😴"
    sleeping = await bots.list_bots(status="sleeping")
    assert {b["name"] for b in sleeping["bots"]} == {"napper"}
    active = await bots.list_bots(status="active")
    assert "napper" not in {b["name"] for b in active["bots"]}
    with pytest.raises(HTTPException) as excinfo:
        await bots.update_bot("napper", _admin_request(), bots.BotUpdateRequest(status="vibing"))
    assert excinfo.value.status_code == 422
    with pytest.raises(HTTPException) as excinfo:
        await bots.list_bots(status="vibing")
    assert excinfo.value.status_code == 422


async def test_team_run_skips_inactive_members() -> None:
    from alpha.bots.registry import get_bot_registry
    from alpha.groups.runner import get_group_run_service

    registry = get_bot_registry()
    registry.get_or_create("doer")
    retiring = registry.get_or_create("retiring")
    registry.update_bot("retiring", status="archived")
    assert retiring.status == "archived"

    run = get_group_run_service().start_run("crew", "Ship it", members=["doer", "retiring"])
    try:
        assert run.members == ["doer"]
        assert run.member_results["retiring"]["status"] == "skipped"
    finally:
        get_group_run_service().cancel_run(run.run_id)


async def test_team_run_rejects_all_inactive_roster() -> None:
    from alpha.bots.registry import get_bot_registry
    from alpha.groups.runner import get_group_run_service

    registry = get_bot_registry()
    registry.get_or_create("gone")
    registry.update_bot("gone", status="archived")
    with pytest.raises(ValueError, match="no active members"):
        get_group_run_service().start_run("empty-crew", "Ship it", members=["gone"])


async def test_structured_message_kinds() -> None:
    from alpha.subagents.messaging import MESSAGE_KINDS, get_agent_roster

    assert "task_handoff" in MESSAGE_KINDS and "message" in MESSAGE_KINDS
    roster = get_agent_roster("thread-kinds")
    roster.register_agent("coder", status="idle")
    sent = roster.send_message("lead_agent", "coder", "Take this", kind="task_assignment")
    assert sent["status"] == "ok"
    inbox = roster.get_inbox("coder")
    assert inbox[0].kind == "task_assignment"
    bad = roster.send_message("lead_agent", "coder", "Take this", kind="telepathy")
    assert bad["status"] == "error"


async def test_agent_messages_router_validates_kind() -> None:
    bad = agent_messages.SendMessageRequest(sender_name="lead_agent", receiver_name="all", content="hi", kind="telepathy")
    with pytest.raises(HTTPException) as excinfo:
        await agent_messages.send_agent_message.__wrapped__(thread_id="thread-kinds-2", body=bad)
    assert excinfo.value.status_code == 422

    ok = agent_messages.SendMessageRequest(sender_name="lead_agent", receiver_name="all", content="hi", kind="task_handoff")
    result = await agent_messages.send_agent_message.__wrapped__(thread_id="thread-kinds-2", body=ok)
    assert result["status"] == "ok"
