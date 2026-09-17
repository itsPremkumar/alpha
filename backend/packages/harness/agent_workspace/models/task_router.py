"""Per-task model routing: task type -> category chain -> model, with fallback.

Builds on the intent categories (`models.category_router`) and the measured
performance registry (`models.performance_registry`): static category chains
first, measured success rates re-rank models over time. External APIs stay
optional acceleration — routing degrades to local chains, never fails.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TASK_TO_CATEGORY: dict[str, str] = {
    "reasoning": "ultrabrain",
    "architecture": "ultrabrain",
    "coding": "deep",
    "debug": "deep",
    "browser": "deep",
    "frontend": "visual-engineering",
    "ui": "visual-engineering",
    "writing": "writing",
    "docs": "writing",
    "creative": "artistry",
    "quick": "quick",
    "research": "unspecified-high",
}

LOCAL_FIRST_CHAINS: dict[str, list[str]] = {
    "ultrabrain": ["ollama/qwen3:32b", "gpt-4o", "claude-opus-5"],
    "deep": ["ollama/qwen3-coder:30b", "gpt-4o", "claude-opus-5"],
    "visual-engineering": ["ollama/qwen3:32b", "gpt-4o"],
    "writing": ["ollama/llama3.1:8b", "gpt-4o-mini"],
    "quick": ["ollama/llama3.1:8b", "gpt-4o-mini"],
    "artistry": ["ollama/llama3.1:8b", "gpt-4o"],
    "unspecified-high": ["ollama/qwen3:32b", "gpt-4o"],
    "unspecified-low": ["ollama/llama3.1:8b", "gpt-4o-mini"],
}


@dataclass
class RouteDecision:
    task_type: str
    category: str
    chain: list[str] = field(default_factory=list)
    primary: str = ""
    source: str = "static"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _measured_rank(category: str, candidates: list[str]) -> list[str]:
    """Re-rank a static chain by measured success rate when data exists."""
    try:
        from agent_workspace.models import performance_registry as perf

        get_scores = getattr(perf, "get_success_rates", None) or getattr(perf, "success_rates", None)
        scores = get_scores(category) if callable(get_scores) else {}
        if not scores:
            return candidates
        return sorted(candidates, key=lambda m: -float(scores.get(m, scores.get(category, 0.0)) or 0.0))
    except Exception:
        logger.debug("Performance re-rank unavailable for %s", category, exc_info=True)
        return candidates


def route_task(task_type: str, *, available_models: list[str] | None = None, prefer_local: bool = True) -> RouteDecision:
    category = TASK_TO_CATEGORY.get((task_type or "").lower(), "unspecified-low")
    try:
        from agent_workspace.models.category_router import DEFAULT_CATEGORY_SPECS

        spec = DEFAULT_CATEGORY_SPECS.get(category)
        static_chain = list(spec.models) if spec else []
    except Exception:
        static_chain = []
    if not static_chain:
        static_chain = list(LOCAL_FIRST_CHAINS.get(category, LOCAL_FIRST_CHAINS["unspecified-low"]))
    chain = _measured_rank(category, static_chain)
    if available_models is not None:
        have = set(available_models)
        chain = [m for m in chain if m in have] or chain
    if prefer_local:
        chain = sorted(chain, key=lambda m: 0 if m.startswith("ollama/") else 1)
    primary = chain[0] if chain else ""
    return RouteDecision(task_type=task_type, category=category, chain=chain, primary=primary, source="measured" if chain != static_chain else "static")
