"""Intent presets ('categories') for task delegation.

A category names *intent* (``research``, ``quick``, ...) rather than a model
or agent: at ``task`` dispatch it selects the first configured model in its
chain, gates on required models, and overlays skills/tools/budgets plus an
operator guidance suffix on the task input. It applies to ANY subagent type.

Trust model: category text is operator configuration (``config.yaml``), the
same trust level as custom-agent system prompts. It is still applied on the
task ``HumanMessage`` channel (downgrade-safe), never interpolated into
framework-owned system text.

Loop guard: denials are sticky — a category can only ADD to the subagent's
``disallowed_tools``, never lift a denial. In particular a child can never
re-enable ``task`` through a category; nested delegation stays off unless
the subagent definition itself allows it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_workspace.config.subagents_config import SubagentCategoryConfig


class CategoryResolutionError(ValueError):
    """Unknown category, unmet requirements, or unresolvable model chain."""


@dataclass
class CategoryResolution:
    """Result of applying a category to a resolved subagent config."""

    config_overrides: dict[str, Any] = field(default_factory=dict)
    prompt_suffix: str = ""


def _subagents_section(app_config: Any | None) -> Any | None:
    if app_config is None:
        return None
    # Accept both the full AppConfig and a bare SubagentsAppConfig, mirroring
    # the registry's getattr(app_config, "subagents", app_config) convention.
    return getattr(app_config, "subagents", app_config)


def _operator_categories(app_config: Any | None) -> dict:
    section = _subagents_section(app_config)
    categories = getattr(section, "categories", None) if section is not None else None
    return dict(categories) if categories else {}


def get_category(name: str, app_config: Any | None = None):
    """Resolve a category: operator ``subagents.categories`` wins by name."""
    operator = _operator_categories(app_config)
    if name in operator:
        return operator[name]
    return BUILTIN_CATEGORIES.get(name)


def list_category_names(app_config: Any | None = None) -> list[str]:
    """All category names: built-ins overlaid by operator definitions."""
    names = list(BUILTIN_CATEGORIES)
    for name in _operator_categories(app_config):
        if name not in names:
            names.append(name)
    return names


def _model_lookup(app_config: Any | None):
    """Return a name->config resolver, or None when unavailable in this runtime."""
    get_model_config = getattr(app_config, "get_model_config", None)
    if callable(get_model_config):
        return get_model_config
    return None


def apply_category(base_config, category_name: str, *, app_config: Any | None = None) -> CategoryResolution:
    """Overlay a category preset onto a resolved subagent config.

    Args:
        base_config: The ``SubagentConfig`` already resolved by the registry.
        category_name: Category to apply (``general`` = identity no-op).
        app_config: Full AppConfig or SubagentsAppConfig; may be None when the
            category needs no model lookups.

    Returns:
        ``CategoryResolution`` with dataclass ``replace()`` overrides and a
        task-input suffix (empty when the category adds no guidance).

    Raises:
        CategoryResolutionError: unknown category, unmet ``requires_models``,
            or a ``models`` chain with no configured member.
    """
    category = get_category(category_name, app_config)
    if category is None or not isinstance(category, SubagentCategoryConfig):
        available = ", ".join(list_category_names(app_config)) or "none"
        raise CategoryResolutionError(f"Unknown task category '{category_name}'. Available: {available}")

    lookup = _model_lookup(app_config)
    if category.requires_models:
        if lookup is None:
            raise CategoryResolutionError(f"Task category '{category_name}' requires configured models ({', '.join(category.requires_models)}), but no application model configuration is available in this runtime.")
        missing = [name for name in category.requires_models if lookup(name) is None]
        if missing:
            raise CategoryResolutionError(f"Task category '{category_name}' is unavailable: required model(s) not configured: {', '.join(missing)}")

    overrides: dict[str, Any] = {}
    if category.models:
        if lookup is None:
            raise CategoryResolutionError(f"Task category '{category_name}' selects a model chain, but no application model configuration is available in this runtime.")
        selected = next((name for name in category.models if lookup(name) is not None), None)
        if selected is None:
            raise CategoryResolutionError(f"Task category '{category_name}' has no configured model: none of {', '.join(category.models)} resolve in top-level `models:`.")
        overrides["model"] = selected
    if category.skills is not None:
        overrides["skills"] = list(category.skills)
    if category.tools is not None:
        overrides["tools"] = list(category.tools)
    if category.max_turns is not None:
        overrides["max_turns"] = category.max_turns
    if category.timeout_seconds is not None:
        overrides["timeout_seconds"] = category.timeout_seconds
    # Sticky denials: union only, so a category can never lift a denial the
    # subagent definition (or its default) carries — nested `task` stays off.
    denied = set(getattr(base_config, "disallowed_tools", None) or [])
    denied |= set(category.disallowed_tools or [])
    if denied != set(getattr(base_config, "disallowed_tools", None) or []):
        overrides["disallowed_tools"] = sorted(denied)

    suffix = ""
    if category.prompt_append and category.prompt_append.strip():
        suffix = f"\n\n[Category preset '{category_name}' guidance (operator configuration):\n{category.prompt_append.strip()}\n]"
    return CategoryResolution(config_overrides=overrides, prompt_suffix=suffix)


# Built-in presets. Behavior-only (no model pins) so they work on every
# deployment; operators add model chains via `subagents.categories`.
BUILTIN_CATEGORIES: dict[str, SubagentCategoryConfig] = {
    "general": SubagentCategoryConfig(
        description="Default intent preset: no changes, the subagent runs with its own configuration.",
    ),
    "research": SubagentCategoryConfig(
        description="Thorough multi-source investigation with verified claims.",
        prompt_append=("Be thorough: consult multiple sources, verify load-bearing claims against primary evidence, and report uncertainty and contradictions explicitly instead of smoothing them over."),
        max_turns=100,
    ),
    "quick": SubagentCategoryConfig(
        description="Terse low-latency execution for small bounded tasks.",
        prompt_append=("Optimize for latency: be terse, skip exhaustive verification, and prefer the cheapest credible path that satisfies the task."),
        max_turns=30,
    ),
}
