"""Unit tests for the hierarchical delegation engine."""

from agent_workspace.subagents.deep_handoff_contract import DeepExecutionStatus
from agent_workspace.subagents.hierarchical_delegator import (
    HierarchicalDelegationEngine,
    get_delegation_engine,
    normalize_agent_type,
)


class TestNormalizeAgentType:
    def test_known_types(self):
        assert normalize_agent_type("architect") == "architect"
        assert normalize_agent_type("Deep-Debugger") == "debugger"
        assert normalize_agent_type("code_review") == "code_reviewer"

    def test_unknown_type_rejected(self):
        try:
            normalize_agent_type("unknown-agent")
        except ValueError:
            return
        raise AssertionError("unknown agent type should raise ValueError")


class TestHierarchicalDelegationEngine:
    def test_delegate_returns_compact_contract(self):
        engine = HierarchicalDelegationEngine()
        contract = engine.delegate(
            agent_type="architect",
            task_description="Refactor payment module boundaries",
            target_files=["billing.py"],
            max_iterations=5,
        )
        assert contract.status == DeepExecutionStatus.SUCCESS
        assert len(contract.executive_summary.split()) <= 300
        assert len(contract.to_parent_text()) <= 4100
        assert contract.session_id

    def test_workspace_isolated_per_session(self):
        engine = HierarchicalDelegationEngine()
        first = engine.delegate(agent_type="debugger", task_description="Fix crash", max_iterations=2)
        second = engine.delegate(agent_type="security", task_description="Audit inputs", max_iterations=2)
        assert first.session_id != second.session_id
        assert engine.get_session(first.session_id) is not None
        assert engine.get_session(second.session_id) is not None

    def test_list_agents_covers_fleet(self):
        engine = HierarchicalDelegationEngine()
        agents = engine.list_agents()
        types = {item["agent_type"] for item in agents}
        assert {"architect", "debugger", "security", "test_synthesizer", "performance", "code_reviewer"} <= types

    def test_telemetry_unknown_session(self):
        engine = HierarchicalDelegationEngine()
        payload = engine.telemetry("missing-session")
        assert payload["success"] is False

    def test_telemetry_known_session(self):
        engine = HierarchicalDelegationEngine()
        contract = engine.delegate(agent_type="performance", task_description="Profile hot path", max_iterations=2)
        payload = engine.telemetry(contract.session_id)
        assert payload["success"] is True
        assert payload["session_id"] == contract.session_id

    def test_invalid_task_rejected_as_error_contract(self):
        engine = HierarchicalDelegationEngine()
        contract = engine.delegate(agent_type="architect", task_description="   ", max_iterations=2)
        assert contract.status == DeepExecutionStatus.UNRECOVERABLE_ERROR

    def test_shared_singleton(self):
        assert get_delegation_engine() is get_delegation_engine()
