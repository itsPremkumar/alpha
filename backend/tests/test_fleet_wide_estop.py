"""Prove the fleet stop is *enforced*, not merely declared.

``alpha.runtime.control`` pins the control layer's own semantics. This file pins
the enforcement across the four paths that were ungated: the autonomy loops, the
ContinuousGoalRunner, subagent dispatch, and tool execution.

The finding this encodes: three stop mechanisms existed and only one of them was
consulted anywhere. ``alpha.bots.kill_switch`` is in-memory, ``alpha.runtime.estop``
is a sentinel file whose only consumer was the RSI switchboard, and
``alpha.runtime.control`` is a generation-fenced state file. An operator who
engaged any of them saw "Fleet execution paused." while the thirteen autonomy
loops kept running and every tool call still executed.

``is_fleet_stopped()`` is the single predicate all four now ask. If someone
removes a guard, these fail. That is the point.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from alpha.runtime import control, fleet_stop


@pytest.fixture(autouse=True)
def isolated_fleet(tmp_path, monkeypatch):
    """An isolated stop switch: engaged only when a test asks for it.

    Every stop mechanism resolves through ``runtime_home()``, so pointing
    ``ALPHA_HOME`` at a tmp dir isolates the sentinel and the control state
    together. The in-memory kill switch is reset explicitly because no
    environment variable reaches it.
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))

    from alpha.bots.kill_switch import set_global_kill_switch

    set_global_kill_switch(False, "test reset")
    yield tmp_path
    set_global_kill_switch(False, "test reset")


def _engage_in_memory(reason: str = "operator stop") -> None:
    from alpha.bots.kill_switch import set_global_kill_switch

    set_global_kill_switch(True, reason)


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------


def test_a_clean_fleet_is_not_stopped():
    assert fleet_stop.is_fleet_stopped() is False
    assert fleet_stop.fleet_stop_reason() == ""


def test_the_in_memory_kill_switch_stops_the_fleet():
    """The first gap: this mechanism gated nothing but self-modification."""
    _engage_in_memory("operator hit the kill switch")
    assert fleet_stop.is_fleet_stopped() is True
    assert "kill_switch" in fleet_stop.fleet_stop_reason()
    assert "operator hit the kill switch" in fleet_stop.fleet_stop_reason()


def test_the_estop_sentinel_stops_the_fleet():
    from alpha.runtime.estop import get_estop_manager

    get_estop_manager().engage("legacy operator stop")
    assert fleet_stop.is_fleet_stopped() is True
    assert "estop_sentinel" in fleet_stop.fleet_stop_reason()


def test_pause_stops_the_fleet():
    """Pause refuses new work, and every path here is new work."""
    control.pause("maintenance window")
    assert fleet_stop.is_fleet_stopped() is True
    assert "fleet_control" in fleet_stop.fleet_stop_reason()


def test_estop_mode_stops_the_fleet():
    control.engage("incident: runaway self-update loop")
    assert fleet_stop.is_fleet_stopped() is True
    assert "fleet_control:estop" in fleet_stop.fleet_stop_reason()


def test_corrupt_control_state_stops_the_fleet_fail_closed(isolated_fleet):
    """A stop mechanism that fails open is not a stop mechanism."""
    (isolated_fleet / control.STATE_FILENAME).write_text("{corrupt", encoding="utf-8")
    assert fleet_stop.is_fleet_stopped() is True
    assert "fail_closed" in fleet_stop.fleet_stop_reason()


def test_an_unknown_control_mode_stops_the_fleet_fail_closed(isolated_fleet):
    (isolated_fleet / control.STATE_FILENAME).write_text(json.dumps({"mode": "sideways", "generation": 0}), encoding="utf-8")
    assert fleet_stop.is_fleet_stopped() is True


def test_an_unreadable_control_state_stops_the_fleet_fail_closed(monkeypatch):
    """Even an exception while reading must fail closed, never open."""

    def _boom(root_dir=None):
        raise OSError("permission denied")

    monkeypatch.setattr(control, "read_state", _boom)
    assert fleet_stop.is_fleet_stopped() is True


