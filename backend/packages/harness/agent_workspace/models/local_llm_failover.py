"""Local LLM Failover & Hybrid Cost Router.

Monitors local inference endpoints (Ollama, vLLM, LM Studio) and dynamically
routes low-complexity, high-volume tasks (formatting, lint repairs, docstrings, triage)
away from cloud APIs when financial budgets or rate limits are approached.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LocalEndpointStatus:
    provider: str
    endpoint_url: str
    is_reachable: bool
    active_model: str | None = None
    latency_ms: float = 0.0
    checked_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalLLMFailoverRouter:
    """Detects budget or rate-limit pressure and fails over to local model runtimes."""

    DEFAULT_ENDPOINTS = {
        "ollama": "http://localhost:11434",
        "vllm": "http://localhost:8000/v1",
        "lmstudio": "http://localhost:1234/v1",
    }

    # Tasks suitable for local 7B-32B coder models without sacrificing accuracy
    LOW_COMPLEXITY_TASKS = frozenset({
        "lint_repair",
        "docstring",
        "formatting",
        "unit_test_boilerplate",
        "triage",
        "summary",
        "git_commit_msg",
    })

    def __init__(self, endpoints: dict[str, str] | None = None):
        self.endpoints = endpoints or dict(self.DEFAULT_ENDPOINTS)
        self._cached_status: dict[str, LocalEndpointStatus] = {}

    def check_endpoint(self, provider: str, timeout: float = 1.0) -> LocalEndpointStatus:
        """Probe local inference endpoint health."""
        url = self.endpoints.get(provider, "")
        if not url:
            return LocalEndpointStatus(provider=provider, endpoint_url="", is_reachable=False)

        probe_url = f"{url}/api/tags" if provider == "ollama" else f"{url}/models"
        start = time.perf_counter()
        reachable = False
        model_name = None

        try:
            req = urllib.request.Request(probe_url, headers={"User-Agent": "Alpha-Watchdog"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 204):
                    reachable = True
                    model_name = "qwen2.5-coder:14b" if provider == "ollama" else "deepseek-coder"
        except Exception:
            reachable = False

        latency = (time.perf_counter() - start) * 1000
        status = LocalEndpointStatus(
            provider=provider,
            endpoint_url=url,
            is_reachable=reachable,
            active_model=model_name,
            latency_ms=round(latency, 2),
        )
        self._cached_status[provider] = status
        return status

    def should_failover(
        self,
        project_id: str,
        task_type: str,
        force_local: bool = False,
    ) -> tuple[bool, str, str, str]:
        """Determine whether the request should failover to a local endpoint.

        Returns: (should_failover, provider, model, reason)
        """
        if force_local:
            for p in ("ollama", "vllm", "lmstudio"):
                stat = self.check_endpoint(p)
                if stat.is_reachable:
                    return True, p, stat.active_model or "local-coder", "Operator forced local model execution"
            return False, "cloud_default", "cloud-default", "Forced local requested but no endpoint reachable"

        # Check project cost ratio from CostGovernor
        budget_ratio = 0.0
        try:
            from agent_workspace.models.cost_governor import get_cost_governor

            gov = get_cost_governor()
            summary = gov.get_project_summary(project_id)
            budget_ratio = summary.get("budget_utilized_ratio", 0.0)
        except Exception:
            pass

        is_budget_strained = budget_ratio >= 0.80
        is_low_complexity = task_type.lower().strip() in self.LOW_COMPLEXITY_TASKS

        if is_budget_strained or is_low_complexity:
            for p in ("ollama", "vllm", "lmstudio"):
                stat = self.check_endpoint(p)
                if stat.is_reachable:
                    reason = (
                        f"Budget constraint ({budget_ratio*100:.1f}% utilized)"
                        if is_budget_strained
                        else f"Task type '{task_type}' optimized for local inference"
                    )
                    return True, p, stat.active_model or "local-coder", reason

        return False, "cloud_premium", "claude-3-7-sonnet", "Task retained on cloud reasoning model"

    def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        return {p: self.check_endpoint(p).to_dict() for p in self.endpoints}


_FAILOVER_ROUTER: LocalLLMFailoverRouter | None = None


def get_local_failover_router() -> LocalLLMFailoverRouter:
    global _FAILOVER_ROUTER
    if _FAILOVER_ROUTER is None:
        _FAILOVER_ROUTER = LocalLLMFailoverRouter()
    return _FAILOVER_ROUTER
