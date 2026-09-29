"""Model-catalog schema shared by ``config.yaml`` and the deprecated ``models.yaml``.

These are the three declarations that used to exist *only* in ``models.yaml``:
the bring-your-own-provider offers, the keyless gateway list, and the fallback
price table. ``config.yaml`` now carries them too, and this module is where the
shape lives so both loaders validate identically.

It is deliberately dependency-light — only pydantic and the two sibling config
models — so :mod:`alpha.config.app_config` can import it at module scope without
an import cycle back through :mod:`alpha.config.models_catalog`.

``models[]``, ``providers:``, ``default_model`` and ``model_routing`` are *not*
here: those already live on ``AppConfig`` and always did. The one piece of
``models[]`` that is here is :class:`ModelPricing`, the value of the inline
``models[].pricing`` block. It belongs beside :class:`ModelPriceEntry` because
the two are the same fact at two scopes — same unit, same currency rules — and
a reader comparing them must not have to open a second file to learn that they
use *different key names*.

:func:`format_pricing_error` renders a :class:`ModelPricing` failure as one
sentence naming the model and the offending key, so the caller can raise a
load-time error a config author can act on.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "CatalogModelEntry",
    "CatalogProviderEntry",
    "FreeGatewayEntry",
    "ModelPriceEntry",
    "ModelPricing",
    "format_pricing_error",
]


class CatalogModelEntry(BaseModel):
    """One model offered by a provider in the bring-your-own-provider catalog.

    These are *catalog* entries — what the Settings picker offers — not
    necessarily what a run executes. A name that also exists in ``models[]``
    must declare identical capabilities, which
    :mod:`alpha.models.catalog_consistency` enforces.
    """

    id: str = Field(..., min_length=1, description="Catalog identifier, unique across the catalog")
    name: str = Field(..., min_length=1, description="Human-readable display name")
    model_id: str = Field(..., min_length=1, description="Provider-side model identifier")
    supports_thinking: bool = Field(default=False, description="Whether this model supports extended thinking")
    supports_vision: bool | None = Field(default=None, description="Whether this model accepts image input")
    context_window: int | None = Field(default=None, gt=0, description="Total context capacity in tokens")
    description: str | None = Field(default=None, description="One-line description shown in the picker")

    model_config = ConfigDict(extra="forbid")


class CatalogProviderEntry(BaseModel):
    """A bring-your-own-provider the operator can attach a key to."""

    id: str = Field(..., min_length=1, description="Unique provider id")
    name: str = Field(..., min_length=1, description="Human-readable provider name")
    category: str = Field(
        ...,
        description="One of: keyless_free, recurring_free, free_gateway, trial_credits, paid, custom",
    )
    key_env: str | None = Field(default=None, description="Environment variable holding the API key; null for keyless providers")
    portal_url: str = Field(default="", description="Where the operator obtains a key")
    free_tier_note: str = Field(default="", description="Free-tier terms shown in the picker")
    base_url: str | None = Field(default=None, description="Default OpenAI-compatible endpoint")
    default_use: str = Field(default="langchain_openai:ChatOpenAI", description="Model class path used to build this provider's clients")
    models: list[CatalogModelEntry] = Field(default_factory=list, description="Models this provider offers")

    model_config = ConfigDict(extra="forbid")

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        allowed = {"keyless_free", "recurring_free", "free_gateway", "trial_credits", "paid", "custom"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"unknown provider category '{value}'; allowed: {', '.join(sorted(allowed))}")
        return normalized


class FreeGatewayEntry(BaseModel):
    """A keyless public gateway the free router may fall back to.

    Never carries a personal credential: ``auth_header`` holds anonymous
    constants only (some gateways require a syntactically valid but meaningless
    Authorization value).
    """

    id: str = Field(..., min_length=1, description="Unique gateway id")
    base_url: str = Field(..., min_length=1, description="OpenAI-compatible base URL")
    chat_path: str | None = Field(default=None, description="Relative chat path; null means provider-specific schema")
    models_path: str | None = Field(default=None, description="Relative catalog path; null means no public catalog")
    auth_header: list[list[str]] = Field(
        default_factory=list,
        description="Anonymous constant headers as [name, value] pairs. Never a personal credential.",
    )
    documented_models: list[str] = Field(default_factory=list, description="Model ids documented as free when the catalog omits them")
    openai_compat: bool = Field(default=True, description="Whether the gateway speaks the OpenAI chat schema")

    model_config = ConfigDict(extra="forbid")

    @field_validator("auth_header")
    @classmethod
    def _pairs(cls, value: list[list[str]]) -> list[list[str]]:
        for entry in value:
            if not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(part, str) for part in entry):
                raise ValueError("auth_header entries must be [name, value] string pairs")
        return value


class ModelPriceEntry(BaseModel):
    """Fallback price for a model name, used when ``models[].pricing`` omits it.

    Prices are per one million tokens, matching ``models[].pricing`` so the two
    sources never need conversion. Keep ``models[].pricing`` authoritative for
    anything you actually run: this table is a fallback so a console run of an
    unpriced model still gets an estimate instead of ``None``.
    """

    input: float = Field(..., ge=0, description="Price per 1M input tokens (cache miss)")
    output: float = Field(..., ge=0, description="Price per 1M output tokens")

    model_config = ConfigDict(extra="forbid")


class ModelPricing(BaseModel):
    """Per-model price block, read from ``models[].pricing`` by the console.

    ``ModelConfig`` is ``extra="allow"`` — it must stay that way, because
    operators legitimately pass provider kwargs (``api_key``, ``base_url``,
    ``max_tokens``, ...) that Alpha knows nothing about. ``pricing`` is the one
    Alpha key hiding in that open map, and that combination is why a typo used
    to be invisible: ``pricing: {inpt_per_million: 1.0}`` parsed cleanly,
    survived the load, and produced ``total_cost: null`` from every console route
    with no error anywhere. A shipped key that silently does nothing is worse
    than an absent key, because it tells the operator their config is in effect
    when it is not.

    So the value gets a real schema and *this model only* is
    ``extra="forbid"``. The surrounding extras map stays open. The field names
    are exactly the ones the console's cost display reads
    (``app/gateway/routers/console.py::_build_pricing_map``); they are not
    spelled independently here, which is the whole point — a schema that
    declared different names than the consumer would make a working config fail.

    Note the deliberate asymmetry with :class:`ModelPriceEntry`, which is the
    same unit under the *fallback* table: the fallback is keyed by bare model
    name and therefore cannot carry a currency, so it says ``input``/``output``,
    while an inline block is display metadata and says ``*_per_million`` plus a
    ``currency``. Neither is a typo for the other; they are two scopes.
    """

    currency: str = Field(
        default="USD",
        description=("ISO code shown next to every cost figure. Use ONE currency across all priced models: a mixed-currency config disables cost reporting entirely rather than summing incomparable numbers."),
    )
    input_per_million: float = Field(
        default=0.0,
        ge=0,
        description="Price per 1M input tokens on a cache miss.",
    )
    output_per_million: float = Field(
        default=0.0,
        ge=0,
        description="Price per 1M output tokens.",
    )
    input_cache_hit_per_million: float | None = Field(
        default=None,
        ge=0,
        description=("Price per 1M prompt-cache-hit input tokens. Omit to bill cache hits at the full (miss) input price, which over-estimates rather than inventing a discount the operator never declared."),
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        """Upper-case so ``cny`` and ``CNY`` are one currency, not two.

        The console compares currencies to decide whether it may sum them, and it
        does that comparison on the normalized value. Normalizing here rather than
        at the read site means the stored config and the displayed currency are the
        same string, so a mixed-currency detection cannot disagree with what the
        operator sees.
        """
        normalized = str(value).strip().upper()
        if not normalized:
            raise ValueError("`currency` must be a non-empty ISO code (e.g. USD, CNY); omit the key to use USD")
        return normalized

    @property
    def is_declaration_only(self) -> bool:
        """True when the block names no price at all, so it cannot price anything.

        ``{currency: USD}`` alone is the shape an operator writes while stubbing an
        entry out. It is tolerated rather than rejected, because an all-zero price
        is a legitimate free-model assertion, and the console already skips such an
        entry (it will not report a cost of zero for a model that has none).
        """
        return self.input_per_million <= 0 and self.output_per_million <= 0


def format_pricing_error(exc: ValidationError, *, model_name: str) -> str:
    """Render a ``models[].pricing`` failure as one actionable sentence.

    Pydantic's own message for an extra key is "Extra inputs are not permitted",
    which names the field the operator misspelled only deep in a nested
    ``loc`` tuple. A config author scanning a load error needs the *model* and
    the *key* in the sentence, so both are put there and the allowed keys are
    listed — the fix is usually a rename, and the allowed set is the rename
    target.

    ``did you mean`` is included only when a single close match exists, because a
    confident wrong suggestion is worse than none.
    """
    import difflib

    allowed = list(ModelPricing.model_fields)
    problems: list[str] = []
    for error in exc.errors():
        location = error.get("loc") or ()
        key = str(location[0]) if location else "<pricing>"
        if error.get("type") == "extra_forbidden":
            close = difflib.get_close_matches(key, allowed, n=1, cutoff=0.6)
            hint = f"; did you mean {close[0]!r}?" if close else ""
            problems.append(f"unknown key {key!r} (allowed: {', '.join(allowed)}){hint}")
        else:
            problems.append(f"{key}: {error.get('msg', 'invalid value')}")
    detail = "; ".join(problems) or "invalid pricing block"
    return f"Model {model_name!r} has an invalid `pricing` block: {detail}. Prices are per 1M tokens."
