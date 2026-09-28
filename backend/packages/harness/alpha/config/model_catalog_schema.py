"""Model-catalog schema shared by ``config.yaml`` and the deprecated ``models.yaml``.

These are the three declarations that used to exist *only* in ``models.yaml``:
the bring-your-own-provider offers, the keyless gateway list, and the fallback
price table. ``config.yaml`` now carries them too, and this module is where the
shape lives so both loaders validate identically.

It is deliberately dependency-light — only pydantic and the two sibling config
models — so :mod:`alpha.config.app_config` can import it at module scope without
an import cycle back through :mod:`alpha.config.models_catalog`.

``models[]``, ``providers:``, ``default_model`` and ``model_routing`` are *not*
here: those already live on ``AppConfig`` and always did.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CatalogModelEntry",
    "CatalogProviderEntry",
    "FreeGatewayEntry",
    "ModelPriceEntry",
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
