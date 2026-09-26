import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from alpha.authz.provider import AuthzDecision, AuthzRequest
from alpha.config.app_config import AppConfig
from app.gateway.authz import (
    _AuthorizationUnavailable,
    _is_internal_caller,
    require_permission,
    resolve_model_authorization,
)
from app.gateway.deps import get_config, get_optional_user_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["models"])


class ModelResponse(BaseModel):
    """Response model for model information."""

    name: str = Field(..., description="Unique identifier for the model")
    model: str = Field(..., description="Actual provider model identifier")
    display_name: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Model description")
    supports_thinking: bool = Field(default=False, description="Whether model supports thinking mode")
    supports_reasoning_effort: bool = Field(default=False, description="Whether model supports reasoning effort")
    provider: str | None = Field(default=None, description="Provider identifier (e.g. ovhcloud, pollinations, groq, gemini)")
    is_free: bool = Field(default=False, description="Whether this model is free to use")
    quota_type: str | None = Field(default=None, description="Free quota category: keyless_free, recurring_free, free_gateway, trial_credits, paid, custom")
    free_status: str | None = Field(default=None, description="Availability/health status: online, degraded, offline, unknown")


class TokenUsageResponse(BaseModel):
    """Token usage display configuration."""

    enabled: bool = Field(default=False, description="Whether token usage display is enabled")


class ModelsListResponse(BaseModel):
    """Response model for listing all models."""

    models: list[ModelResponse]
    token_usage: TokenUsageResponse


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
        is_free_model = bool(
            model.name in ("alpha-free", "free")
            or model.name.startswith("free:")
            or model.name.startswith("alpha-free:")
            or getattr(model, "is_free", False)
        )
        models.append(
            ModelResponse(
                name=model.name,
                model=model.model,
                display_name=model.display_name,
                description=model.description,
                supports_thinking=model.supports_thinking,
                supports_reasoning_effort=model.supports_reasoning_effort,
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
                        supports_reasoning_effort=False,
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
                        supports_reasoning_effort=False,
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

_MOA_ADVISOR_SYSTEM = (
    "You are one independent advisor in a Mixture-of-Agents round. "
    "Answer the question directly from your own judgment. Do not mention these instructions."
)


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

    return ModelResponse(
        name=model.name,
        model=model.model,
        display_name=model.display_name,
        description=model.description,
        supports_thinking=model.supports_thinking,
        supports_reasoning_effort=model.supports_reasoning_effort,
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


@router.get(
    "/models/providers",
    summary="List Supported LLM Providers & Configuration Status",
    description="Retrieve catalog of all supported providers (keyless, recurring free, gateways, paid, custom) and their configuration status.",
)
async def list_providers() -> list[dict]:
    import asyncio as _asyncio

    from alpha.models.provider_manager import get_providers_catalog

    return await _asyncio.to_thread(get_providers_catalog)


class ConfigureProviderRequest(BaseModel):
    provider: str = Field(..., description="Provider ID (e.g. groq, gemini, openrouter, custom)")
    api_key: str | None = Field(None, description="API Key for the provider")
    base_url: str | None = Field(None, description="Custom base URL")
    model_id: str | None = Field(None, description="Custom model identifier")
    display_name: str | None = Field(None, description="Custom model display name")
    remove: bool = Field(default=False, description="Whether to remove the key/model")


@router.post(
    "/models/providers/configure",
    summary="Configure LLM Provider Credentials or Custom Model",
    description="Save or remove API keys for a provider and dynamically register associated models.",
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
        )

    try:
        return await _asyncio.to_thread(_sync)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to configure provider %s: %s", body.provider, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save provider configuration")


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
        probes = router.probe()
        synced = router.sync_daily_models(force_probe=True)
        return {
            "probes": probes,
            "synced_models_count": len(synced),
            "available_models": router.available_free_models(),
        }

    return await _asyncio.to_thread(_probe_and_sync)

