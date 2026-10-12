import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.authz.provider import AuthzDecision, AuthzRequest
from alpha.community.url_safety import ModelEndpointBlockedError
from alpha.config.app_config import AppConfig
from alpha.config.reasoning_effort import CANONICAL_EFFORTS, EFFORT_LABELS
from alpha.models.effort_translation import effective_effort_support
from app.gateway.authz import (
    _AuthorizationUnavailable,
    _is_internal_caller,
    require_permission,
    resolve_model_authorization,
)
from app.gateway.deps import get_config, get_optional_user_from_request, require_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["models"])

_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to refresh a provider model catalog."


class ModelResponse(BaseModel):
    """Response model for model information."""

    name: str = Field(..., description="Unique identifier for the model")
    model: str = Field(..., description="Actual provider model identifier")
    display_name: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Model description")
    supports_thinking: bool = Field(default=False, description="Whether model supports thinking mode")
    supports_reasoning_effort: bool = Field(default=False, description="Whether model supports reasoning effort")
    #: ``None`` means **not reported**, which is a different fact from ``false``.
    #: The client renders the three states distinctly, so a capability nobody
    #: measured is never shown as an absence. Configured entries carry the
    #: operator's own declaration; augmented namespaces often have none.
    supports_vision: bool | None = Field(default=None, description="Whether the model accepts image input; null = not reported")
    #: Effective input window in tokens, or ``null`` when no namespace declared
    #: one. Kept separate from a published model card on purpose: a gateway can
    #: proxy an endpoint whose enforced window differs from the model's.
    context_window: int | None = Field(default=None, description="Effective input context window in tokens; null = not reported")
    #: USD per 1M tokens. Absent from either side means unknown, not zero; a
    #: genuine free model reports ``0.0``.
    input_price_per_million: float | None = Field(default=None, description="USD per 1M input tokens; null = not reported")
    output_price_per_million: float | None = Field(default=None, description="USD per 1M output tokens; null = not reported")
    reasoning_efforts: list[str] = Field(
        default_factory=list,
        description=(
            "The reasoning-effort rungs this model actually serves, weakest first. Empty means the entry declares no ladder, which the chat UI must render as 'no effort control' rather than offering rungs the factory would silently clamp."
        ),
    )
    default_reasoning_effort: str | None = Field(
        default=None,
        description="Rung used when a run requests no explicit effort; null lets the provider's own default apply.",
    )
    provider: str | None = Field(default=None, description="Provider identifier (e.g. ovhcloud, pollinations, groq, gemini)")
    is_free: bool = Field(default=False, description="Whether this model is free to use")
    quota_type: str | None = Field(default=None, description="Free quota category: keyless_free, recurring_free, free_gateway, trial_credits, paid, custom")
    free_status: str | None = Field(default=None, description="Availability/health status: online, degraded, offline, unknown")


class TokenUsageResponse(BaseModel):
    """Token usage display configuration."""

    enabled: bool = Field(default=False, description="Whether token usage display is enabled")


def _positive_int(value: Any) -> int | None:
    """A positive int, or ``None``. Rejects bools and non-positive numbers."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _positive_float(value: Any) -> float | None:
    """A non-negative float, or ``None``.

    Zero is **kept** on purpose: a genuinely free model reports ``0.0``, and
    that is a real price. Anything non-numeric is unknown, not free.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


def _pricing_value(model: Any, key: str) -> Any:
    """Read one price from a model's optional inline ``pricing`` block.

    ``ModelConfig`` is ``extra="allow"``, so the block may be absent entirely or
    carry a subset. Anything unreadable yields ``None`` (unknown) rather than a
    fabricated zero, because a wrong price silently misreports run cost.
    """
    pricing = getattr(model, "pricing", None)
    if not isinstance(pricing, dict):
        return None
    return pricing.get(key)


class ModelsListResponse(BaseModel):
    """Response model for listing all models."""

    models: list[ModelResponse]
    token_usage: TokenUsageResponse
    #: The canonical effort ladder, weakest → strongest. Shipped by the server so the
    #: client orders, labels, and offers rungs from one source instead of a
    #: hardcoded copy that drifts when a provider adds a level.
    reasoning_effort_levels: list[str] = Field(default_factory=lambda: list(CANONICAL_EFFORTS))
    #: Display label per rung, keyed by the canonical names above.
    reasoning_effort_labels: dict[str, str] = Field(default_factory=lambda: dict(EFFORT_LABELS))


# ---------------------------------------------------------------------------
# Provider model discovery
# ---------------------------------------------------------------------------


