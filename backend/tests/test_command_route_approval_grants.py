from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.gateway.routers import commands as commands_router
from app.gateway.routers.commands import AutoTriggerRequest, CommandExecuteRequest, PhaseTransitionRequest


def _request(*, role: str, auth_source: str = "session") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="user-123", system_role=role), auth_source=auth_source))


@pytest.mark.asyncio
async def test_execute_route_does_not_treat_member_approval_flag_as_a_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {"status": "approval_required", "command": "/security lockdown", "data": {}})

    monkeypatch.setattr(commands_router.command_registry, "execute", execute)

    await commands_router.execute_command(
        CommandExecuteRequest(command="/security lockdown", context={"approved": True, "actor": "spoofed-admin"}),
        _request(role="member"),
    )

    assert "approved" not in captured["context"]


@pytest.mark.asyncio
async def test_execute_route_binds_admin_grant_to_authenticated_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {"status": "approval_required", "command": "/security lockdown", "data": {}})

    monkeypatch.setattr(commands_router.command_registry, "execute", execute)

    await commands_router.execute_command(
        CommandExecuteRequest(command="/security lockdown", context={"approved": True, "actor": "spoofed-admin"}),
        _request(role="admin"),
    )

    assert captured["context"]["approved"] is True
    assert captured["context"]["actor"] == "user-123"


@pytest.mark.asyncio
async def test_pat_cannot_mint_approval_grant_even_for_admin_user() -> None:
    context = await commands_router._trusted_command_context(
        _request(role="admin", auth_source="pat"),
        {"approved": True, "actor": "spoofed-admin"},
    )

    assert "approved" not in context


@pytest.mark.asyncio
async def test_phase_transition_does_not_forward_untrusted_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def trigger(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {"matched": False})

    monkeypatch.setattr(commands_router.autonomous_command_engine, "trigger_phase_transition", trigger)

    await commands_router.trigger_phase_transition(
        PhaseTransitionRequest(phase="verification", context={"approved": True}),
        _request(role="member"),
    )

    assert "approved" not in captured["context"]


@pytest.mark.asyncio
async def test_auto_trigger_does_not_forward_untrusted_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def identify_and_trigger(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {"matched": False})

    monkeypatch.setattr(commands_router.autonomous_command_engine, "identify_and_trigger", identify_and_trigger)

    await commands_router.auto_trigger_command(
        AutoTriggerRequest(prompt="lock down security", context={"approved": True}),
        _request(role="member"),
    )

    assert "approved" not in captured["context"]
