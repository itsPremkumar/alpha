"""Unit tests for TaskRouterMod (Autonomous Capability Matching and Routing)."""

import pytest

from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry
from alpha.mods.controllers.task_router_mod import TaskRouterMod
from alpha.mods.kernel import ModKernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome


@pytest.fixture
def test_registry(tmp_path):
    roster_path = tmp_path / "roster.json"
    # BotRegistry always installs Alpha's canonical starter roster. Reuse its
    # coder/architect and add only isolated test-specific profiles.
    reg = BotRegistry(storage_path=roster_path)
    reg.register(
        BotProfile(
            name="retired_bot",
            display_name="Retired Bot",
            role="Old Specialist",
            soul="Test profile for archived bot routing.",
            department="engineering",
            capabilities=["legacy"],
            status="archived",
        )
    )
    reg.register(
        BotProfile(
            name="sleeper_bot",
            display_name="Sleeping Bot",
            role="Specialist",
            soul="Test profile for sleeping bot routing.",
            department="engineering",
            capabilities=["special"],
            status="sleeping",
        )
    )
    return reg


@pytest.fixture
def router_mod(test_registry):
    return TaskRouterMod(registry=test_registry)


@pytest.fixture
def kernel(router_mod):
    k = ModKernel()
    k.register_mod(router_mod)
    return k


@pytest.mark.asyncio
async def test_task_router_answers_direct_routing_query(kernel):
    ev = AlphaEvent(
        name="task.routed",
        payload={
            "task_type": "coding",
            "prompt": "Implement a Python function",
        },
        correlation=CorrelationContext.create(),
    )

    res = await kernel.dispatch(ev)
    assert res.outcome == EventOutcome.ANSWER
    assert res.response_payload is not None
    assert res.response_payload["selected_bot"] == "coder"
    assert "coder" in res.reason


@pytest.mark.asyncio
async def test_task_router_never_falls_back_when_no_bot_meets_required_tags(kernel, monkeypatch):
    import alpha.mods.controllers.task_router_mod as task_router_module

    monkeypatch.setattr(task_router_module, "infer_capability_tags", lambda _text: frozenset({"unavailable_skill"}))
    ev = AlphaEvent(
        name="run.admit",
        payload={"prompt": "Perform a specialized task"},
        correlation=CorrelationContext.create(),
    )

    res = await kernel.dispatch(ev)

    assert res.outcome == EventOutcome.DENY
    assert "NO_ELIGIBLE_BOT" in res.reason


@pytest.mark.asyncio
async def test_task_router_rewrites_unassigned_admission(kernel):
    ev = AlphaEvent(
        name="run.admit",
        payload={
            "prompt": "Design system architecture boundary",
        },
        correlation=CorrelationContext.create(),
    )

    res = await kernel.dispatch(ev)
    # Must be rewritten to assign architect!
    assert res.outcome == EventOutcome.CONTINUE
    assert res.event.payload.get("assigned_bot") == "architect"
    assert res.event.payload.get("bot_name") == "architect"
    assert res.event.payload.get("model_category") == "ultrabrain"


@pytest.mark.asyncio
async def test_task_router_denies_archived_or_suspended_bot(kernel):
    ev = AlphaEvent(
        name="run.admit",
        payload={
            "bot_name": "retired_bot",
            "prompt": "Do some work",
        },
        correlation=CorrelationContext.create(),
    )

    res = await kernel.dispatch(ev)
    assert res.outcome == EventOutcome.DENY
    assert "BOT_UNAVAILABLE" in res.reason
    assert "archived" in res.reason


@pytest.mark.asyncio
async def test_task_router_wakes_sleeping_bot(kernel, test_registry):
    sleeper = test_registry.get_bot("sleeper_bot")
    assert sleeper.status == "sleeping"

    ev = AlphaEvent(
        name="run.admit",
        payload={
            "bot_name": "sleeper_bot",
            "prompt": "Urgent task",
        },
        correlation=CorrelationContext.create(),
    )

    res = await kernel.dispatch(ev)
    assert res.outcome == EventOutcome.CONTINUE

    # Bot should now be active
    sleeper_updated = test_registry.get_bot("sleeper_bot")
    assert sleeper_updated.status == "active"


@pytest.mark.asyncio
async def test_task_router_records_history(kernel, router_mod):
    ev = AlphaEvent(
        name="task.routed",
        payload={"prompt": "Write documentation for API"},
        correlation=CorrelationContext.create(),
    )
    await kernel.dispatch(ev)

    history = router_mod.get_routing_history()
    assert len(history) >= 1
    assert history[-1]["task_type"] == "writing"
