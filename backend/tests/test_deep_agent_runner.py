"""Tests for the production deep-agent execution seam.

Two jobs:

1. **Honesty.** Every field of a returned contract must come from something the
   run actually produced. `hierarchical_delegator`'s docstring refuses to
   synthesise a success status, invented oracles, stamps or token counts, and
   this runner inherits that obligation.
2. **Wiring.** `set_deep_agent_runner` had no production caller - only
   `test_hierarchical_delegator.py` registered one - so hierarchical delegation
   was built, tested and unreachable. `TestProductionWiring` is the pin that
   fails if the composition root ever stops registering a runner again.

No model calls. The executor is substituted; what is under test is the
projection and the wiring, not the agent loop.
"""

from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

from alpha.subagents.deep_handoff_contract import DeepExecutionStatus, DeepTaskSpec
from alpha.subagents.deep_runner import (
    DEEP_AGENT_TO_SUBAGENT,
    DeepAgentSubagentRunner,
    _clip_words,
    _measured_tokens,
    _observed_oracles,
)
from alpha.subagents.hierarchical_delegator import (
    DeepAgentSession,
    HierarchicalDelegationEngine,
    get_deep_agent_runner,
    set_deep_agent_runner,
)

#: The backend root, for the source pins that read application wiring.
BACKEND = Path(__file__).resolve().parents[1]


class _FakeStatus:
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class _Result:
    """Stands in for the executor's result object."""

    def __init__(self, status, result="", error="", stop_reason=None, tokens=None, bash=None):
        self.status = status
        self.result = result
        self.error = error
        self.stop_reason = stop_reason
        self.token_usage_records = tokens
        self.bash_executions = bash


def _session(agent_type: str = "debugger", **spec_kwargs) -> DeepAgentSession:
    return DeepAgentSession(
        session_id="sess-1",
        agent_type=agent_type,
        spec=DeepTaskSpec(goal="Fix the crash", agent_type=agent_type, **spec_kwargs),
        workspace_dir="/tmp/ws",
    )


@contextlib.contextmanager
def _patched_run(result: _Result, worker_name: str = "deep-debugger"):
    """Substitute the executor and the shipped-specialist catalog for one call."""

    fake_executor = mock.MagicMock()
    fake_executor.execute.return_value = result
    executor_module = mock.MagicMock()
    executor_module.SubagentExecutor.return_value = fake_executor
    executor_module.SubagentStatus = _FakeStatus

    base = mock.MagicMock()
    base.system_prompt = "sys"
    base.tools = None
    base.inherit_all = True
    # Mirrors the real catalog deny-list: a hierarchical child must not nest.
    base.disallowed_tools = ["task", "ralph_loop", "session_search"]
    base.skills = None
    base.max_turns = 150
    base.timeout_seconds = 900
    builtins_module = mock.MagicMock()
    builtins_module.BUILTIN_SUBAGENTS = {worker_name: base}

    with mock.patch.dict(
        sys.modules,
        {"alpha.subagents.executor": executor_module, "alpha.subagents.builtins": builtins_module},
    ):
        yield fake_executor


class TestMapping(unittest.TestCase):
    def test_every_deep_type_maps_to_a_shipped_specialist(self):
        # Each maps onto a real `deep-*` catalog entry; a type with no worker is
        # refused by name rather than downgraded to general-purpose.
        expected = {
            "architect",
            "debugger",
            "security",
            "test_synthesizer",
            "performance",
            "code_reviewer",
        }
        self.assertEqual(set(DEEP_AGENT_TO_SUBAGENT), expected)
        for target in DEEP_AGENT_TO_SUBAGENT.values():
            self.assertTrue(target.startswith("deep-"), target)


