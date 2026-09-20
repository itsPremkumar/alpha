"""Swarm Resource Governor: Heterogeneous Model Routing and Rate-Limit Adaptive Throttling."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_GOVERNOR: SwarmResourceGovernor | None = None


def get_swarm_resource_governor() -> SwarmResourceGovernor:
    global _GLOBAL_GOVERNOR
    if _GLOBAL_GOVERNOR is None:
        _GLOBAL_GOVERNOR = SwarmResourceGovernor()
    return _GLOBAL_GOVERNOR


class SwarmResourceGovernor:
    """Manages heterogeneous model tier routing and adaptive concurrency throttling."""

    MODEL_TIERS = {
        "frontier": "claude-3-7-sonnet",
        "fast": "gemini-2.5-flash",
        "local": "ollama/qwen2.5-coder",
        "verifier": "gpt-4o",
    }

    def __init__(self):
        # provider -> list of timestamp floats
        self._rate_limit_hits: dict[str, list[float]] = {}
        self._throttle_window_seconds: float = 60.0

    def resolve_model_for_role(self, role: str, is_batch: bool = False) -> str:
        """Determines appropriate model tier based on node responsibility."""
        role_lower = role.lower()
        if any(w in role_lower for w in ("judge", "synthesizer", "architect", "ceo", "commander")):
            return self.MODEL_TIERS["frontier"]
        if any(w in role_lower for w in ("verifier", "qa", "gate", "red_team")):
            return self.MODEL_TIERS["verifier"]
        if is_batch or any(w in role_lower for w in ("extract", "scrape", "lint", "filter", "format")):
            return self.MODEL_TIERS["local"]
        return self.MODEL_TIERS["fast"]

    def record_rate_limit(self, provider: str = "default") -> None:
        """Records a 429 rate limit hit."""
        now = time.time()
        if provider not in self._rate_limit_hits:
            self._rate_limit_hits[provider] = []
        self._rate_limit_hits[provider].append(now)
        logger.warning(f"Rate limit hit recorded for provider '{provider}'. Adaptive throttling engaged.")

    def get_effective_concurrency(self, base_concurrency: int, provider: str = "default") -> int:
        """Dynamically adjusts concurrency based on recent rate limit pressure."""
        now = time.time()
        hits = [t for t in self._rate_limit_hits.get(provider, []) if (now - t) < self._throttle_window_seconds]
        self._rate_limit_hits[provider] = hits

        if not hits:
            return base_concurrency
        if len(hits) == 1:
            return max(2, base_concurrency // 2)
        if len(hits) == 2:
            return max(1, base_concurrency // 4)
        return 1  # Full serialization under severe provider pressure

    def get_backoff_delay(self, provider: str = "default") -> float:
        """Calculates jittered exponential backoff delay."""
        now = time.time()
        hits = [t for t in self._rate_limit_hits.get(provider, []) if (now - t) < self._throttle_window_seconds]
        if not hits:
            return 0.0
        exponent = min(len(hits), 5)
        base_delay = 2.0**exponent
        jitter = random.uniform(0.5, 1.5)
        return round(base_delay * jitter, 2)

    def get_status(self) -> dict[str, Any]:
        now = time.time()
        active_throttles = {}
        for prov, hits in self._rate_limit_hits.items():
            recent = [t for t in hits if (now - t) < self._throttle_window_seconds]
            if recent:
                active_throttles[prov] = {
                    "recent_hits": len(recent),
                    "backoff_seconds": self.get_backoff_delay(prov),
                }
        return {
            "model_tiers": dict(self.MODEL_TIERS),
            "throttled_providers": active_throttles,
            "is_throttling_active": len(active_throttles) > 0,
        }
