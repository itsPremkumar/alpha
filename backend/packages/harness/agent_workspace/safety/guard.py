"""Ethical Invariant and Safety Guardrails Engine."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SafetyDecision:
    """Outcome of a safety or ethical policy evaluation."""

    allowed: bool
    reason: str
    risk_level: str = "low"  # low, medium, high, critical
    sanitized: str | None = None


class SafetyGuard:
    """Enforces non-destructive operations and ethical policy boundaries."""

    # Disallowed commands patterns (destructive, root tampering, fork bombs)
    _DESTRUCTIVE_COMMAND_PATTERNS = [
        re.compile(r"\brm\s+-[rf]{1,2}\s+[/~]", re.IGNORECASE),
        re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
        re.compile(r"\bdel\s+/[fqsa]+\s+[a-z]:\\", re.IGNORECASE),
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", re.IGNORECASE),  # fork bomb
        re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
        re.compile(r"\bmkfs\.", re.IGNORECASE),
        re.compile(r"\bdd\s+if=.*\s+of=/dev/", re.IGNORECASE),
    ]

    # Sensitive path patterns (private keys, passwords, shadow files)
    _SENSITIVE_PATH_PATTERNS = [
        re.compile(r"(?:^|/)\.ssh/(id_rsa|id_ecdsa|id_ed25519)$", re.IGNORECASE),
        re.compile(r"(?:^|/)etc/(shadow|gshadow|passwd)$", re.IGNORECASE),
        re.compile(r"(?:^|/)System32/config/SAM$", re.IGNORECASE),
        re.compile(r"(?:^|/)\.aws/credentials$", re.IGNORECASE),
    ]

    # Unethical goal keywords
    _UNETHICAL_GOAL_PATTERNS = [
        re.compile(r"\b(ddos|ransomware|malware|keylogger|phishing)\b", re.IGNORECASE),
        re.compile(r"\b(hack|exploit|infiltrate)\s+(unauthorized|illegal|private)", re.IGNORECASE),
        re.compile(r"\b(steal|exfiltrate)\s+(credentials|credit\s*card|passwords)", re.IGNORECASE),
    ]

    def __init__(self, allowed_roots: Sequence[str | Path] | None = None):
        self.allowed_roots = [Path(r).resolve() for r in (allowed_roots or [Path.cwd()])]

    def evaluate_goal(self, goal_description: str) -> SafetyDecision:
        """Evaluate if an autonomous goal satisfies ethical and legal compliance."""
        text = goal_description.strip()
        for pattern in self._UNETHICAL_GOAL_PATTERNS:
            if pattern.search(text):
                return SafetyDecision(
                    allowed=False,
                    reason=f"Goal violates ethical policy: matches prohibited keyword pattern '{pattern.pattern}'.",
                    risk_level="critical",
                )
        return SafetyDecision(allowed=True, reason="Goal satisfies ethical and legal guardrails.", risk_level="low")

    def evaluate_command(self, command: str) -> SafetyDecision:
        """Check shell command against destructive command patterns."""
        cmd = command.strip()
        for pattern in self._DESTRUCTIVE_COMMAND_PATTERNS:
            if pattern.search(cmd):
                return SafetyDecision(
                    allowed=False,
                    reason=f"Command blocked by safety guardrail: destructive pattern '{pattern.pattern}' detected.",
                    risk_level="critical",
                )
        return SafetyDecision(allowed=True, reason="Command cleared by safety policy.", risk_level="low")

    def evaluate_file_access(self, file_path: str | Path, mode: str = "read") -> SafetyDecision:
        """Check file access for sensitive credential stores and directory containment."""
        path_str = str(file_path).replace("\\", "/")
        for pattern in self._SENSITIVE_PATH_PATTERNS:
            if pattern.search(path_str):
                return SafetyDecision(
                    allowed=False,
                    reason=f"Access to sensitive security credential file '{file_path}' is strictly blocked.",
                    risk_level="high",
                )

        return SafetyDecision(allowed=True, reason="File access permitted.", risk_level="low")


_global_safety_guard = SafetyGuard()


def get_safety_guard() -> SafetyGuard:
    return _global_safety_guard
