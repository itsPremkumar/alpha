"""L1 quota: per-user record + credit limits (local, standalone deployment).

Design provenance: check semantics (``memoryUsage + delta > memoryLimit``
=> ``memory_limit_exceeded``; ``creditUsage >= creditLimit`` =>
``credit_limit_exceeded``; negative limit = unlimited) and the credit formula
(``(input/1000*inputRate + cache/1000*cacheRate + output/1000*outputRate) *
modelMultiplier``) follow ``tencentdb-agent-memory``
``MemoryCore/src/core/quota/quota-manager.ts`` and ``credit-calculator.ts``
(MIT). Alpha runs standalone, so instead of the source's remote
``IQuotaReporter`` the usage counters live in a local ``quota.json`` under the
L1 root (the source's Noop-reporter deployment is the local equivalent).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from .paths import atomic_write_text, l1_root, quota_path

logger = logging.getLogger(__name__)

#: Credit rates (credits per 1k tokens) — the source's DEFAULT_RATES.
DEFAULT_INPUT_RATE = 1.0
DEFAULT_CACHE_RATE = 0.2
DEFAULT_OUTPUT_RATE = 4.0
#: Default model multiplier when the model is not in the table (source default).
DEFAULT_MODEL_MULTIPLIER = 1.0
#: Expensive models cost more credits (source's model multiplier table).
MODEL_MULTIPLIERS: dict[str, float] = {
    "gpt-4o": 15.0,
    "gpt-5": 15.0,
    "claude-4.5-sonnet": 15.0,
    "deepseek-v3.2": 0.8,
    "deepseek-v3": 0.8,
}


@dataclass(slots=True)
class QuotaCheck:
    """Outcome of one quota check (mirrors the source's QuotaCheckResult)."""

    allowed: bool
    reason: str = ""
    current: float = 0.0
    limit: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "current": self.current,
            "limit": self.limit,
        }


def calculate_credits(
    *,
    input_tokens: int = 0,
    cache_tokens: int = 0,
    output_tokens: int = 0,
    model: str | None = None,
) -> float:
    """Credits for one LLM call (source ``CreditCalculator.calculate``)."""
    multiplier = MODEL_MULTIPLIERS.get(model or "", DEFAULT_MODEL_MULTIPLIER)
    total = (
        (max(0, input_tokens) / 1000.0) * DEFAULT_INPUT_RATE
        + (max(0, cache_tokens) / 1000.0) * DEFAULT_CACHE_RATE
        + (max(0, output_tokens) / 1000.0) * DEFAULT_OUTPUT_RATE
    )
    return total * multiplier


def usage_from_response(response: Any, *, model: str | None = None) -> float:
    """Best-effort credits from an LLM response's token usage.

    Reads LangChain-style ``response.usage_metadata`` / ``response.response_metadata.token_usage``
    or a plain dict; returns 0.0 when the response carries no usage (never
    invents a number).
    """
    usage: Any = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        usage = getattr(response, "response_metadata", None)
        usage = usage.get("token_usage") if isinstance(usage, dict) else None
    if not isinstance(usage, dict):
        return 0.0
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cache_tokens = int(
        (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_tokens") or 0)
    )
    if input_tokens <= 0 and output_tokens <= 0 and cache_tokens <= 0:
        return 0.0
    return calculate_credits(
        input_tokens=input_tokens,
        cache_tokens=cache_tokens,
        output_tokens=output_tokens,
        model=model,
    )


class L1QuotaManager:
    """Local quota state for one user: record limit + cumulative credits.

    ``memory_limit`` / ``credit_limit`` come from config (negative = the
    source's "unlimited" convention; Alpha's config validators keep them
    positive, but the check honours negatives for parity).
    """

    def __init__(
        self,
        *,
        user_id: str | None,
        memory_limit: int,
        credit_limit: float,
        storage_path: str | None = None,
    ) -> None:
        self._user_id = user_id
        self._memory_limit = int(memory_limit)
        self._credit_limit = float(credit_limit)
        self._storage_path = storage_path
        self._lock = threading.Lock()
        self._credit_usage = self._load_credit_usage()

    # -- persistence ------------------------------------------------------
    def _load_credit_usage(self) -> float:
        path = quota_path(l1_root(self._storage_path), self._user_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return float(raw.get("credit_usage", 0.0))
        except FileNotFoundError:
            return 0.0
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("L1 quota: unreadable %s (%s); starting at 0", path, exc)
            return 0.0

    def _persist(self) -> None:
        path = quota_path(l1_root(self._storage_path), self._user_id)
        payload = {
            "credit_usage": self._credit_usage,
            "credit_limit": self._credit_limit,
            "memory_limit": self._memory_limit,
            "updated_at": time.time(),
        }
        try:
            atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=1))
        except OSError as exc:
            logger.warning("L1 quota: could not persist %s (%s)", path, exc)

    # -- checks -----------------------------------------------------------
    @property
    def credit_usage(self) -> float:
        return self._credit_usage

    def check_records(self, current_count: int, delta: int = 1) -> QuotaCheck:
        """Record-count quota: refuse when ``current + delta`` exceeds limit."""
        limit = self._memory_limit
        if limit >= 0 and current_count + delta > limit:
            return QuotaCheck(
                allowed=False,
                reason="memory_limit_exceeded",
                current=float(current_count),
                limit=float(limit),
            )
        return QuotaCheck(allowed=True, current=float(current_count), limit=float(limit))

    def check_credits(self) -> QuotaCheck:
        """Credit quota: refuse when cumulative usage reached the limit."""
        limit = self._credit_limit
        if limit >= 0 and self._credit_usage >= limit:
            return QuotaCheck(
                allowed=False,
                reason="credit_limit_exceeded",
                current=self._credit_usage,
                limit=limit,
            )
        return QuotaCheck(allowed=True, current=self._credit_usage, limit=limit)

    # -- accounting -------------------------------------------------------
    def add_credits(self, credits: float) -> float:
        """Accumulate credits from a run; returns the new cumulative usage."""
        if credits <= 0:
            return self._credit_usage
        with self._lock:
            self._credit_usage += float(credits)
            self._persist()
            return self._credit_usage


__all__ = [
    "L1QuotaManager",
    "QuotaCheck",
    "calculate_credits",
    "usage_from_response",
]
