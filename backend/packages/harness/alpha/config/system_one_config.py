"""Configuration for System One models (Jev by TypeSafe AI).

System One models are a different class of model from LLMs: they do not
generate text. They take a ``state`` plus a map of typed ``questions`` and
return typed, calibrated decisions (``choice`` / ``score`` / ``boolean``)
with probabilities and a confidence score. They are 70-500ms, cannot
hallucinate or produce type errors, and are designed to be used as
"smart if-statements" inside ordinary software.

Alpha uses them as the *default* decision engine for classification,
routing, scoring and verification, falling back to the existing heuristic
or LLM path whenever System One is disabled, unavailable, or not confident
enough to act on.

See https://docs.typesafe.ai/concepts/system-one for the model class, and
https://vercel.com/ai-gateway/models/jev for the gateway provider.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

# Provider presets. The Vercel AI Gateway route is the free/default one.
PROVIDER_VERCEL_GATEWAY = "vercel-gateway"
PROVIDER_TYPESAFE = "typesafe"

_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
_TYPESAFE_BASE_URL = "https://api.typesafe.ai/v1"


class RiskTier(StrEnum):
    """How bad is it if this decision is wrong?

    Confidence thresholds are not one number. TypeSafe's own worked example
    accepts ~0.6 to show an account balance but requires >0.85 to approve a
    transfer, because the cost of a wrong classification differs by orders of
    magnitude. Every call site should name a tier rather than reuse a global.
    """

    READ = "read"                # a wrong answer costs a retry
    WRITE = "write"              # recoverable, but visible to the user
    DESTRUCTIVE = "destructive"  # irreversible


class SystemOneConfig(BaseModel):
    """Settings for the System One (Jev) decision engine."""

    enabled: bool = Field(
        default=True,
        description="Master switch. When False every call site uses its existing heuristic/LLM path directly.",
    )
    provider: str = Field(
        default=PROVIDER_VERCEL_GATEWAY,
        description=f"'{PROVIDER_VERCEL_GATEWAY}' (Vercel AI Gateway, currently free) or '{PROVIDER_TYPESAFE}' (TypeSafe direct API).",
    )
    base_url: str = Field(
        default=_GATEWAY_BASE_URL,
        description="Base URL of the System One API. Defaults to the provider preset; override to self-host or proxy.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key. Use '$ENV_VAR' (e.g. '$AI_GATEWAY_API_KEY') to resolve from the environment.",
    )
    model: str = Field(
        default="typesafe-ai/jev",
        description="Model id sent in the `model` field. 'typesafe-ai/jev' on the Vercel gateway; 'jev-latest' on the TypeSafe API.",
    )
    timeout_ms: int = Field(
        default=2000,
        ge=100,
        le=60_000,
        description="Per-request timeout. System One answers in 70-500ms, so a short budget keeps the fast path fast.",
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
    browser_require_fresh: bool = Field(
        default=True,
        description=(
            "Re-verify the page has not changed before executing a browser decision, and re-decide if it has. "
            "Protects against the silent failure where a click lands on whatever now occupies that index."
        ),
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
        description=(
            "Consecutive browser actions that changed nothing before the run is called stuck. Catches loops where the "
            "agent tries several different actions that each do nothing, which 'same step repeated' misses."
        ),
    )
    browser_max_seconds: float = Field(
        default=120.0,
        ge=0,
        le=3600,
        description=(
            "Wall-clock budget for one browser run, in seconds. A step budget alone is not enough because each step can "
            "be slow (a text-model round trip, a page load). 0 disables the budget."
        ),
    )
    browser_recoveries_limit: int = Field(
        default=2,
        ge=0,
        le=10,
        description=(
            "Deterministic recovery attempts before a failed browser step ends the run: retry the action once, then "
            "re-observe and re-decide. 0 restores the old behaviour where the first failure stops the run."
        ),
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
        description="Cap on candidates sent to one ranking request (System One choice heads allow at most 255 options).",
    )
    max_rerank_candidates: int = Field(
        default=24,
        ge=2,
        le=255,
        description="Cap on memory/RAG candidates scored per rerank call; keep small because each pair needs signal.",
    )

    def threshold_for(self, tier: str | RiskTier) -> float:
        """Confidence floor for a risk tier, falling back to `min_confidence`.

        Unknown tiers degrade to the global floor rather than raising, so a typo
        in a new call site can never disable safety.
        """
        key = tier.value if isinstance(tier, RiskTier) else str(tier)
        return float(self.thresholds.get(key, self.min_confidence))
