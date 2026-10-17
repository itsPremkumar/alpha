"""Rate-limit-aware model routing and concurrency governance for swarms."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Mapping
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


def _plan_tasks(plan: Any | None) -> list[Any]:
    """Tasks of a plan, or ``[]`` when there is no readable plan.

    Duck-typed rather than imported: the governor is a process singleton read
    from routes and tools, and a hard import would pull the whole swarm data
    model into a status read.
    """

    if plan is None:
        return []
    tasks = getattr(plan, "tasks", None)
    if isinstance(tasks, Mapping):
        return [task for task in tasks.values() if task is not None]
    if isinstance(tasks, (list, tuple)):
        return [task for task in tasks if task is not None]
    return []


def _requested_models(plan: Any | None) -> list[str] | None:
    """Distinct non-``None`` ``model_override`` values a plan asked for.

    ``None`` (not ``[]``) when no plan was supplied, so a caller can tell
    "nothing has been measured" from "nothing was requested".
    """

    if plan is None:
        return None
    requested: list[str] = []
    for task in _plan_tasks(plan):
        override = getattr(task, "model_override", None)
        if isinstance(override, str) and override and override not in requested:
            requested.append(override)
    return requested


def _used_models(plan: Any | None) -> list[str] | None:
    """Distinct models that actually produced output, from worker evidence.

    Every worker stamps ``model`` into its evidence via ``_model_label`` - the
    resolved model name, not the requested one.  That distinction is the point:
    a requested model that was never reached must not be reported as used.
    """

    if plan is None:
        return None
    used: list[str] = []
    for task in _plan_tasks(plan):
        evidence = getattr(task, "evidence", None)
        if not isinstance(evidence, (list, tuple)):
            continue
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            label = item.get("model")
            if isinstance(label, str) and label and label not in used:
                used.append(label)
    return used


class SwarmResourceGovernor:
    """Adapt concurrency after 429s, and report which models a swarm used.

    **This class does not route models.**  It used to carry a ``MODEL_TIERS``
    table of hardcoded names (``claude-3-7-sonnet``, ``gemini-2.5-fl``,
    ``gpt-4o``, ``ollama/qwen2.5-coder``) and a ``resolve_model_for_role``
    that matched role-name substrings.  Both were dead: the runner resolves
    models through ``resolve_subagent_model_name(..., app_config=...)`` and
    ``task_node.model_override``, and nothing called the resolver.

    The defect was that ``get_status()`` still *published* those names, and it
    is served by ``GET /api/swarms/governor/status`` and by the swarm tool's own
    ``governor`` action.  So an operator surface - and the model itself - were
    told the swarm routes to models Alpha does not use, which also violates the
    root rule that every model name lives in ``config.yaml``, never in code.

    What is reported now is **measured**: the models a plan requested
    (``model_override``) and the models that actually produced output (the
    ``model`` field each worker stamps into its evidence).  Absent is ``None``,
    never a fallback name.
    """

    ROUTING_METHOD = "config_yaml_task_override_v1"

    def __init__(self, *, throttle_window_seconds: float = 60.0):
        self._rate_limit_hits: dict[str, list[float]] = {}
        self._last_success: dict[str, float] = {}
        self._throttle_window_seconds = max(1.0, float(throttle_window_seconds))
        self._lock = threading.RLock()

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

    def get_status(self, plan: Any | None = None) -> dict[str, Any]:
        """Throttling state plus the models a swarm actually used.

        ``plan`` is optional because the governor is a process singleton and the
        route may be read before any plan exists.  With no plan the model fields
        are ``None`` - "not measured yet" - rather than a fallback name, which
        is the whole point of the change.

        Two distinct lists, deliberately not merged:

        - ``models_requested`` - what the plan asked for (``model_override``).
          ``None`` entries mean "inherit the configured default" and are omitted.
        - ``models_used`` - what actually produced output, read from the
          ``model`` field each worker stamps into its evidence.  This is the
          measured answer to "which model ran this task", and it is the only one
          that may be used for billing or attribution.
        """

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
            "routing_method": self.ROUTING_METHOD,
            "models_requested": _requested_models(plan),
            "models_used": _used_models(plan),
            "throttled_providers": active,
            "is_throttling_active": bool(active),
            "last_success": last_success,
        }
