"""Unit tests for DeepDebuggerAgent."""

from agent_workspace.subagents.builtins.deep_debugger_agent import (
    DEEP_DEBUGGER_AGENT_CONFIG,
    DeepDebuggerAgent,
)


class TestDeepDebuggerAgent:
    def test_config(self):
        assert DEEP_DEBUGGER_AGENT_CONFIG.name == "deep-debugger"

    def test_fault_localization_ranking(self):
        agent = DeepDebuggerAgent()
        ranked = agent.localize_fault(
            "TypeError: None has no attribute value",
            ["value = None", "result = compute(value)", "print('ok')"],
        )
        assert len(ranked) == 3
        assert ranked[0]["suspiciousness"] >= ranked[-1]["suspiciousness"]

    def test_patch_synthesis(self):
        agent = DeepDebuggerAgent()
        patch = agent.synthesize_patch("value = None", "value = default()")
        assert "---" in patch
        assert "+++" in patch

    def test_diagnose_returns_contract(self):
        agent = DeepDebuggerAgent()
        contract = agent.diagnose("ValueError: bad input", ["x = parse(raw)"], session_id="s-debug")
        assert contract.is_success()
        assert contract.unified_diff
        assert contract.session_id == "s-debug"
