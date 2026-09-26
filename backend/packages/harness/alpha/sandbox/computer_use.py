"""Sandboxed Computer-Use Engine with 3-Tier Blast-Radius Enclaves.

Inspired by Chapters 10, 11, and 34 of the Master Architecture Blueprint:
- Agent Zero-style computer worker supporting terminal & file actions
- Strict 3-Tier Safety Gate:
    1. SAFE: read-only, workspace tests, git status -> auto-execute
    2. SENSITIVE: package installs, git push, env updates -> requires approval
    3. FORBIDDEN: destructive root commands, credential theft, disk wipes -> hard reject

Honesty contract: this engine **classifies and gates** commands; it does not run
them. Nothing here spawns a process, so a command that clears the gate is
reported as ``validated`` with an explicit disclosure -- never as ``executed``
with an ``exit_code``. (It previously claimed both, which is a fabricated
success on a host-mutating surface.)

Approval contract: a SENSITIVE command is refused with an opaque
``approval_id``. The id is granted out of band by an **operator** through
:meth:`ComputerWorker.grant_approval`; there is deliberately no boolean or
string a model-facing tool can pass to approve its own action.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

#: Disclosed on every non-executing result so no caller can mistake this engine
#: for something that actually touched the host.
NO_EXECUTION_DISCLOSURE = (
    "validation only: this engine classified the command against the blast-radius policy "
    "and spawned no process, so the host is unchanged and there is no exit code"
)


class ActionSafetyTier(StrEnum):
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
    """Sandboxed computer worker with blast-radius policy gate and execution audit trail.

    The gate is a *classification* gate, not an execution engine: see
    :data:`NO_EXECUTION_DISCLOSURE`. Sensitive commands need an operator-issued,
    single-use, command-bound :meth:`grant_approval`.
    """

    def __init__(self, sandbox_name: str = "default_sandbox"):
        self.sandbox_name: str = sandbox_name
        self._audit_log: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        # approval_id -> {command, approved_by, expires_at, consumed}
        self._approvals: dict[str, dict[str, Any]] = {}

    # -- operator approval surface (NOT a model-facing tool argument) ---------
    def grant_approval(self, approval_id: str, *, approved_by: str = "operator") -> dict[str, Any]:
        """Approve a paused SENSITIVE command. Single use, command-bound, expiring.

        This is the only way a SENSITIVE command can pass the gate. No tool
        argument can reach it, so a model cannot authorise its own action.
        Granting arms the id; the first matching :meth:`execute` spends it.
        """
        with self._lock:
            record = self._approvals.get(str(approval_id))
            if record is None:
                return {"approved": False, "approval_id": str(approval_id), "reason": "no such pending approval"}
            if record["consumed"]:
                return {"approved": False, "approval_id": str(approval_id), "reason": "that approval was already used"}
            if record["expires_at"] <= time.monotonic():
                self._approvals.pop(str(approval_id), None)
                return {"approved": False, "approval_id": str(approval_id), "reason": "that approval expired"}
            record["approved_by"] = str(approved_by or "operator")
            return {
                "approved": True,
                "approval_id": str(approval_id),
                "command": record["command"],
                "approved_by": record["approved_by"],
                "single_use": True,
            }

    def revoke_approvals(self) -> dict[str, Any]:
        """Revoke every pending approval (operator reset)."""
        with self._lock:
            count = len(self._approvals)
            self._approvals.clear()
            return {"revoked_count": count}

    def execute(
        self,
        command: str,
        approval_id: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Classify a command and run it through the blast-radius safety gates.

        ``approval_id`` must be an id previously handed out by a paused
        SENSITIVE request *and* granted by an operator. A bare boolean is not
        accepted: the model-facing tool used to pass one, which meant the model
        could lift the gate on its own.
        """
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
                "disclosure": NO_EXECUTION_DISCLOSURE,
            }

        approval_state: dict[str, Any] = {"used": False, "reason": "not required"}
        if classification.tier == ActionSafetyTier.SENSITIVE:
            consumed = self._consume_approval(approval_id, command)
            approval_state = {"used": consumed["valid"], "reason": consumed["reason"]}
            if not consumed["valid"]:
                pending_id = self._register_pending(command)
                record = {
                    "timestamp": t_now,
                    "command": command,
                    "tier": "sensitive",
                    "status": "awaiting_approval",
                    "reason": classification.reason,
                    "approval_id": pending_id,
                }
                self._audit_log.append(record)
                return {
                    "status": "approval_required",
                    "tier": classification.tier.value,
                    "error": f"Action paused: {classification.reason}",
                    "classification": classification.to_dict(),
                    "approval_id": pending_id,
                    "approval_reason": consumed["reason"],
                    "next_step": "a human operator must grant this approval_id out of band; no tool argument can approve it",
                    "disclosure": NO_EXECUTION_DISCLOSURE,
                }

        # Safe, or a SENSITIVE command with a real operator approval behind it.
        record = {
            "timestamp": t_now,
            "command": command,
            "tier": classification.tier.value,
            "status": "validated" if not dry_run else "dry_run",
            "approval_used": approval_state["used"],
        }
        self._audit_log.append(record)

        return {
            "status": "validated" if not dry_run else "dry_run",
            "command": command,
            "tier": classification.tier.value,
            "classification": classification.to_dict(),
            "approval_used": approval_state["used"],
            "detail": f"[Sandbox '{self.sandbox_name}'] Command cleared the {classification.tier.value} blast-radius gate.",
            "executed": False,
            "exit_code": None,
            "disclosure": NO_EXECUTION_DISCLOSURE,
        }

    # -- approval plumbing ---------------------------------------------------
    def _register_pending(self, command: str) -> str:
        approval_id = secrets.token_urlsafe(18)
        with self._lock:
            self._approvals[approval_id] = {
                "command": command,
                "approved_by": "",
                "expires_at": time.monotonic() + 300.0,
                "consumed": False,
            }
        return approval_id

    def _consume_approval(self, approval_id: str, command: str) -> dict[str, Any]:
        if not approval_id:
            return {"valid": False, "reason": "a SENSITIVE command needs an operator-granted approval_id"}
        with self._lock:
            record = self._approvals.get(str(approval_id))
            if record is None:
                return {"valid": False, "reason": "no such approval is pending; a model cannot mint one"}
            if not record["approved_by"]:
                return {"valid": False, "reason": "that approval was never granted by an operator"}
            if record["consumed"]:
                return {"valid": False, "reason": "that approval was already used"}
            if record["expires_at"] <= time.monotonic():
                self._approvals.pop(str(approval_id), None)
                return {"valid": False, "reason": "that approval expired"}
            if record["command"] != command:
                return {"valid": False, "reason": "that approval was granted for a different command"}
            record["consumed"] = True
        return {"valid": True, "reason": "operator approval consumed", "approved_by": record["approved_by"]}

    def get_audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)
