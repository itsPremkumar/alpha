"""Failure Sentinel Mod (FailureSentinelMod).

Provides intelligent failure fingerprinting, anti-loop detection, and auto-repair routing
across bot actions, tool executions, and turn transitions.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.bots.failure_reasons import (
    classify_agent_error,
    failure_class,
    is_auto_retryable,
)
from alpha.mods.context import CapabilityContext
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)


@dataclass
class FailureRecord:
    fingerprint: str
    error_message: str
    reason_code: str
    failure_class: str
    tool_name: str | None
    bot_name: str | None
    timestamp: float = field(default_factory=time.time)
    attempt: int = 1
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailureSentinelMod:
    """Intelligent failure sentinels and auto-repair routing mod.

    Intercepts error and tool completion events:
    1. Computes deterministic failure fingerprints from normalized error text and context.
    2. Detects repetitive failure loops (anti-loop safeguard).
    3. Triggers intelligent auto-repair routing (retry with exponential backoff for transient
       failures, escalation/reassignment for capability failures, deny for permanent failures).
    4. Durably records failure receipts in the evidence ledger.
    """

    name: str = "failure_sentinel"
    version: str = "1.0.0"
    priority: int = int(ModPriority.RECOVERY)
    required_capabilities: set[str] = {"evidence:record", "clock:schedule"}
    subscribed_events: set[str] = {
        "tool.completed",
        "turn.failed",
        "bot.turn_failed",
        "task.failed",
        "bot.failed",
        "error.occurred",
    }

    def __init__(
        self,
        *,
        loop_threshold: int = 3,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        history_window_seconds: float = 1800.0,
        max_history_entries: int = 200,
    ):
        self.loop_threshold = max(2, loop_threshold)
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.history_window_seconds = history_window_seconds
        self.max_history_entries = max_history_entries

        self._history: list[FailureRecord] = []
        self._fingerprint_counts: dict[str, int] = {}
        self._active_loops: set[str] = set()
        self._lock = threading.RLock()

    # -------------------------------------------------------------------------
    # Query & Diagnostic API
    # -------------------------------------------------------------------------

    def get_recent_failures(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._history[-limit:]]

    def get_fingerprint_count(self, fingerprint: str) -> int:
        with self._lock:
            return self._fingerprint_counts.get(fingerprint, 0)

    def get_active_loops(self) -> list[str]:
        with self._lock:
            return sorted(list(self._active_loops))

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
            self._fingerprint_counts.clear()
            self._active_loops.clear()

    # -------------------------------------------------------------------------
    # Failure Fingerprinting
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_error(error_text: str) -> str:
        """Strip transient memory addresses, timestamps, and temp paths for stable hashing."""
        text = str(error_text or "").strip()
        # Strip hex memory addresses (e.g. 0x7f8a9b1c)
        text = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", text)
        # Strip UUIDs
        text = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "UUID", text)
        # Strip ISO timestamps
        text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "TIMESTAMP", text)
        # Strip line numbers
        text = re.sub(r"line \d+", "line N", text)
        return text.lower()[:500]

    def compute_fingerprint(
        self,
        error_text: str,
        *,
        tool_name: str | None = None,
        bot_name: str | None = None,
    ) -> str:
        """Compute a deterministic 16-character SHA-256 fingerprint."""
        norm_text = self.normalize_error(error_text)
        tool = (tool_name or "").lower().strip()
        bot = (bot_name or "").lower().strip()
        raw = f"{bot}:{tool}:{norm_text}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"fp_{digest}"

    # -------------------------------------------------------------------------
    # Mod Handler Pipeline
    # -------------------------------------------------------------------------

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        payload = event.payload

        # For tool.completed, only inspect if it represents a failure
        if event.name == "tool.completed":
            is_failure = payload.get("status") in ("error", "failed") or int(payload.get("exit_code") or 0) != 0 or bool(payload.get("error"))
            if not is_failure:
                return await next_fn(event)

        # Extract error indicators
        error_msg = str(payload.get("error") or payload.get("message") or payload.get("content") or "Unknown runtime fault")
        tool_name = payload.get("tool_name")
        bot_name = payload.get("bot_name") or event.correlation.agent_id

        # 1. Classify error
        reason_code = payload.get("reason_code") or classify_agent_error(error_msg)
        cls = failure_class(reason_code)
        fp = self.compute_fingerprint(error_msg, tool_name=tool_name, bot_name=bot_name)

        now = time.time()
        attempt = int(event.correlation.attempt or 1)

        # 2. Record failure in sliding window
        record = FailureRecord(
            fingerprint=fp,
            error_message=error_msg[:300],
            reason_code=reason_code,
            failure_class=cls,
            tool_name=tool_name,
            bot_name=bot_name,
            timestamp=now,
            attempt=attempt,
            correlation_id=event.correlation.run_id,
        )

        with self._lock:
            # Prune old history outside window
            cutoff = now - self.history_window_seconds
            self._history = [r for r in self._history if r.timestamp >= cutoff]

            self._history.append(record)
            if len(self._history) > self.max_history_entries:
                self._history.pop(0)

            # Recompute count in window for this fingerprint
            count = sum(1 for r in self._history if r.fingerprint == fp)
            self._fingerprint_counts[fp] = count

            # 3. Anti-Loop Detection
            if count >= self.loop_threshold:
                self._active_loops.add(fp)
                logger.error(
                    "FailureSentinelMod: ANTI-LOOP TRIGGERED for fingerprint '%s' (seen %d times >= threshold %d): %s",
                    fp,
                    count,
                    self.loop_threshold,
                    error_msg[:120],
                )

                # Durably record anti-loop receipt
                ctx.evidence.record(
                    {
                        "kind": "sentinel_anti_loop_triggered",
                        "fingerprint": fp,
                        "loop_count": count,
                        "tool_name": tool_name,
                        "bot_name": bot_name,
                        "reason": reason_code,
                        "error_sample": error_msg[:200],
                    },
                    correlation=event.correlation,
                )

                # Halt infinite retry loop -> escalate
                return EventResult.escalate(
                    event,
                    reason=(f"ANTI_LOOP_TRIGGERED: Repeated identical failure {count} times (fingerprint '{fp}'). Breaking failure loop to prevent infinite execution burn."),
                    metadata={
                        "anti_loop": True,
                        "fingerprint": fp,
                        "count": count,
                        "reason_code": reason_code,
                        "suggested_action": "escalate_to_supervisor_or_human",
                    },
                )

        # 4. Auto-Repair Routing (when not in a detected loop)
        # Transient failure -> Retry with exponential backoff
        if is_auto_retryable(reason_code) or cls == "transient":
            delay = min(self.max_backoff_seconds, self.initial_backoff_seconds * (2 ** max(0, attempt - 1)))
            logger.info(
                "FailureSentinelMod: transient failure '%s' (attempt %d). Scheduling retry in %.1fs",
                reason_code,
                attempt,
                delay,
            )
            return EventResult.retry(
                event,
                reason=f"TRANSIENT_FAILURE: Auto-retryable error ({reason_code}). Backing off for {delay:.1f}s",
                metadata={"retry_delay": delay, "attempt": attempt + 1, "fingerprint": fp},
            )

        # Capability / Blocked failure -> Escalate / Reassign
        if cls == "capability":
            logger.warning(
                "FailureSentinelMod: capability missing '%s' on bot '@%s'. Suggesting reassignment",
                reason_code,
                bot_name,
            )
            return EventResult.escalate(
                event,
                reason=f"CAPABILITY_FAILURE: Bot '@{bot_name}' lacks capability for this action ({reason_code})",
                metadata={"auto_repair": "reassign", "action": "reassign_bot", "fingerprint": fp},
            )

        # Permanent unrecoverable failure -> Deny
        if cls == "permanent":
            logger.error("FailureSentinelMod: permanent failure '%s': %s", reason_code, error_msg[:120])
            return EventResult.deny(
                event,
                reason=f"PERMANENT_FAILURE: Unrecoverable error ({reason_code}): {error_msg[:150]}",
                metadata={"permanent": True, "fingerprint": fp},
            )

        return await next_fn(event)
