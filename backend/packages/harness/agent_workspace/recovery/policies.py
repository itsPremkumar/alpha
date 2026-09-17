"""Bounded run-recovery policies per failure class.

Complements the watchdog worker recovery (`supervision.recovery`) at the run
level: each failure class gets a retry budget, backoff, and a terminal
strategy. Exhaustion escalates instead of looping forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureClass = Literal["model_timeout", "model_rate_limit", "tool_failure", "tool_timeout", "context_overflow", "container_crash", "unknown"]
Strategy = Literal["retry", "fallback_model", "replan", "restore_checkpoint", "escalate", "abort"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: tuple[float, ...]
    terminal_strategy: Strategy


POLICIES: dict[FailureClass, RetryPolicy] = {
    "model_timeout": RetryPolicy(max_attempts=3, backoff_seconds=(2.0, 8.0, 30.0), terminal_strategy="fallback_model"),
    "model_rate_limit": RetryPolicy(max_attempts=4, backoff_seconds=(5.0, 15.0, 45.0, 120.0), terminal_strategy="fallback_model"),
    "tool_failure": RetryPolicy(max_attempts=2, backoff_seconds=(1.0, 5.0), terminal_strategy="replan"),
    "tool_timeout": RetryPolicy(max_attempts=2, backoff_seconds=(2.0, 10.0), terminal_strategy="replan"),
    "context_overflow": RetryPolicy(max_attempts=1, backoff_seconds=(0.0,), terminal_strategy="restore_checkpoint"),
    "container_crash": RetryPolicy(max_attempts=2, backoff_seconds=(3.0, 15.0), terminal_strategy="escalate"),
    "unknown": RetryPolicy(max_attempts=1, backoff_seconds=(2.0,), terminal_strategy="escalate"),
}


def classify_failure(error: str) -> FailureClass:
    text = (error or "").lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "model_rate_limit"
    if ("timeout" in text or "timed out" in text) and "tool" in text:
        return "tool_timeout"
    if "timeout" in text or "timed out" in text:
        return "model_timeout"
    if "context" in text and ("length" in text or "overflow" in text or "too long" in text or "tokens" in text):
        return "context_overflow"
    if "container" in text or "sandbox" in text and ("crash" in text or "died" in text or "lost" in text):
        return "container_crash"
    if "tool" in text and ("fail" in text or "error" in text):
        return "tool_failure"
    return "unknown"


@dataclass(frozen=True)
class RecoveryDecision:
    action: Strategy
    attempt: int
    wait_seconds: float
    reason: str


def decide(error: str, *, attempt: int) -> RecoveryDecision:
    """Decide the next step for attempt N (1-based) after a failure."""
    failure = classify_failure(error)
    policy = POLICIES[failure]
    if attempt <= policy.max_attempts:
        wait = policy.backoff_seconds[min(attempt - 1, len(policy.backoff_seconds) - 1)]
        return RecoveryDecision(action="retry", attempt=attempt, wait_seconds=wait, reason=f"{failure}: retry {attempt}/{policy.max_attempts}")
    return RecoveryDecision(action=policy.terminal_strategy, attempt=attempt, wait_seconds=0.0, reason=f"{failure}: budget exhausted -> {policy.terminal_strategy}")


# --- Bot-turn retry policy -------------------------------------------------
# A retried bot turn NEVER mints a fresh session. Transient classes resume
# as-is; context overflow runs compression first (the one sanctioned context
# mutation); auth/quota/config/model/unknown are never auto-retried — a retry
# cannot fix them and only burns quota.

BOT_RETRY_RESUME = "resume"
BOT_RETRY_COMPRESS_THEN_RESUME = "compress_then_resume"
BOT_RETRY_NONE = "none"


def bot_turn_retry_action(error: str, *, failure_reason: str | None = None) -> str:
    """Map a bot-turn failure to resume | compress_then_resume | none.

    Accepts reason codes from either vocabulary (recovery classes like
    ``model_rate_limit`` or bot codes like ``provider_rate_limit``) or raw
    error text, which is classified directly.
    """
    from agent_workspace.bots.failure_reasons import (
        ALL_REASONS,
        CONTEXT_OVERFLOW,
        MISSING_CONFIG,
        MODEL_UNAVAILABLE,
        PROVIDER_AUTH_OR_ACCESS,
        PROVIDER_QUOTA_LIMIT,
        PROVIDER_RATE_LIMIT,
        PROVIDER_SERVER_ERROR,
        RUNTIME_OFFLINE,
        classify_agent_error,
        is_auto_retryable,
    )

    _RECOVERY_TO_BOT = {
        "model_rate_limit": PROVIDER_RATE_LIMIT,
        "model_timeout": RUNTIME_OFFLINE,
        "tool_timeout": RUNTIME_OFFLINE,
        "tool_failure": PROVIDER_SERVER_ERROR,
        "context_overflow": CONTEXT_OVERFLOW,
        "container_crash": RUNTIME_OFFLINE,
        "unknown": "unknown",
    }
    reason = failure_reason
    if reason is not None and reason not in ALL_REASONS:
        reason = _RECOVERY_TO_BOT.get(reason, "unknown")
    if reason is None:
        reason = classify_agent_error(error)
    if reason == CONTEXT_OVERFLOW:
        return BOT_RETRY_COMPRESS_THEN_RESUME
    if is_auto_retryable(reason):
        return BOT_RETRY_RESUME
    if reason in (PROVIDER_AUTH_OR_ACCESS, PROVIDER_QUOTA_LIMIT, MISSING_CONFIG, MODEL_UNAVAILABLE):
        return BOT_RETRY_NONE
    text = (error or "").lower()
    if "timeout" in text or "timed out" in text or "temporarily" in text:
        return BOT_RETRY_RESUME
    return BOT_RETRY_NONE
