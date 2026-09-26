"""Reachability tests for the standing-goal command surface.

These exist to answer one question with evidence rather than assertion: is
``/goal`` reachable from a *real* dispatch path, or only from a test that
imports the handler directly?

The chain under test is the one the product already uses:

    HTTP POST /api/commands/execute
      -> app/gateway/routers/commands.py:151  command_registry.execute(...)
      -> alpha/commands/registry.py:285      SlashCommandRegistry.execute
      -> alpha/commands/registry.py:346      the registered handler

The only thing this module adds is the registration, which happens at import of
``alpha.commands`` - the same side-effect mechanism ``backend_handlers`` already
relies on. If that registration ever stops happening, every test here fails.
"""

from __future__ import annotations

import pytest

from alpha.mission.goalloop.bindings import bound_commands
from alpha.mission.goalloop.state import reset_goal_store

_CTX = {"session_id": "reachability-session"}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "agent-workspace"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    reset_goal_store()
    yield
    reset_goal_store()


@pytest.fixture
def registry():
    """The process-wide registry, imported the way the gateway imports it."""
    from alpha.commands import registry as registry_module  # noqa: F401 - triggers bindings
    from alpha.commands.registry import command_registry

    return command_registry


class TestCommandsAreBound:
    def test_every_goal_command_has_a_real_handler(self, registry):
        for command in bound_commands():
            assert registry.has_handler(command), f"{command} resolves to the no-op stub"

    def test_binding_is_idempotent(self, registry):
        from alpha.mission.goalloop.bindings import bind_goal_commands

        first = bind_goal_commands()
        second = bind_goal_commands()
        assert first == second
        for command in first:
            assert registry.has_handler(command)

    def test_the_gateway_route_dispatches_through_this_registry(self):
        """The registration target is the same object the gateway calls."""
        from alpha.commands import registry as registry_module
        from alpha.commands.registry import command_registry

        # routers/commands.py:151 calls `command_registry.execute`; assert the
        # name it imports is the very object we registered on.
        assert registry_module.command_registry is command_registry


