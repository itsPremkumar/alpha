"""Graceful Model Failover & Multi-Key Auth Rotation inspired by OpenClaw."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class KeyHealth:
    api_key: str
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    total_calls: int = 0
    total_successes: int = 0

    @property
    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until


@dataclass
class ModelEndpointConfig:
    provider: str
    model_name: str
    api_keys: list[str] = field(default_factory=list)
    priority: int = 0  # 0 is highest priority
    cooldown_seconds: float = 60.0
    _key_index: int = 0
    _keys_health: dict[str, KeyHealth] = field(default_factory=dict)

    def __post_init__(self):
        if not self.api_keys:
            self.api_keys = ["default_key"]
        for k in self.api_keys:
            if k not in self._keys_health:
                self._keys_health[k] = KeyHealth(api_key=k)

    def get_next_available_key(self) -> str | None:
        """Round-robin through keys, preferring those not currently in cooldown."""
        now = time.time()
        n = len(self.api_keys)
        for i in range(n):
            idx = (self._key_index + i) % n
            key = self.api_keys[idx]
            health = self._keys_health[key]
            if not health.is_in_cooldown:
                self._key_index = (idx + 1) % n
                return key
        return None

    def record_key_success(self, key: str) -> None:
        health = self._keys_health.get(key)
        if health:
            health.consecutive_failures = 0
            health.total_calls += 1
            health.total_successes += 1

    def record_key_failure(self, key: str, is_rate_limit: bool = False) -> None:
        health = self._keys_health.get(key)
        if health:
            health.total_calls += 1
            health.consecutive_failures += 1
            if is_rate_limit or health.consecutive_failures >= 3:
                health.cooldown_until = time.time() + self.cooldown_seconds
                logger.warning(
                    f"ModelEndpoint {self.provider}/{self.model_name} key ending in ...{key[-4:]} entered cooldown for {self.cooldown_seconds}s"
                )


class ModelFailoverChain:
    """Orchestrates prioritized model candidate fallback and multi-key rotation."""

    def __init__(self, endpoints: list[ModelEndpointConfig] | None = None):
        self.endpoints = sorted(endpoints or [], key=lambda e: e.priority)
        self.failover_history: list[dict[str, Any]] = []

    def add_endpoint(self, endpoint: ModelEndpointConfig) -> None:
        self.endpoints.append(endpoint)
        self.endpoints.sort(key=lambda e: e.priority)

    def execute(self, invoke_fn: Callable[[ModelEndpointConfig, str], Any]) -> Any:
        """Attempt invocation across candidate endpoints in priority order, rotating keys on errors."""
        last_error = None

        for endpoint in self.endpoints:
            # Try available keys for this endpoint
            attempted_keys = set()
            while len(attempted_keys) < len(endpoint.api_keys):
                key = endpoint.get_next_available_key()
                if not key or key in attempted_keys:
                    break
                attempted_keys.add(key)

                try:
                    res = invoke_fn(endpoint, key)
                    endpoint.record_key_success(key)
                    return res
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    is_rate_limit = "429" in err_str or "rate limit" in err_str or "quota" in err_str
                    endpoint.record_key_failure(key, is_rate_limit=is_rate_limit)

                    self.failover_history.append({
                        "provider": endpoint.provider,
                        "model": endpoint.model_name,
                        "key_masked": f"...{key[-4:]}",
                        "error": str(e),
                        "rate_limit": is_rate_limit,
                        "timestamp": time.time(),
                    })
                    # Loop will try next key or fall over to next candidate model

        raise RuntimeError(f"All failover candidates exhausted. Last error: {last_error}")
