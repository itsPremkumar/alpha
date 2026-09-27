"""Config-declared model routing: map intents and cost tiers to *your* models.

The routing tables in :mod:`alpha.models.task_router`,
:mod:`alpha.models.category_router` and :mod:`alpha.models.workforce_router`
used to carry hardcoded provider model ids (``gpt-4o``, ``claude-opus-5``,
``ollama/qwen3:32b``, ...). Those names exist in no operator's ``models[]``,
so a routing decision could name a model that :func:`alpha.models.factory.create_chat_model`
rejects — and the caller's only signal was a warning as the lead agent silently
degraded to the default model.

This section is the fix: an operator declares which of *their* configured models
serve which intent category and cost tier, and the routers resolve against
``models[]``. Nothing is hardcoded, and an unresolvable name is a config-load
error rather than a runtime surprise.

Mirrors the mechanism the rest of the field converged on — Continue's
``roles:``, Aider's ``weak_model_name``/``editor_model_name``, OpenHands'
``usage_id`` registry, Letta's required ``model`` + ``embedding`` slots — all of
which route by *declared slot* rather than by vendor model string.
"""

from pydantic import BaseModel, Field, field_validator


class ModelRoutingConfig(BaseModel):
    """Operator-declared intent/tier -> configured-model mappings.

    ``categories`` keys are intent categories (``quick``, ``deep``,
    ``ultrabrain``, ``writing``, ``artistry``, ``visual-engineering``,
    ``unspecified-low``, ``unspecified-high``); ``tiers`` keys are cost tiers
    (``frontier``, ``coding``, ``fast``, ``local``). Both values are ordered
    lists of names from ``models[]``: index 0 is the primary, the remainder are
    fallbacks tried in order.

    Every name is validated against ``models[]`` at config load by
    :meth:`alpha.config.app_config.AppConfig._validate_model_routing`, so a
    typo fails the Gateway at startup instead of degrading a routed turn to the
    default model at request time.
    """

    enabled: bool = Field(
        default=True,
        description=("Master switch for intent/tier model routing. When false the routers return the static built-in chains unchanged (advisory only) and no declared mapping is consulted."),
    )
    default_model: str | None = Field(
        default=None,
        description=("Model used when a requested category or tier has no entry in `categories` / `tiers` and no chain member resolves. Must name an entry in `models`. Defaults to the configured `default_model`."),
    )
    categories: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Intent category -> ordered configured model names (primary first).",
    )
    tiers: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Cost tier (frontier/coding/fast/local) -> ordered configured model names (primary first).",
    )

    @field_validator("categories", "tiers")
    @classmethod
    def _reject_empty_chains(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        """A declared route with no usable model is a config error, not a no-op.

        An empty list would otherwise read as "declared but unresolvable" and
        silently fall through to the default model, which is the exact silent
        degradation this section exists to remove. Strip blank names so
        ``["", "  "]`` cannot masquerade as a configured chain.
        """
        cleaned: dict[str, list[str]] = {}
        for key, names in value.items():
            normalized = [name.strip() for name in names if isinstance(name, str) and name.strip()]
            if not normalized:
                raise ValueError(f"model_routing entry '{key}' has no model names. Remove the entry or list at least one name from `models`.")
            cleaned[key.strip().lower()] = normalized
        return cleaned

    def chain_for_category(self, category: str) -> list[str]:
        """Ordered configured models for an intent category, or ``[]``."""
        return list(self.categories.get((category or "").strip().lower(), []))

    def chain_for_tier(self, tier: str) -> list[str]:
        """Ordered configured models for a cost tier, or ``[]``."""
        return list(self.tiers.get((tier or "").strip().lower(), []))
