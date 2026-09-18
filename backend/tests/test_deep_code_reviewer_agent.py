"""Unit tests for DeepCodeReviewerAgent."""

from agent_workspace.subagents.builtins.deep_code_reviewer_agent import (
    DEEP_CODE_REVIEWER_AGENT_CONFIG,
    DeepCodeReviewerAgent,
)


class TestDeepCodeReviewerAgent:
    def test_config(self):
        assert DEEP_CODE_REVIEWER_AGENT_CONFIG.name == "deep-code-reviewer"

    def test_public_api_extraction(self):
        agent = DeepCodeReviewerAgent()
        api = agent.extract_public_api("def public():\n  pass\ndef _private():\n  pass\n")
        assert api == {"public"}

    def test_pass_verdict(self):
        agent = DeepCodeReviewerAgent()
        result = agent.review_patch("def api():\n  return 1\n", "def api():\n  return 2\n")
        assert result["verdict"] == "PASS"

    def test_breaking_change_fails(self):
        agent = DeepCodeReviewerAgent()
        result = agent.review_patch("def api():\n  return 1\n", "def other():\n  return 1\n")
        assert result["verdict"] == "FAIL"
        assert result["removed_api"] == ["api"]

    def test_syntax_error_fails(self):
        agent = DeepCodeReviewerAgent()
        result = agent.review_patch("def api():\n  return 1\n", "def broken(((:\n")
        assert result["verdict"] == "FAIL"

    def test_review_returns_contract(self):
        agent = DeepCodeReviewerAgent()
        contract = agent.review("def api():\n  return 1\n", "def api():\n  return 2\n", session_id="s-review")
        assert contract.is_success()
        assert "verdict=PASS" in contract.invariant_assertions
