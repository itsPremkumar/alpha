"""BlastRadiusGuardMod — deterministic tool risk classification and hold-and-release gate."""

from __future__ import annotations

import logging
import re
import time
import uuid
from enum import StrEnum
from typing import Any

from alpha.mods.context import CapabilityContext
from alpha.mods.manifest import ModManifest
from alpha.mods.preview import preview_impact
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)


class RiskLevel(StrEnum):
    """Deterministic blast radius risk levels."""

    R0 = "R0"  # Read-only (view_file, grep_search, read_url)
    R1 = "R1"  # Local reversible (single file edit)
    R2 = "R2"  # Broad mutation (multi-file edits, project refactors)
    R3 = "R3"  # Network / credentials (outbound APIs, token/secret reads)
    R4 = "R4"  # Destructive / irreversible (rm -rf, DROP TABLE, force push)
    R5 = "R5"  # Production critical (cloud deployments, cluster modifications)


# High-risk patterns in shell / terminal commands
_R4_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+.*(-[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*-f[a-zA-Z]*r|--force\s+--recursive|--recursive\s+--force|-r\s+.*-f|-f\s+.*-r)\b", re.IGNORECASE),
    re.compile(r"\b(remove-item|del|erase|ri|rmdir|rd)\b.*(-recurse\b.*-force\b|-force\b.*-recurse\b|/[sS]\s+/[qQ]|/[qQ]\s+/[sS])", re.IGNORECASE),
    re.compile(r"\bgit\s+(push\b.*(--force|-f\b)|reset\s+--hard|clean\s+-[a-zA-Z]*f)", re.IGNORECASE),
    re.compile(r"\b(drop\s+(database|table|schema)|truncate\s+table|delete\s+from\s+[a-zA-Z_]+\s*;?$)\b", re.IGNORECASE),
    re.compile(r"\b(mkfs|dd\s+if=|fdisk|format\s+[a-zA-Z]:)\b", re.IGNORECASE),
    re.compile(r"\b(shutil\.rmtree)\b", re.IGNORECASE),
]

_R5_PRODUCTION_PATTERNS = [
    re.compile(r"\b(kubectl\s+(delete|apply|drain|scale)|helm\s+(uninstall|upgrade|install)|terraform\s+(apply|destroy)|pulumi\s+(up|destroy))\b", re.IGNORECASE),
]

_R3_NETWORK_SECRETS_PATTERNS = [
    re.compile(r"\b(curl|wget|ssh|scp|sftp|nc|ncat|telnet)\b", re.IGNORECASE),
    re.compile(r"\b(aws|gcloud|az|kubectl|helm)\b", re.IGNORECASE),
    re.compile(r"\b(api_key|token|secret|password|bearer)\b", re.IGNORECASE),
]

_R0_SHELL_PATTERNS = [
    re.compile(r"^\s*(ls|dir|cat|head|tail|grep|find|findstr|echo|pwd|git\s+(status|diff|log|branch))\b", re.IGNORECASE),
]

_R0_TOOLS = {
    "view_file",
    "grep_search",
    "read_url",
    "find_by_name",
    "list_dir",
    "read_url_content",
    "read_browser_page",
    "code_search",
    "symbol_search",
    "get_documentation",
}

_R1_TOOLS = {
    "replace_file_content",
    "write_to_file",
    "edit_file",
}

_CRITICAL_PATH_PATTERNS = [
    re.compile(r"(\.env|credentials|id_rsa|id_ed25519|\.ssh/|\.aws/)", re.IGNORECASE),
    re.compile(r"^(/etc/|/usr/|/var/|/bin/|/sbin/|c:\\windows|c:\\program files)", re.IGNORECASE),
]