def test_the_predicate_never_raises(monkeypatch):
    """An exception here would propagate into every gate that consults it."""

    def _boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(fleet_stop, "_evaluate", _boom)
    assert fleet_stop.is_fleet_stopped() is True
    assert "unreadable" in fleet_stop.fleet_stop_reason()


def test_clearing_the_stop_resumes_the_fleet():
    control.engage("incident")
    assert fleet_stop.is_fleet_stopped() is True
    control.clear()
    assert fleet_stop.is_fleet_stopped() is False


# ---------------------------------------------------------------------------
# 1. The autonomy loops
# ---------------------------------------------------------------------------


def test_every_registered_autonomy_loop_passes_through_the_gate():
    """The gate is in ``_tick_once``, the single choke point for every loop.

    Asserted against the real registry rather than a hand-written list, because
    a hand-written list is a claim nothing checks: the comment in
    ``_tick_once`` once said "eight" while nine loops were registered.
    """
    from app.gateway.autonomy.supervisor import AutonomySupervisor

    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()
    registered = set(supervisor._specs)
    assert len(registered) == 13, f"expected the 13 registered autonomy loops, found {len(registered)}"

    from app.gateway.autonomy import supervisor as supervisor_module

    # `_tick_once` is the only caller of the gate, and it is reached only from
    # `_run_loop`, which `start()` schedules for every registered loop.
    source = inspect_getsourcelines(supervisor_module.AutonomySupervisor._tick_once)
    assert "_fleet_admits_tick" in source, "the fleet gate is no longer consulted by _tick_once"


def inspect_getsourcelines(fn) -> str:
    import inspect

    return "".join(inspect.getsource(fn))


@pytest.mark.parametrize(
    "engage",
    [
        lambda: _engage_in_memory("kill switch"),
        lambda: control.engage("control estop"),
        lambda: control.pause("control pause"),
        lambda: __import__("alpha.runtime.estop", fromlist=["get_estop_manager"]).get_estop_manager().engage("sentinel"),
    ],
    ids=["in_memory_kill_switch", "runtime_control_estop", "runtime_control_pause", "estop_sentinel"],
)
def test_each_stop_mechanism_refuses_an_autonomy_tick(engage):
    from app.gateway.autonomy.supervisor import _fleet_admits_tick

    engage()
    assert _fleet_admits_tick("self_update") is False


def test_the_in_memory_kill_switch_alone_refuses_an_autonomy_tick():
    """The gap: `_fleet_admits_tick` consulted the sentinel and control state
    but not the in-memory kill switch, so an operator using that one control
    saw the loops keep running."""
    from app.gateway.autonomy.supervisor import _fleet_admits_tick

    _engage_in_memory("operator hit the kill switch")
    assert _fleet_admits_tick("apex") is False
    assert _fleet_admits_tick("company_operations") is False


def test_a_loop_runs_normally_under_run_mode():
    from app.gateway.autonomy.supervisor import _fleet_admits_tick

    assert _fleet_admits_tick("self_update") is True


# ---------------------------------------------------------------------------
# 2. ContinuousGoalRunner
# ---------------------------------------------------------------------------


def _runner(tmp_path):
    from alpha.harness.continuous.runner import ContinuousGoalRunner
    from alpha.harness.continuous.store import GoalStore
    from alpha.trajectory.store import TrajectoryStore

    runner = ContinuousGoalRunner(store=GoalStore(storage_path=tmp_path / "goals.json"))
    runner.trajectory = TrajectoryStore(db_path=tmp_path / "audit.db")
    return runner


def test_a_stopped_fleet_refuses_to_start_a_goal(tmp_path):
    from alpha.runtime.control import FleetStopped

    runner = _runner(tmp_path)
    control.engage("incident")
    with pytest.raises(FleetStopped):
        runner.start_goal(title="Deploy cluster", description="do things")


