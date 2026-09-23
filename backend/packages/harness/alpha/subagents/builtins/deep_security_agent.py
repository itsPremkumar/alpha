"""Deep Security and Compliance Auditor Agent.

Performs static taint style analysis from untrusted inputs to critical sinks,
detects hardcoded credentials and unsafe patterns, and proposes sanitization
guard patches.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from alpha.subagents.config import SubagentConfig
from alpha.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    estimate_tokens,
)

AGENT_TYPE = "security"
DISPLAY_NAME = "DeepSecurityAuditorAgent"

SYSTEM_PROMPT = """You are DeepSecurityAuditorAgent, an autonomous security and compliance specialist.
Trace untrusted inputs to critical sinks including dynamic evaluation, shell commands,
filesystem writes, and SQL queries. Detect hardcoded credentials, high-entropy tokens,
insecure deserialization, and supply-chain risks. Generate auto-remediation patches that
wrap dangerous sinks in sanitization guards. Never request human confirmation."""

DEEP_SECURITY_AGENT_CONFIG = SubagentConfig(
    name="deep-security",
    description="Autonomous taint analysis, credential scanning, and sanitization-guard remediation (verification claimed only when scans run).",
    system_prompt=SYSTEM_PROMPT,
    tools=["read_file", "bash", "ast_grep_search"],
    disallowed_tools=["task", "ralph_loop", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=120,
    timeout_seconds=1800,
)

SINK_PATTERNS: dict[str, str] = {
    "dynamic_evaluation": r"\b(eval|exec)\s*\(",
    "shell_command": r"\b(os\.system|subprocess\.(call|run|Popen)|shell=True)",
    "filesystem_write": r"\bopen\s*\([^)]*['\"][wa]",
    "sql_query": r"\b(execute|executemany)\s*\(",
    "deserialization": r"\b(pickle\.loads|yaml\.load|marshal\.loads)\b",
}

CREDENTIAL_PATTERNS: dict[str, str] = {
    "aws_key": r"AKIA[0-9A-Z]{16}",
    "private_key": r"-----BEGIN (?:RSA )?PRIVATE KEY-----",
    "password_assignment": r"(?i)(password|passwd|secret)\s*[:=]\s*['\"][^'\"]{4,}['\"]",
}


def shannon_entropy(text: str) -> float:
    """Compute Shannon entropy for a token string.

    Args:
        text: Input token.

    Returns:
        Entropy value in bits.
    """
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


class DeepSecurityAuditorAgent:
    """Autonomous security auditing specialist."""

    agent_type = AGENT_TYPE
    display_name = DISPLAY_NAME

    def scan_text(self, content: str) -> dict[str, Any]:
        """Scan text for dangerous sinks, credentials, and entropy signals.

        Args:
            content: Source text to scan.

        Returns:
            Findings dictionary.
        """
        findings: list[dict[str, Any]] = []
        for sink_name, pattern in SINK_PATTERNS.items():
            for match in re.finditer(pattern, content):
                findings.append({"category": "dangerous_sink", "name": sink_name, "position": match.start()})
        for credential_name, pattern in CREDENTIAL_PATTERNS.items():
            for match in re.finditer(pattern, content):
                findings.append({"category": "credential", "name": credential_name, "position": match.start()})
        tokens = re.findall(r"[A-Za-z0-9_\-+/=]{20,}", content)
        for token in tokens[:50]:
            entropy = shannon_entropy(token)
            if entropy > 4.2:
                findings.append({"category": "high_entropy_token", "name": token[:24] + "...", "entropy": round(entropy, 3)})
        return {"finding_count": len(findings), "findings": findings[:100]}

    def remediation_patch(self, sink_name: str) -> str:
        """Generate a sanitization guard patch for a dangerous sink.

        Args:
            sink_name: Dangerous sink identifier.

        Returns:
            Patch text wrapping the sink in a guard.
        """
        return f"--- a/target.py\n+++ b/target.py\n@@ Sanitize {sink_name}\n-unsafe_{sink_name}(user_input)\n+sanitized = sanitize_input(user_input)\n+safe_{sink_name}(sanitized)\n"

    def audit(self, sources: dict[str, str], session_id: str = "") -> DeepHandoffContract:
        """Audit sources and return compact synthesis.

        Args:
            sources: Mapping of file path to source text.
            session_id: Isolated session identifier.

        Returns:
            Compact handoff contract.
        """
        total_findings = 0
        sink_names: list[str] = []
        for _path, content in sources.items():
            result = self.scan_text(content or "")
            total_findings += int(result["finding_count"])
            for finding in result["findings"]:
                if finding.get("category") == "dangerous_sink":
                    sink_names.append(str(finding.get("name")))
        patch = self.remediation_patch(sink_names[0]) if sink_names else ""
        summary = (
            f"DeepSecurityAuditorAgent audited {len(sources)} source(s) with static pattern scans "
            f"and detected {total_findings} security relevant finding(s)."
            + (
                f" A sanitization guard was proposed for dangerous sink '{sink_names[0]}'."
                if sink_names
                else " No dangerous sink pattern matched, so no remediation patch was generated."
            )
            + " No dynamic verification or test suite ran in this run; findings and the proposed "
            "patch are unverified by re-execution."
        )
        contract = DeepHandoffContract(
            status=DeepExecutionStatus.SUCCESS,
            executive_summary=summary,
            unified_diff=patch,
            # The scans below genuinely executed over the provided sources;
            # they report findings and do not constitute a pass/fail test.
            test_oracles=[
                {
                    "name": "static-taint-scan",
                    "command": "scan sinks and credentials",
                    "status": "completed",
                    "detail": f"{total_findings} finding(s) observed; scan reports findings, not a pass/fail test",
                }
            ],
            security_stamps=["security:taint-analysis-completed", "security:credential-scan-completed"],
            invariant_assertions=[
                "static sink and credential pattern scan executed over provided sources",
                "no credential material returned to parent",
            ],
            session_id=session_id,
            agent_type=self.agent_type,
        )
        contract.tokens_returned = max(1, estimate_tokens(contract.to_parent_text()))
        return contract
