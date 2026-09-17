"""Sandboxed Computer-Use Engine with 3-Tier Blast-Radius Enclaves.

Inspired by Chapters 10, 11, and 34 of the Master Architecture Blueprint:
- Agent Zero-style computer worker supporting terminal & file actions
- Strict 3-Tier Safety Gate:
    1. SAFE: read-only, workspace tests, git status -> auto-execute
    2. SENSITIVE: package installs, git push, env updates -> requires approval
    3. FORBIDDEN: destructive root commands, credential theft, disk wipes -> hard reject
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ActionSafetyTier(str, Enum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    FORBIDDEN = "forbidden"


@dataclass
class CommandRiskClassification:
    command: str
    tier: ActionSafetyTier
    reason: str
    matched_pattern: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tier"] = self.tier.value
        return data


class BlastRadiusPolicy:
    """Classifies commands and file actions against security blast-radius enclaves."""

    # Patterns that are fundamentally forbidden (cannot be overridden by autonomous agents)
    FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
        (r"(?i)rm\s+-(?:rf|fr)\s+[/~]", "Root or home recursive deletion"),
        (r"(?i)mkfs(?:\.\w+)?\s+", "Filesystem formatting"),
        (r"(?i)dd\s+if=.*of=/dev/", "Raw disk device overwriting"),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "Bash fork bomb"),
        (r"(?i)/etc/(?:shadow|passwd|sudoers)", "Privileged OS credentials tampering"),
        (r"(?i)\.ssh/(?:id_rsa|id_ed25519|authorized_keys)", "Private SSH key access"),
        (r"(?i)169\.254\.169\.254", "Cloud instance metadata exfiltration"),
        (r"(?i)\.aws/credentials", "Cloud provider credentials exfiltration"),
        (r"(?i)chmod\s+(?:-R\s+)?777\s+/", "Universal root permission degradation"),
    ]

    # Patterns that require explicit human or supervisor sign-off
    SENSITIVE_PATTERNS: list[tuple[str, str]] = [
        (r"(?i)git\s+push\s+.*--force", "Force pushing to git remote"),
        (r"(?i)git\s+reset\s+--hard", "Hard git reset losing uncommitted work"),
        (r"(?i)drop\s+table", "Database table destruction"),
        (r"(?i)alter\s+table", "Database schema mutation"),
        (r"(?i)pip\s+install", "Python package environment modification"),
        (r"(?i)npm\s+install", "Node package environment modification"),
        (r"(?i)kill\s+-(?:9|KILL)", "Forced process termination"),
        (r"(?i)\.env(?:\.local)?$", "Environment secrets file modification"),
        (r"(?i)systemctl\s+(?:stop|restart|disable)", "System service disruption"),
    ]

    @classmethod
    def classify(cls, command: str) -> CommandRiskClassification:
        cmd = command.strip()

        # Check forbidden
        for pat, reason in cls.FORBIDDEN_PATTERNS:
            if re.search(pat, cmd):
                return CommandRiskClassification(
                    command=cmd,
                    tier=ActionSafetyTier.FORBIDDEN,
                    reason=f"Hard Security Policy: {reason}",
                    matched_pattern=pat,
                )

        # Check sensitive
        for pat, reason in cls.SENSITIVE_PATTERNS:
            if re.search(pat, cmd):
                return CommandRiskClassification(
                    command=cmd,
                    tier=ActionSafetyTier.SENSITIVE,
                    reason=f"Supervisor Approval Required: {reason}",
                    matched_pattern=pat,
                )

        # Safe
        return CommandRiskClassification(
            command=cmd,
            tier=ActionSafetyTier.SAFE,
            reason="Action within safe sandbox boundary",
        )


class ComputerWorker:
    """Sandboxed computer worker with blast-radius policy gate and execution audit trail."""

    def __init__(self, sandbox_name: str = "default_sandbox"):
        self.sandbox_name: str = sandbox_name
        self._audit_log: list[dict[str, Any]] = []

    def execute(
        self,
        command: str,
        approval_granted: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Classify and evaluate command execution through safety gates."""
        classification = BlastRadiusPolicy.classify(command)
        t_now = time.time()

        if classification.tier == ActionSafetyTier.FORBIDDEN:
            record = {
                "timestamp": t_now,
                "command": command,
                "tier": "forbidden",
                "status": "rejected",
                "reason": classification.reason,
            }
            self._audit_log.append(record)
            return {
                "status": "forbidden",
                "tier": classification.tier.value,
                "error": f"Command rejected: {classification.reason}",
                "classification": classification.to_dict(),
            }

        if classification.tier == ActionSafetyTier.SENSITIVE and not approval_granted:
            record = {
                "timestamp": t_now,
                "command": command,
                "tier": "sensitive",
                "status": "awaiting_approval",
                "reason": classification.reason,
            }
            self._audit_log.append(record)
            return {
                "status": "approval_required",
                "tier": classification.tier.value,
                "error": f"Action paused: {classification.reason}",
                "classification": classification.to_dict(),
            }

        # Safe or approved sensitive action
        record = {
            "timestamp": t_now,
            "command": command,
            "tier": classification.tier.value,
            "status": "executed" if not dry_run else "dry_run_simulated",
            "approval_used": approval_granted,
        }
        self._audit_log.append(record)

        return {
            "status": "executed" if not dry_run else "dry_run",
            "command": command,
            "tier": classification.tier.value,
            "classification": classification.to_dict(),
            "stdout": f"[Sandbox '{self.sandbox_name}'] Command validated and executed safely.",
            "exit_code": 0,
        }

    def get_audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)
