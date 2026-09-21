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
        from alpha.models import performance_registry as perf

        get_scores = getattr(perf, "get_success_rates", None) or getattr(perf, "success_rates", None)
        scores = get_scores(category) if callable(get_scores) else {}
        if not scores:
            return candidates
        return sorted(candidates, key=lambda m: -float(scores.get(m, scores.get(category, 0.0)) or 0.0))
    except Exception:
        logger.debug("Performance re-rank unavailable for %s", category, exc_info=True)
        return candidates


#: Categories to move *up* to when System One says the request needs the
#: flagship model. A wrong escalation costs money; a wrong downgrade costs
#: quality — so the only direction this table ever moves is up.
ESCALATION_LADDER: dict[str, str] = {
    "quick": "unspecified-low",
    "unspecified-low": "unspecified-high",
    "writing": "unspecified-high",
    "artistry": "unspecified-high",
    "visual-engineering": "deep",
    "unspecified-high": "deep",
    "deep": "ultrabrain",
}


def route_task(task_type: str, *, available_models: list[str] | None = None, prefer_local: bool = True) -> RouteDecision:
    category = TASK_TO_CATEGORY.get((task_type or "").lower(), "unspecified-low")
    try:
        from alpha.models.category_router import DEFAULT_CATEGORY_SPECS

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


def _escalate(decision: RouteDecision) -> RouteDecision:
    """Move one rung up the ladder. Never downgrades."""
    target = ESCALATION_LADDER.get(decision.category)
    if not target or target == decision.category:
        return decision
    try:
        from alpha.models.category_router import DEFAULT_CATEGORY_SPECS

        spec = DEFAULT_CATEGORY_SPECS.get(target)
        static_chain = list(spec.models) if spec else []
    except Exception:
        static_chain = []
    if not static_chain:
        static_chain = list(LOCAL_FIRST_CHAINS.get(target, LOCAL_FIRST_CHAINS["unspecified-low"]))
    chain = _measured_rank(target, static_chain)
    primary = chain[0] if chain else decision.primary
    return RouteDecision(
        task_type=decision.task_type,
        category=target,
        chain=chain or decision.chain,
        primary=primary,
        source=f"{decision.source}+escalated",
    )


async def aroute_task(
    task_type: str,
    prompt: str = "",
    *,
    available_models: list[str] | None = None,
    prefer_local: bool = True,
) -> RouteDecision:
    """Static/measured routing, upgraded when System One says the request is hard.

    Falls back to :func:`route_task` unchanged whenever System One is disabled,
    unreachable, or not confident — and because escalation only ever moves up a
    rung, a bad signal can cost money but never quality.
    """
    decision = route_task(task_type, available_models=available_models, prefer_local=prefer_local)
    if not prompt.strip():
        return decision
    try:
        from alpha.models.escalation import needs_flagship_model

        escalate = await needs_flagship_model(prompt, task_type=task_type)
    except Exception:
        logger.debug("System One escalation unavailable for %s; keeping static route.", task_type, exc_info=True)
        return decision
    if escalate is None:
        return decision
    if not escalate:
        return decision
    return _escalate(decision)


def route_task_smart(
    task_type: str,
    prompt: str = "",
    *,
    available_models: list[str] | None = None,
    prefer_local: bool = True,
) -> RouteDecision:
    """Sync wrapper around :func:`aroute_task`; falls back rather than blocking a live loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(aroute_task(task_type, prompt, available_models=available_models, prefer_local=prefer_local))
    logger.debug("route_task_smart called inside a running loop; using static route.")
    return route_task(task_type, available_models=available_models, prefer_local=prefer_local)
