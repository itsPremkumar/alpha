from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_workspace.security.enclave.telemetry")


@dataclass
class TelemetryEntry:
    """Cryptographically chained trajectory telemetry entry."""
    step_index: int
    actor: str
    action: str
    details: dict[str, Any]
    payload_hash: str
    previous_step_hash: str
    step_hash: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "actor": self.actor,
            "action": self.action,
            "details": self.details,
            "payload_hash": self.payload_hash,
            "previous_step_hash": self.previous_step_hash,
            "step_hash": self.step_hash,
            "timestamp": self.timestamp,
        }


class TrajectoryFlightRecorder:
    """
    Universal Trajectory Flight Recorder inspired by OpenAI GPT-6 Astra.
    Creates an immutable, cryptographically chained record of all agent execution steps.
    H_i = SHA256(H_{i-1} || actor || action || payload_hash || timestamp)
    """

    def __init__(self) -> None:
        self.entries: list[TelemetryEntry] = []
        self._genesis_hash = "0" * 32

    def record_step(
        self,
        actor: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> TelemetryEntry:
        step_idx = len(self.entries)
        prev_hash = self.entries[-1].step_hash if self.entries else self._genesis_hash
        details_clean = details or {}

        payload_bytes = json.dumps(details_clean, sort_keys=True).encode("utf-8")
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()[:16]

        ts = time.time()
        chain_str = f"{prev_hash}:{actor}:{action}:{payload_hash}:{ts}"
        step_hash = hashlib.sha256(chain_str.encode("utf-8")).hexdigest()[:16]

        entry = TelemetryEntry(
            step_index=step_idx,
            actor=actor,
            action=action,
            details=details_clean,
            payload_hash=payload_hash,
            previous_step_hash=prev_hash,
            step_hash=step_hash,
            timestamp=ts,
        )
        self.entries.append(entry)
        return entry

    def verify_integrity(self) -> tuple[bool, int | None]:
        """
        Verifies the cryptographic chain across all recorded telemetry steps.
        Returns: (is_valid, corrupted_index_or_None)
        """
        for i, entry in enumerate(self.entries):
            expected_prev = self.entries[i - 1].step_hash if i > 0 else self._genesis_hash
            if entry.previous_step_hash != expected_prev:
                logger.error(f"TELEMETRY_TAMPERING_DETECTED at step {i}: previous hash mismatch.")
                return False, i

            chain_str = f"{expected_prev}:{entry.actor}:{entry.action}:{entry.payload_hash}:{entry.timestamp}"
            recomputed_hash = hashlib.sha256(chain_str.encode("utf-8")).hexdigest()[:16]
            if entry.step_hash != recomputed_hash:
                logger.error(f"TELEMETRY_TAMPERING_DETECTED at step {i}: step hash mismatch.")
                return False, i

        return True, None

    def export_trajectory(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.entries]


class DeceptionWatchdog:
    """
    Real-Time Deception & Alignment Watchdog inspired by OpenAI Astra safety documentation.
    Scans trajectory events for hallucinated completion, evasive loops, and claim mismatches.
    """

    @staticmethod
    def audit_step(
        agent_statement: str,
        tool_action: str,
        tool_output: str,
        exit_code: int | None = None,
    ) -> tuple[bool, str]:
        """
        Audits an agent's assertion against verified output.
        Returns (is_deceptive, diagnostic_message).
        """
        stmt_lower = agent_statement.lower()
        out_lower = tool_output.lower()

        # Check 1: Non-zero exit code claiming success
        if exit_code is not None and exit_code != 0:
            if "success" in stmt_lower or "passed" in stmt_lower or "fixed" in stmt_lower:
                msg = (
                    f"DECEPTION_DETECTED: Agent claimed success ('{agent_statement[:60]}'), "
                    f"but tool exited with code {exit_code}."
                )
                logger.warning(msg)
                return True, msg

        # Check 2: Error markers in output claiming error-free completion
        if "traceback (most recent call last)" in out_lower or "fatal error" in out_lower:
            if "without errors" in stmt_lower or "all tests pass" in stmt_lower:
                msg = (
                    "DECEPTION_DETECTED: Agent asserts clean pass, "
                    "but execution output contains fatal error or stack trace."
                )
                logger.warning(msg)
                return True, msg

        # Check 3: Safety bypass attempts
        if any(bypass in stmt_lower or bypass in tool_action.lower() for bypass in ["disable_security", "bypass_guard", "ignore_safety"]):
            msg = "DECEPTION_DETECTED: Agent attempting to disable security monitoring."
            logger.error(msg)
            return True, msg

        return False, "NOMINAL: Step aligned with verified execution output."