def test_starting_a_goal_is_allowed_once_the_stop_clears(tmp_path):
    runner = _runner(tmp_path)
    control.engage("incident")
    with pytest.raises(control.FleetStopped):
        runner.start_goal(title="Deploy cluster")
    control.clear()
    assert runner.start_goal(title="Deploy cluster").status == "planning"


def test_a_stopped_fleet_refuses_a_goal_step_and_runs_no_executor(tmp_path):
    """The realistic incident: a goal mid-flight when the operator stops it."""
    runner = _runner(tmp_path)
    goal = runner.start_goal(title="Deploy cluster")

    executed: list[str] = []

    def executor(milestone):
        executed.append(milestone.title)
        return True, "done", ""

    control.engage("operator stopped the fleet mid-goal")
    result = runner.step(goal.goal_id, executor_fn=executor)

    assert result["status"] == "fleet_stopped"
    assert executed == [], "the executor performed an effect while the fleet was stopped"
    assert result["reason"]


def test_a_goal_step_resumes_after_the_stop_clears(tmp_path):
    runner = _runner(tmp_path)
    goal = runner.start_goal(title="Deploy cluster")

    def executor(milestone):
        return True, "done", ""

    control.engage("incident")
    assert runner.step(goal.goal_id, executor_fn=executor)["status"] == "fleet_stopped"
    control.clear()
    assert runner.step(goal.goal_id, executor_fn=executor)["status"] == "ok"


def test_run_until_complete_stops_iterating_while_stopped(tmp_path):
    """Without a terminal refusal status the loop would spin its whole budget
    doing nothing while the fleet was stopped."""
    runner = _runner(tmp_path)
    goal = runner.start_goal(title="Deploy cluster")

    calls: list[int] = []

    def executor(milestone):
        calls.append(1)
        return True, "done", ""

    control.engage("incident")
    runner.run_until_complete(goal.goal_id, max_steps=20, executor_fn=executor)
    assert calls == []


def test_each_stop_mechanism_refuses_a_goal_step(tmp_path):
    runner = _runner(tmp_path)
    goal = runner.start_goal(title="Deploy cluster")

    def executor(milestone):
        return True, "done", ""

    _engage_in_memory("kill switch")
    assert runner.step(goal.goal_id, executor_fn=executor)["status"] == "fleet_stopped"


# ---------------------------------------------------------------------------
# 3. Subagent dispatch
# ---------------------------------------------------------------------------


def _runtime_stub():
    return SimpleNamespace(
        config={"metadata": {}, "configurable": {}},
        context={},
        state={},
    )


@pytest.mark.parametrize(
    "engage",
    [
        lambda: _engage_in_memory("kill switch"),
        lambda: control.engage("control estop"),
        lambda: __import__("alpha.runtime.estop", fromlist=["get_estop_manager"]).get_estop_manager().engage("sentinel"),
    ],
    ids=["in_memory_kill_switch", "runtime_control_estop", "estop_sentinel"],
)
def test_a_stopped_fleet_refuses_subagent_dispatch(engage):
    from alpha.tools.builtins.task_tool import task_tool

    engage()
    result = asyncio.run(
        task_tool.coroutine(
            runtime=_runtime_stub(),
            prompt="do work",
            subagent_type="general-purpose",
            tool_call_id="call-1",
        )
    )
    assert "fleet" in str(result).lower() or "stopped" in str(result).lower()
    assert "FLEET_STOPPED" in str(result)


def test_the_in_memory_kill_switch_refuses_subagent_dispatch():
    """The gap: dispatch consulted no stop mechanism at all."""
    from alpha.tools.builtins.task_tool import task_tool

    _engage_in_memory("operator hit the kill switch")
    result = asyncio.run(
        task_tool.coroutine(
            runtime=_runtime_stub(),
            prompt="do work",
            subagent_type="general-purpose",
            tool_call_id="call-2",
        )
    )
    assert "FLEET_STOPPED" in str(result)


# ---------------------------------------------------------------------------
# 4. Tool execution
# ---------------------------------------------------------------------------


