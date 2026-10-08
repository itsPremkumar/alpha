"""Unit and integration tests for BotModeMod (Autonomous Goal-Driven Controller)."""

import pytest

from alpha.mods.controllers.bot_mode_mod import BotModeMod
from alpha.mods.kernel import ModKernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome


@pytest.fixture
def kernel():
    return ModKernel()


@pytest.fixture
def bot_mode_mod():
    return BotModeMod(
        stall_threshold_steps=3,
        stall_timeout_seconds=5.0,
        heartbeat_timeout_seconds=10.0,
        max_remediation_attempts=2,
        default_max_steps=10,
    )


@pytest.mark.asyncio
async def test_bot_mode_mission_admission(kernel, bot_mode_mod):
    kernel.register_mod(bot_mode_mod)

    corr = CorrelationContext.create(run_id="run_mission_1", agent_id="coder")
    ev_start = AlphaEvent(
        name="bot.mission_started",
        payload={
            "mission_id": "msn_100",
            "bot_name": "coder",
            "prompt": "Implement user authentication module",
            "max_steps": 15,
        },
        correlation=corr,
    )

    res = await kernel.dispatch(ev_start)
    assert res.outcome == EventOutcome.CONTINUE

    # Verify mission is tracked
    mission = bot_mode_mod.get_mission("msn_100")
    assert mission is not None
    assert mission.bot_name == "coder"
    assert mission.goal == "Implement user authentication module"
    assert mission.max_steps == 15
    assert mission.status == "active"
    assert mission.current_step == 0


@pytest.mark.asyncio
async def test_bot_mode_step_progression_and_limit(kernel, bot_mode_mod):
    kernel.register_mod(bot_mode_mod)

    corr = CorrelationContext.create(run_id="run_step_test", agent_id="researcher")
    # Start mission with max 3 steps
    await kernel.dispatch(
        AlphaEvent(
            name="bot.mission_started",
            payload={"mission_id": "msn_step", "bot_name": "researcher", "max_steps": 3},
            correlation=corr,
        )
    )

    # Step 1, 2, 3 should succeed
    for step_num in range(1, 4):
        res = await kernel.dispatch(
            AlphaEvent(
                name="bot.turn_started",
                payload={"step": step_num},
                correlation=corr,
            )
        )
        assert res.outcome == EventOutcome.CONTINUE

    mission = bot_mode_mod.get_mission("msn_step")
    assert mission.current_step == 3
    assert mission.status == "active"

    # Step 4 exceeds max_steps -> should be DENIED
    res_exceeded = await kernel.dispatch(
        AlphaEvent(
            name="bot.turn_started",
            payload={"step": 4},
            correlation=corr,
        )
    )
    assert res_exceeded.outcome == EventOutcome.DENY
    assert "MISSION_STEP_LIMIT_EXCEEDED" in res_exceeded.reason
    assert mission.status == "failed"


@pytest.mark.asyncio
async def test_bot_mode_repetition_stall_detection_and_self_healing(kernel, bot_mode_mod):
    kernel.register_mod(bot_mode_mod)

    corr = CorrelationContext.create(run_id="run_stall_test", agent_id="qa_bot")
    await kernel.dispatch(
        AlphaEvent(
            name="bot.mission_started",
            payload={"mission_id": "msn_stall", "bot_name": "qa_bot", "max_steps": 20},
            correlation=corr,
        )
    )

    # Dispatch identical step turns repeatedly
    identical_payload = {
        "tool_name": "run_command",
        "tool_args": {"cmd": "pytest tests/test_auth.py"},
        "output": "ModuleNotFoundError: no module named auth",
    }

    # Step 1: initial attempt
    res1 = await kernel.dispatch(AlphaEvent(name="bot.turn_completed", payload=identical_payload, correlation=corr))
    assert res1.outcome == EventOutcome.CONTINUE

    # Step 2: repetition
    res2 = await kernel.dispatch(AlphaEvent(name="bot.turn_completed", payload=identical_payload, correlation=corr))
    assert res2.outcome == EventOutcome.CONTINUE

    # Step 3: reaches stall threshold (3 repetitions) -> triggers self-healing rewrite!
    res3 = await kernel.dispatch(AlphaEvent(name="bot.turn_completed", payload=identical_payload, correlation=corr))
    assert res3.outcome == EventOutcome.CONTINUE
    assert res3.event.payload.get("remediation_required") is True
    assert "STALL_DETECTED" in res3.event.payload.get("remediation_prompt")

    mission = bot_mode_mod.get_mission("msn_stall")
    assert mission.status == "remediating"
    assert mission.remediation_attempts == 1

    # Step 4: still repeating -> triggers 2nd remediation
    res4 = await kernel.dispatch(AlphaEvent(name="bot.turn_completed", payload=identical_payload, correlation=corr))
    assert res4.event.payload.get("remediation_required") is True
    assert mission.remediation_attempts == 2

    # Step 5: reaches max remediation attempts (2) -> ESCALATE!
    res5 = await kernel.dispatch(AlphaEvent(name="bot.turn_completed", payload=identical_payload, correlation=corr))
    assert res5.outcome == EventOutcome.ESCALATE
    assert "UNRECOVERABLE_STALL" in res5.reason
    assert mission.status == "failed"


