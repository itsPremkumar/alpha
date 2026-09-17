"""Smart Approvals evaluator with comment-stripping and prompt-injection defense."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from agent_workspace.safety.guardian.circuit_breaker import get_denial_breaker
from agent_workspace.safety.guardian.floors import get_permanent_allowlist


@dataclass
class GuardianReviewResult:
    verdict: Literal["APPROVE", "DENY", "ESCALATE"]
    reason: str
    sanitized_command: str


def strip_shell_comments(command: str) -> str:
    """Strip unquoted trailing `# ...` comments before evaluation to prevent injection."""
    cleaned_lines = []
    for line in command.splitlines():
        in_single = in_double = False
        i = 0
        cut_idx = len(line)
        while i < len(line):
            ch = line[i]
            if ch == "\\" and in_double and i + 1 < len(line):
                i += 2
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                cut_idx = i
                break
            i += 1
        cleaned_line = line[:cut_idx].rstrip()
        if cleaned_line or not cleaned_lines:
            cleaned_lines.append(cleaned_line)
    return "\n".join(cleaned_lines).strip()


_DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\s+-[rf]{1,2}\s+[/~]", re.IGNORECASE), "Recursive root or home directory deletion"),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), "Disk format operation"),
    (re.compile(r"\bdel\s+/[fqsa]+\s+[a-z]:\\", re.IGNORECASE), "System drive deletion"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", re.IGNORECASE), "Fork bomb attack"),
    (re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE), "Raw disk overwrite"),
    (re.compile(r"\bdrop\s+database\b", re.IGNORECASE), "Database drop statement"),
    (re.compile(r"\btruncate\s+table\b", re.IGNORECASE), "Database table truncation"),
    (re.compile(r"\bchmod\s+-R\s+777\s+/", re.IGNORECASE), "Root permission tampering"),
]

_SUSPICIOUS_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|above)\s+instructions", re.IGNORECASE),
    re.compile(r"approve\s+this\s+command", re.IGNORECASE),
    re.compile(r"system\s*override", re.IGNORECASE),
]


def evaluate_command_safety(
    command: str,
    auxiliary_evaluator_fn: Callable[[str], str] | None = None,
) -> GuardianReviewResult:
    """Evaluate a command against safety floors, injection heuristics, and guardian rules."""
    breaker = get_denial_breaker()
    if breaker.is_tripped:
        return GuardianReviewResult(
            verdict="DENY",
            reason=breaker.trip_reason,
            sanitized_command=command,
        )

    # 1. Strip shell comments
    sanitized = strip_shell_comments(command)
    if not sanitized:
        return GuardianReviewResult(
            verdict="APPROVE",
            reason="Empty command or comment-only",
            sanitized_command=sanitized,
        )

    # 2. Check prompt injection heuristics
    for pat in _SUSPICIOUS_INJECTION_PATTERNS:
        if pat.search(command):
            # Prompt injection attempt detected in command
            breaker.record_verdict("DENY", "Detected prompt injection patterns inside command text")
            return GuardianReviewResult(
                verdict="DENY",
                reason="Denied: Prompt injection attempt detected inside command text.",
                sanitized_command=sanitized,
            )

    # 3. Check permanent allowlist
    allowlist = get_permanent_allowlist()
    if allowlist.is_allowlisted(sanitized):
        breaker.record_verdict("APPROVE")
        return GuardianReviewResult(
            verdict="APPROVE",
            reason="Approved via permanent operator allowlist rule.",
            sanitized_command=sanitized,
        )

    # 4. Check dangerous patterns
    for pat, desc in _DANGEROUS_PATTERNS:
        if pat.search(sanitized):
            breaker.record_verdict("DENY", desc)
            return GuardianReviewResult(
                verdict="DENY",
                reason=f"Denied dangerous operation: {desc}.",
                sanitized_command=sanitized,
            )

    # 5. Check if auxiliary LLM reviewer provided
    if auxiliary_evaluator_fn:
        aux_verdict = auxiliary_evaluator_fn(sanitized).strip().upper()
        if "APPROVE" in aux_verdict:
            breaker.record_verdict("APPROVE")
            return GuardianReviewResult(verdict="APPROVE", reason="Approved by auxiliary guardian model.", sanitized_command=sanitized)
        elif "DENY" in aux_verdict:
            breaker.record_verdict("DENY", "Denied by guardian model")
            return GuardianReviewResult(verdict="DENY", reason="Denied by auxiliary guardian model.", sanitized_command=sanitized)
        return GuardianReviewResult(verdict="ESCALATE", reason="Guardian model requested operator escalation.", sanitized_command=sanitized)

    # By default, safe non-matching command approves
    breaker.record_verdict("APPROVE")
    return GuardianReviewResult(
        verdict="APPROVE",
        reason="Command passed safety verification.",
        sanitized_command=sanitized,
    )
