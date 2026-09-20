"""4-5+9. Approval custody + policy-first auto-mode + approve-once store.

OpenClaw 2.0 binds every exec approval to (request, command, session,
person) with an audit trail, plus opt-in auto-mode (policy first,
low-risk auto-pass, human for high-risk) and approve-once for recurring
tasks. Alpha has guardrails + ask_clarification; this adds the
custody envelope + reusable store without changing existing tool flow.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal

ApprovalOutcome = Literal["allow", "deny", "ask", "auto_allow"]
RiskLevel = Literal["low", "medium", "high"]


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class ApprovalDecision:
    trace_id: str
    session_id: str
    user_id: str
    command: str
    command_hash: str
    risk: RiskLevel
    outcome: ApprovalOutcome
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    recurring_key: str = ""  # approve-once scope, e.g. "cron:<schedule_id>:<hash>"


_HIGH_RISK_MARKERS = ("rm -rf", "mkfs", ":(){", "shutdown", "chmod 777", "curl|sh", "wget|sh")


def classify_risk(command: str) -> RiskLevel:
    lowered = command.lower()
    if any(marker in lowered for marker in _HIGH_RISK_MARKERS):
        return "high"
    if any(word in lowered for word in ("sudo", "docker", "kubectl", "psql", "drop ", "delete ")):
        return "medium"
    return "low"


@dataclass
class ApprovalCustodyStore:
    """In-memory custody ledger with approve-once support.

    Persistent backends can subclass and override save/lookup; default is
    process-local (same durability contract as Alpha's memory RunRecord
    grace period — durable history stays in RunStore).
    """

    auto_mode: bool = False
    approve_once_ttl_seconds: float = 7 * 24 * 3600
    _decisions: list[ApprovalDecision] = field(default_factory=list)
    _approve_once: dict[str, float] = field(default_factory=dict)

    def request(
        self,
        *,
        trace_id: str,
        session_id: str,
        user_id: str,
        command: str,
        recurring_key: str = "",
    ) -> ApprovalDecision:
        risk = classify_risk(command)
        chash = command_hash(command)

        # Approve-once: a live recurring grant auto-allows the same command.
        if recurring_key:
            grant_key = f"{recurring_key}:{chash}"
            expires = self._approve_once.get(grant_key, 0)
            if expires > time.time():
                decision = ApprovalDecision(
                    trace_id,
                    session_id,
                    user_id,
                    command,
                    chash,
                    risk,
                    "auto_allow",
                    "approve-once grant",
                    recurring_key=recurring_key,
                )
                self._decisions.append(decision)
                return decision

        if self.auto_mode and risk == "low":
            outcome: ApprovalOutcome = "auto_allow"
            reason = "policy-first auto-mode: low-risk pass"
        elif risk == "high":
            outcome, reason = "ask", "high-risk requires human"
        else:
            outcome, reason = ("ask", "human review") if not self.auto_mode else ("ask", "medium-risk requires human")

        decision = ApprovalDecision(
            trace_id,
            session_id,
            user_id,
            command,
            chash,
            risk,
            outcome,
            reason,
            recurring_key=recurring_key,
        )
        self._decisions.append(decision)
        return decision

    def grant_recurring(self, recurring_key: str, command: str) -> None:
        """Approve once for a recurring task (cron/scheduler scope)."""
        key = f"{recurring_key}:{command_hash(command)}"
        self._approve_once[key] = time.time() + self.approve_once_ttl_seconds

    def revoke_recurring(self, recurring_key: str = "", command: str = "") -> None:
        if recurring_key and command:
            self._approve_once.pop(f"{recurring_key}:{command_hash(command)}", None)
        elif recurring_key:
            for key in [k for k in self._approve_once if k.startswith(recurring_key + ":")]:
                self._approve_once.pop(key, None)
        else:
            self._approve_once.clear()

    def history(self, session_id: str = "") -> list[ApprovalDecision]:
        if not session_id:
            return list(self._decisions)
        return [d for d in self._decisions if d.session_id == session_id]


_store_singleton: ApprovalCustodyStore | None = None


def get_approval_store() -> ApprovalCustodyStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = ApprovalCustodyStore()
    return _store_singleton
