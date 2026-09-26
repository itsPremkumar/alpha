"""Rate-limit-aware model routing and concurrency governance for swarms."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_GOVERNOR: SwarmResourceGovernor | None = None


def get_swarm_resource_governor() -> SwarmResourceGovernor:
    global _GLOBAL_GOVERNOR
    if _GLOBAL_GOVERNOR is None:
        _GLOBAL_GOVERNOR = SwarmResourceGovernor()
    return _GLOBAL_GOVERNOR


def reset_swarm_resource_governor() -> None:
    """Reset the process-local governor for isolated tests or an explicit reload."""

    global _GLOBAL_GOVERNOR
    _GLOBAL_GOVERNOR = None


class SwarmResourceGovernor:
    """Manage heterogeneous model tiers and adapt concurrency after 429s."""

    # Stable fallback labels are retained for callers that use the governor
    # without a model catalog.  The runner records the model actually used in
    # task evidence; these values are routing hints, not billing claims.
    MODEL_TIERS = {
        "frontier": "claude-3-7-sonnet",
        "fast": "gemini-2.5-flash",
        "local": "ollama/qwen2.5-coder",
        "verifier": "gpt-4o",
    }

    def __init__(self, *, throttle_window_seconds: float = 60.0):
        self._rate_limit_hits: dict[str, list[float]] = {}
        self._last_success: dict[str, float] = {}
        self._throttle_window_seconds = max(1.0, float(throttle_window_seconds))
        self._lock = threading.RLock()

    def resolve_model_for_role(self, role: str, is_batch: bool = False) -> str:
        """Return a deterministic model tier for a worker role."""

        role_lower = str(role or "").lower()
        if any(word in role_lower for word in ("judge", "synthesizer", "architect", "ceo", "commander")):
            return self.MODEL_TIERS["frontier"]
        if any(word in role_lower for word in ("verifier", "qa", "gate", "red_team")):
            return self.MODEL_TIERS["verifier"]
        if is_batch or any(word in role_lower for word in ("extract", "scrape", "lint", "filter", "format")):
            return self.MODEL_TIERS["local"]
        return self.MODEL_TIERS["fast"]

    def record_rate_limit(self, provider: str = "default") -> None:
        """Record a provider 429 and enter adaptive throttling."""

        now = time.time()
        provider = str(provider or "default")
        with self._lock:
            hits = self._rate_limit_hits.setdefault(provider, [])
            hits.append(now)
            # Keep the ledger bounded even under a provider outage.
            cutoff = now - self._throttle_window_seconds
            self._rate_limit_hits[provider] = [hit for hit in hits if hit >= cutoff][-32:]
        logger.warning("Rate limit hit recorded for provider '%s'; adaptive throttling engaged", provider)

    def record_success(self, provider: str = "default") -> None:
        with self._lock:
            self._last_success[str(provider or "default")] = time.time()

    def get_effective_concurrency(self, base_concurrency: int, provider: str = "default") -> int:
        """Lower concurrency deterministically under recent rate-limit pressure."""

        base = max(1, int(base_concurrency))
        now = time.time()
        provider = str(provider or "default")
        with self._lock:
            hits = [hit for hit in self._rate_limit_hits.get(provider, []) if now - hit < self._throttle_window_seconds]
            self._rate_limit_hits[provider] = hits
        if not hits:
            return base
        if len(hits) == 1:
            return max(1, base // 2)
        if len(hits) == 2:
            return max(1, base // 4)
        return 1

    def get_backoff_delay(self, provider: str = "default") -> float:
        """Return a bounded jittered exponential retry delay."""

        now = time.time()
        provider = str(provider or "default")
        with self._lock:
            hits = [hit for hit in self._rate_limit_hits.get(provider, []) if now - hit < self._throttle_window_seconds]
        if not hits:
            return 0.0
        exponent = min(len(hits), 5)
        return round((2.0**exponent) * random.uniform(0.5, 1.5), 2)

    def get_status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            active: dict[str, dict[str, Any]] = {}
            for provider, hits in self._rate_limit_hits.items():
                recent = [hit for hit in hits if now - hit < self._throttle_window_seconds]
                if recent:
                    active[provider] = {
                        "recent_hits": len(recent),
                        "backoff_seconds": self.get_backoff_delay(provider),
                        "last_hit": recent[-1],
                        "cooldown_until": recent[-1] + self._throttle_window_seconds,
                    }
            last_success = dict(self._last_success)
        return {
            "model_tiers": dict(self.MODEL_TIERS),
            "routing_method": "role-tier-hints-v1",
            "throttled_providers": active,
            "is_throttling_active": bool(active),
            "last_success": last_success,
        }
