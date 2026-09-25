"""Provider manager and credentials persistence for LLM providers.

Supports:
- Keyless free providers (OVHcloud, Pollinations, LLM7, Vireonix, Cehpoint, BlockRun, AI Horde)
- Recurring $0 free quota providers (Gemini, Groq, SambaNova, Mistral, Cohere, Cloudflare)
- Free-model gateways (OpenRouter :free models, Bytez)
- Trial credit providers (NVIDIA NIM, Cerebras)
- Frontier/commercial providers (OpenAI, Anthropic, DeepSeek)
- Custom / Local OpenAI-compatible endpoints (Ollama, LM Studio, vLLM)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

CREDENTIALS_FILE_NAME = "provider_credentials.json"


@dataclass
class ModelDescriptor:
    id: str
    name: str
    model_id: str
    supports_thinking: bool = False
    description: str | None = None


@dataclass
class ProviderDescriptor:
    id: str
    name: str
    category: str  # "keyless_free" | "recurring_free" | "free_gateway" | "trial_credits" | "paid" | "custom"
    key_env: str | None
    portal_url: str
    free_tier_note: str
    default_base_url: str | None
    default_models: list[ModelDescriptor]
    default_use: str = "langchain_openai:ChatOpenAI"


PROVIDER_SPECS: list[ProviderDescriptor] = [
    # 1. Keyless Free Providers
    ProviderDescriptor(
        id="ovhcloud",
        name="OVHcloud AI Endpoints",
        category="keyless_free",
        key_env=None,
        portal_url="https://endpoints.kepler.ai.cloud.ovh.net/",
        free_tier_note="100% Free European sovereign endpoints. No API key required.",
        default_base_url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        default_models=[
            ModelDescriptor(
                id="free:ovhcloud:Meta-Llama-3_3-70B-Instruct",
                name="LLaMA 3.3 70B (OVHcloud Free)",
                model_id="Meta-Llama-3_3-70B-Instruct",
                description="Meta LLaMA 3.3 70B running on OVHcloud European AI cloud. Keyless & fast.",
            ),
            ModelDescriptor(
                id="free:ovhcloud:Qwen3-Coder-30B-A3B-Instruct",
                name="Qwen3 Coder 30B (OVHcloud Free)",
                model_id="Qwen3-Coder-30B-A3B-Instruct",
                description="Alibaba Qwen3 Coder 30B MoE model for programming tasks.",
            ),
            ModelDescriptor(
                id="free:ovhcloud:Mistral-7B-Instruct-v0.3",
                name="Mistral 7B v0.3 (OVHcloud Free)",
                model_id="Mistral-7B-Instruct-v0.3",
                description="Fast lightweight instruction-tuned model by Mistral AI.",
            ),
            ModelDescriptor(
                id="free:ovhcloud:gpt-oss-20b",
                name="GPT-OSS 20B (OVHcloud Free)",
                model_id="gpt-oss-20b",
                description="Open-weight GPT architecture on sovereign infrastructure.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="pollinations",
        name="Pollinations AI",
        category="keyless_free",
        key_env=None,
        portal_url="https://pollinations.ai",
        free_tier_note="Instant public AI router with high concurrency. Zero auth required.",
        default_base_url="https://text.pollinations.ai",
        default_models=[
            ModelDescriptor(
                id="free:pollinations:openai-fast",
                name="OpenAI Fast (Pollinations Free)",
                model_id="openai-fast",
                description="Ultra-fast public text model via Pollinations router.",
            ),
            ModelDescriptor(
                id="free:pollinations:openai",
                name="OpenAI Standard (Pollinations Free)",
                model_id="openai",
                description="Standard OpenAI compatible generation via Pollinations.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="llm7",
        name="LLM7 Gateway",
        category="keyless_free",
        key_env=None,
        portal_url="https://llm7.io",
        free_tier_note="Free community router with Codestral and Mistral-Nemo.",
        default_base_url="https://api.llm7.io/v1",
        default_models=[
            ModelDescriptor(
                id="free:llm7:codestral-latest",
                name="Codestral Latest (LLM7 Free)",
                model_id="codestral-latest",
                description="Mistral Codestral for coding and code review without key.",
            ),
            ModelDescriptor(
                id="free:llm7:mistral-Nemo-Instruct-2407",
                name="Mistral Nemo 12B (LLM7 Free)",
                model_id="mistral-Nemo-Instruct-2407",
                description="Mistral Nemo 12B 128k context model hosted for free.",
            ),
            ModelDescriptor(
                id="free:llm7:gemma4:31b",
                name="Gemma 4 31B (LLM7 Free)",
                model_id="gemma4:31b",
                description="Google Gemma 4 architecture via LLM7.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="vireonix",
        name="Vireonix AI",
        category="keyless_free",
        key_env=None,
        portal_url="https://vireonix.ru",
        free_tier_note="Public OpenAI-compatible free router.",
        default_base_url="https://api.vireonix.ru/v1",
        default_models=[
            ModelDescriptor(
                id="free:vireonix:auto",
                name="Vireonix Auto Free",
                model_id="auto",
                description="Dynamic auto-routed model via Vireonix open gateway.",
            )
        ],
    ),
    ProviderDescriptor(
        id="cehpoint",
        name="Cehpoint AI",
        category="keyless_free",
        key_env=None,
        portal_url="https://cehpoint.co.in",
        free_tier_note="Free endpoint with fine-tuned model.",
        default_base_url="https://ai.cehpoint.co.in/api/v1",
        default_models=[
            ModelDescriptor(
                id="free:cehpoint:cehpoint-ai",
                name="Cehpoint AI Free",
                model_id="cehpoint-ai",
                description="Free public inference model by Cehpoint.",
            )
        ],
    ),
    # 2. Recurring $0 Free Quota Providers (Free API Key Required)
    ProviderDescriptor(
        id="gemini",
        name="Google AI Studio (Gemini)",
        category="recurring_free",
        key_env="GEMINI_API_KEY",
        portal_url="https://aistudio.google.com/apikey",
        free_tier_note="Recurring $0 free tier: 15 RPM / 1,500 requests per day with Gemini 2.5 Flash & Pro.",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_models=[
            ModelDescriptor(
                id="gemini-2.5-flash",
                name="Gemini 2.5 Flash",
                model_id="gemini-2.5-flash",
                supports_thinking=True,
                description="Google flagship multimodal model with speed, reasoning, and 1M token context.",
            ),
            ModelDescriptor(
                id="gemini-2.5-flash-lite",
                name="Gemini 2.5 Flash-Lite",
                model_id="gemini-2.5-flash-lite",
                supports_thinking=False,
                description="Extremely fast, cost-effective Gemini model for real-time applications.",
            ),
            ModelDescriptor(
                id="gemini-2.0-pro-exp-02-05",
                name="Gemini 2.0 Pro Experimental",
                model_id="gemini-2.0-pro-exp-02-05",
                supports_thinking=True,
                description="Next-gen frontier reasoning model from Google DeepMind.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="groq",
        name="GroqCloud",
        category="recurring_free",
        key_env="GROQ_API_KEY",
        portal_url="https://console.groq.com/keys",
        free_tier_note="Recurring $0 free quota: 30 RPM / 14,400 requests/day at 500+ tokens/sec on LPUs.",
        default_base_url="https://api.groq.com/openai/v1",
        default_models=[
            ModelDescriptor(
                id="groq-llama-3.3-70b",
                name="LLaMA 3.3 70B Versatile (Groq)",
                model_id="llama-3.3-70b-versatile",
                supports_thinking=False,
                description="Meta LLaMA 3.3 70B running at lightning speed on Groq LPUs.",
            ),
            ModelDescriptor(
                id="groq-deepseek-r1-70b",
                name="DeepSeek R1 Distill 70B (Groq)",
                model_id="deepseek-r1-distill-llama-70b",
                supports_thinking=True,
                description="DeepSeek R1 reasoning distilled into LLaMA 70B, running with near-zero latency.",
            ),
            ModelDescriptor(
                id="groq-llama-3.1-8b",
                name="LLaMA 3.1 8B Instant (Groq)",
                model_id="llama-3.1-8b-instant",
                supports_thinking=False,
                description="Sub-100ms response time for lightweight agent tasks and tool calls.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="openrouter",
        name="OpenRouter",
        category="free_gateway",
        key_env="OPENROUTER_API_KEY",
        portal_url="https://openrouter.ai/keys",
        free_tier_note="Single API key unlocks Union Alpha and 25+ rotating :free models at $0 cost.",
        default_base_url="https://openrouter.ai/api/v1",
        default_models=[
            ModelDescriptor(
                id="union-alpha",
                name="Union Alpha (Stealth Model)",
                model_id="stealth/union-alpha",
                supports_thinking=True,
                description="Unified hybrid agent intelligence architecture model.",
            ),
            ModelDescriptor(
                id="openrouter-free-llama-70b",
                name="LLaMA 3.3 70B :free (OpenRouter)",
                model_id="meta-llama/llama-3.3-70b-instruct:free",
                supports_thinking=False,
                description="Meta LLaMA 3.3 70B free tier via OpenRouter gateway.",
            ),
            ModelDescriptor(
                id="openrouter-free-deepseek-r1",
                name="DeepSeek R1 :free (OpenRouter)",
                model_id="deepseek/deepseek-r1:free",
                supports_thinking=True,
                description="Full DeepSeek R1 reasoning model with open router free tier.",
            ),
            ModelDescriptor(
                id="openrouter-free-gemini-flash",
                name="Gemini 2.0 Flash :free (OpenRouter)",
                model_id="google/gemini-2.0-flash-exp:free",
                supports_thinking=True,
                description="Google Gemini 2.0 Flash free endpoint on OpenRouter.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="sambanova",
        name="SambaNova Cloud",
        category="recurring_free",
        key_env="SAMBANOVA_API_KEY",
        portal_url="https://cloud.sambanova.ai/apis",
        free_tier_note="Recurring $0 developer tier on ultra-fast SN40L Reconfigurable Dataflow Units.",
        default_base_url="https://api.sambanova.ai/v1",
        default_models=[
            ModelDescriptor(
                id="sambanova-llama-3.3-70b",
                name="LLaMA 3.3 70B (SambaNova)",
                model_id="Meta-Llama-3.3-70B-Instruct",
                supports_thinking=False,
                description="High throughput LLaMA 3.3 70B on SambaNova RDUs.",
            ),
            ModelDescriptor(
                id="sambanova-deepseek-r1",
                name="DeepSeek R1 (SambaNova)",
                model_id="DeepSeek-R1",
                supports_thinking=True,
                description="Full 671B DeepSeek R1 reasoning model on SambaNova RDUs.",
            ),
            ModelDescriptor(
                id="sambanova-qwen-coder-32b",
                name="Qwen 2.5 Coder 32B (SambaNova)",
                model_id="Qwen2.5-Coder-32B-Instruct",
                supports_thinking=False,
                description="Specialized coding model running at enterprise speeds.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="mistral",
        name="Mistral La Plateforme",
        category="recurring_free",
        key_env="MISTRAL_API_KEY",
        portal_url="https://console.mistral.ai/api-keys/",
        free_tier_note="Free Experiment tier with Codestral, Mistral Small, and Nemo.",
        default_base_url="https://api.mistral.ai/v1",
        default_models=[
            ModelDescriptor(
                id="mistral-codestral",
                name="Codestral (Mistral)",
                model_id="codestral-latest",
                supports_thinking=False,
                description="Mistral AI state-of-the-art model for coding and code generation.",
            ),
            ModelDescriptor(
                id="mistral-small",
                name="Mistral Small (Mistral)",
                model_id="mistral-small-latest",
                supports_thinking=False,
                description="Fast and cost-efficient enterprise model by Mistral AI.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="cohere",
        name="Cohere Coral",
        category="recurring_free",
        key_env="COHERE_API_KEY",
        portal_url="https://dashboard.cohere.com/api-keys",
        free_tier_note="Free Trial Key with 1,000 API calls/month.",
        default_base_url="https://api.cohere.ai/compatibility/v1",
        default_models=[
            ModelDescriptor(
                id="cohere-command-r-plus",
                name="Command R+ (Cohere)",
                model_id="command-r-plus-08-2024",
                supports_thinking=False,
                description="Cohere flagship model optimized for conversational agents and RAG.",
            )
        ],
    ),
    ProviderDescriptor(
        id="cloudflare",
        name="Cloudflare Workers AI",
        category="recurring_free",
        key_env="CLOUDFLARE_API_KEY",
        portal_url="https://dash.cloudflare.com/",
        free_tier_note="10,000 free neurons/day across Cloudflare global edge network.",
        default_base_url="https://api.cloudflare.com/client/v4/accounts/default/ai/v1",
        default_models=[
            ModelDescriptor(
                id="cf-llama-3.3-70b",
                name="LLaMA 3.3 70B FP8 (Cloudflare)",
                model_id="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                supports_thinking=False,
                description="Meta LLaMA 3.3 70B served from Cloudflare distributed edge GPUs.",
            )
        ],
    ),
    # 3. Trial Credit Providers
    ProviderDescriptor(
        id="nvidia",
        name="NVIDIA NIM",
        category="trial_credits",
        key_env="NVIDIA_API_KEY",
        portal_url="https://build.nvidia.com/",
        free_tier_note="1,000 free inference API credits on NVIDIA enterprise DGX cloud.",
        default_base_url="https://integrate.api.nvidia.com/v1",
        default_models=[
            ModelDescriptor(
                id="nvidia-nemotron-70b",
                name="Nemotron 70B (NVIDIA NIM)",
                model_id="nvidia/llama-3.1-nemotron-70b-instruct",
                supports_thinking=False,
                description="NVIDIA aligned model trained for exceptional reasoning and instruction following.",
            ),
            ModelDescriptor(
                id="nvidia-deepseek-r1",
                name="DeepSeek R1 (NVIDIA NIM)",
                model_id="deepseek-ai/deepseek-r1",
                supports_thinking=True,
                description="DeepSeek R1 full reasoning hosted on NVIDIA NIM microservices.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="cerebras",
        name="Cerebras Cloud",
        category="trial_credits",
        key_env="CEREBRAS_API_KEY",
        portal_url="https://cloud.cerebras.ai/",
        free_tier_note="1 Million free tokens/day on wafer-scale inference engine (2,000+ t/s).",
        default_base_url="https://api.cerebras.ai/v1",
        default_models=[
            ModelDescriptor(
                id="cerebras-llama-3.3-70b",
                name="LLaMA 3.3 70B (Cerebras 2,000 t/s)",
                model_id="llama-3.3-70b",
                supports_thinking=False,
                description="World-record inference speeds on wafer-scale engines.",
            )
        ],
    ),
    # 4. Commercial Frontier Providers
    ProviderDescriptor(
        id="openai",
        name="OpenAI",
        category="paid",
        key_env="OPENAI_API_KEY",
        portal_url="https://platform.openai.com/api-keys",
        free_tier_note="Official OpenAI commercial platform (GPT-4o, GPT-4o-mini, o3-mini).",
        default_base_url="https://api.openai.com/v1",
        default_models=[
            ModelDescriptor(
                id="gpt-4o",
                name="GPT-4o",
                model_id="gpt-4o",
                supports_thinking=False,
                description="OpenAI flagship omni model for multimodal reasoning and coding.",
            ),
            ModelDescriptor(
                id="gpt-4o-mini",
                name="GPT-4o mini",
                model_id="gpt-4o-mini",
                supports_thinking=False,
                description="Affordable and intelligent small model for fast tasks.",
            ),
            ModelDescriptor(
                id="o3-mini",
                name="o3-mini",
                model_id="o3-mini",
                supports_thinking=True,
                description="OpenAI high-speed reasoning model with configurable reasoning effort.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="anthropic",
        name="Anthropic Claude",
        category="paid",
        key_env="ANTHROPIC_API_KEY",
        portal_url="https://console.anthropic.com/settings/keys",
        free_tier_note="Official Anthropic API (Claude 3.7 Sonnet with Hybrid Thinking, Claude 3.5 Sonnet).",
        default_base_url="https://api.anthropic.com",
        default_use="langchain_anthropic:ChatAnthropic",
        default_models=[
            ModelDescriptor(
                id="claude-3-7-sonnet",
                name="Claude 3.7 Sonnet",
                model_id="claude-3-7-sonnet-latest",
                supports_thinking=True,
                description="Anthropic hybrid thinking model with unmatched coding abilities.",
            ),
            ModelDescriptor(
                id="claude-3-5-sonnet",
                name="Claude 3.5 Sonnet",
                model_id="claude-3-5-sonnet-latest",
                supports_thinking=False,
                description="Industry-standard benchmark model for coding and agent workflows.",
            ),
        ],
    ),
    ProviderDescriptor(
        id="deepseek",
        name="DeepSeek Official",
        category="paid",
        key_env="DEEPSEEK_API_KEY",
        portal_url="https://platform.deepseek.com/api_keys",
        free_tier_note="Direct high-concurrency DeepSeek V3 and R1 reasoning endpoints at ultra-low price.",
        default_base_url="https://api.deepseek.com/v1",
        default_models=[
            ModelDescriptor(
                id="deepseek-chat",
                name="DeepSeek V3",
                model_id="deepseek-chat",
                supports_thinking=False,
                description="Direct 671B parameter DeepSeek V3 general intelligence model.",
            ),
            ModelDescriptor(
                id="deepseek-reasoner",
                name="DeepSeek R1",
                model_id="deepseek-reasoner",
                supports_thinking=True,
                description="Direct DeepSeek R1 reasoning model with visible thought chains.",
            ),
        ],
    ),
    # 5. Custom / Local Endpoints
    ProviderDescriptor(
        id="custom",
        name="Custom / Local OpenAI Endpoint",
        category="custom",
        key_env="CUSTOM_LLM_API_KEY",
        portal_url="",
        free_tier_note="Connect any local server (Ollama, LM Studio, vLLM) or private proxy.",
        default_base_url="http://127.0.0.1:11434/v1",
        default_models=[],
    ),
]


def _credentials_path() -> Path:
    store_dir = runtime_home() / "models"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / CREDENTIALS_FILE_NAME


def load_credentials_file() -> dict[str, Any]:
    path = _credentials_path()
    if not path.exists():
        return {"providers": {}, "custom_models": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"providers": {}, "custom_models": []}
        data.setdefault("providers", {})
        data.setdefault("custom_models", [])
        return data
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {"providers": {}, "custom_models": []}


def save_credentials_file(data: dict[str, Any]) -> None:
    path = _credentials_path()
    temp_path = path.with_suffix(".tmp")
    try:
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except Exception as exc:
        logger.error("Failed to write credentials file %s: %s", path, exc)
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    secret = secret.strip()
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"


def get_providers_catalog() -> list[dict[str, Any]]:
    """Build honest provider catalog with configuration status and masked keys."""
    creds = load_credentials_file()
    saved_providers = creds.get("providers", {})

    catalog = []
    for spec in PROVIDER_SPECS:
        configured = False
        masked = None
        current_base_url = spec.default_base_url

        if spec.category == "keyless_free":
            configured = True
        elif spec.key_env:
            # Check environment or persisted credentials
            env_key = os.getenv(spec.key_env, "").strip()
            persisted = saved_providers.get(spec.id, {})
            persisted_key = (persisted.get("api_key") or "").strip()

            active_key = env_key or persisted_key
            if active_key:
                configured = True
                masked = mask_secret(active_key)
            if persisted.get("base_url"):
                current_base_url = persisted["base_url"]

        catalog.append(
            {
                "id": spec.id,
                "name": spec.name,
                "category": spec.category,
                "key_env": spec.key_env,
                "configured": configured,
                "masked_key": masked,
                "portal_url": spec.portal_url,
                "free_tier_note": spec.free_tier_note,
                "base_url": current_base_url,
                "default_models": [asdict(m) for m in spec.default_models],
            }
        )
    return catalog


def configure_provider(
    provider_id: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model_id: str | None = None,
    display_name: str | None = None,
    remove: bool = False,
) -> dict[str, Any]:
    """Configure or remove credentials for an LLM provider and sync environment."""
    from datetime import datetime

    spec = next((s for s in PROVIDER_SPECS if s.id == provider_id), None)
    if not spec and provider_id != "custom":
        raise ValueError(f"Unknown provider '{provider_id}'")

    creds = load_credentials_file()
    providers = creds.setdefault("providers", {})
    custom_models = creds.setdefault("custom_models", [])

    if remove:
        if provider_id in providers:
            del providers[provider_id]
        if spec and spec.key_env and spec.key_env in os.environ:
            del os.environ[spec.key_env]
        if model_id:
            creds["custom_models"] = [m for m in custom_models if m.get("id") != model_id and m.get("name") != model_id]
        save_credentials_file(creds)
        return {
            "success": True,
            "provider": provider_id,
            "configured": False,
            "message": f"Configuration removed for {provider_id}",
        }

    # Updating or adding provider configuration
    clean_key = api_key.strip() if api_key else ""
    if clean_key:
        providers[provider_id] = {
            "api_key": clean_key,
            "base_url": base_url or (spec.default_base_url if spec else None),
            "configured_at": datetime.now(UTC).isoformat(),
        }
        if spec and spec.key_env:
            os.environ[spec.key_env] = clean_key

    # Adding a custom model if requested
    if model_id:
        custom_id = f"{provider_id}:{model_id}" if provider_id != "custom" else model_id
        entry = {
            "id": custom_id,
            "name": display_name or model_id,
            "model": model_id,
            "provider": provider_id,
            "base_url": base_url or (spec.default_base_url if spec else None),
            "api_key": clean_key or (providers.get(provider_id, {}).get("api_key") if spec else ""),
            "supports_thinking": False,
        }
        # Update or append
        existing_idx = next((i for i, m in enumerate(custom_models) if m.get("id") == custom_id), None)
        if existing_idx is not None:
            custom_models[existing_idx] = entry
        else:
            custom_models.append(entry)

    save_credentials_file(creds)
    return {
        "success": True,
        "provider": provider_id,
        "configured": True,
        "masked_key": mask_secret(clean_key) if clean_key else None,
        "message": f"Successfully configured credentials for {provider_id}",
    }


def sync_all_persisted_credentials_to_env() -> None:
    """Load all persisted credentials and populate os.environ so clients find them."""
    creds = load_credentials_file()
    saved = creds.get("providers", {})
    for spec in PROVIDER_SPECS:
        if not spec.key_env:
            continue
        persisted = saved.get(spec.id)
        if persisted and persisted.get("api_key"):
            key = persisted["api_key"].strip()
            # If not already present in environment, inject it
            if not os.getenv(spec.key_env):
                os.environ[spec.key_env] = key


def get_active_provider_models(app_config: Any | None = None) -> list[dict[str, Any]]:
    """Return all models unlocked by currently configured providers + keyless models."""
    sync_all_persisted_credentials_to_env()
    catalog = get_providers_catalog()
    models: list[dict[str, Any]] = []

    for prov in catalog:
        if not prov["configured"]:
            continue
        for m in prov["default_models"]:
            is_free = prov["category"] in ("keyless_free", "recurring_free", "free_gateway", "trial_credits")
            models.append(
                {
                    "name": m["id"],
                    "model": m["model_id"],
                    "display_name": m["name"],
                    "description": m.get("description") or f"Model from {prov['name']}",
                    "provider": prov["id"],
                    "is_free": is_free,
                    "quota_type": prov["category"],
                    "free_status": "online",
                    "supports_thinking": m.get("supports_thinking", False),
                    "supports_reasoning_effort": False,
                }
            )

    # Add custom models
    creds = load_credentials_file()
    for cm in creds.get("custom_models", []):
        models.append(
            {
                "name": cm.get("id", cm.get("name")),
                "model": cm.get("model", cm.get("id")),
                "display_name": cm.get("name"),
                "description": f"Custom model ({cm.get('provider', 'custom')})",
                "provider": cm.get("provider", "custom"),
                "is_free": False,
                "quota_type": "custom",
                "free_status": "online",
                "supports_thinking": cm.get("supports_thinking", False),
                "supports_reasoning_effort": False,
            }
        )

    return models
