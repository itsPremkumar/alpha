"""Provider manager and credentials persistence for LLM providers.

Supports:
- Keyless free providers (OVHcloud, Pollinations, LLM7, Vireonix, Cehpoint, BlockRun, AI Horde)
- Recurring $0 free quota providers (Gemini, Groq, SambaNova, Mistral, Cohere, Cloudflare)
- Free-model gateways (OpenRouter :free models, Bytez)
- Trial credit providers (NVIDIA NIM, Cerebras)
- Frontier/commercial providers (OpenAI, Anthropic, DeepSeek)
- Custom / Local OpenAI-compatible endpoints (Ollama, LM Studio, vLLM)

Two hardening properties this module owns for bring-your-own-model:

* **Egress policy.** A caller-supplied ``base_url`` is screened by
  :func:`alpha.community.url_safety.assert_model_endpoint_url` *before* it is
  persisted, so configuring a provider can never point model egress at cloud
  metadata, loopback (unless the provider is a declared local endpoint) or an
  internal RFC 1918 host. Persisted endpoints are re-screened offline on every
  read, so a file written before this guard existed cannot keep routing egress
  internally.
* **Credential storage at rest.** Provider keys are no longer written as
  plaintext JSON. The file is an envelope encrypted with Windows DPAPI
  (user scope) where available, a key-file cipher elsewhere, and only falls
  back to plaintext with a loud warning when neither exists. A pre-existing
  plaintext file is migrated transparently on first read, and
  :func:`migrate_plaintext_credentials_file` performs the same migration
  explicitly. See :func:`credentials_storage_status` for the honest, per-host
  state of that guarantee.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from alpha.community.url_safety import ModelEndpointBlockedError, assert_model_endpoint_url
from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

CREDENTIALS_FILE_NAME = "provider_credentials.json"
CREDENTIALS_KEY_FILE_NAME = "provider_credentials.key"

#: Envelope markers written to the credentials file. Absence of ``schema`` is
#: what identifies a legacy plaintext file, so the two formats are told apart by
#: a field that only the new writer emits.
CREDENTIALS_SCHEMA = "alpha.provider_credentials"
CREDENTIALS_VERSION = 2

#: Endpoint kinds. ``remote`` is the default and the strict tier; ``local`` is
#: the declared opt-in for same-host OpenAI-compatible servers. Cloud metadata
#: is refused in both (see the egress policy note above).
PROVIDER_KIND_REMOTE = "remote"
PROVIDER_KIND_LOCAL = "local"
PROVIDER_KINDS = (PROVIDER_KIND_REMOTE, PROVIDER_KIND_LOCAL)
#: Providers whose documented purpose is a local OpenAI-compatible server, so
#: they default to the local tier instead of demanding an explicit opt-in.
LOCAL_BY_DEFAULT_PROVIDERS = frozenset({"custom"})

#: Operator allowlist for a private model endpoint (e.g. a LAN gateway). An
#: allowlisted host is still refused when it resolves to cloud metadata, and an
#: allowlist entry never lifts the metadata refusal.
PRIVATE_HOST_ALLOWLIST_ENV = "ALPHA_MODEL_ENDPOINT_PRIVATE_HOSTS"

#: Header names a model endpoint must never receive from configuration: they
#: either retarget the connection or corrupt the request framing.
_FORBIDDEN_ENDPOINT_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "proxy-authorization",
        "proxy-connection",
        "upgrade",
        "keep-alive",
        "te",
        "trailer",
    }
)
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _credentials_key_path() -> Path:
    return _credentials_path().parent / CREDENTIALS_KEY_FILE_NAME


# ---------------------------------------------------------------------------
# Credential encryption at rest
# ---------------------------------------------------------------------------
#
# Provider API keys are bearer secrets: anything that can read the runtime home
# can spend the operator's money. The envelope below keeps them out of plain
# sight, with the honest caveat that a cipher keyed from the same host only
# protects against at-rest copies, backups and other OS users - never against
# code already running as this user. Windows DPAPI (CryptProtectData, user
# scope) is the real win: the key material lives in the user's credential
# store, not in the file. Elsewhere the fallback key file (0600) is equivalent
# to file permissions, and that is exactly what ``credentials_storage_status``
# reports - no host is told it has protection it does not have.


def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_protect(data: bytes) -> bytes:
    """Encrypt with Windows DPAPI at user scope (CryptProtectData)."""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(payload: bytes) -> _DATA_BLOB:
        buffer = ctypes.create_string_buffer(payload, len(payload))
        return _DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    source = _blob(data)
    result = _DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN: never prompt (this runs in a server process).
    if not ctypes.WinDLL("Crypt32.dll").CryptProtectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.WinDLL("Kernel32.dll").LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _dpapi_unprotect(data: bytes) -> bytes:
    """Decrypt with Windows DPAPI at user scope (CryptUnprotectData)."""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(payload: bytes) -> _DATA_BLOB:
        buffer = ctypes.create_string_buffer(payload, len(payload))
        return _DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    source = _blob(data)
    result = _DATA_BLOB()
    if not ctypes.WinDLL("Crypt32.dll").CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.WinDLL("Kernel32.dll").LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _fernet_key() -> bytes:
    """Load (or create) the local key file used when DPAPI is unavailable.

    Generated with 0600 permissions and living beside the ciphertext, so it
    protects against at-rest copies and other OS users, not against code
    running as this user. The provider catalog reports the active backend so
    nobody has to guess which guarantee applies.
    """
    from cryptography.fernet import Fernet

    path = _credentials_key_path()
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        logger.warning("Could not restrict permissions on the credential key file %s", path)
    return key


def credential_encryption_backend() -> str:
    """Active at-rest protection: ``dpapi-user``, ``fernet-file`` or ``none``."""
    if _dpapi_available():
        return "dpapi-user"
    try:
        import cryptography  # noqa: F401
    except Exception:
        return "none"
    return "fernet-file"


def _encrypt_payload(plaintext: bytes) -> tuple[str, str]:
    """Return ``(ciphertext_b64, backend)`` for the credential payload."""
    backend = credential_encryption_backend()
    if backend == "dpapi-user":
        return base64.b64encode(_dpapi_protect(plaintext)).decode("ascii"), backend
    if backend == "fernet-file":
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).encrypt(plaintext).decode("ascii"), backend
    logger.warning(
        "No at-rest encryption available for %s (neither Windows DPAPI nor the 'cryptography' package). "
        "Provider keys are being stored in PLAINTEXT; restrict access to the runtime home or install one of them.",
        CREDENTIALS_FILE_NAME,
    )
    return base64.b64encode(plaintext).decode("ascii"), "none"


def _decrypt_payload(ciphertext_b64: str, backend: str) -> bytes:
    """Reverse :func:`_encrypt_payload`; raises when the payload cannot be read."""
    raw = base64.b64decode(ciphertext_b64.encode("ascii"), validate=True)
    if backend == "dpapi-user":
        return _dpapi_unprotect(raw)
    if backend == "fernet-file":
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(raw)
    return raw


def _restrict_permissions(path: Path) -> None:
    """Best-effort 0600 on the credentials file (no-op on Windows)."""
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass


def _normalize_credentials(data: Any) -> dict[str, Any] | None:
    """Return *data* as a credentials dict, or ``None`` when it is not one."""
    if not isinstance(data, dict):
        return None
    data.setdefault("providers", {})
    data.setdefault("custom_models", [])
    if not isinstance(data["providers"], dict) or not isinstance(data["custom_models"], list):
        return None
    return data


def _provider_kind(entry: dict[str, Any], provider_id: str) -> str:
    """Tier a persisted entry is screened under (legacy entries included).

    Files written before the ``kind`` field existed are tiered by provider id:
    ``custom`` is the documented local-endpoint provider, everything else gets
    the strict remote tier. Without that, an upgrade would either break every
    existing Ollama/LM Studio user or silently keep unvalidated endpoints.
    """
    declared = entry.get("kind")
    if declared in PROVIDER_KINDS:
        return str(declared)
    return PROVIDER_KIND_LOCAL if provider_id in LOCAL_BY_DEFAULT_PROVIDERS else PROVIDER_KIND_REMOTE


def private_host_allowlist() -> tuple[str, ...]:
    """Operator allowlist of private hosts a model endpoint may use."""
    raw = os.getenv(PRIVATE_HOST_ALLOWLIST_ENV, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _screen_persisted_endpoint(provider_id: str, entry: dict[str, Any], base_url: Any) -> bool:
    """Drop a persisted ``base_url`` the egress policy refuses.

    Runs on the read path with ``resolve_dns=False``: scheme, URL-embedded
    credentials, IP literals and reserved names are screened without a network
    round trip (this runs on every config load), and a DNS name is tiered as
    public. Configure-time validation is the DNS-resolving gate. Returns True
    when the endpoint was removed.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    kind = _provider_kind(entry, provider_id)
    try:
        assert_model_endpoint_url(
            base_url,
            allow_loopback=kind == PROVIDER_KIND_LOCAL,
            allowed_private_hosts=private_host_allowlist(),
            resolve_dns=False,
            action="route model traffic to",
        )
    except ModelEndpointBlockedError as exc:
        logger.warning(
            "Ignoring persisted endpoint for provider '%s': %s (endpoint: %s). Re-configure the provider with an allowed base_url.",
            provider_id,
            exc.reason,
            exc.url,
        )
        return True
    return False


