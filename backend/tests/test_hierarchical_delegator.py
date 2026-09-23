"""Unit tests for the hierarchical delegation engine."""

from alpha.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    build_error_contract,
)
from alpha.subagents.hierarchical_delegator import (
    HierarchicalDelegationEngine,
    get_deep_agent_runner,
    get_delegation_engine,
    normalize_agent_type,
    set_deep_agent_runner,
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
    def test_delegate_without_runner_reports_honest_failure(self):
        """With no execution backend the engine must refuse to fabricate a run.

        Stage-4c: the old ``_synthesize_contract`` unconditionally returned
        SUCCESS with invented oracles, stamps, and token counts. Offline
        delegation must instead report an honest UNRECOVERABLE_ERROR.
        """
        engine = HierarchicalDelegationEngine()
        contract = engine.delegate(
            agent_type="architect",
            task_description="Refactor payment module boundaries",
            target_files=["billing.py"],
            max_iterations=5,
        )
        assert contract.status == DeepExecutionStatus.UNRECOVERABLE_ERROR
        assert contract.error_detail
        assert "No deep agent execution backend" in contract.error_detail
        # no fabricated verification claims or invented token usage
        assert contract.test_oracles == []
        assert contract.security_stamps == []
        assert contract.tokens_consumed == 0
        assert len(contract.executive_summary.split()) <= 300
        assert len(contract.to_parent_text()) <= 4100
        assert contract.session_id

    def test_delegate_relays_runner_contract_without_synthesis(self):
        """When a runner is registered its real contract is relayed verbatim."""
        engine = HierarchicalDelegationEngine()

        def _runner(session):
            return DeepHandoffContract(
                status=DeepExecutionStatus.SUCCESS,
                executive_summary=f"Real run output for {session.spec.goal[:40]}",
                tokens_consumed=42,
            )

        previous = set_deep_agent_runner(_runner)
        try:
            assert get_deep_agent_runner() is _runner
            contract = engine.delegate(agent_type="debugger", task_description="Fix crash")
        finally:
            set_deep_agent_runner(previous)
        assert contract.status == DeepExecutionStatus.SUCCESS
        # token usage is exactly what the run reported, never synthesized
        assert contract.tokens_consumed == 42
        session = engine.get_session(contract.session_id)
        assert session is not None
        assert session.status == "completed"
        assert session.tokens_consumed == 42

    def test_delegate_records_runner_failure_status(self):
        """A runner-reported failure is recorded honestly, not upgraded."""
        engine = HierarchicalDelegationEngine()

        def _runner(session):
            return build_error_contract(session.session_id, session.agent_type, "model backend unavailable")

        previous = set_deep_agent_runner(_runner)
        try:
            contract = engine.delegate(agent_type="security", task_description="Audit inputs")
        finally:
            set_deep_agent_runner(previous)
        assert contract.status == DeepExecutionStatus.UNRECOVERABLE_ERROR
        assert "model backend unavailable" in contract.error_detail
        session = engine.get_session(contract.session_id)
        assert session is not None
        assert session.status == "failed"
        assert session.tokens_consumed == 0

    def test_workspace_isolated_per_session(self):
        engine = HierarchicalDelegationEngine()
        first = engine.delegate(agent_type="debugger", task_description="Fix crash", max_iterations=2)
        second = engine.delegate(agent_type="security", task_description="Audit inputs", max_iterations=2)
        assert first.session_id != second.session_id
        offline_session = engine.get_session(first.session_id)
        assert offline_session is not None
        # offline delegation fails honestly instead of marking success
        assert offline_session.status == "failed"
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
        # no offline token usage is invented
        assert payload["tokens_consumed"] == 0

    def test_invalid_task_rejected_as_error_contract(self):
        engine = HierarchicalDelegationEngine()
        contract = engine.delegate(agent_type="architect", task_description="   ", max_iterations=2)
        assert contract.status == DeepExecutionStatus.UNRECOVERABLE_ERROR

    def test_shared_singleton(self):
        assert get_delegation_engine() is get_delegation_engine()