class TestHonesty(unittest.TestCase):
    def test_unknown_agent_type_refuses_by_name(self):
        runner = DeepAgentSubagentRunner(tools=[object()])
        contract = runner(_session(agent_type="quantum"))
        self.assertIs(contract.status, DeepExecutionStatus.UNRECOVERABLE_ERROR)
        self.assertIn("quantum", contract.error_detail)

    def test_empty_tool_pool_refuses_rather_than_running_an_instrumentless_child(self):
        runner = DeepAgentSubagentRunner(tools=[])
        contract = runner(_session())
        self.assertIs(contract.status, DeepExecutionStatus.UNRECOVERABLE_ERROR)
        self.assertIn("tool pool", contract.error_detail)

    def test_success_carries_only_real_text_and_never_invents_a_patch(self):
        result = _Result(_FakeStatus.COMPLETED, result="Found the bug and fixed it.")
        runner = DeepAgentSubagentRunner(tools=[object()])
        with _patched_run(result):
            contract = runner(_session())
        self.assertIs(contract.status, DeepExecutionStatus.SUCCESS)
        self.assertIn("fixed it", contract.executive_summary)
        # Nothing was measured for these, so nothing is claimed.
        self.assertEqual(contract.unified_diff, "")
        self.assertEqual(contract.security_stamps, [])
        self.assertEqual(contract.invariant_assertions, [])
        self.assertEqual(contract.artifacts, [])

    def test_a_capped_completion_is_not_reported_as_success(self):
        result = _Result(_FakeStatus.COMPLETED, result="partial work", stop_reason="token_capped")
        runner = DeepAgentSubagentRunner(tools=[object()])
        with _patched_run(result):
            contract = runner(_session())
        self.assertIs(contract.status, DeepExecutionStatus.PARTIAL_PROGRESS)
        self.assertIn("token_capped", contract.error_detail)

    def test_an_empty_completion_is_partial_never_success(self):
        result = _Result(_FakeStatus.COMPLETED, result="")
        runner = DeepAgentSubagentRunner(tools=[object()])
        with _patched_run(result):
            contract = runner(_session())
        self.assertIs(contract.status, DeepExecutionStatus.PARTIAL_PROGRESS)

    def test_a_failed_run_reports_the_real_error(self):
        result = _Result(_FakeStatus.FAILED, error="model backend unavailable")
        runner = DeepAgentSubagentRunner(tools=[object()])
        with _patched_run(result):
            contract = runner(_session())
        self.assertIs(contract.status, DeepExecutionStatus.UNRECOVERABLE_ERROR)
        self.assertIn("model backend unavailable", contract.error_detail)

    def test_an_executor_exception_becomes_a_contract_not_an_escape(self):
        runner = DeepAgentSubagentRunner(tools=[object()])
        executor_module = mock.MagicMock()
        # The constructor itself raising is the shape a real assembly failure
        # takes, so the runner must survive it rather than propagate.
        executor_module.SubagentExecutor.side_effect = RuntimeError("boom")
        executor_module.SubagentStatus = _FakeStatus
        base = mock.MagicMock()
        base.system_prompt = "sys"
        base.tools = None
        base.inherit_all = True
        base.disallowed_tools = ["task"]
        base.skills = None
        base.max_turns = 150
        base.timeout_seconds = 900
        builtins_module = mock.MagicMock()
        builtins_module.BUILTIN_SUBAGENTS = {"deep-debugger": base}
        with mock.patch.dict(
            sys.modules,
            {"alpha.subagents.executor": executor_module, "alpha.subagents.builtins": builtins_module},
        ):
            contract = runner(_session())
        self.assertIs(contract.status, DeepExecutionStatus.UNRECOVERABLE_ERROR)
        self.assertIn("boom", contract.error_detail)

    def test_a_lazy_tool_pool_is_resolved_on_first_use(self):
        # The Gateway cannot assemble tools at startup, so the pool is resolved
        # per child. An empty resolution refuses; it is never cached, so a pool
        # that failed during a startup race can recover on the next child.
        calls: list[int] = []

        def supplier():
            calls.append(1)
            return [] if len(calls) == 1 else [object()]

        runner = DeepAgentSubagentRunner(tools_supplier=supplier)
        first = runner(_session())
        self.assertIs(first.status, DeepExecutionStatus.UNRECOVERABLE_ERROR)
        self.assertIn("tool pool", first.error_detail)
        with _patched_run(_Result(_FakeStatus.COMPLETED, result="ok")):
            second = runner(_session())
        self.assertIs(second.status, DeepExecutionStatus.SUCCESS)

    def test_a_successful_tool_pool_is_memoised(self):
        calls: list[int] = []

        def supplier():
            calls.append(1)
            return [object()]

        runner = DeepAgentSubagentRunner(tools_supplier=supplier)
        with _patched_run(_Result(_FakeStatus.COMPLETED, result="ok")):
            runner(_session())
            runner(_session())
        # Tool assembly is not free; the second child reuses the first pool.
        self.assertEqual(len(calls), 1)

    def test_tokens_come_from_measured_records_only(self):
        self.assertEqual(_measured_tokens(_Result(None, tokens=[{"input_tokens": 10, "output_tokens": 5}])), 15)
        # Absent usage stays 0; it is never estimated upward.
        self.assertEqual(_measured_tokens(_Result(None)), 0)
        self.assertEqual(_measured_tokens(_Result(None, tokens=[{"model": "x"}])), 0)

    def test_oracles_exist_only_for_commands_that_ran(self):
        oracles = _observed_oracles(_Result(None, bash=[{"command": "pytest -q", "status": "success", "status_marker": "ok"}]))
        self.assertEqual(len(oracles), 1)
        self.assertEqual(oracles[0]["status"], "passed")
        # No executions -> no oracle rows at all.
        self.assertEqual(_observed_oracles(_Result(None)), [])
        # An entry with no command cannot be evidence.
        self.assertEqual(_observed_oracles(_Result(None, bash=[{"status": "success"}])), [])

    def test_a_non_success_command_never_claims_passed(self):
        oracles = _observed_oracles(_Result(None, bash=[{"command": "pytest", "status": "failed"}]))
        self.assertEqual(oracles[0]["status"], "failed")

    def test_the_summary_is_clipped_to_the_contract_cap(self):
        long_text = " ".join(["word"] * 500)
        clipped = _clip_words(long_text, 300)
        self.assertEqual(len(clipped.split()), 300)


