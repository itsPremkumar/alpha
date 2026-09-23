"""Unit tests for DeepTestSynthesizerAgent."""

from alpha.subagents.builtins.deep_test_synthesizer_agent import (
    DEEP_TEST_SYNTHESIZER_AGENT_CONFIG,
    DeepTestSynthesizerAgent,
)


class TestDeepTestSynthesizerAgent:
    def test_config(self):
        assert DEEP_TEST_SYNTHESIZER_AGENT_CONFIG.name == "deep-test-synthesizer"

    def test_coverage_gaps(self):
        agent = DeepTestSynthesizerAgent()
        gaps = agent.identify_coverage_gaps({"a": 0.95, "b": 0.4, "c": 0.1}, threshold=0.8)
        assert gaps == ["b", "c"]

    def test_generated_tests_cover_boundaries(self):
        agent = DeepTestSynthesizerAgent()
        source = agent.generate_tests("billing", "calculate_total")
        assert "hypothesis" in source
        assert "calculate_total" in source

    def test_mutation_score(self):
        agent = DeepTestSynthesizerAgent()
        assert agent.mutation_score(8, 10) == 0.8
        assert agent.mutation_score(0, 0) == 0.0

    def test_synthesize_returns_contract(self):
        agent = DeepTestSynthesizerAgent()
        contract = agent.synthesize({"billing": 0.5}, "billing", "calculate_total", session_id="s-test")
        assert contract.is_success()
        # Stage-4c honesty: the generated tests never ran, so no oracle may
        # claim a pass, and no verification stamp or mutation score exists.
        assert all(oracle.get("passed") is not True for oracle in contract.test_oracles)
        assert any(oracle.get("status") == "not_run" for oracle in contract.test_oracles)
        assert contract.security_stamps == []
        assert "Mutation score" not in contract.executive_summary
        assert "were not executed" in contract.executive_summary
