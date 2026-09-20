"""Unit tests for DeepSecurityAuditorAgent."""

from alpha.subagents.builtins.deep_security_agent import (
    DEEP_SECURITY_AGENT_CONFIG,
    DeepSecurityAuditorAgent,
)


class TestDeepSecurityAgent:
    def test_config(self):
        assert DEEP_SECURITY_AGENT_CONFIG.name == "deep-security"

    def test_sink_detection(self):
        agent = DeepSecurityAuditorAgent()
        result = agent.scan_text("eval(user_input)\nos.system('ls ' + name)")
        assert result["finding_count"] >= 2
        names = {finding["name"] for finding in result["findings"]}
        assert "dynamic_evaluation" in names

    def test_credential_detection(self):
        agent = DeepSecurityAuditorAgent()
        result = agent.scan_text("password = \"supersecret123\"")
        assert any(finding["category"] == "credential" for finding in result["findings"])

    def test_remediation_patch(self):
        agent = DeepSecurityAuditorAgent()
        patch = agent.remediation_patch("shell_command")
        assert "sanitize_input" in patch

    def test_audit_returns_contract(self):
        agent = DeepSecurityAuditorAgent()
        contract = agent.audit({"app.py": "eval(user_input)"}, session_id="s-sec")
        assert contract.is_success()
        assert "security:taint-analysis-completed" in contract.security_stamps