def _screen_persisted_endpoints(data: dict[str, Any]) -> None:
    """Apply :func:`_screen_persisted_endpoint` across a loaded credentials doc."""
    for provider_id, entry in list(data.get("providers", {}).items()):
        if not isinstance(entry, dict):
            continue
        if _screen_persisted_endpoint(provider_id, entry, entry.get("base_url")):
            entry.pop("base_url", None)
    for model in data.get("custom_models", []):
        if not isinstance(model, dict):
            continue
        provider_id = str(model.get("provider") or "custom")
        if _screen_persisted_endpoint(provider_id, model, model.get("base_url")):
            model.pop("base_url", None)


def load_credentials_file() -> dict[str, Any]:
    """Load credentials, decrypting the envelope and migrating legacy plaintext.

    The returned shape is unchanged (``{"providers": {...}, "custom_models":
    [...]}`` with plaintext keys in memory) so every existing consumer keeps
    working; only the on-disk representation changed. A legacy plaintext file
    is rewritten encrypted on first read.
    """
    path = _credentials_path()
    if not path.exists():
        return {"providers": {}, "custom_models": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {"providers": {}, "custom_models": []}
    if not isinstance(raw, dict):
        logger.warning("Ignoring %s: unexpected top-level JSON shape", path)
        return {"providers": {}, "custom_models": []}

    if raw.get("schema") == CREDENTIALS_SCHEMA:
        plaintext: Any = None
        try:
            plaintext = _decrypt_payload(str(raw.get("ciphertext", "")), str(raw.get("encryption", "")))
        except Exception as exc:
            logger.error(
                "Failed to decrypt %s with the '%s' backend (%s: %s). Provider keys are unreadable in this process; "
                "re-configure the affected providers. Keys encrypted for another OS user or host cannot be recovered here.",
                path,
                raw.get("encryption"),
                type(exc).__name__,
                exc,
            )
            return {"providers": {}, "custom_models": []}
        data = _normalize_credentials(json.loads(plaintext.decode("utf-8")))
        if data is None:
            logger.warning("Ignoring %s: decrypted payload is not a credentials document", path)
            return {"providers": {}, "custom_models": []}
        _screen_persisted_endpoints(data)
        return data

    # Legacy plaintext file: usable immediately, rewritten encrypted below.
    data = _normalize_credentials(raw)
    if data is None:
        logger.warning("Ignoring %s: unexpected credentials shape", path)
        return {"providers": {}, "custom_models": []}
    _screen_persisted_endpoints(data)
    try:
        save_credentials_file(data)
        logger.info("Migrated %s from plaintext to an encrypted credentials envelope (%s).", path, credential_encryption_backend())
    except Exception as exc:  # a failed upgrade must not lose the caller's config
        logger.error("Could not migrate plaintext credentials in %s: %s", path, exc)
    return data


def save_credentials_file(data: dict[str, Any]) -> None:
    """Encrypt and atomically persist the credentials document."""
    path = _credentials_path()
    temp_path = path.with_suffix(".tmp")
    payload = json.dumps(data, indent=2).encode("utf-8")
    ciphertext, backend = _encrypt_payload(payload)
    envelope = {
        "schema": CREDENTIALS_SCHEMA,
        "version": CREDENTIALS_VERSION,
        "encryption": backend,
        "ciphertext": ciphertext,
    }
    try:
        temp_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        _restrict_permissions(temp_path)
        temp_path.replace(path)
    except Exception as exc:
        logger.error("Failed to write credentials file %s: %s", path, exc)
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def migrate_plaintext_credentials_file() -> bool:
    """Rewrite a legacy plaintext credentials file as an encrypted envelope.

    Returns True when a migration happened, False when there was nothing to do
    (already encrypted, or no file). Idempotent, and callable explicitly by an
    operator or a startup hook; :func:`load_credentials_file` performs the same
    migration implicitly on first read.
    """
    path = _credentials_path()
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot migrate %s: unreadable (%s)", path, exc)
        return False
    if not isinstance(raw, dict) or raw.get("schema") == CREDENTIALS_SCHEMA:
        return False
    data = _normalize_credentials(raw)
    if data is None:
        logger.warning("Cannot migrate %s: unexpected credentials shape", path)
        return False
    save_credentials_file(data)
    logger.info("Migrated plaintext credentials in %s to an encrypted envelope (%s).", path, credential_encryption_backend())
    return True


def credentials_storage_status() -> dict[str, Any]:
    """Honest disclosure of credential-at-rest protection on THIS host.

    ``protection`` is one of:

    * ``dpapi-user`` - Windows DPAPI, user scope. Key material is in the OS
      credential store, so another OS user, a stolen disk image or a backup of
      the runtime home does not yield usable keys.
    * ``fernet-file`` - encrypted with a key file stored (0600) beside it.
      Equivalent to file permissions: it stops at-rest copies and other OS
      users, not a process running as this user.
    * ``none`` - no cipher available; keys are stored in plaintext. Treat the
      runtime home as secret material.

    None of these protect against code already executing as the OS user that
    runs the Gateway - that process can decrypt by design, because it must send
    the key to the provider.
    """
    path = _credentials_path()
    backend = credential_encryption_backend()
    file_exists = path.exists()
    encrypted = False
    if file_exists:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            encrypted = isinstance(raw, dict) and raw.get("schema") == CREDENTIALS_SCHEMA
        except Exception:
            encrypted = False
    return {
        "path": str(path),
        "exists": file_exists,
        "encrypted_at_rest": encrypted,
        "protection": backend if encrypted else ("plaintext" if file_exists else "none"),
        "legacy_plaintext_pending_migration": file_exists and not encrypted,
        "plaintext_on_disk": file_exists and not encrypted,
    }


def mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    secret = secret.strip()
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"


def resolve_provider_api_key(entry: dict[str, Any]) -> str:
    """Effective key for a persisted provider entry.

    Resolution order: a literal stored in the entry, then the env var named by
    ``api_key_env``. The env indirection is what lets an operator point a
    provider at a key that only ever exists in the environment (for example
    ``$OPENROUTER_API_KEY``) instead of pasting a secret into an API call that
    is then written to disk.
    """
    literal = (entry.get("api_key") or "").strip()
    if literal:
        return literal
    env_name = (entry.get("api_key_env") or "").strip()
    if env_name and _ENV_NAME_PATTERN.match(env_name):
        return (os.getenv(env_name) or "").strip()
    return ""


def resolve_provider_kind(provider_id: str, declared: Any = None) -> str:
    """Validated endpoint tier for *provider_id*.

    Unknown values raise rather than silently falling back, so a typo cannot
    quietly hand a private endpoint the strict-remote expectation (or, worse,
    hand a remote endpoint the local tier).
    """
    if declared is None or (isinstance(declared, str) and not declared.strip()):
        return PROVIDER_KIND_LOCAL if provider_id in LOCAL_BY_DEFAULT_PROVIDERS else PROVIDER_KIND_REMOTE
    clean = str(declared).strip().lower()
    if clean not in PROVIDER_KINDS:
        raise ValueError(f"Unknown provider kind '{declared}'. Allowed: {', '.join(PROVIDER_KINDS)}")
    return clean


def validate_endpoint_headers(headers: Any) -> dict[str, str]:
    """Normalize extra request headers for a model endpoint.

    Rejects framing/connection headers (``Host`` and friends) and any name or
    value carrying CR/LF, because a configured header is attacker-adjacent
    input that would otherwise allow request splitting or a silent retarget of
    the connection.
    """
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object of header name -> value")
    cleaned: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("header names must be non-empty")
        if not isinstance(raw_value, (str, int, float)) or isinstance(raw_value, bool):
            raise ValueError(f"header '{name}' must have a string value")
        value = str(raw_value).strip()
        if name.lower() in _FORBIDDEN_ENDPOINT_HEADERS:
            raise ValueError(f"header '{name}' is not allowed on a model endpoint")
        if any(ch in name or ch in value for ch in ("\r", "\n", "\x00")):
            raise ValueError(f"header '{name}' must not contain CR, LF or NUL")
        cleaned[name] = value
    return cleaned


def get_providers_catalog() -> list[dict[str, Any]]:
    """Build honest provider catalog with configuration status and masked keys."""
    creds = load_credentials_file()
    saved_providers = creds.get("providers", {})

    catalog = []
    for spec in PROVIDER_SPECS:
        configured = False
        masked = None
        current_base_url = spec.default_base_url
        declared_kind: Any = None
        api_key_env: str | None = None
        header_names: list[str] = []

        if spec.category == "keyless_free":
            configured = True
        elif spec.key_env:
            # Check environment or persisted credentials
            env_key = os.getenv(spec.key_env, "").strip()
            persisted = saved_providers.get(spec.id, {})
            if not isinstance(persisted, dict):
                persisted = {}

            active_key = env_key or resolve_provider_api_key(persisted)
            if active_key:
                configured = True
                masked = mask_secret(active_key)
            if persisted.get("base_url"):
                current_base_url = persisted["base_url"]
            declared_kind = persisted.get("kind")
            api_key_env = (persisted.get("api_key_env") or "").strip() or None
            if persisted.get("headers"):
                header_names = sorted(validate_endpoint_headers(persisted["headers"]))

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
                # Endpoint tier + key/header shape, so a UI can offer the local
                # tier explicitly and show where a key comes from. Header
                # *values* are never echoed (they can carry a secret); only
                # names are.
                "kind": resolve_provider_kind(spec.id, declared_kind),
                "api_key_env": api_key_env,
                "header_names": header_names,
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
    *,
    api_key_env: str | None = None,
    kind: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Configure or remove credentials for an LLM provider and sync environment.

    ``api_key_env`` names the environment variable that holds the key, so the
    secret never travels through the request body or into the credentials file
    (only the variable's *name* is stored). ``kind`` selects the endpoint tier:
    ``remote`` (default) demands a public endpoint, ``local`` is the declared
    opt-in for a same-host OpenAI-compatible server. ``headers`` adds extra
    request headers, with framing/connection headers rejected.

    A caller-supplied ``base_url`` is screened by the shared egress policy
    before anything is persisted; a refused endpoint raises
    :class:`alpha.community.url_safety.ModelEndpointBlockedError` (a
    ``ValueError``), which the Gateway reports as HTTP 400.
    """
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

    # Endpoint tier + caller-supplied endpoint are validated before any state
    # is touched, so a refused request leaves the stored configuration intact.
    resolved_kind = resolve_provider_kind(provider_id, kind)
    clean_headers = validate_endpoint_headers(headers)
    supplied_base_url = (base_url or "").strip()
    if supplied_base_url:
        screened_base_url = assert_model_endpoint_url(
            supplied_base_url,
            allow_loopback=resolved_kind == PROVIDER_KIND_LOCAL,
            allowed_private_hosts=private_host_allowlist(),
            resolve_dns=True,
            action="route model traffic to",
        )
    else:
        screened_base_url = ""
    effective_base_url = screened_base_url or (spec.default_base_url if spec else None)

    # Updating or adding provider configuration
    clean_key = api_key.strip() if api_key else ""
    clean_env_name = (api_key_env or "").strip()
    if clean_env_name and not _ENV_NAME_PATTERN.match(clean_env_name):
        raise ValueError(f"api_key_env '{clean_env_name}' is not a valid environment variable name")
    env_key = (os.getenv(clean_env_name, "") or "").strip() if clean_env_name else ""
    if clean_env_name and not env_key:
        raise ValueError(f"api_key_env '{clean_env_name}' is not set in the environment; refusing to persist a provider without a key")
    # A key sourced from the environment is never copied into the file: only
    # the variable name is stored, and the value stays in the environment.
    stored_key = clean_key
    effective_key = clean_key or env_key

    existing_entry = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
    if stored_key or clean_env_name:
        entry: dict[str, Any] = {
            "base_url": effective_base_url,
            "configured_at": datetime.now(UTC).isoformat(),
            "kind": resolved_kind,
        }
        if stored_key:
            entry["api_key"] = stored_key
        if clean_env_name:
            entry["api_key_env"] = clean_env_name
        if clean_headers:
            entry["headers"] = clean_headers
        providers[provider_id] = entry
    elif screened_base_url and existing_entry:
        # Endpoint-only update (no new key): keep the stored key, adopt the
        # endpoint and tier the operator just supplied.
        existing_entry["base_url"] = screened_base_url
        existing_entry["kind"] = resolved_kind
        if clean_headers:
            existing_entry["headers"] = clean_headers
    if effective_key and spec and spec.key_env:
        os.environ[spec.key_env] = effective_key

    # Adding a custom model if requested
    if model_id:
        custom_id = f"{provider_id}:{model_id}" if provider_id != "custom" else model_id
        persisted_provider_entry = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
        # Only an already-stored literal is copied into the model entry. A key
        # sourced from ``api_key_env`` stays in the environment (the provider's
        # key_env was synced above, which is where AppConfig reads it from), so
        # the env-indirection keeps working for custom models too.
        model_key = stored_key or (persisted_provider_entry.get("api_key") or "").strip()
        entry = {
            "id": custom_id,
            "name": display_name or model_id,
            "model": model_id,
            "provider": provider_id,
            "base_url": effective_base_url,
            "api_key": model_key,
            "kind": resolved_kind,
            "supports_thinking": False,
        }
        if clean_env_name:
            entry["api_key_env"] = clean_env_name
        if clean_headers:
            entry["headers"] = clean_headers
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
        "masked_key": mask_secret(effective_key) if effective_key else None,
        "kind": resolved_kind,
        "api_key_env": clean_env_name or None,
        "base_url": effective_base_url,
        "header_names": sorted(clean_headers),
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
        if isinstance(persisted, dict) and resolve_provider_api_key(persisted):
            key = resolve_provider_api_key(persisted)
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
