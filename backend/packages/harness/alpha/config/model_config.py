from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    supports_reasoning_effort: bool = Field(default_factory=lambda: False, description="Whether the model supports reasoning effort")
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

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [str(entry).strip().lower() for entry in value]
        unknown = sorted({entry for entry in normalized if entry and entry not in MODEL_CAPABILITIES})
        if unknown:
            allowed = ", ".join(sorted(MODEL_CAPABILITIES))
            raise ValueError(f"unknown model capability(ies) {unknown}; allowed: {allowed}")
        return [entry for entry in normalized if entry]
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