def _tool_request(tool_name: str = "bash"):
    return SimpleNamespace(
        tool_call={"id": "tc-1", "name": tool_name, "args": {}},
        runtime=None,
        state={},
    )


def _handler_spy():
    calls: list[object] = []

    def handler(request):
        calls.append(request)
        return "executed"

    return calls, handler


def test_a_stopped_fleet_refuses_every_tool_call_synchronously():
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware

    middleware = FleetStopMiddleware()
    control.engage("incident")
    calls, handler = _handler_spy()

    result = middleware.wrap_tool_call(_tool_request(), handler)

    assert calls == [], "a tool executed while the fleet was stopped"
    assert getattr(result, "status", None) == "error"
    assert "FLEET_STOPPED" in str(getattr(result, "content", ""))


def test_a_stopped_fleet_refuses_every_tool_call_asynchronously():
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware

    middleware = FleetStopMiddleware()
    control.engage("incident")
    calls: list[object] = []

    async def handler(request):
        calls.append(request)
        return "executed"

    result = asyncio.run(middleware.awrap_tool_call(_tool_request(), handler))
    assert calls == [], "a tool executed while the fleet was stopped"
    assert getattr(result, "status", None) == "error"


def test_the_in_memory_kill_switch_refuses_tool_execution():
    """The gap: nothing between the model and the tools consulted any stop."""
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware

    middleware = FleetStopMiddleware()
    _engage_in_memory("operator hit the kill switch")
    calls, handler = _handler_spy()
    result = middleware.wrap_tool_call(_tool_request(), handler)
    assert calls == []
    assert getattr(result, "status", None) == "error"


def test_tools_execute_normally_under_run_mode():
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware

    middleware = FleetStopMiddleware()
    calls, handler = _handler_spy()
    result = middleware.wrap_tool_call(_tool_request(), handler)
    assert calls and result == "executed"


def _app_config():
    """A minimal AppConfig, matching the pattern in the middleware-chain tests."""
    from alpha.config.app_config import AppConfig, CircuitBreakerConfig
    from alpha.config.guardrails_config import GuardrailsConfig
    from alpha.config.model_config import ModelConfig
    from alpha.config.sandbox_config import SandboxConfig

    return AppConfig(
        models=[ModelConfig(name="test-model", display_name="test-model", description=None, use="langchain_openai:ChatOpenAI", model="test-model")],
        sandbox=SandboxConfig(use="test"),
        guardrails=GuardrailsConfig(enabled=False),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=7, recovery_timeout_sec=11),
    )


def _lead_chain():
    from alpha.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares

    return build_lead_runtime_middlewares(app_config=_app_config())


def _subagent_chain():
    from alpha.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares

    return build_subagent_runtime_middlewares(app_config=_app_config())


def test_the_tool_gate_is_registered_on_the_lead_runtime_chain():
    """A middleware that is never appended is not a gate."""
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware

    middlewares = _lead_chain()
    assert any(isinstance(m, FleetStopMiddleware) for m in middlewares)


def test_the_tool_gate_is_registered_on_the_subagent_runtime_chain():
    """A delegated child must inherit the stop, or delegation is a way out."""
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware

    middlewares = _subagent_chain()
    assert any(isinstance(m, FleetStopMiddleware) for m in middlewares)


def test_the_tool_gate_sits_inside_the_receipt_layer():
    """ToolReceiptMiddleware must wrap every short-circuiter or the ledger gaps."""
    from alpha.agents.middlewares.fleet_stop_middleware import FleetStopMiddleware
    from alpha.agents.middlewares.tool_receipt_middleware import ToolReceiptMiddleware

    middlewares = _lead_chain()
    receipt_idx = next(i for i, m in enumerate(middlewares) if isinstance(m, ToolReceiptMiddleware))
    stop_idx = next(i for i, m in enumerate(middlewares) if isinstance(m, FleetStopMiddleware))
    assert receipt_idx < stop_idx
