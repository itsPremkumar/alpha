"""Configuration for System One models (Jev and local Laya).

System One models are a different class of model from LLMs: they do not
generate text. They take a ``state`` plus a map of typed ``questions`` and
return typed, calibrated decisions (``choice`` / ``score`` / ``boolean``)
with probabilities and a confidence score. They are fast, cannot hallucinate
or produce type errors, and are designed to be used as "smart if-statements"
inside ordinary software.

Alpha supports the hosted Jev routes and Convai Innovations' open-weight
`Laya <https://github.com/NandhaKishorM/laya>`_ server. Laya exposes the
same Jev-compatible ``/v1/systemone`` wire contract, so the client and all
call sites remain provider-neutral. Alpha uses System One as the *default*
decision engine for classification, routing, scoring and verification,
falling back to the existing heuristic or LLM path whenever it is disabled,
unavailable, or not confident enough to act on.

See https://docs.typesafe.ai/concepts/system-one for the hosted model class,
https://vercel.com/ai-gateway/models/jev for the gateway provider, and
https://github.com/NandhaKishorM/laya for the local model.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

# Provider presets. The Vercel AI Gateway route is the hosted default.
PROVIDER_VERCEL_GATEWAY = "vercel-gateway"
PROVIDER_TYPESAFE = "typesafe"
# Laya is self-hosted. Its server intentionally uses the TypeSafe/Jev
# `/v1/systemone` contract, so no second decision schema is needed here.
PROVIDER_LAYA = "laya"

_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
_TYPESAFE_BASE_URL = "https://api.typesafe.ai/v1"
# Loopback is the safe default for the local, optionally unauthenticated server.
# The setup helper writes the actual URL into config.yaml when the user chooses
# a different host or port.
_LAYA_BASE_URL = "http://127.0.0.1:8000"

_PROVIDER_BASE_URLS = {
    PROVIDER_VERCEL_GATEWAY: _GATEWAY_BASE_URL,
    PROVIDER_TYPESAFE: _TYPESAFE_BASE_URL,
    PROVIDER_LAYA: _LAYA_BASE_URL,
}
_PROVIDER_DEFAULT_MODELS = {
    PROVIDER_VERCEL_GATEWAY: "typesafe-ai/jev",
    PROVIDER_TYPESAFE: "jev-latest",
    # Empty means Laya's Router chooses English vs multilingual by script.
    PROVIDER_LAYA: "",
}


def provider_base_url(provider: str) -> str:
    """Return the documented base URL for a known provider preset."""
    return _PROVIDER_BASE_URLS.get(provider, _GATEWAY_BASE_URL)


def provider_default_model(provider: str) -> str:
    """Return the default model id (or empty string for Laya auto-routing)."""
    return _PROVIDER_DEFAULT_MODELS.get(provider, _PROVIDER_DEFAULT_MODELS[PROVIDER_VERCEL_GATEWAY])


class RiskTier(StrEnum):
    """How bad is it if this decision is wrong?

    Confidence thresholds are not one number. TypeSafe's own worked example
    accepts ~0.6 to show an account balance but requires >0.85 to approve a
    transfer, because the cost of a wrong classification differs by orders of
    magnitude. Every call site should name a tier rather than reuse a global.
    """

    READ = "read"  # a wrong answer costs a retry
    WRITE = "write"  # recoverable, but visible to the user
    DESTRUCTIVE = "destructive"  # irreversible


class SystemOneConfig(BaseModel):
    """Settings for the System One (Jev) decision engine."""

    enabled: bool = Field(
        default=True,
        description="Master switch. When False every call site uses its existing heuristic/LLM path directly.",
    )
    provider: str = Field(
        default=PROVIDER_VERCEL_GATEWAY,
        description=(f"'{PROVIDER_VERCEL_GATEWAY}' (Vercel AI Gateway), '{PROVIDER_TYPESAFE}' (TypeSafe direct API), or '{PROVIDER_LAYA}' (local Laya server)."),
    )
    base_url: str = Field(
        default=_GATEWAY_BASE_URL,
        description=("Base URL of the System One API. For provider: laya this is normally http://127.0.0.1:8000; the server appends /v1/systemone."),
    )
    api_key: str | None = Field(
        default=None,
        description=("API key. Use '$ENV_VAR' (e.g. '$AI_GATEWAY_API_KEY') to resolve from the environment. Optional for a loopback Laya server; set '$LAYA_API_KEY' when the server is protected."),
    )
    model: str = Field(
        default="typesafe-ai/jev",
        description=("Model id sent in the `model` field. Use 'typesafe-ai/jev' on Vercel, 'jev-latest' on TypeSafe, 'english'/'multilingual'/'typed-decisions' for Laya, or an empty string to let Laya auto-route."),
    )
    timeout_ms: int = Field(
        default=2000,
        ge=100,
        le=60_000,
        description="Per-request timeout. System One answers quickly, so a short budget keeps the fast path fast.",
    )
    laya_max_state_chars: int = Field(
        default=12_000,
        ge=1_000,
        le=50_000,
        description="Laya preflight cap. Requests larger than this abstain rather than letting a short checkpoint context truncate evidence silently.",
    )
    laya_max_request_chars: int = Field(
        default=24_000,
        ge=2_000,
        le=100_000,
        description="Laya serialized request cap, including state, instructions, criteria, and model metadata. Exceeding it abstains before HTTP.",
    )
    laya_max_questions: int = Field(
        default=64,
        ge=1,
        le=64,
        description="Maximum questions sent to the Laya HTTP server in one call.",
    )
    laya_max_choice_options: int = Field(
        default=20,
        ge=2,
        le=255,
        description="Laya preflight cap for one choice question; larger label spaces are sent to a bounded shortlist/partitioning path instead.",
    )
    laya_max_partition_requests: int = Field(
        default=16,
        ge=1,
        le=64,
        description="Maximum number of local Laya partition/final requests for one high-cardinality decision. Exceeding the budget returns None and preserves the caller's fallback.",
    )
    laya_max_partition_latency_ms: int = Field(
        default=60_000,
        ge=1_000,
        le=300_000,
        description="Total wall-clock budget for one local Laya partition/final tournament. Expiry returns None and preserves the caller's fallback.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries for transient failures (429 rate limit, 529 overloaded, 5xx, timeouts) with exponential backoff.",
    )
    min_confidence: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Below this confidence a decision is treated as 'not confident enough' and the caller falls back to the LLM/heuristic path.",
    )
    fail_open: bool = Field(
        default=True,
        description="On transport/fatal error: True degrades to the existing path (recommended); False makes decisions unavailable so callers use their own policy.",
    )
    circuit_breaker_threshold: int = Field(
        default=5,
        ge=1,
        description="Consecutive failures before the client short-circuits and stops calling out for `circuit_breaker_cooldown_s`.",
    )
    circuit_breaker_cooldown_s: float = Field(
        default=60.0,
        ge=0.0,
        description="Seconds to stay open after the breaker trips; after this the next call is allowed through as a probe.",
    )
    enable_skill_scan: bool = Field(default=True, description="Use System One for skill security scanning decisions.")
    enable_guardrails: bool = Field(default=True, description="Use System One for tool-call guardrail decisions.")
    enable_deliberation_router: bool = Field(default=True, description="Use System One for deliberation strategy routing.")
    enable_goal_analysis: bool = Field(default=True, description="Use System One for autoconfig goal/domain/complexity analysis.")
    enable_memory_escalation: bool = Field(default=True, description="Use System One to gate memory Tier-2 escalation.")
    enable_goal_completion: bool = Field(default=True, description="Use System One to evaluate goal completion.")
    log_decisions: bool = Field(
        default=False,
        description="Log every System One decision at DEBUG (state is truncated; never logs the API key).",
    )
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {"read": 0.60, "write": 0.75, "destructive": 0.90},
        description="Per-risk-tier confidence floors keyed by RiskTier value. Higher stakes demand higher confidence before acting.",
    )
    enable_browser_action: bool = Field(
        default=True,
        description="Use System One to choose browser operations and element targets (indexed action space).",
    )
    enable_computer_action: bool = Field(
        default=False,
        description="Use System One to choose Windows desktop operations and accessibility-element targets (indexed action space).",
    )
    browser_require_fresh: bool = Field(
        default=True,
        description=("Re-verify the page has not changed before executing a browser decision, and re-decide if it has. Protects against the silent failure where a click lands on whatever now occupies that index."),
    )
    browser_stale_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="How many times to re-decide when the page moves under a browser decision before giving up.",
    )
    browser_ineffective_limit: int = Field(
        default=3,
        ge=1,
        le=20,
        description=("Consecutive browser actions that changed nothing before the run is called stuck. Catches loops where the agent tries several different actions that each do nothing, which 'same step repeated' misses."),
    )
    browser_max_seconds: float = Field(
        default=120.0,
        ge=0,
        le=3600,
        description=("Wall-clock budget for one browser run, in seconds. A step budget alone is not enough because each step can be slow (a text-model round trip, a page load). 0 disables the budget."),
    )
    browser_recoveries_limit: int = Field(
        default=2,
        ge=0,
        le=10,
        description=("Deterministic recovery attempts before a failed browser step ends the run: retry the action once, then re-observe and re-decide. 0 restores the old behaviour where the first failure stops the run."),
    )
    enable_injection_scan: bool = Field(
        default=True,
        description="Use System One to detect prompt injection in fetched or browser-rendered content.",
    )
    enable_citation_support: bool = Field(
        default=True,
        description="Use System One to judge whether a cited receipt actually supports the claim it is attached to.",
    )
    enable_trace_verify: bool = Field(
        default=True,
        description="Use System One to verify the tool-call trace (right tool, right args, output actually used).",
    )
    enable_model_routing: bool = Field(
        default=True,
        description="Use System One to decide whether a prompt needs the flagship model or can run on a cheap one.",
    )
    enable_selection: bool = Field(
        default=True,
        description="Use System One to rank skill/tool candidates (coarse pass, then re-judge the top few with full text).",
    )
    enable_rag_rerank: bool = Field(
        default=True,
        description="Use System One to rerank memory / RAG candidates by relevance to the query.",
    )
    enable_compaction_retention: bool = Field(
        default=True,
        description="Use System One to decide which context survives compaction.",
    )
    enable_acceptance: bool = Field(
        default=True,
        description="Use System One to evaluate per-criterion acceptance of subagent results.",
    )
    enable_epistemic: bool = Field(
        default=True,
        description="Use System One calibrated probabilities as belief updates for epistemic/evidence scoring.",
    )
    shadow_mode: bool = Field(
        default=False,
        description="Compute and record decisions, but return None so every call site runs its existing path. Use this to measure before trusting.",
    )
    record_decisions: bool = Field(
        default=False,
        description="Append every decision to `calibration_log_path` as JSONL. Off by default; turn on with shadow_mode to measure before acting.",
    )
    calibration_log_path: str | None = Field(
        default=None,
        description="Path to the JSONL decision log. Relative paths resolve under the Alpha state directory. Requires `record_decisions`.",
    )

    max_selection_candidates: int = Field(
        default=60,
        ge=2,
        le=255,
        description="Cap on candidates sent to one ranking request (System One choice heads allow at most 255 options; local Laya applies its smaller laya_max_choice_options budget).",
    )
    max_rerank_candidates: int = Field(
        default=24,
        ge=2,
        le=255,
        description="Cap on memory/RAG candidates scored per rerank call; keep small because each pair needs signal.",
    )

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        if value not in _PROVIDER_BASE_URLS:
            supported = ", ".join(sorted(_PROVIDER_BASE_URLS))
            raise ValueError(f"unsupported System One provider {value!r}; choose one of: {supported}")
        return value

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> "SystemOneConfig":
        """Give each known provider safe local defaults without clobbering overrides."""
        if "base_url" not in self.model_fields_set:
            object.__setattr__(self, "base_url", provider_base_url(self.provider))
        if "model" not in self.model_fields_set:
            object.__setattr__(self, "model", provider_default_model(self.provider))
        return self

    def threshold_for(self, tier: str | RiskTier) -> float:
        """Confidence floor for a risk tier, falling back to `min_confidence`.

        Unknown tiers degrade to the global floor rather than raising, so a typo
        in a new call site can never disable safety.
        """
        key = tier.value if isinstance(tier, RiskTier) else str(tier)
        return float(self.thresholds.get(key, self.min_confidence))