class DiscoveredModelResponse(BaseModel):
    """One model as reported by a provider's live catalog.

    The three context numbers stay separate on purpose: a gateway can proxy an
    endpoint whose real window differs from the published model card, and
    conflating them is how a summarization threshold ends up wrong.
    """

    id: str
    name: str = ""
    context_length: int | None = None
    endpoint_context_length: int | None = None
    endpoint_max_completion_tokens: int | None = None
    supports_vision: bool = False
    supports_thinking: bool = False
    reasoning_efforts: list[str] = Field(default_factory=list)
    supported_parameters: list[str] = Field(default_factory=list)
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    is_free: bool = False
    #: True when this id is already a configured, runnable model.
    configured: bool = False


class DiscoveryProviderResponse(BaseModel):
    """Discovery state for one provider."""

    provider: str
    ok: bool
    error: str | None = None
    fetched_at: float = 0.0
    age_seconds: float = 0.0
    stale: bool = True
    source_url: str | None = None
    model_count: int = 0
    free_count: int = 0
    models: list[DiscoveredModelResponse] = Field(default_factory=list)


class DiscoveryListResponse(BaseModel):
    providers: list[DiscoveryProviderResponse]
    #: Providers declared in config.yaml's `model_catalog:` that support discovery.
    supported: list[str] = Field(default_factory=list)


class RefreshDiscoveryRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64, description="Provider id from `supported`")


class AdoptModelRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=256, description="Discovered model id")
    #: Optional name for the new config entry; defaults to the model id.
    name: str | None = Field(default=None, max_length=128)
    #: Add as the deployment default model. Rejected when false by default so a
    #: bulk import cannot silently switch every run's model.
    make_default: bool = False


def _discovery_view(state, configured_names: set[str], limit: int) -> DiscoveryProviderResponse:
    """Project a cached :class:`DiscoveryState` into the API shape."""
    models = []
    free_count = 0
    for raw in state.models[:limit]:
        if raw.get("is_free"):
            free_count += 1
        models.append(
            DiscoveredModelResponse(
                id=raw["id"],
                name=raw.get("name") or raw["id"],
                context_length=raw.get("context_length"),
                endpoint_context_length=raw.get("endpoint_context_length"),
                endpoint_max_completion_tokens=raw.get("endpoint_max_completion_tokens"),
                supports_vision=bool(raw.get("supports_vision")),
                supports_thinking=bool(raw.get("supports_thinking")),
                reasoning_efforts=raw.get("reasoning_efforts") or [],
                supported_parameters=raw.get("supported_parameters") or [],
                input_price_per_million=raw.get("input_price_per_million"),
                output_price_per_million=raw.get("output_price_per_million"),
                is_free=bool(raw.get("is_free")),
                configured=raw["id"] in configured_names,
            )
        )
    age = state.age_seconds()
    return DiscoveryProviderResponse(
        provider=state.provider,
        ok=state.ok,
        error=state.error,
        fetched_at=state.fetched_at,
        age_seconds=age,
        stale=not state.is_fresh(),
        source_url=state.source_url,
        model_count=len(state.models),
        free_count=free_count if state.ok else 0,
        models=models,
    )


@router.get(
    "/models/discovery",
    response_model=DiscoveryListResponse,
    summary="List models discovered from provider catalogs",
    description=(
        "Return the cached result of each provider's live model catalog, normalized to a "
        "single shape. Providers add and retire models continuously, so this is fetched from "
        "the provider on a TTL rather than hardcoded. A provider that cannot be reached is "
        "reported with `ok: false` and the reason — it never fails the whole response. "
        "`stale: true` means the cached copy is past its TTL and will be refetched on the next "
        "request. Use POST /models/discovery/refresh to force a refetch."
    ),
)
async def list_discovered_models(
    request: Request,
    config: AppConfig = Depends(get_config),
    provider: str | None = None,
    refresh: bool = False,
    limit: int = 200,
) -> DiscoveryListResponse:
    from alpha.models import discovery

    limit = max(1, min(limit, discovery.MAX_MODELS_PER_PROVIDER))
    configured_names = {m.name for m in config.models} | {m.model for m in config.models}

    targets = [provider] if provider else discovery.known_providers()
    if provider and provider not in discovery.known_providers():
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not discoverable. It must declare a `base_url` under `model_catalog:` in config.yaml. Discoverable: {', '.join(discovery.known_providers()) or 'none'}",
        )

    views: list[DiscoveryProviderResponse] = []
    for provider_id in targets:
        if refresh:
            try:
                base, key = discovery.configured_endpoint(provider_id)
            except ValueError as exc:
                views.append(
                    DiscoveryProviderResponse(
                        provider=provider_id,
                        ok=False,
                        error=str(exc),
                        stale=True,
                    )
                )
                continue
            state = await discovery.afetch_models(provider_id, base, key)
        else:
            state = await asyncio.to_thread(discovery.get_state, provider_id)
        views.append(_discovery_view(state, configured_names, limit))

    return DiscoveryListResponse(providers=views, supported=discovery.known_providers())


