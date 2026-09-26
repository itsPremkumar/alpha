"""Keyless free-LLM router package.

Layered so each seam is stub-able in isolation:

* :mod:`providers` — provider specs + the single HTTP seam (``request``)
* :mod:`catalog`   — discovery refresh, tri-state health, cooldown, failover
* :mod:`chat_model` — ``ChatFreeLLM`` (the ``BaseChatModel`` factory entry)

Import order matters: ``providers`` first so ``catalog``'s parent-package
import sees the submodule attribute during package initialization.
"""

from alpha.models.free_router.catalog import (
    CACHE_FILENAME,
    COOLDOWN_BASE_SECONDS,
    COOLDOWN_MAX_SECONDS,
    DEFAULT_TTL_SECONDS,
    FreeChatResult,
    FreeLLMRouter,
    FreeLLMUnavailableError,
    ProviderState,
    available_free_models,
    get_free_router,
    reset_free_router,
)
from alpha.models.free_router.chat_model import ChatFreeLLM
from alpha.models.free_router.providers import (
    AI_HORDE_ANONYMOUS_KEY,
    DEFAULT_TIMEOUT,
    DISCOVERY_TIMEOUT,
    PROVIDER_ORDER,
    PROVIDERS,
    DiscoveryResult,
    ProbeResult,
    ProviderChatResult,
    ProviderError,
    ProviderSpec,
    chat_completion,
    discover,
    health_probe,
    request,
)

__all__ = [
    "AI_HORDE_ANONYMOUS_KEY",
    "CACHE_FILENAME",
    "COOLDOWN_BASE_SECONDS",
    "COOLDOWN_MAX_SECONDS",
    "DEFAULT_TIMEOUT",
    "DEFAULT_TTL_SECONDS",
    "DISCOVERY_TIMEOUT",
    "ChatFreeLLM",
    "DiscoveryResult",
    "FreeChatResult",
    "FreeLLMRouter",
    "FreeLLMUnavailableError",
    "PROVIDERS",
    "PROVIDER_ORDER",
    "ProbeResult",
    "ProviderChatResult",
    "ProviderError",
    "ProviderSpec",
    "ProviderState",
    "chat_completion",
    "discover",
    "get_free_router",
    "available_free_models",
    "health_probe",
    "request",
    "reset_free_router",
]
