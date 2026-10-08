"""VerificationEvidenceGateMod — claim honesty and acceptance criteria enforcement gate."""

from __future__ import annotations

import logging
import re
import time

from alpha.mods.context import CapabilityContext
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)

# Pattern detecting completion claims in assistant messages
_SUCCESS_CLAIM_PATTERNS = [
    re.compile(r"\b(i\s+have\s+(fixed|resolved|completed|implemented|solved))\b", re.IGNORECASE),
    re.compile(r"\b(all\s+tests\s+(pass|passed|are\s+passing))\b", re.IGNORECASE),
    re.compile(r"\b(task\s+(is\s+)?(complete|completed|finished))\b", re.IGNORECASE),
    re.compile(r"\b(successfully\s+(updated|verified|resolved|passed))\b", re.IGNORECASE),
]


class VerificationEvidenceGateMod:
    """Claim honesty enforcer that intercepts completion claims and demands verifiable receipts.

    Alpha's canonical invariant: A task or run is NEVER verified based on model assertions alone;
    it requires measured, verifiable execution receipts (e.g. pytest exit code 0, linter passes).
    """

    name = "verification_evidence_gate"
    version = "1.0.0"
    priority = int(ModPriority.VERIFICATION)
    required_capabilities = {"evidence:read", "evidence:record"}
    subscribed_events = {
        "turn.complete",
        "task.completion_requested",
        "mission.completion_requested",
        "tool.completed",
    }

    def __init__(self, *, strict_mode: bool = True):
        self._strict_mode = strict_mode

    def _claims_success(self, event: AlphaEvent) -> bool:
        """Detect whether the turn or completion request asserts success/completion."""
        payload = event.payload
        # For task and mission completion requests, treating as completion claim unless explicit failure
        if event.name in {"task.completion_requested", "mission.completion_requested"}:
            status = str(payload.get("status") or "").lower()
            if status not in {"failed", "cancelled", "aborted", "error"}:
                return True

        # Explicit status flag
        status = str(payload.get("status") or "").lower()
        if status in {"success", "completed", "done", "finished"}:
            return True

        # Textual assertions in messages or final output
        text = ""
        if "message" in payload:
            text = str(payload["message"])
        elif "output" in payload:
            text = str(payload["output"])
        elif "messages" in payload:
            msgs = payload["messages"]
            if isinstance(msgs, list) and msgs:
                last_msg = msgs[-1]
                text = str(getattr(last_msg, "content", last_msg))

        if text:
            for pat in _SUCCESS_CLAIM_PATTERNS:
                if pat.search(text):
                    return True
        return False

    def _has_valid_evidence_receipt(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
    ) -> tuple[bool, str]:
        """Validate whether the correlation context has an authentic, passing execution receipt."""
        if not event.correlation.run_id:
            return False, "No run identity is available to bind verification evidence."
        receipts = ctx.evidence.get_by_correlation(event.correlation)

        for rcpt in receipts:
            # Prevent circular self-reinforcement: acceptance verdicts are not execution receipts
            if rcpt.get("kind") == "acceptance_verdict":
                continue

            receipt_correlation = rcpt.get("correlation")
            if not isinstance(receipt_correlation, dict) or receipt_correlation.get("run_id") != event.correlation.run_id:
                continue
            # Check test outcome
            if "exit_code" in rcpt and rcpt["exit_code"] == 0:
                return True, f"Found valid test execution receipt {rcpt['id']} (exit_code=0)"
            if rcpt.get("status") in {"passed", "verified", "success"}:
                return True, f"Found passing verification receipt {rcpt['id']}"
            if "test_results" in rcpt:
                res = rcpt["test_results"]
                if isinstance(res, dict) and res.get("failed", 0) == 0 and res.get("passed", 0) > 0:
                    return True, f"Found passing test suite receipt {rcpt['id']}"

        return False, "No valid test execution receipt found with passing exit code or verified status."

    def _capture_tool_evidence(self, ctx: CapabilityContext, event: AlphaEvent) -> None:
        """Extract and persist verified receipts from test or command executions."""
        payload = event.payload
        tool_name = str(payload.get("tool_name") or "")
        content = str(payload.get("content") or "")
        status = str(payload.get("status") or "success")
        tool_args = payload.get("tool_args") or {}
        cmd = str(tool_args.get("CommandLine") or tool_args.get("command") or tool_args.get("cmd") or "")

        # Check if tool ran tests
        is_test_tool = any(k in tool_name.lower() for k in ("test", "pytest", "jest", "vitest"))
        is_test_cmd = any(k in cmd.lower() for k in ("pytest", "unittest", "npm test", "pnpm test", "cargo test", "go test", "ctest"))

        if is_test_tool or is_test_cmd:
            exit_code = payload.get("exit_code")
            if exit_code is None and "exit code: 0" in content.lower():
                exit_code = 0

            passed_match = re.search(r"(\d+)\s+passed", content, re.IGNORECASE)
            failed_match = re.search(r"(\d+)\s+failed", content, re.IGNORECASE)
            failed_count = int(failed_match.group(1)) if failed_match else 0
            passed_count = int(passed_match.group(1)) if passed_match else 0

            is_passing = (status == "success" and failed_count == 0 and (passed_count > 0 or exit_code == 0)) or (exit_code == 0 and failed_count == 0)

            if is_passing:
                ctx.evidence.record(
                    {
                        "kind": "test",
                        "status": "passed",
                        "exit_code": 0 if exit_code is None else exit_code,
                        "test_results": {"passed": passed_count, "failed": failed_count},
                        "tool_name": tool_name,
                        "command": cmd,
                        "summary": f"Passing test suite execution ({passed_count} passed)",
                    },
                    correlation=event.correlation,
                )

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        if event.name == "tool.completed":
            self._capture_tool_evidence(ctx, event)
            return await next_fn(event)

        if not self._claims_success(event):
            return await next_fn(event)

        has_evidence, evidence_reason = self._has_valid_evidence_receipt(ctx, event)

        if has_evidence:
            logger.info("VerificationEvidenceGateMod approved completion: %s", evidence_reason)
            # Record explicit verification gate seal
            ctx.evidence.record(
                {
                    "kind": "acceptance_verdict",
                    "status": "VERIFIED",
                    "reason": evidence_reason,
                    "verified_at": time.time(),
                },
                correlation=event.correlation,
            )
            return await next_fn(event)

        # Evidence missing! Intercept and demand proof!
        remediation_prompt = (
            "Verification denied: acceptance criteria lacks measured execution evidence. "
            "You claimed task completion or that tests pass, but no authenticated execution receipt "
            "was recorded. Run the tests or execute verification before completing the task."
        )
        logger.warning(
            "VerificationEvidenceGateMod intercepted unverified completion for run %s: %s",
            event.correlation.run_id,
            evidence_reason,
        )

        mutated_payload = dict(event.payload)
        mutated_payload["remediation_required"] = True
        mutated_payload["remediation_prompt"] = remediation_prompt
        mutated_event = event.copy(payload=mutated_payload)

        return EventResult.rewrite(
            mutated_event=mutated_event,
            reason=f"EVIDENCE_GATE_DENIAL: {evidence_reason}",
            metadata={
                "gate": self.name,
                "remediation_prompt": remediation_prompt,
                "unverified_reason": evidence_reason,
            },
        )
