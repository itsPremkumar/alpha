"""Intent-Driven Category Routing Matrix & Dual Fallback Engine.

Inspired by oh-my-openagent (OmO) category routing:
Agents select a Category (intent) instead of choosing hardcoded model names:
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
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CategorySpec:
    name: str
    models: list[str]
    reasoning_effort: str = "medium"
    temperature: float = 0.5
    prompt_append: str = ""
    description: str = ""


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
        """Resolve category with proactive provider availability checking."""
        spec = self._categories.get(category.lower())
        if not spec:
            raise KeyError(
                f"Unknown category '{category}'. Available categories: {list(self._categories.keys())}"
            )

        if not available_models:
            return spec

        # Filter model chain to those available
        valid_models = [m for m in spec.models if m in available_models]
        if not valid_models:
            # Return spec with original models if none match
            return spec

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