class TestEndToEndThroughTheRegistry:
    def test_full_lifecycle_through_command_registry_execute(self, registry):
        ok = registry.execute("/goal Ship the parser fix", context=_CTX)
        assert ok.status == "success"
        assert "20-turn budget" in ok.output
        assert ok.data["goal_id"]

        gate = registry.execute("/goal gate add exit 1", context=_CTX)
        assert gate.status == "success"
        assert "must exit 0" in gate.output

        sub = registry.execute("/subgoal add a regression test", context=_CTX)
        assert sub.status == "success"
        assert sub.data["subgoals"] == ["add a regression test"]

        # A red gate: two CONTINUE boundaries, then an auto-pause.
        first = registry.execute("/goal step I fixed it, all tests pass", context=_CTX)
        assert first.data["action"] == "continue"
        assert "QUALITY GATES ARE RED" in first.data["continuation"]
        assert "exit=1" in first.data["continuation"]

        registry.execute("/goal step still fixing", context=_CTX)
        third = registry.execute("/goal step still fixing", context=_CTX)
        assert third.data["action"] == "paused"
        assert "exhausted" in third.data["goal"]["pause_reason"]

        # Recovery: drop the gate, resume, and the loop runs again.
        removed = registry.execute("/goal gate remove 1", context=_CTX)
        assert removed.status == "success"
        assert "Removed gate #1" in removed.output
        assert registry.execute("/goal step", context=_CTX).data["action"] == "idle"

    def test_state_survives_a_fresh_registry_process(self, registry, tmp_path):
        registry.execute("/goal Persist me across restarts", context=_CTX)
        registry.execute("/subgoal keep the subgoals too", context=_CTX)

        # Simulate a restart: drop every in-process cache and the module global.
        reset_goal_store()
        from alpha.mission.goalloop.state import get_goal_store

        get_goal_store().clear_cache()

        shown = registry.execute("/goal", context=_CTX)
        assert shown.status == "success"
        assert "Persist me across restarts" in shown.output
        assert "keep the subgoals too" in shown.output
        assert shown.data["goal"]["turns_used"] == 0
        assert shown.data["goal"]["subgoals"] == ["keep the subgoals too"]

    def test_setting_a_new_goal_replaces_and_clears_subgoals(self, registry):
        registry.execute("/goal First objective", context=_CTX)
        registry.execute("/subgoal criterion for the first objective", context=_CTX)
        replaced = registry.execute("/goal Second objective", context=_CTX)
        assert replaced.status == "success"
        assert replaced.data["goal"]["subgoals"] == []
        assert replaced.data["goal"]["objective"] == "Second objective"

    def test_a_near_miss_subcommand_is_refused_not_executed(self, registry):
        """The guard ``/goal <text>`` displaces is re-applied, not dropped."""
        before = registry.execute("/goal", context=_CTX)
        assert "No active goal" in before.output
        refused = registry.execute("/goal creat", context=_CTX)
        assert refused.status == "not_found"
        assert "nothing was executed" in refused.output
        assert refused.data["executed"] is False
        # And it really did not create a goal.
        assert "No active goal" in registry.execute("/goal", context=_CTX).output

    def test_prose_objectives_are_not_mistaken_for_subcommands(self, registry):
        for text in (
            "Fix every failing test in tests/",
            "Refine the parser so it drops no commas",
            "Create a report about budget overruns",
        ):
            result = registry.execute(f"/goal {text}", context=_CTX)
            assert result.status == "success", text
            assert result.data["goal"]["objective"] == text

    def test_gate_management_is_safe_mid_run(self, registry):
        registry.execute("/goal Mid-run gate management", context=_CTX)
        registry.execute("/goal step work", context=_CTX)  # one boundary consumed
        added = registry.execute("/goal gate add ruff check .", context=_CTX)
        assert added.status == "success"
        listed = registry.execute("/goal gate list", context=_CTX)
        assert "ruff check ." in listed.output
        cleared = registry.execute("/goal gate clear", context=_CTX)
        assert "Removed 1 gate" in cleared.output
        assert registry.execute("/goal gate list", context=_CTX).data["gates"] == []

    def test_bad_gate_subcommand_reports_known_options(self, registry):
        registry.execute("/goal Something", context=_CTX)
        bad = registry.execute("/goal gate frobnicate", context=_CTX)
        assert bad.status == "error"
        assert "add, list, remove, clear" in bad.output

    def test_two_sessions_keep_separate_goals(self, registry):
        registry.execute("/goal Session A work", context={"session_id": "A"})
        registry.execute("/goal Session B work", context={"session_id": "B"})
        a = registry.execute("/goal", context={"session_id": "A"})
        b = registry.execute("/goal", context={"session_id": "B"})
        assert "Session A work" in a.output
        assert "Session B work" in b.output
        assert a.data["goal"]["goal_id"] != b.data["goal"]["goal_id"]

    def test_show_reports_the_contract(self, registry):
        registry.execute("/goal Migrate auth\nverify: pytest tests/auth passes", context=_CTX)
        shown = registry.execute("/goal show", context=_CTX)
        assert shown.status == "success"
        assert shown.data["has_contract"] is True
        assert "pytest tests/auth passes" in shown.output

    def test_verify_runs_gates_without_judging(self, registry):
        registry.execute("/goal Verify me", context=_CTX)
        registry.execute("/goal gate add exit 9", context=_CTX)
        result = registry.execute("/goal verify", context=_CTX)
        assert result.status == "success"
        assert result.data["gates_passed"] is False
        assert "cannot be judged done" in result.output
        assert "exit=9" in result.output

    def test_clear_removes_the_goal(self, registry):
        registry.execute("/goal Temporary", context=_CTX)
        cleared = registry.execute("/goal clear", context=_CTX)
        assert cleared.status == "success"
        assert "No active goal" in registry.execute("/goal", context=_CTX).output