class BlastRadiusGuardMod:
    """Tool risk classifier (R0-R5) and hold-and-release gate with diff/impact preview."""

    name = "blast_radius_guard"
    version = "1.0.0"
    priority = int(ModPriority.SECURITY)
    required_capabilities = {"tools:read", "ui:render", "storage:write", "storage:read"}
    subscribed_events = {"tool.requested"}
    manifest = ModManifest.create(
        name="blast_radius_guard",
        version="1.0.0",
        description="Tool risk classifier (R0-R5) and hold-and-release gate with diff/impact preview.",
        hooks=("tool.requested",),
        calls=("tools:read", "ui:render", "storage:write", "storage:read"),
        state_writes=("held_actions", "approved_ids", "rejected_ids"),
        gating=True,
    )

    def __init__(self, *, strict_network: bool = False, hold_store: Any | None = None):
        self._strict_network = strict_network
        # The durable store is the authority on whether a hold was released or
        # rejected; the in-memory sets below remain a fast path for a decision
        # taken in this process and are never the only record.
        self._hold_store = hold_store
        self._held_actions: dict[str, dict[str, Any]] = {}
        self._approved_ids: set[str] = set()
        self._rejected_ids: set[str] = set()
        self._rejected_reasons: dict[str, str] = {}

    @property
    def hold_store(self) -> Any:
        """The durable hold store, resolved lazily so tests need no runtime home."""
        if self._hold_store is None:
            from alpha.mods.approvals import get_hold_store

            self._hold_store = get_hold_store()
        return self._hold_store

    def classify_risk(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> tuple[RiskLevel, str, dict[str, Any]]:
        """Classify tool execution risk deterministically."""
        # 1. Builtin read-only tools
        if tool_name in _R0_TOOLS:
            return RiskLevel.R0, "Standard read-only operation", {}

        # 2. Command execution tools (bash, terminal, run_command, shell)
        if tool_name in {"run_command", "bash", "shell", "terminal", "powershell", "cmd"}:
            cmd = str(tool_args.get("CommandLine") or tool_args.get("command") or tool_args.get("cmd") or "").strip()
            # Check R5 production infrastructure commands
            for pat in _R5_PRODUCTION_PATTERNS:
                if pat.search(cmd):
                    return (
                        RiskLevel.R5,
                        f"Production cluster/infra command matching '{pat.pattern}'",
                        {"command": cmd, "preview": f"Cluster/Infra: {cmd}"},
                    )
            # Check R4 destructive
            for pat in _R4_DESTRUCTIVE_PATTERNS:
                if pat.search(cmd):
                    return (
                        RiskLevel.R4,
                        f"Destructive shell command matching '{pat.pattern}'",
                        {"command": cmd, "preview": f"Command to execute: {cmd}"},
                    )
            # Check R3 network / secrets
            for pat in _R3_NETWORK_SECRETS_PATTERNS:
                if pat.search(cmd):
                    return (
                        RiskLevel.R3,
                        f"Network or credential operation matching '{pat.pattern}'",
                        {"command": cmd, "preview": f"Network/Secret command: {cmd}"},
                    )
            # Check R0 shell command
            for pat in _R0_SHELL_PATTERNS:
                if pat.search(cmd):
                    return RiskLevel.R0, "Safe read-only shell invocation", {"command": cmd}
            # Default shell command is local mutation R1
            return RiskLevel.R1, "Standard shell execution", {"command": cmd}

        # 3. File modifications
        if tool_name in _R1_TOOLS:
            target = str(tool_args.get("TargetFile") or tool_args.get("path") or "").strip()
            for crit_pat in _CRITICAL_PATH_PATTERNS:
                if crit_pat.search(target):
                    return (
                        RiskLevel.R4,
                        f"Mutation targeting critical/sensitive path '{target}'",
                        {"target_file": target, "preview": f"Target: {target}"},
                    )
            preview_content = str(tool_args.get("ReplacementContent") or tool_args.get("CodeContent") or "")
            return (
                RiskLevel.R1,
                f"Local file write/edit to {target}",
                {"target_file": target, "preview": preview_content[:300]},
            )

        # 4. Git or deployment tools
        t_lower = tool_name.lower()
        if any(w in t_lower for w in ("deploy", "cluster", "k8s", "terraform", "production", "fleet")):
            return RiskLevel.R5, f"Production critical tool {tool_name}", {"args": tool_args}

        return RiskLevel.R1, f"Standard tool invocation ({tool_name})", {"args": tool_args}

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        payload = event.payload
        tool_name = str(payload.get("tool_name") or payload.get("name") or "")
        tool_args = dict(payload.get("tool_args") or payload.get("args") or {})

        risk_level, reason, details = self.classify_risk(tool_name, tool_args)

        tool_call_id = event.correlation.tool_call_id or event.event_id
        run_id = str(event.correlation.run_id or "")

        # A durable decision outranks every in-memory fast path: the operator
        # may have answered from a different process, or before a restart.
        store_status, store_record = self._store_status(tool_name, tool_args, run_id, tool_call_id)
        if store_status == "rejected":
            reject_reason = (store_record.decision_reason if store_record else "") or "Rejected by operator"
            logger.warning("Refusing rejected tool call %s: %s", tool_call_id, reject_reason)
            return EventResult.deny(
                event=event,
                reason=f"ACTION_REJECTED: Operator rejected this action: {reject_reason}",
            )
        if store_status == "approved":
            logger.info("Proceeding with durably approved high-risk tool call %s", tool_call_id)
            return await next_fn(event)
        if store_status == "expired":
            return EventResult.deny(
                event=event,
                reason=("APPROVAL_EXPIRED: A hold on this action expired before an operator decided it. Re-request the action to open a fresh hold; the previous approval, if any, is void."),
            )

        # If previously rejected by operator, deny immediately
        is_rejected = tool_call_id in self._rejected_ids or payload.get("hold_id") in self._rejected_ids
        if is_rejected:
            reject_reason = self._rejected_reasons.get(tool_call_id) or "Rejected by operator"
            logger.warning("Rejecting previously denied tool call %s: %s", tool_call_id, reject_reason)
            return EventResult.deny(
                event=event,
                reason=f"ACTION_REJECTED: Operator rejected this action: {reject_reason}",
            )

        # If already approved by operator, release and proceed
        is_approved = tool_call_id in self._approved_ids or payload.get("hold_id") in self._approved_ids or payload.get("approval_id") in self._approved_ids
        if is_approved:
            logger.info("Proceeding with previously approved high-risk tool call %s", tool_call_id)
            return await next_fn(event)

        # Gate on R4, R5, or strict R3
        requires_hold = risk_level in (RiskLevel.R4, RiskLevel.R5) or (self._strict_network and risk_level == RiskLevel.R3)

        if requires_hold:
            impact = preview_impact(tool_name, tool_args)
            # The durable hold is the record an operator actually decides on, so
            # it is opened *before* the DEFER is returned: a hold that exists
            # only in this process is a hold a Gateway restart discards.
            durable, created = self.open_durable_hold(
                tool_name=tool_name,
                tool_args=tool_args,
                run_id=run_id,
                tool_call_id=tool_call_id,
                risk_level=risk_level.value,
                reason=reason,
                impact=impact.to_dict(),
            )
            hold_id = durable.hold_id if durable is not None else f"hold_{uuid.uuid4().hex[:10]}"
            hold_record = {
                "hold_id": hold_id,
                "tool_call_id": tool_call_id,
                "event_id": event.event_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "risk_level": risk_level.value,
                "reason": reason,
                "details": details,
                "timestamp": time.time(),
                "durable": durable is not None,
            }
            self._held_actions[hold_id] = hold_record

            # Render UI Card
            card_id = ctx.ui.render_card(
                {
                    "id": hold_id,
                    "title": f"Security Gate Hold: {risk_level.value} Risk",
                    "description": reason,
                    "preview": details.get("preview", ""),
                    "tool_name": tool_name,
                    "requires_approval": True,
                    "impact": impact.to_dict(),
                }
            )

            logger.warning(
                "BlastRadiusGuardMod holding tool execution %s (Risk: %s, hold=%s, durable=%s): %s",
                tool_name,
                risk_level.value,
                hold_id,
                durable is not None,
                reason,
            )

            return EventResult.defer(
                event=event,
                reason=f"APPROVAL_REQUIRED: Tool '{tool_name}' triggered Risk {risk_level.value} ({reason})",
                response_payload={
                    "hold_id": hold_id,
                    "card_id": card_id,
                    "risk_level": risk_level.value,
                    "preview": details.get("preview", ""),
                    "impact": impact.to_dict(),
                    "durable": durable is not None,
                    "reused_existing_hold": not created,
                },
                metadata={"hold_record": hold_record},
            )

        # R0, R1, R2, normal R3 pass through
        return await next_fn(event)

    def _store_status(self, tool_name: str, tool_args: dict[str, Any], run_id: str, tool_call_id: str) -> tuple[str, Any]:
        """Resolve the durable hold state for this exact action.

        Any failure to read the store returns ``"unreadable"``, which the caller
        treats as no decision rather than as an approval: a store that cannot be
        read has not approved anything.
        """
        try:
            status, record = self.hold_store.status_for(tool_name, tool_args, run_id, tool_call_id)
        except Exception as exc:
            logger.error("BlastRadiusGuardMod could not read the hold store: %s", exc)
            return "unreadable", None
        mapping = {
            "proceed": "approved",
            "refuse": "rejected",
            "wait": "pending",
        }
        return mapping.get(str(getattr(status, "value", status)), "pending"), record

    def open_durable_hold(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        run_id: str,
        tool_call_id: str,
        risk_level: str,
        reason: str,
        impact: dict[str, Any] | None = None,
    ) -> Any:
        """Persist a hold so a decision survives a restart and another process."""
        try:
            return self.hold_store.open_hold(
                tool_name=tool_name,
                tool_args=tool_args,
                run_id=run_id,
                tool_call_id=tool_call_id,
                risk_level=risk_level,
                reason=reason,
                impact=impact,
            )
        except Exception as exc:
            logger.error("BlastRadiusGuardMod could not persist hold: %s", exc)
            return None

    def approve(self, hold_id: str) -> bool:
        """Operator approves a held high-risk action."""
        record = self._held_actions.pop(hold_id, None)
        if record:
            tool_call_id = record["tool_call_id"]
            self._approved_ids.add(tool_call_id)
            self._approved_ids.add(hold_id)
            try:
                self.hold_store.approve(hold_id, operator="local")
            except Exception as exc:
                logger.error("Approval of %s is process-local only: %s", hold_id, exc)
            logger.info("Operator approved held action %s (tool_call_id=%s)", hold_id, tool_call_id)
            return True
        # A hold recorded durably but not held in this memory still resolves, so
        # an approval from another process releases the action it named.
        durable = self.hold_store.get(hold_id)
        if durable is not None:
            self.hold_store.approve(hold_id, operator="local")
            self._approved_ids.add(durable.tool_call_id)
            self._approved_ids.add(hold_id)
            return True
        return False

    def reject(self, hold_id: str, reason: str = "Rejected by operator") -> bool:
        """Operator rejects a held high-risk action."""
        record = self._held_actions.pop(hold_id, None)
        if record:
            tool_call_id = record["tool_call_id"]
            self._rejected_ids.add(tool_call_id)
            self._rejected_ids.add(hold_id)
            self._rejected_reasons[tool_call_id] = reason
            try:
                self.hold_store.reject(hold_id, operator="local", reason=reason)
            except Exception as exc:
                logger.error("Rejection of %s is process-local only: %s", hold_id, exc)
            logger.info("Operator rejected held action %s: %s", hold_id, reason)
            return True
        durable = self.hold_store.get(hold_id)
        if durable is not None:
            self.hold_store.reject(hold_id, operator="local", reason=reason)
            self._rejected_ids.add(durable.tool_call_id)
            self._rejected_reasons[durable.tool_call_id] = reason
            return True
        return False

    def cleanup_expired_holds(self, max_age_seconds: float = 3600.0) -> int:
        """Remove held actions that have exceeded their time-to-live."""
        now = time.time()
        expired = [hid for hid, rec in self._held_actions.items() if now - rec.get("timestamp", now) > max_age_seconds]
        for hid in expired:
            self._held_actions.pop(hid, None)
        return len(expired)

    def list_held_actions(self) -> list[dict[str, Any]]:
        return list(self._held_actions.values())