class TestProductionWiring(unittest.TestCase):
    """The runner must be registered by the composition root, not only by tests."""

    def test_gateway_startup_registers_a_deep_agent_runner(self):
        source = (BACKEND / "app" / "gateway" / "app.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"DeepAgentSubagentRunner|install_deep_agent_runner|set_deep_agent_runner",
            "Gateway startup must register the deep agent runner; an unregistered seam means hierarchical delegation reports UNRECOVERABLE_ERROR forever.",
        )

    def test_the_registration_is_not_only_in_tests(self):
        # Guard the original defect directly: no production module may be the
        # only caller of the seam.
        offenders = []
        for path in (BACKEND / "packages" / "harness").rglob("*.py"):
            if path.name.startswith("test_"):
                continue
            if "set_deep_agent_runner" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(path.name)
        # `deep_runner` exposes the installer; it is the allowed production owner.
        self.assertTrue(
            set(offenders) <= {"deep_runner.py", "hierarchical_delegator.py"},
            f"unexpected production callers: {offenders}",
        )


class TestEngineIntegration(unittest.TestCase):
    def test_registering_the_runner_makes_delegate_return_a_real_contract(self):
        engine = HierarchicalDelegationEngine()
        result = _Result(_FakeStatus.COMPLETED, result="real output", tokens=[{"input_tokens": 7, "output_tokens": 3}])
        runner = DeepAgentSubagentRunner(tools=[object()])
        previous = set_deep_agent_runner(runner)
        try:
            self.assertIs(get_deep_agent_runner(), runner)
            with _patched_run(result):
                contract = engine.delegate(agent_type="debugger", task_description="Fix crash")
            self.assertIs(contract.status, DeepExecutionStatus.SUCCESS)
            self.assertEqual(contract.tokens_consumed, 10)
        finally:
            set_deep_agent_runner(previous)

    def test_offline_mode_still_refuses_honestly(self):
        # Restoring `None` must put the engine back in honest-failure mode.
        engine = HierarchicalDelegationEngine()
        previous = set_deep_agent_runner(None)
        try:
            contract = engine.delegate(agent_type="debugger", task_description="Fix crash")
            self.assertIs(contract.status, DeepExecutionStatus.UNRECOVERABLE_ERROR)
        finally:
            set_deep_agent_runner(previous)


if __name__ == "__main__":
    unittest.main()
