from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from alpha.config.model_catalog_schema import ModelPricing, format_pricing_error
from alpha.config.reasoning_effort import EFFORT_LABELS, EFFORT_STYLES, canonical_order, is_effort, normalize_effort
from alpha.multimodal.capabilities import MODEL_CAPABILITIES


class ProviderConfig(BaseModel):
    """Named provider profile: shared connection defaults for model entries.

    A model entry references a profile via ``ModelConfig.provider`` and
    inherits every key it does not set itself (class path, endpoint, keys,
    timeouts, retries, headers, ...). Model-level keys always win, so a
    profile carries organization-wide defaults while each model keeps its
    identity (``name``/``model``/``display_name`` are never inherited).
    """

    name: str = Field(..., description="Unique provider name referenced by models[].provider")
    use: str | None = Field(
        default=None,
        description="Default model class path (e.g. langchain_openai:ChatOpenAI); a model entry's own `use` wins.",
    )
    model_config = ConfigDict(extra="allow")


class ModelConfig(BaseModel):
    """Config section for a model"""

    name: str = Field(..., description="Unique name for the model")
    display_name: str | None = Field(..., default_factory=lambda: None, description="Display name for the model")
    description: str | None = Field(..., default_factory=lambda: None, description="Description for the model")
    use: str | None = Field(
        default=None,
        description=("Class path of the model provider (e.g. langchain_openai:ChatOpenAI). May be omitted when `provider` names a profile that supplies it; the factory raises an actionable error when neither provides one."),
    )
    provider: str | None = Field(
        default=None,
        description=("Name of a top-level `providers:` profile whose keys act as defaults for this entry. Keys set on the model itself always take precedence; `name` is never inherited."),
    )
    fallbacks: list[str] | None = Field(
        default=None,
        max_length=5,
        description=(
            "Ordered fallback model names tried when this model fails with a "
            "retryable error (rate limit 429, server 5xx, timeout/connection "
            "failure). Entries reference other `models[]` names; chains resolve "
            "transitively with cycle detection. Empty/None disables failover."
        ),
    )
    model: str = Field(..., description="Model name")
    model_config = ConfigDict(extra="allow")
    use_responses_api: bool | None = Field(
        default=None,
        description="Whether to route OpenAI ChatOpenAI calls through the /v1/responses API",
    )
    output_version: str | None = Field(
        default=None,
        description="Structured output version for OpenAI responses content, e.g. responses/v1",
    )
    supports_thinking: bool = Field(default_factory=lambda: False, description="Whether the model supports thinking")
    supports_reasoning_effort: bool = Field(
        default_factory=lambda: False,
        description=("Whether the model accepts a reasoning-effort level. Redundant (and implied) when `reasoning_efforts` is declared; keep it for entries that accept an effort but let the provider choose which rungs exist."),
    )
    reasoning_efforts: list[str] | None = Field(
        default=None,
        description=(
            "The reasoning-effort rungs this model actually serves, weakest first. Declaring the "
            "ladder is the contract: a request above the ceiling is clamped down and a request "
            f"below the floor is raised to it (never a provider 400), and the picker in the chat "
            f"UI offers exactly these rungs. Allowed rungs: {', '.join(EFFORT_LABELS)}. Omit to "
            "declare nothing, which the UI renders as 'this model has no effort control' rather "
            "than offering a control the factory would silently ignore."
        ),
    )
    default_reasoning_effort: str | None = Field(
        default=None,
        description=("Rung used when a run requests no explicit effort. Must appear in `reasoning_efforts` when both are declared. Leave unset to let the provider's own default apply."),
    )
    reasoning_effort_style: str | None = Field(
        default=None,
        description=(
            "Which provider wire shape carries the rung. Omit (or `auto`) to detect it from the "
            f"model class. Allowed: {', '.join(EFFORT_STYLES)}. Set it explicitly only for a "
            "gateway whose effort knob does not match its SDK package — for example an "
            "OpenRouter-shaped endpoint served through an OpenAI-compatible client."
        ),
    )
    when_thinking_enabled: dict | None = Field(
        default_factory=lambda: None,
        description="Extra settings to be passed to the model when thinking is enabled",
    )
    when_thinking_disabled: dict | None = Field(
        default_factory=lambda: None,
        description="Extra settings to be passed to the model when thinking is disabled",
    )
    supports_vision: bool = Field(default_factory=lambda: False, description="Whether the model supports vision/image inputs")
    capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Multimodal capabilities this model serves in the T1 chain (alpha.multimodal.chain): "
            "any of tts, stt, image_gen, vision, ocr. wake_word is deliberately NOT a model capability "
            "(it is scored locally from streamed frames). Unknown values raise at config load — a typo "
            "must never silently disable a capability. Vision-capable models may also rely on supports_vision."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_inline_pricing(cls, data: Any) -> Any:
        """Give the one *known* extra a real schema, leaving the rest open.

        ``ModelConfig`` is ``extra="allow"`` and must stay that way: operators
        legitimately pass provider kwargs (``api_key``, ``base_url``,
        ``max_tokens``, ``temperature``, ``max_retries``, and every knob a
        provider adds later) that Alpha cannot enumerate, and deny-by-default
        would break every working install. ``pricing`` is the exception that
        makes the rule safe to have: it is *Alpha* metadata, not a provider
        kwarg, so Alpha owns its shape and must own its validation.

        Without this, ``pricing: {inpt_per_million: 1.0}`` parsed cleanly,
        survived the load, reached the console's cost display, matched nothing,
        and produced ``total_cost: null`` — the same class of bug as the
        ``model_pricing:`` table that shipped with eight entries and was read by
        nobody, one level down. A declared-and-validated value inside a
        free-form map is the standard way to keep both properties: strict where
        the key is known, open where it genuinely is not.

        Runs as ``mode="before"`` on the raw mapping because the value is an
        *extra*: pydantic would never hand an extra to a field validator, and a
        declared field would stop the operator's provider kwargs from being able
        to be keys of the same mapping. The validated, normalized result is
        written back as the plain dict the console's reader already expects, so
        no consumer has to learn about a new type.

        The message names the model and the offending key. A load error that
        says only "extra inputs are not permitted" cannot be acted on by
        someone who has a dozen model entries.
        """
        if not isinstance(data, dict):
            return data
        if "pricing" not in data or data["pricing"] is None:
            return data
        model_name = str(data.get("name") or "<unnamed>")
        try:
            parsed = ModelPricing.model_validate(data["pricing"])
        except ValidationError as exc:
            raise ValueError(format_pricing_error(exc, model_name=model_name)) from None
        data["pricing"] = parsed.model_dump()
        return data

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [str(entry).strip().lower() for entry in value]
        unknown = sorted({entry for entry in normalized if entry and entry not in MODEL_CAPABILITIES})
        if unknown:
            allowed = ", ".join(sorted(MODEL_CAPABILITIES))
            raise ValueError(f"unknown model capability(ies) {unknown}; allowed: {allowed}")
        return [entry for entry in normalized if entry]

    @field_validator("reasoning_efforts")
    @classmethod
    def _validate_reasoning_efforts(cls, value: list[str] | None) -> list[str] | None:
        """Reject a misspelled rung instead of dropping it.

        ``normalize_effort`` cannot tell "absent" from "unrecognized", and this
        is a hand-authored operator assertion, so a typo must fail at config
        load rather than quietly shrink the ladder. Aliases (``off``,
        ``x-high``, ``ultra``) are accepted and rewritten to canonical rungs.
        """
        if value is None:
            return None
        unknown = [str(entry) for entry in value if not is_effort(entry)]
        if unknown:
            raise ValueError(f"unknown reasoning effort(s) {unknown}; allowed: {', '.join(EFFORT_LABELS)}")
        ladder = canonical_order(value)
        if not ladder:
            raise ValueError("`reasoning_efforts` was declared but resolved to no usable rung; omit the key instead")
        return ladder

    @field_validator("default_reasoning_effort")
    @classmethod
    def _validate_default_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_effort(value)
        if normalized is None:
            raise ValueError(f"unknown default_reasoning_effort {value!r}; allowed: {', '.join(EFFORT_LABELS)}")
        return normalized

    @field_validator("reasoning_effort_style")
    @classmethod
    def _validate_reasoning_effort_style(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized not in EFFORT_STYLES:
            raise ValueError(f"unknown reasoning_effort_style {value!r}; allowed: {', '.join(EFFORT_STYLES)}")
        return normalized

    @model_validator(mode="after")
    def _validate_reasoning_effort_default(self):
        """A default outside the declared ladder is an operator error.

        Silently dropping it would run every request at the provider default
        while the operator believed they had pinned a level, so this raises.
        The reverse order is fine and common: a default with no ladder still
        applies, clamped by whatever the provider accepts.
        """
        default = self.default_reasoning_effort
        if default is None:
            return self
        if not is_effort(default):
            raise ValueError(f"unknown default_reasoning_effort {default!r}; allowed: {', '.join(EFFORT_LABELS)}")
        if self.reasoning_efforts and canonical_order([default])[0] not in self.reasoning_efforts:
            raise ValueError(f"default_reasoning_effort {default!r} is not in reasoning_efforts {self.reasoning_efforts}; add it to the declared ladder or drop one of the two keys")
        return self

    context_window: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Positive total context window size in tokens (prompt + completion). Used to compute the real-time "
            "context usage percentage displayed in the chat UI, and attached to the model's langchain profile "
            "(`max_input_tokens`) so fraction-based summarization triggers can resolve their thresholds for "
            "third-party OpenAI-compatible models that carry no built-in profile. Distinct from `max_tokens`, "
            "which is the per-call output cap passed to the provider. Leave unset if unknown; the UI will hide "
            "the percentage and fraction summarization clauses will degrade with a warning."
        ),
    )
    stream_chunk_timeout: float | None = Field(
        default=None,
        description=(
            "Maximum seconds to wait between successive streaming chunks before "
            "langchain-openai raises StreamChunkTimeoutError. None means use the "
            "factory default (240s for OpenAI-compatible clients). Tune higher for "
            "reasoning models with long thinking pauses; lower for latency-sensitive "
            "interactive endpoints. Has no effect on non-OpenAI-compatible providers."
        ),
    )
    thinking: dict | None = Field(
        default_factory=lambda: None,
        description=(
            "Thinking settings for the model. If provided, these settings will be passed to the model when thinking is enabled. "
            "This is a shortcut for `when_thinking_enabled` and will be merged with `when_thinking_enabled` if both are provided."
        ),
    )
