"""Reasoning Governor dynamically modulating thinking tokens and model effort."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Literal

ReasoningTier = Literal["fast", "balanced", "deep"]


@dataclass
class ReasoningConfig:
    tier: ReasoningTier
    thinking_budget_tokens: int
    temperature: float
    timeout_seconds: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "thinking_budget_tokens": self.thinking_budget_tokens,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "reason": self.reason,
        }

    def clamp_to_budget(
        self,
        *,
        model_context_window: int | None = None,
        token_budget_remaining: int | None = None,
        model_supports_thinking: bool = True,
    ) -> ReasoningConfig:
        """Clamp thinking budget to model + run-budget reality.

        Never raises: clamping is fail-safe so a misconfigured budget cannot
        break the run. Non-thinking models always collapse to 0 thinking.
        """
        budget = self.thinking_budget_tokens
        if not model_supports_thinking:
            budget = 0
        if model_context_window is not None and model_context_window > 0:
            # Reserve at least half the window for input + output outside thinking.
            budget = min(budget, max(0, model_context_window // 4))
        if token_budget_remaining is not None and token_budget_remaining >= 0:
            budget = min(budget, max(0, token_budget_remaining // 2))
        return ReasoningConfig(
            tier=self.tier,
            thinking_budget_tokens=int(budget),
            temperature=self.temperature,
            timeout_seconds=self.timeout_seconds,
            reason=self.reason,
        )


class ReasoningGovernor:
    """Modulates thinking token allocation based on task complexity heuristics."""

    TIER_PRESETS: dict[ReasoningTier, dict[str, float | int]] = {
        "fast": {
            "thinking_budget_tokens": 0,
            "temperature": 0.1,
            "timeout_seconds": 15.0,
        },
        "balanced": {
            "thinking_budget_tokens": 4000,
            "temperature": 0.5,
            "timeout_seconds": 60.0,
        },
        "deep": {
            "thinking_budget_tokens": 16000,
            "temperature": 0.7,
            "timeout_seconds": 180.0,
        },
    }

    # Commands or quick questions that need zero reasoning overhead
    FAST_PREFIXES = [
        re.compile(r"^\s*(ls|dir|pwd|status|git status|check|echo|cat|grep|view|read|find)\b", re.IGNORECASE),
        re.compile(r"\b(fix typo|format json|lint check)\b", re.IGNORECASE),
    ]

    DEEP_PATTERNS = [
        re.compile(
            r"\b(architect|redesign|refactor|optimize|security audit|concurrency|race condition)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(complex|multi-threaded|distributed|consensus|in-depth|reasoning)\b", re.IGNORECASE),
    ]

    # Fast mode must never swallow risky work even when the wording is short.
    _RISK_GUARD_RE = re.compile(
        r"\b(prod|production|critical|security|auth|finance|deploy|release|migration|delete|drop|sudo|rm\s+-rf)\b",
        re.IGNORECASE,
    )
    _IMPLEMENTATION_VERB_RE = re.compile(r"\b(add|create|implement|build|write|develop|migrate|deploy)\b", re.IGNORECASE)

    def __init__(self, default_tier: ReasoningTier = "balanced"):
        self.default_tier = default_tier
        self._lock = threading.Lock()
        self._manual_override: ReasoningTier | None = None

    @property
    def manual_override(self) -> ReasoningTier | None:
        with self._lock:
            return self._manual_override

    @manual_override.setter
    def manual_override(self, value: ReasoningTier | None) -> None:
        with self._lock:
            self._manual_override = value

    def set_override(self, tier: ReasoningTier | None) -> None:
        with self._lock:
            self._manual_override = tier

    def _build(self, tier: ReasoningTier, reason: str) -> ReasoningConfig:
        preset = self.TIER_PRESETS[tier]
        return ReasoningConfig(
            tier=tier,
            thinking_budget_tokens=int(preset["thinking_budget_tokens"]),
            temperature=float(preset["temperature"]),
            timeout_seconds=float(preset["timeout_seconds"]),
            reason=reason,
        )

    def evaluate(self, prompt: str) -> ReasoningConfig:
        """Evaluate prompt and determine optimal reasoning tier and token budget."""
        with self._lock:
            override = self._manual_override
        if override:
            return self._build(override, f"Manual override active: {override}")

        text = prompt.strip()

        # 1. Check Deep patterns (risk guard cannot demote deep work)
        for p in self.DEEP_PATTERNS:
            if p.search(text):
                return self._build("deep", "Complex architecture / deep reasoning pattern matched.")

        # 2. Check Fast prefix / commands (only if short, non-implementation, non-risky)
        if len(text) < 100 and not self._IMPLEMENTATION_VERB_RE.search(text) and not self._RISK_GUARD_RE.search(text):
            for p in self.FAST_PREFIXES:
                if p.search(text):
                    return self._build("fast", "Lightweight fast-mode pattern matched (0 thinking overhead).")

        # 3. Default balanced
        return self._build(self.default_tier, "Standard balanced reasoning profile.")

    def evaluate_with_budget(
        self,
        prompt: str,
        *,
        model_context_window: int | None = None,
        token_budget_remaining: int | None = None,
        model_supports_thinking: bool = True,
    ) -> ReasoningConfig:
        """Evaluate then clamp to model context + remaining run budget."""
        return self.evaluate(prompt).clamp_to_budget(
            model_context_window=model_context_window,
            token_budget_remaining=token_budget_remaining,
            model_supports_thinking=model_supports_thinking,
        )


_global_governor = ReasoningGovernor()
_global_lock = threading.Lock()


def get_reasoning_governor() -> ReasoningGovernor:
    return _global_governor


def reset_global_governor(default_tier: ReasoningTier = "balanced") -> ReasoningGovernor:
    """Test/reset helper: replace the process-global governor cleanly."""
    global _global_governor
    with _global_lock:
        _global_governor = ReasoningGovernor(default_tier=default_tier)
        return _global_governor