@router.post(
    "/models/discovery/refresh",
    response_model=DiscoveryProviderResponse,
    summary="Force a provider catalog refetch",
    description=("Refetch one provider's model catalog immediately, bypassing the TTL. Admin-only, because it makes an outbound request to a provider endpoint."),
)
async def refresh_discovered_models(request: Request, body: RefreshDiscoveryRequest) -> DiscoveryProviderResponse:
    from alpha.models import discovery

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    try:
        base, key = discovery.configured_endpoint(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = await discovery.afetch_models(body.provider, base, key)
    return _discovery_view(state, set(), discovery.MAX_MODELS_PER_PROVIDER)


@router.get(
    "/models",
    response_model=ModelsListResponse,
    summary="List All Models",
    description="Retrieve a list of all available AI models configured in the system.",
)
async def list_models(
    request: Request,
    config: AppConfig = Depends(get_config),
) -> ModelsListResponse:
    """List all available models from configuration.

    Returns model information suitable for frontend display,
    excluding sensitive fields like API keys and internal configuration.

    When ``authorization.enabled`` is true, only models the caller's role may
    ``list`` are returned (filtered via ``provider.filter_resources``). A
    provider error yields an empty list (fail-closed) or all models (fail-open).

    Returns:
        A list of all configured models with their metadata and token usage display settings.

    Example Response:
        ```json
        {
            "models": [
                {
                    "name": "gpt-4",
                    "model": "gpt-4",
                    "display_name": "GPT-4",
                    "description": "OpenAI GPT-4 model",
                    "supports_thinking": false,
                    "supports_reasoning_effort": false
                },
                {
                    "name": "claude-3-opus",
                    "model": "claude-3-opus",
                    "display_name": "Claude 3 Opus",
                    "description": "Anthropic Claude 3 Opus model",
                    "supports_thinking": true,
                    "supports_reasoning_effort": false
                }
            ],
            "token_usage": {
                "enabled": true
            }
        }
        ```
    """
    visible_models = config.models
    fail_closed = config.authorization.fail_closed

    user = await get_optional_user_from_request(request)
    if user is not None:
        try:
            provider, principal = resolve_model_authorization(user, is_internal=_is_internal_caller(request, user))
        except _AuthorizationUnavailable as exc:
            if exc.fail_closed:
                visible_models = []
        else:
            if provider is not None and principal is not None:
                try:
                    allowed_names = provider.filter_resources(principal, "model", [m.name for m in config.models])
                    if not isinstance(allowed_names, list) or any(not isinstance(n, str) for n in allowed_names):
                        raise TypeError("AuthorizationProvider.filter_resources must return list[str]")
                    allowed_set = set(allowed_names)
                    visible_models = [m for m in config.models if m.name in allowed_set]
                except Exception:
                    logger.warning("Authorization provider failed while filtering models", exc_info=True)
                    visible_models = [] if fail_closed else config.models

    models = []
    seen_names: set[str] = set()

    for model in visible_models:
        is_free_model = bool(model.name in ("alpha-free", "free") or model.name.startswith("free:") or model.name.startswith("alpha-free:") or getattr(model, "is_free", False))
        models.append(
            ModelResponse(
                name=model.name,
                model=model.model,
                display_name=model.display_name,
                description=model.description,
                supports_thinking=model.supports_thinking,
                supports_reasoning_effort=model.supports_reasoning_effort,
                reasoning_efforts=effective_effort_support(model),
                default_reasoning_effort=getattr(model, "default_reasoning_effort", None),
                # `None` for "not declared" rather than a false default: the
                # three states are rendered differently and a vision flag this
                # entry never set is not evidence the model cannot see images.
                supports_vision=getattr(model, "supports_vision", None),
                context_window=_positive_int(getattr(model, "context_window", None)),
                input_price_per_million=_positive_float(_pricing_value(model, "input_per_million")),
                output_price_per_million=_positive_float(_pricing_value(model, "output_per_million")),
                provider=getattr(model, "provider", None) or ("free" if is_free_model else None),
                is_free=is_free_model,
                quota_type="keyless" if is_free_model else "paid",
                free_status="online" if is_free_model else None,
            )
        )
        seen_names.add(model.name)

    # Augment with active provider models (configured API keys + custom models)
    try:
        from alpha.models.provider_manager import get_active_provider_models

        for apm in get_active_provider_models(config):
            if apm["name"] not in seen_names:
                models.append(
                    ModelResponse(
                        name=apm["name"],
                        model=apm["model"],
                        display_name=apm["display_name"],
                        description=apm["description"],
                        supports_thinking=apm["supports_thinking"],
                        # Read the augmented entry's own fields instead of
                        # hardcoding no: the provider catalog can declare an
                        # effort ladder, and asserting otherwise both hides a
                        # real capability and manufactures drift against a
                        # `models[]` entry of the same name.
                        supports_reasoning_effort=bool(apm.get("supports_reasoning_effort")),
                        reasoning_efforts=list(apm.get("reasoning_efforts") or []),
                        default_reasoning_effort=apm.get("default_reasoning_effort"),
                        supports_vision=apm.get("supports_vision"),
                        context_window=_positive_int(apm.get("context_window")),
                        provider=apm.get("provider"),
                        is_free=apm.get("is_free", False),
                        quota_type=apm.get("quota_type"),
                        free_status=apm.get("free_status", "online"),
                    )
                )
                seen_names.add(apm["name"])
    except Exception as exc:
        logger.debug("Could not augment models list with active provider models: %s", exc)

    # Augment with available keyless free models
    try:
        from alpha.models.free_router import get_free_router

        free_router = get_free_router()
        for fm in free_router.available_free_models():
            if fm["id"] not in seen_names:
                models.append(
                    ModelResponse(
                        name=fm["id"],
                        model=fm.get("model_id", fm.get("id", "auto")),
                        display_name=fm["name"],
                        description=fm.get("description") or f"Keyless free model via {fm['provider']}",
                        supports_thinking=bool(fm.get("supports_thinking", False)),
                        # The router forwards whatever reasoning the serving
                        # gateway returns and can send a rung, so the entry
                        # says so; the ladder stays empty because the served
                        # rungs depend on whichever gateway answers, which the
                        # client must render as "no fixed ladder" rather than
                        # inventing one.
                        supports_reasoning_effort=bool(fm.get("supports_reasoning_effort")),
                        supports_vision=fm.get("supports_vision"),
                        context_window=_positive_int(fm.get("context_window")),
                        provider=fm.get("provider"),
                        is_free=True,
                        quota_type="keyless",
                        free_status="online",
                    )
                )
                seen_names.add(fm["id"])
    except Exception as exc:
        logger.debug("Could not augment models list with free models: %s", exc)

    return ModelsListResponse(
        models=models,
        token_usage=TokenUsageResponse(enabled=config.token_usage.enabled),
    )


class MoaStatusResponse(BaseModel):
    """Honest runtime status view of the Mixture-of-Agents (MoA) subsystem."""

    capability_id: str = Field(..., description="Capability catalog id for the MoA engine")
    engine: dict | None = Field(..., description="moa_engine capability registry entry (enabled/loadable), or null when unavailable")
    engine_error: str | None = Field(None, description="Why the capability status could not be resolved")
    command: dict | None = Field(..., description="Real /moa slash-command entry: catalog data plus handler availability")
    command_error: str | None = Field(None, description="Why the /moa command entry could not be resolved")
    limits: dict = Field(..., description="Engine limits from alpha.deliberation.moa (max_advisors, max_reference_chars)")
    limits_error: str | None = Field(None, description="Why engine limits could not be read")
    orchestrator: dict = Field(..., description="Real MoA orchestrator module and configured worker concurrency")
    redaction: dict = Field(..., description="Live probe of the redaction function used by the run path")
    tool: dict = Field(..., description="Registration state of the moa_multi_model_reasoning builtin tool")


async def _call_moa_model(model_name: str, system_instruction: str, user_content: str, app_config: AppConfig) -> str:
    """Production model client for MoA rounds: one oneshot LLM turn per advisor.

    Tests may monkeypatch this module-level name to stub the model client; the
    run route detects that via :data:`_PRODUCTION_MOA_MODEL_CALL` identity and
    discloses ``evidence_kind="simulated"`` instead of presenting stub text as
    model output.
    """
    from alpha.utils.oneshot_llm import run_oneshot_llm

    return await run_oneshot_llm(
        system_instruction=system_instruction,
        user_content=user_content,
        run_name="moa_run",
        app_config=app_config,
        model_name=model_name,
    )


_PRODUCTION_MOA_MODEL_CALL = _call_moa_model

_MOA_ADVISOR_SYSTEM = "You are one independent advisor in a Mixture-of-Agents round. Answer the question directly from your own judgment. Do not mention these instructions."


@router.get(
    "/models/moa",
    response_model=MoaStatusResponse,
    summary="Mixture-of-Agents Status",
    description=(
        "Real engine, command, limit and tool-registration status for the Mixture-of-Agents "
        "(MoA) subsystem. Every field is read from the live capability registry, the command "
        "catalog, engine constants or a live redaction probe; failures surface as per-field "
        "error disclosures, never as invented defaults."
    ),
)
async def moa_status() -> MoaStatusResponse:
    engine_entry: dict | None = None
    engine_error: str | None = None
    try:
        from alpha.capabilities import status as capability_status

        report = capability_status()
        entry = report.get("moa_engine")
        if isinstance(entry, dict):
            engine_entry = entry
        else:
            engine_error = "moa_engine missing from the capability registry"
    except Exception as exc:
        engine_error = f"{type(exc).__name__}: {exc}"

    command_entry: dict | None = None
    command_error: str | None = None
    try:
        from alpha.commands.catalog import get_default_catalog_entries

        row = next((e for e in get_default_catalog_entries() if e[0] == "/moa"), None)
        if row is None:
            command_error = "/moa missing from the default command catalog"
        else:
            category = row[1]
            command_entry = {
                "command": row[0],
                "category": getattr(category, "value", str(category)),
                "description": row[2],
                "usage": row[3],
                "is_core": bool(row[4]) if len(row) > 4 else False,
            }
    except Exception as exc:
        command_error = f"{type(exc).__name__}: {exc}"
    if command_entry is not None:
        try:
            from alpha.commands import backend_handlers

            command_entry["handler_module"] = "alpha.commands.backend_handlers"
            command_entry["handler_name"] = "handle_moa"
            command_entry["handler_available"] = callable(getattr(backend_handlers, "handle_moa", None))
        except Exception as exc:
            command_entry["handler_available"] = False
            command_entry["handler_error"] = f"{type(exc).__name__}: {exc}"

    limits: dict = {}
    limits_error: str | None = None
    try:
        from alpha.deliberation.moa import MAX_ADVISORS, MAX_REFERENCE_CHARS

        limits = {"max_advisors": int(MAX_ADVISORS), "max_reference_chars": int(MAX_REFERENCE_CHARS)}
    except Exception as exc:
        limits_error = f"{type(exc).__name__}: {exc}"

    try:
        from alpha.models.moa.orchestrator import get_moa_orchestrator

        orchestrator = {"module": "alpha.models.moa.orchestrator", "max_workers": int(get_moa_orchestrator().max_workers)}
    except Exception as exc:
        orchestrator = {"module": "alpha.models.moa.orchestrator", "error": f"{type(exc).__name__}: {exc}"}

    redaction: dict
    try:
        from alpha.models.moa.redact import redact_pii_and_secrets

        redaction = {
            "module": "alpha.models.moa.redact",
            "email_masked": "[redacted email]" in redact_pii_and_secrets("reach me at probe@example.com"),
            "phone_masked": "[redacted phone]" in redact_pii_and_secrets("call 555-123-4567 today"),
            "secret_masked": "[redacted secret key]" in redact_pii_and_secrets("token ghp_1234567890abcdef1234567890abcdef1234"),
        }
    except Exception as exc:
        redaction = {"module": "alpha.models.moa.redact", "error": f"{type(exc).__name__}: {exc}"}

    tool: dict = {"name": "moa_multi_model_reasoning"}
    try:
        from alpha.tools.tools import BUILTIN_TOOLS, SUBAGENT_TOOLS

        registered_names = {getattr(t, "name", t if isinstance(t, str) else None) for t in (*BUILTIN_TOOLS, *SUBAGENT_TOOLS)}
        tool["registered"] = "moa_multi_model_reasoning" in registered_names
    except Exception as exc:
        tool["error"] = f"{type(exc).__name__}: {exc}"

    return MoaStatusResponse(
        capability_id="moa_engine",
        engine=engine_entry,
        engine_error=engine_error,
        command=command_entry,
        command_error=command_error,
        limits=limits,
        limits_error=limits_error,
        orchestrator=orchestrator,
        redaction=redaction,
        tool=tool,
    )


class MoaRunRequest(BaseModel):
    """Request body for a real Mixture-of-Agents round."""

    prompt: str = Field(..., min_length=1, description="Question to put to the advisor panel")
    candidate_models: list[str] = Field(..., min_length=1, description="Configured model names to consult in parallel (capped at engine MAX_ADVISORS)")


class MoaRunCandidate(BaseModel):
    """One advisor candidate exactly as the engine reported it (redacted by the engine)."""

    model_name: str
    success: bool
    response: str = ""
    error: str | None = None
    duration_ms: float = 0.0


class MoaRunResponse(BaseModel):
    """MoA round result with an explicit evidence disclosure."""

    prompt: str = Field(..., description="Engine-normalized (PII/secret-redacted) prompt actually used")
    consensus_response: str
    candidates: list[MoaRunCandidate]
    candidate_models: list[str]
    total_duration_ms: float
    evidence_kind: Literal["real", "simulated", "failed"] = Field(
        ...,
        description=(
            "'real' = outputs generated by the production model client during this request; "
            "'simulated' = the model client is a stub/injection (test or substitute), outputs are NOT model generations; "
            "'failed' = the production client was used but every candidate failed, so no model output exists."
        ),
    )
    evidence_note: str


class MoaAdvisorError(RuntimeError):
    """Advisor-level failure text that must not leak into model credentials."""


async def _call_moa_advisor(model_name: str, question: str, app_config: AppConfig) -> str:
    """Advisor call executed inside the engine's worker threads.

    A fresh event loop runs the async production client (worker threads have no
    running loop; the request loop is blocked waiting on ``to_thread``). Any
    exception is reduced to its type plus a REDACTED message through the same
    ``redact_pii_and_secrets`` the engine applies to prompts, so a provider
    error text carrying an email, phone or API key can never reach the
    response the client sees.
    """
    try:
        return await _call_moa_model(model_name, _MOA_ADVISOR_SYSTEM, question, app_config)
    except Exception as exc:
        try:
            from alpha.models.moa.redact import redact_pii_and_secrets

            safe = redact_pii_and_secrets(str(exc))
        except Exception:  # redaction must never mask the failure itself
            safe = f"<{type(exc).__name__} message withheld: redaction unavailable>"
        raise MoaAdvisorError(f"{type(exc).__name__}: {safe}") from None


@router.post(
    "/models/moa/run",
    response_model=MoaRunResponse,
    summary="Run a Mixture-of-Agents Round",
    description=(
        "Run one real Mixture-of-Agents round: parallel advisor candidates through the "
        "MoA orchestrator (PII/secret redaction applied by the engine) plus its consensus "
        "synthesis. Each candidate model passes the same model:use authorization as "
        "GET /api/models/{model_name}. The response discloses evidence_kind honestly: "
        "'simulated' whenever the model client in use is a stub (tests), 'failed' when the "
        "production client ran but produced no output."
    ),
)
@require_permission("runs", "create")
async def run_moa_round(
    body: MoaRunRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> MoaRunResponse:
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    from alpha.deliberation.moa import MAX_ADVISORS

    names = [m.strip() for m in body.candidate_models]
    if any(not n for n in names):
        raise HTTPException(status_code=422, detail="candidate_models entries must be non-empty model names")
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="candidate_models must not repeat model names")
    if len(names) > MAX_ADVISORS:
        raise HTTPException(status_code=422, detail=f"Too many candidate models: {len(names)} > engine MAX_ADVISORS ({MAX_ADVISORS})")

    for name in names:
        # Reuses the existing get_model handler, so 404 (unknown) and 403
        # (model:use denied) behave exactly as GET /api/models/{model_name}.
        await get_model(name, request, config)

    import asyncio as _asyncio

    from alpha.models.moa.orchestrator import get_moa_orchestrator

    def _worker(model_name: str, question: str) -> str:
        return _asyncio.run(_call_moa_advisor(model_name, question, config))

    def _round():
        return get_moa_orchestrator().execute_moa_round(prompt, names, _worker)

    result = await _asyncio.to_thread(_round)

    candidates = [
        MoaRunCandidate(
            model_name=c.model_name,
            success=c.success,
            response=c.response or "",
            error=c.error,
            duration_ms=round(c.duration_ms, 2),
        )
        for c in result.candidates
    ]
    succeeded = sum(1 for c in candidates if c.success)

    if _call_moa_model is not _PRODUCTION_MOA_MODEL_CALL:
        evidence_kind: Literal["real", "simulated", "failed"] = "simulated"
        evidence_note = "The model client in use is a stub/injection - candidate outputs are simulated, not model generations."
    elif succeeded == 0:
        evidence_kind = "failed"
        evidence_note = "The production model client ran, but every candidate failed - no model output exists in this result."
    else:
        evidence_kind = "real"
        evidence_note = f"Production model client generated {succeeded}/{len(candidates)} candidate outputs in this request (engine-redacted)."

    return MoaRunResponse(
        prompt=result.prompt,
        consensus_response=result.consensus_response,
        candidates=candidates,
        candidate_models=list(names),
        total_duration_ms=round(result.total_duration_ms, 2),
        evidence_kind=evidence_kind,
        evidence_note=evidence_note,
    )


# ROUTE ORDER IS LOAD-BEARING. This single-segment literal must stay ABOVE the
# `/models/{model_name}` catch-all below. Starlette matches in registration
# order, so a catch-all declared first swallows this path and answers
# `404 {"detail": "Model 'providers' not found"}` - indistinguishable from a
# genuinely absent model. `frontend/src/lib/api.ts` `fetchProvidersCatalog`
# calls it, so the Settings provider catalog rendered that 404. Same reason
# `routers/skills.py` keeps its collection routes above `/skills/{skill_name}`.
# Pinned by tests/test_route_order_literal_above_catchall.py.
@router.get(
    "/models/providers",
    summary="List Supported LLM Providers & Configuration Status",
    description="Retrieve catalog of all supported providers (keyless, recurring free, gateways, paid, custom) and their configuration status.",
)
async def list_providers() -> list[dict]:
    import asyncio as _asyncio

    from alpha.models.provider_manager import get_providers_catalog

    return await _asyncio.to_thread(get_providers_catalog)


@router.get(
    "/models/{model_name}",
    response_model=ModelResponse,
    summary="Get Model Details",
    description="Retrieve detailed information about a specific AI model by its name.",
)
async def get_model(
    model_name: str,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> ModelResponse:
    """Get a specific model by name.

    Args:
        model_name: The unique name of the model to retrieve.

    Returns:
        Model information if found.

    Raises:
        HTTPException: 404 if model not found; 403 if the caller's role may not
        ``use`` the model (only when ``authorization.enabled`` is true). A
        provider resolution error yields 403 (fail-closed) or allows the request
        (fail-open), mirroring ``list_models``'s provider-error semantics.

    Example Response:
        ```json
        {
            "name": "gpt-4",
            "display_name": "GPT-4",
            "description": "OpenAI GPT-4 model",
            "supports_thinking": false
        }
        ```
    """
    model = config.get_model_config(model_name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    # Phase 3: enforce model:use authorization (deny → 403, not 404, since the
    # model exists but the role lacks permission to use it).
    fail_closed = config.authorization.fail_closed
    user = await get_optional_user_from_request(request)
    if user is not None:
        try:
            provider, principal = resolve_model_authorization(user, is_internal=_is_internal_caller(request, user))
        except _AuthorizationUnavailable:
            if fail_closed:
                raise HTTPException(status_code=403, detail=f"Model '{model_name}' is not available for your role")
        else:
            if provider is not None and principal is not None:
                try:
                    decision = provider.authorize(AuthzRequest(principal=principal, resource="model", action="use", target=model_name))
                    if not isinstance(decision, AuthzDecision):
                        raise TypeError("AuthorizationProvider.authorize must return AuthzDecision")
                    allowed = decision.allow
                except Exception:
                    logger.warning(
                        "Authorization provider failed while checking model:use for %s",
                        model_name,
                        exc_info=True,
                    )
                    allowed = not fail_closed
                if not allowed:
                    raise HTTPException(status_code=403, detail=f"Model '{model_name}' is not available for your role")

    # The detail route returned six fields while the list route returned the
    # declared effort ladder, so the same model read two ways disagreed: detail
    # reported no ladder for `alpha-free` and the list reported
    # `[low, medium, high]`. Both now project the same facts.
    return ModelResponse(
        name=model.name,
        model=model.model,
        display_name=model.display_name,
        description=model.description,
        supports_thinking=model.supports_thinking,
        supports_reasoning_effort=model.supports_reasoning_effort,
        reasoning_efforts=effective_effort_support(model),
        default_reasoning_effort=getattr(model, "default_reasoning_effort", None),
        supports_vision=getattr(model, "supports_vision", None),
        context_window=_positive_int(getattr(model, "context_window", None)),
        input_price_per_million=_positive_float(_pricing_value(model, "input_per_million")),
        output_price_per_million=_positive_float(_pricing_value(model, "output_per_million")),
    )


@router.get(
    "/models/local/health",
    summary="Local Endpoint Health",
    description="Probe an OpenAI-compatible local endpoint (Ollama, LM Studio, llama.cpp server). Loopback hosts only, unless allowlisted — never an arbitrary outbound probe.",
)
async def local_endpoint_health(base_url: str = "http://127.0.0.1:11434") -> dict:
    import asyncio as _asyncio

    def _probe():
        from alpha.models.local import probe_openai_compatible

        return probe_openai_compatible(base_url).to_dict()

    return await _asyncio.to_thread(_probe)


@router.get(
    "/models/free/catalog",
    summary="Free LLM Provider Catalog",
    description=(
        "Keyless free-LLM router view: per-provider tri-state health "
        "(true=proven, false=failing, null=unknown), discovery status, cooldowns, "
        "and the providers currently eligible for chat. Reachability on anonymous "
        "gateways is never guaranteed — this endpoint reports what was actually "
        "observed, it does not promise availability."
    ),
)
async def free_llm_catalog(refresh: bool = False, probe: bool = False) -> dict:
    """Honest catalog/health view of the free-LLM router.

    Args:
        refresh: force a catalog re-discovery before answering.
        probe: run small liveness probes first (failed probes stay
            inconclusive and never flip a proven-healthy provider down).

    Returns:
        The router's disclosed catalog view (providers, health, eligible
        candidates, selection-method note). Discovery/probe failures surface
        as honest per-provider error fields, not HTTP errors.
    """
    import asyncio as _asyncio

    def _view() -> dict:
        from alpha.models.free_router import get_free_router

        router = get_free_router()
        if refresh:
            router.refresh(force=True)
        probes = router.probe() if probe else None
        view = router.catalog_dict()
        if probes is not None:
            view["probes"] = probes
        return view

    return await _asyncio.to_thread(_view)


class ConfigureProviderRequest(BaseModel):
    provider: str = Field(..., description="Provider ID (e.g. groq, gemini, openrouter, custom)")
    api_key: str | None = Field(None, description="API Key for the provider (avoid on shared hosts: prefer api_key_env)")
    api_key_env: str | None = Field(
        default=None,
        description=(
            "Name of the environment variable that already holds the key (e.g. OPENROUTER_API_KEY). "
            "The key is then never sent through this API nor written into the credentials file - only the "
            "variable's name is stored, and the value is read from the environment at use time."
        ),
    )
    base_url: str | None = Field(
        default=None,
        description=(
            "Custom base URL. Screened by the shared egress policy before it is stored: non-http(s) schemes, URL-embedded credentials, cloud metadata endpoints and (unless kind='local') loopback/private hosts are rejected with 400."
        ),
    )
    model_id: str | None = Field(None, description="Custom model identifier")
    display_name: str | None = Field(None, description="Custom model display name")
    kind: Literal["remote", "local"] | None = Field(
        default=None,
        description=(
            "Endpoint tier. 'remote' (default for most providers) requires a public endpoint; 'local' is the "
            "declared opt-in for a same-host OpenAI-compatible server (Ollama/LM Studio/vLLM on loopback). "
            "Cloud metadata endpoints are refused in both tiers."
        ),
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description=("Extra request headers for this endpoint (e.g. OpenRouter's HTTP-Referer/X-Title). Framing and connection headers (Host, Content-Length, Connection, ...) are rejected, and values may not contain CR/LF."),
    )
    remove: bool = Field(default=False, description="Whether to remove the key/model")


@router.post(
    "/models/providers/configure",
    summary="Configure LLM Provider Credentials or Custom Model",
    description=(
        "Save or remove API keys for a provider and dynamically register associated models. "
        "A caller-supplied base_url is screened against the SSRF egress policy (metadata, loopback, private "
        "and non-http(s) targets are refused with 400 unless the provider is a declared local endpoint), and "
        "the key is stored in an encrypted envelope rather than plaintext."
    ),
)
async def configure_provider_endpoint(body: ConfigureProviderRequest) -> dict:
    import asyncio as _asyncio

    from alpha.models.provider_manager import configure_provider

    def _sync():
        return configure_provider(
            provider_id=body.provider,
            api_key=body.api_key,
            base_url=body.base_url,
            model_id=body.model_id,
            display_name=body.display_name,
            remove=body.remove,
            api_key_env=body.api_key_env,
            kind=body.kind,
            headers=body.headers,
        )

    try:
        return await _asyncio.to_thread(_sync)
    except ModelEndpointBlockedError as exc:
        # A refused endpoint is a client error and the reason is safe to echo:
        # the message is built from the policy, and any URL userinfo is stripped.
        logger.warning("Refused model endpoint for provider %s: %s", body.provider, exc.reason)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to configure provider %s: %s", body.provider, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save provider configuration")


@router.get(
    "/models/providers/credentials-storage",
    summary="Provider Credential Storage Status",
    description=(
        "Honest, per-host disclosure of how provider credentials are protected at rest: the active backend "
        "(Windows DPAPI user scope, a 0600 key file, or plaintext when no cipher is available), whether the "
        "on-disk file is still a legacy plaintext document awaiting migration, and the file path. Contains no "
        "key material."
    ),
)
async def provider_credentials_storage_status() -> dict:
    import asyncio as _asyncio

    from alpha.models.provider_manager import credentials_storage_status, migrate_plaintext_credentials_file

    def _status() -> dict:
        migrated = migrate_plaintext_credentials_file()
        status = credentials_storage_status()
        status["migrated_plaintext_on_request"] = migrated
        return status

    return await _asyncio.to_thread(_status)


@router.post(
    "/models/free/probe",
    summary="Probe and Sync Free LLM Models",
    description="Trigger a fresh probe of all keyless free providers and sync today's available models.",
)
async def probe_free_models_endpoint() -> dict:
    import asyncio as _asyncio

    def _probe_and_sync():
        from alpha.models.free_router import get_free_router

        router = get_free_router()
        # sync_daily_models() refreshes discovery *then* probes, so it is the only
        # ordering that can produce a meaningful probe. This route used to call
        # router.probe() first, which on a cold router could only ever return
        # `{}` (a provider is probeable only once its model list is known), and
        # then discarded the real probes that the following sync produced -
        # reporting HTTP 200 with an empty probe map next to a synced count and
        # an advertised `alpha-free` model. One call, one honest result.
        synced = router.sync_daily_models(force_probe=True)
        return {
            "probes": synced.get("probes") or {},
            "sync_ok": bool(synced.get("ok")),
            "sync_error": synced.get("error"),
            "available_models": router.available_free_models(),
        }

    return await _asyncio.to_thread(_probe_and_sync)
