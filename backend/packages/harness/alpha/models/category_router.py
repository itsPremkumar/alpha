"""Intent-Driven Category Routing Matrix & Dual Fallback Engine.

Agents select a Category (intent) instead of choosing a model name:
- "ultrabrain": Deep architectural reasoning, maximum thinking budget.
- "deep": Multi-step algorithmic coding, browser/system execution.
- "visual-engineering": Frontend UI/UX, CSS, canvas components.
- "artistry": Creative prose and documentation.
- "quick": Fast single-file edits, typos, and simple modifications.
- "unspecified-low" / "unspecified-high": General task rungs.
- "writing": Technical documentation and specification writing.

Provides dual fallback:
1. Proactive selection based on available providers/keys.
2. Reactive runtime recovery shifting to next model upon 429/500/context errors.

**Model names are operator configuration, not code.** ``DEFAULT_CATEGORY_SPECS``
below is a legacy advisory table of vendor model ids kept only as a last-resort
fallback; those names exist in no operator's ``models[]``, so a decision built
from it cannot be executed. Declare real routes under
``config.yaml -> model_routing.categories`` instead — every declared name is
validated against ``models[]`` at config load, and
:func:`resolve_configured_chain` prefers it. See
``alpha.config.model_routing_config``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CategorySpec:
    name: str
    models: list[str]
    reasoning_effort: str = "medium"
    temperature: float = 0.5
    prompt_append: str = ""
    description: str = ""


def resolve_configured_chain(category: str, app_config=None) -> list[str]:
    """Operator-declared models for ``category``, or ``[]`` when not declared.

    Reads ``config.yaml -> model_routing.categories`` and returns the declared
    chain only when routing is enabled. Returns an empty list — never a
    fallback — so callers can distinguish "operator declared this" from "we
    have nothing configured", which is what lets the routers fail closed
    instead of silently degrading to the default model.
    """
    try:
        if app_config is None:
            from alpha.config import get_app_config

            app_config = get_app_config()
        routing = app_config.model_routing
        if not routing.enabled:
            return []
        return routing.chain_for_category(category)
    except Exception:
        # Routing is an optimization. Never fail a call because config could
        # not be read; the caller falls back to the static chain.
        logger.debug("Configured routing unavailable for category '%s'", category, exc_info=True)
        return []


def resolve_configured_tier_chain(tier: str, app_config=None) -> list[str]:
    """Operator-declared models for a cost ``tier``, or ``[]`` when not declared.

    Counterpart to :func:`resolve_configured_chain` for
    :mod:`alpha.models.workforce_router`, reading
    ``config.yaml -> model_routing.tiers``.
    """
    try:
        if app_config is None:
            from alpha.config import get_app_config

            app_config = get_app_config()
        routing = app_config.model_routing
        if not routing.enabled:
            return []
        return routing.chain_for_tier(tier)
    except Exception:
        logger.debug("Configured tier routing unavailable for tier '%s'", tier, exc_info=True)
        return []


# Legacy advisory fallback only.
#
# These are vendor model ids from the upstream OmO table this module was
# modelled on. They are NOT resolvable against an operator's `models[]`, so a
# chain built from this table cannot be executed by `create_chat_model`. They
# are retained so an operator who has declared no `model_routing.categories`
# still gets a non-empty, ordered, human-meaningful suggestion chain from the
# advisory endpoints — never as an executable route. Declare real routes under
# `model_routing.categories`; `resolve_configured_chain` takes precedence.
DEFAULT_CATEGORY_SPECS: dict[str, CategorySpec] = {
    "ultrabrain": CategorySpec(
        name="ultrabrain",
        models=["gpt-4o", "claude-opus-5", "gpt-5.6-sol"],
        reasoning_effort="max",
        temperature=0.2,
        description="Deep logical reasoning and complex architectural decisions.",
    ),
    "deep": CategorySpec(
        name="deep",
        models=["gpt-4o", "claude-opus-5", "deepseek-r1"],
        reasoning_effort="high",
        temperature=0.3,
        description="Deep autonomous work for backend logic, algorithms, and complex refactors.",
    ),
    "visual-engineering": CategorySpec(
        name="visual-engineering",
        models=["claude-3-7-sonnet", "claude-opus-5", "kimi-k3"],
        reasoning_effort="high",
        temperature=0.7,
        description="Frontend, UI/UX, responsive components, CSS styling and animations.",
    ),
    "quick": CategorySpec(
        name="quick",
        models=["kimi-highspeed", "gpt-5.6-luna-fast", "deepseek-v4-flash", "claude-haiku-4-5"],
        reasoning_effort="low",
        temperature=0.1,
        description="Trivial tasks: single-file changes, typos, and lightweight modifications.",
    ),
    "writing": CategorySpec(
        name="writing",
        models=["claude-3-7-sonnet", "kimi-k3", "gpt-5.6-sol"],
        reasoning_effort="medium",
        temperature=0.6,
        description="Documentation, specification writing, and technical prose.",
    ),
    "artistry": CategorySpec(
        name="artistry",
        models=["claude-3-7-sonnet", "kimi-k3", "claude-opus-5"],
        reasoning_effort="high",
        temperature=0.8,
        description="Highly creative and novel architectural exploration.",
    ),
    "unspecified-low": CategorySpec(
        name="unspecified-low",
        models=["grok-4.6", "gpt-5.6-terra", "claude-sonnet-5"],
        reasoning_effort="low",
        temperature=0.4,
        description="General lightweight routing with minimal latency.",
    ),
    "unspecified-high": CategorySpec(
        name="unspecified-high",
        models=["gpt-4o", "claude-opus-5", "glm-5.3"],
        reasoning_effort="high",
        temperature=0.3,
        description="General high-effort tasks.",
    ),
}


class CategoryRouter:
    """Routes agent intent categories to models and manages dual fallbacks."""

    def __init__(self, custom_specs: dict[str, CategorySpec] | None = None):
        self._categories: dict[str, CategorySpec] = dict(DEFAULT_CATEGORY_SPECS)
        if custom_specs:
            self._categories.update(custom_specs)

    def resolve_category(
        self,
        category: str,
        available_models: set[str] | None = None,
    ) -> CategorySpec:
        """Resolve category, preferring the operator's configured chain.

        Precedence: ``config.yaml -> model_routing.categories[category]`` first
        (its names are validated against ``models[]`` at load, so they are
        executable), then this router's static table, then the built-in
        per-category suggestion chain.

        ``available_models`` filters the chain. When it filters *everything*
        away the unfiltered chain is NOT returned — that fallback used to hand
        back model names that resolve against nothing. The spec is returned
        with an empty ``models`` list so the caller sees "nothing available"
        rather than a route that cannot run.
        """
        spec = self._categories.get(category.lower())
        if not spec:
            raise KeyError(f"Unknown category '{category}'. Available categories: {list(self._categories.keys())}")

        configured = resolve_configured_chain(category)
        if configured:
            spec = CategorySpec(
                name=spec.name,
                models=configured,
                reasoning_effort=spec.reasoning_effort,
                temperature=spec.temperature,
                prompt_append=spec.prompt_append,
                description=spec.description,
            )

        if not available_models:
            return spec

        # Filter to what is actually available. An empty result is reported
        # honestly (empty models) instead of restoring the unfiltered chain.
        valid_models = [m for m in spec.models if m in available_models]
        return CategorySpec(
            name=spec.name,
            models=valid_models,
            reasoning_effort=spec.reasoning_effort,
            temperature=spec.temperature,
            prompt_append=spec.prompt_append,
            description=spec.description,
        )

    def get_reactive_fallback(self, category: str, failed_model: str) -> str | None:
        """Reactive recovery: return next model in chain when current model fails."""
        spec = self._categories.get(category.lower())
        if not spec or failed_model not in spec.models:
            return None

        idx = spec.models.index(failed_model)
        if idx + 1 < len(spec.models):
            return spec.models[idx + 1]
        return None

    def register_category(self, spec: CategorySpec) -> None:
        self._categories[spec.name.lower()] = spec