@pytest.mark.asyncio
async def test_bot_mode_progress_resets_stall_counter(kernel, bot_mode_mod):
    kernel.register_mod(bot_mode_mod)

    corr = CorrelationContext.create(run_id="run_progress_test", agent_id="architect")
    await kernel.dispatch(
        AlphaEvent(
            name="bot.mission_started",
            payload={"mission_id": "msn_progress", "bot_name": "architect"},
            correlation=corr,
        )
    )

    # 1. Turn with step A
    await kernel.dispatch(
        AlphaEvent(
            name="bot.turn_completed",
            payload={"tool_name": "view_file", "tool_args": {"path": "a.py"}, "output": "content a"},
            correlation=corr,
        )
    )

    # 2. Turn with step B (distinct action -> progress)
    await kernel.dispatch(
        AlphaEvent(
            name="bot.turn_completed",
            payload={"tool_name": "view_file", "tool_args": {"path": "b.py"}, "output": "content b"},
            correlation=corr,
        )
    )

    mission = bot_mode_mod.get_mission("msn_progress")
    assert mission.stall_count == 0
    assert mission.status == "active"


@pytest.mark.asyncio
async def test_bot_mode_cancelled_mission_refuses_turns(kernel, bot_mode_mod):
    kernel.register_mod(bot_mode_mod)

    corr = CorrelationContext.create(run_id="run_cancel_test", agent_id="devops")
    await kernel.dispatch(
        AlphaEvent(
            name="bot.mission_started",
            payload={"mission_id": "msn_cancel", "bot_name": "devops"},
            correlation=corr,
        )
    )

    # Cancel mission
    success = bot_mode_mod.cancel_mission("msn_cancel", reason="Aborted by operator")
    assert success is True

    # Subsequent turn must be DENIED
    res = await kernel.dispatch(
        AlphaEvent(
            name="bot.turn_started",
            payload={"step": 1},
            correlation=corr,
        )
    )
    assert res.outcome == EventOutcome.DENY
    assert "CANCELLED" in res.reason


@pytest.mark.asyncio
async def test_bot_mode_estop_halts_execution(kernel, bot_mode_mod, tmp_path, monkeypatch):
    from alpha.runtime.estop import EmergencyStopManager

    mgr = EmergencyStopManager(root_dir=tmp_path)
    monkeypatch.setattr("alpha.runtime.estop.get_estop_manager", lambda root_dir=None: mgr)

    kernel.register_mod(bot_mode_mod)
    corr = CorrelationContext.create(run_id="run_estop_test")

    # When engaged -> all bot mode events refused
    mgr.engage("Safety alert")
    ev = AlphaEvent(
        name="bot.turn_started",
        payload={"step": 1},
        correlation=corr,
    )
    res = await kernel.dispatch(ev)
    assert res.outcome == EventOutcome.DENY
    assert "FLEET_ESTOP_ACTIVE" in res.reason
