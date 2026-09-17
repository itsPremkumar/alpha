"""Repetitive Tool-Loop Breaker and Guardrails inspired by OpenClaw."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_VOLATILE_FIELDS = {"timestamp", "time", "date", "duration", "pid", "session_id", "elapsed_ms", "created_at"}


def _normalize_payload(obj: Any) -> Any:
    """Recursively strip volatile fields (PIDs, timestamps, elapsed times)."""
    if isinstance(obj, dict):
        return {
            k: _normalize_payload(v)
            for k, v in obj.items()
            if k.lower() not in _VOLATILE_FIELDS
        }
    elif isinstance(obj, list):
        return [_normalize_payload(x) for x in obj]
    elif isinstance(obj, str):
        # Strip timestamps from text
        stripped = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "", obj)
        return stripped.strip()
    return obj


def hash_tool_call(tool_name: str, args: dict[str, Any], result: Any = None) -> str:
    """Compute a deterministic hash for a tool call ignoring volatile runtime metadata."""
    norm_args = _normalize_payload(args)
    norm_res = _normalize_payload(result) if result is not None else ""
    serialized = f"{tool_name.lower()}|{json.dumps(norm_args, sort_keys=True)}|{json.dumps(norm_res, sort_keys=True, default=str)}"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


@dataclass
class LoopIntervention:
    """Remediation intervention recommended by the loop detector."""

    action: str  # "pivot_strategy", "abort"
    reason: str
    loop_type: str = ""  # "duplicate_call", "failure_streak", "post_compaction_repeat"


@dataclass
class LoopDetectionResult:
    """Outcome of tool call loop detection."""

    is_loop: bool
    loop_type: str = ""  # "duplicate_call", "failure_streak", "post_compaction_repeat"
    recommendation: str = ""

    @property
    def action(self) -> str:
        return "pivot_strategy" if self.is_loop else "continue"

    @property
    def reason(self) -> str:
        return self.recommendation


class ToolLoopDetector:
    """Monitors rolling tool-call history to prevent infinite execution loops."""

    def __init__(
        self,
        consecutive_duplicate_limit: int = 3,
        failure_streak_limit: int = 4,
        post_compaction_guard: bool = True,
    ):
        self.consecutive_duplicate_limit = consecutive_duplicate_limit
        self.failure_streak_limit = failure_streak_limit
        self.post_compaction_guard_enabled = post_compaction_guard
        self._call_history: list[tuple[str, str, bool]] = []  # (tool_name, call_hash, success)
        self._compaction_recent: bool = False

    def notify_compaction(self) -> None:
        """Inform detector that context compaction occurred."""
        self._compaction_recent = True

    def record_and_evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any = None,
        success: bool = True,
    ) -> LoopDetectionResult:
        """Record tool call outcome and evaluate whether execution is stuck in a loop."""
        call_hash = hash_tool_call(tool_name, args, result)

        # 1. Post-compaction check
        if self.post_compaction_guard_enabled and self._compaction_recent and self._call_history:
            last_name, last_hash, last_succ = self._call_history[-1]
            if call_hash == last_hash and not success:
                self._compaction_recent = False
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="post_compaction_repeat",
                    recommendation="Immediate strategy pivot required: post-compaction repeated identical failed call.",
                )
        self._compaction_recent = False

        self._call_history.append((tool_name, call_hash, success))

        # 2. Consecutive identical call check
        if len(self._call_history) >= self.consecutive_duplicate_limit:
            recent_hashes = [h for _, h, _ in self._call_history[-self.consecutive_duplicate_limit :]]
            if len(set(recent_hashes)) == 1:
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="duplicate_call",
                    recommendation=f"Detected tool '{tool_name}' invoked {self.consecutive_duplicate_limit} consecutive times identically. Pivot strategy or query alternate tools.",
                )

        # 3. Failure streak check across same tool
        if not success and len(self._call_history) >= self.failure_streak_limit:
            recent_fails = self._call_history[-self.failure_streak_limit :]
            if all(name == tool_name and not s for name, _, s in recent_fails):
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="failure_streak",
                    recommendation=f"Tool '{tool_name}' failed with {self.failure_streak_limit} consecutive errors with no forward progress. Re-evaluate preconditions.",
                )

        return LoopDetectionResult(is_loop=False)

    def record_and_check(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any = None,
        success: bool | None = None,
    ) -> LoopIntervention | None:
        """Helper that automatically detects success from result and returns LoopIntervention if triggered."""
        if success is None:
            if isinstance(result, dict) and ("error" in result or "err" in result):
                success = False
            elif isinstance(result, str) and ("error:" in result.lower() or "traceback" in result.lower()):
                success = False
            else:
                success = True

        eval_res = self.record_and_evaluate(tool_name, args, result, success=success)
        if eval_res.is_loop:
            return LoopIntervention(
                action="pivot_strategy",
                reason=eval_res.recommendation,
                loop_type=eval_res.loop_type,
            )
        return None
