"""Per-task model routing: task type -> category chain -> model, with fallback.

Builds on the intent categories (`models.category_router`) and the measured
performance registry (`models.performance_registry`): operator-declared chains
first, then static category chains, then measured success rates re-rank models
over time. External APIs stay optional acceleration — routing degrades to local
chains, never fails.

**Fail-closed on unresolvable routes.** When a caller supplies
``available_models`` and nothing in the chain survives the filter, this module
returns an empty chain with an explicit ``reason`` instead of restoring the
unfiltered chain. The old ``[m for m in chain if m in have] or chain`` fallback
returned vendor model ids from the legacy table, which resolve against no
operator's ``models[]``; the lead agent then silently degraded to the default
model, so a "quick" task quietly ran on the flagship and billed accordingly.
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

# Legacy advisory suggestions, mirroring `category_router.DEFAULT_CATEGORY_SPECS`.
# These are vendor model ids, not operator configuration; they are only reached
# when `config.yaml -> model_routing.categories` declares nothing for the
# category. Never treat a chain from here as executable — see the module
# docstring.
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
    #: Why the chain is what it is — in particular why it is empty. An empty
    #: `chain` with an empty `primary` is a fail-closed "nothing resolvable",
    #: never a silent fall back to the default model.
    reason: str = ""

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


def _static_chain(category: str) -> list[str]:
    """Legacy advisory chain for a category, or ``[]`` when unknown."""
    try:
        from alpha.models.category_router import DEFAULT_CATEGORY_SPECS

        spec = DEFAULT_CATEGORY_SPECS.get(category)
        if spec and spec.models:
            return list(spec.models)
    except Exception:
        logger.debug("Static category specs unavailable for '%s'", category, exc_info=True)
    return list(LOCAL_FIRST_CHAINS.get(category, LOCAL_FIRST_CHAINS["unspecified-low"]))


def _declared_chain(category: str) -> list[str]:
    """Operator-declared chain for a category (``model_routing.categories``)."""
    try:
        from alpha.models.category_router import resolve_configured_chain

        return resolve_configured_chain(category)
    except Exception:
        logger.debug("Configured routing unavailable for category '%s'", category, exc_info=True)
        return []


def _routing_default() -> str | None:
    """``model_routing.default_model`` / configured default, used when a
    category declares nothing. Validated against ``models[]`` at config load."""
    try:
        from alpha.config import get_app_config

        config = get_app_config()
        return config.model_routing.default_model or config.default_model_name
    except Exception:
        logger.debug("No routing default available", exc_info=True)
        return None


def _apply_availability(chain: list[str], available_models: list[str] | None, category: str) -> tuple[list[str], str]:
    """Filter a chain to the available models, failing closed when empty.

    Returns ``(chain, note)``. An empty returned chain means "nothing in this
    route is available" and the caller must not invent a primary. The
    ``available_models is None`` case is a pure no-op: with no availability
    information the chain is returned as-is, which keeps the advisory
    suggestion chains working for callers that pass nothing.
    """
    if available_models is None:
        return chain, ""
    have = set(available_models)
    filtered = [m for m in chain if m in have]
    if filtered:
        return filtered, ""
    return [], f"no model in the '{category}' chain is available (chain={chain or '[]'}, available={sorted(have)})"


def route_task(task_type: str, *, available_models: list[str] | None = None, prefer_local: bool = True) -> RouteDecision:
    """Route a task type to an ordered, executable model chain.

    Precedence: operator-declared ``model_routing.categories`` chain, then the
    legacy static advisory chain, then measured-success re-ranking. When
    ``available_models`` is supplied the chain is filtered against it and an
    empty result is reported honestly (empty ``chain``/``primary`` plus a
    ``reason``) instead of falling back to names that cannot be constructed.
    """
    category = TASK_TO_CATEGORY.get((task_type or "").lower(), "unspecified-low")
    declared = _declared_chain(category)
    source = "configured" if declared else "static"
    base_chain = declared or _static_chain(category)
    chain = _measured_rank(category, base_chain)
    if available_models is not None:
        have = set(available_models)
        chain = [m for m in chain if m in have]
    if not chain:
        # Fail closed. Only when availability was actually supplied does this
        # mean "your declared route is unusable"; otherwise the category simply
        # has no suggestion chain and we fall back to the configured default.
        if available_models is not None:
            return RouteDecision(
                task_type=task_type,
                category=category,
                chain=[],
                primary="",
                source=source,
                reason=f"no model in the '{category}' chain is available (declared={declared or '[]'}, available={sorted(set(available_models))})",
            )
        fallback = _routing_default()
        if not fallback:
            return RouteDecision(
                task_type=task_type,
                category=category,
                chain=[],
                primary="",
                source=source,
                reason=f"no route declared for category '{category}' and no default model is configured",
            )
        chain = [fallback]
        source = f"{source}+default"
    if prefer_local and source != "configured":
        chain = sorted(chain, key=lambda m: 0 if m.startswith("ollama/") else 1)
    return RouteDecision(
        task_type=task_type,
        category=category,
        chain=chain,
        primary=chain[0] if chain else "",
        source=source if chain == base_chain else f"{source}+measured",
        reason="",
    )


def _escalate(decision: RouteDecision) -> RouteDecision:
    """Move one rung up the ladder. Never downgrades.

    Escalation is a cost increase, so it never invents a route: when the next
    rung up declares nothing and has no availability, the original decision is
    returned unchanged.
    """
    target = ESCALATION_LADDER.get(decision.category)
    if not target or target == decision.category:
        return decision
    declared = _declared_chain(target)
    base_chain = declared or _static_chain(target)
    if not base_chain:
        return decision
    chain = _measured_rank(target, base_chain)
    if not chain:
        return decision
    return RouteDecision(
        task_type=decision.task_type,
        category=target,
        chain=chain,
        primary=chain[0],
        source=f"{'configured' if declared else 'static'}+escalated",
        reason="",
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
