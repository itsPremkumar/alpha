"""Typed failure-reason codes for bot turns and DM relay replies.

A closed vocabulary of machine-readable reason codes carried ALONGSIDE the
free-text error fields (additive — old consumers keep working). Mirrors the
Hermes bot-failure taxonomy: platform-side codes come from the transport
layer, agent-side codes are derived from raw provider error text.
"""

from __future__ import annotations

import re

# platform-side (assigned by the transport/relay layer)
RUNTIME_OFFLINE = "runtime_offline"
QUEUED_EXPIRED = "queued_expired"
DELIVERY_TIMEOUT = "delivery_timeout"
AGENT_BLOCKED = "agent_blocked"
CANCELLED = "cancelled"

# agent-side (derived from raw agent/provider error text)
PROVIDER_AUTH_OR_ACCESS = "provider_auth_or_access"
PROVIDER_QUOTA_LIMIT = "provider_quota_limit"
PROVIDER_RATE_LIMIT = "provider_rate_limit"
PROVIDER_SERVER_ERROR = "provider_server_error"
CONTEXT_OVERFLOW = "context_overflow"
MISSING_CONFIG = "missing_config"
MODEL_UNAVAILABLE = "model_unavailable"
UNKNOWN = "unknown"

ALL_REASONS = frozenset(
    {
        RUNTIME_OFFLINE,
        QUEUED_EXPIRED,
        DELIVERY_TIMEOUT,
        AGENT_BLOCKED,
        CANCELLED,
        PROVIDER_AUTH_OR_ACCESS,
        PROVIDER_QUOTA_LIMIT,
        PROVIDER_RATE_LIMIT,
        PROVIDER_SERVER_ERROR,
        CONTEXT_OVERFLOW,
        MISSING_CONFIG,
        MODEL_UNAVAILABLE,
        UNKNOWN,
    }
)

#: Reasons a supervisor may retry automatically without human intervention.
AUTO_RETRYABLE = frozenset({RUNTIME_OFFLINE, DELIVERY_TIMEOUT, PROVIDER_RATE_LIMIT, PROVIDER_SERVER_ERROR})


def is_auto_retryable(reason: str) -> bool:
    return reason in AUTO_RETRYABLE


# Classifier precedence: auth outranks quota by design — real provider 401
# bodies often say "invalid, blocked or out of funds".
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (PROVIDER_AUTH_OR_ACCESS, ("401", "403", "unauthorized", "forbidden", "invalid api key", "invalid_api_key", "access denied", "permission denied")),
    (PROVIDER_QUOTA_LIMIT, ("quota", "out of funds", "insufficient funds", "billing", "credit balance")),
    (PROVIDER_RATE_LIMIT, ("429", "rate limit", "rate_limit", "too many requests", "throttl")),
    (PROVIDER_SERVER_ERROR, ("500", "502", "503", "504", "server error", "overloaded", "try again later")),
    (CONTEXT_OVERFLOW, ("context length", "context_length", "too many tokens", "max tokens", "context overflow")),
    (MODEL_UNAVAILABLE, ("model not found", "model_not_found", "model unavailable", "no such model")),
    (MISSING_CONFIG, ("missing config", "not configured", "no api key", "api key missing", "no credentials")),
)


def classify_agent_error(error_text: str | None) -> str:
    """Derive a machine-readable reason from raw agent/provider error text."""
    text = (error_text or "").lower()
    if not text.strip():
        return UNKNOWN
    for reason, needles in _RULES:
        if any(n in text for n in needles):
            return reason
    return UNKNOWN


_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_valid_agent_name(name: str) -> bool:
    return bool(_AGENT_NAME_RE.match(name or ""))
