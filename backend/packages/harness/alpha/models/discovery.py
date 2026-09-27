"""Discover live model catalogs from provider APIs.

Providers add and retire models constantly — OpenRouter in particular turns over
dozens of entries a week and rotates its ``:free`` set daily. A hand-maintained
list in code or config is stale the day it is written, and the industry has
widely documented the resulting bugs: a hardcoded context window that disagrees
with the live registry, a capability table missing a whole model generation, a
"free" model quietly gone.

This module fetches the authoritative list and normalizes it. Three things
make it safe to run against a live agent:

* **Bounded and off-loop.** Every request has a timeout, runs in a worker
  thread, and a failed provider never fails the page — it reports
  ``unavailable`` with the reason.
* **Cached with a TTL and a negative cache.** Persisted under
  ``runtime_home()/models/discovery/`` so a restart does not re-fetch, an
  unchanged provider is not re-fetched on every request, and a provider that was
  down is not retried on every request either.
* **Egress-screened.** Endpoints pass
  :func:`alpha.community.url_safety.assert_model_endpoint_url` before any
  request, the same policy that guards BYO model configuration, so discovery
  cannot be turned into an SSRF probe against loopback or cloud metadata.

## Normalized shape

The output follows OpenRouter's ``/models`` descriptor, which is the best
capability contract available from any provider: it separates the *model's*
context length from the *endpoint's* actual window and output cap, lists
supported parameters as a real allowlist, and declares reasoning as
``{mandatory, supported_efforts, default_effort}`` rather than one boolean.

Collapsing those into a single ``context_window`` is the mistake that produces
the documented drift, so :class:`DiscoveredModel` keeps them separate.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

#: How long a successful fetch is considered fresh.
DEFAULT_TTL_SECONDS = 6 * 3600
#: How long a failure is remembered before the provider is retried. Without this
#: a down provider is re-probed on every single request.
NEGATIVE_TTL_SECONDS = 900
#: Per-request deadline. Discovery must never hold a Gateway response open.
FETCH_TIMEOUT_SECONDS = 12.0
#: Cap on models returned per provider, so a 4000-entry registry cannot blow up
#: the response or the cache file.
MAX_MODELS_PER_PROVIDER = 2000

#: User agent sent to provider catalogs. Some gateways reject a default one.
USER_AGENT = "Alpha-Model-Discovery/1.0 (+model catalog sync)"


@dataclass
class DiscoveredModel:
    """One model as reported by a provider's live catalog.

    The three context numbers are deliberately distinct. A gateway can proxy a
    local server whose real window differs from the published model card, and
    conflating them is how a summarization threshold ends up wrong:

    * ``context_length`` — the model's published total capacity.
    * ``endpoint_context_length`` — what *this* endpoint actually serves.
    * ``endpoint_max_completion_tokens`` — this endpoint's output cap.
    """

    id: str
    name: str = ""
    context_length: int | None = None
    endpoint_context_length: int | None = None
    endpoint_max_completion_tokens: int | None = None
    input_modalities: list[str] = field(default_factory=list)
    supported_parameters: list[str] = field(default_factory=list)
    reasoning_mandatory: bool = False
    reasoning_efforts: list[str] = field(default_factory=list)
    reasoning_default_effort: str | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    input_cache_read_per_million: float | None = None
    description: str | None = None
    #: True when the provider prices this model at zero on both axes. Computed,
    #: not trusted from a provider "free" flag, because those disagree.
    is_free: bool = False
    expires_at: int | None = None

    def supports_vision(self) -> bool:
        return "image" in self.input_modalities

    def supports_thinking(self) -> bool:
        return bool(self.reasoning_efforts) or self.reasoning_mandatory

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["supports_vision"] = self.supports_vision()
        data["supports_thinking"] = self.supports_thinking()
        return data


class DiscoveryState(BaseModel):
    """Cached outcome of one provider fetch."""

    provider: str
    models: list[dict[str, Any]] = Field(default_factory=list)
    fetched_at: float = 0.0
    ok: bool = True
    error: str | None = None
    source_url: str | None = None

    model_config = ConfigDict(extra="forbid")

    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.fetched_at) if self.fetched_at else float("inf")

    def is_fresh(self, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
        """Fresh when the last attempt succeeded inside ``ttl``.

        A failure is cached against the shorter negative TTL, so a provider that
        was down is retried on a sane cadence instead of on every request.
        """
        if not self.fetched_at:
            return False
        budget = ttl if self.ok else NEGATIVE_TTL_SECONDS
        return self.age_seconds() < budget


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_state_cache: dict[str, DiscoveryState] = {}


def _state_dir() -> Path:
    path = runtime_home() / "models" / "discovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(provider: str) -> Path:
    safe = "".join(ch for ch in provider if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValueError(f"invalid discovery provider id: {provider!r}")
    return _state_dir() / f"{safe}.json"


def _read_state(provider: str) -> DiscoveryState | None:
    path = _state_path(provider)
    if not path.is_file():
        return None
    try:
        return DiscoveryState.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.debug("Discarding unreadable discovery cache at %s", path, exc_info=True)
        return None


def _write_state(state: DiscoveryState) -> None:
    try:
        _state_path(state.provider).write_text(state.model_dump_json(), encoding="utf-8")
    except OSError:
        logger.debug("Could not persist discovery cache for %s", state.provider, exc_info=True)


def _cached(provider: str) -> DiscoveryState | None:
    with _state_lock:
        state = _state_cache.get(provider)
    if state is not None:
        return state
    state = _read_state(provider)
    if state is not None:
        with _state_lock:
            _state_cache[provider] = state
    return state


def _store(state: DiscoveryState) -> DiscoveryState:
    with _state_lock:
        _state_cache[state.provider] = state
    _write_state(state)
    return state


def reset_discovery_cache() -> None:
    """Drop in-memory discovery state. Test seam."""
    with _state_lock:
        _state_cache.clear()


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _openrouter_price_per_million(value: Any) -> float | None:
    """OpenRouter prices per token as a string; the catalog is per million."""
    price = _as_float(value)
    if price is None:
        return None
    return price * 1_000_000


def _parse_openrouter(payload: dict[str, Any]) -> list[DiscoveredModel]:
    """Normalize an OpenRouter-style ``{"data": [...]}`` catalog.

    Also serves most OpenAI-compatible gateways, which return the same envelope
    with only ``id`` populated — every richer field simply stays ``None``, and
    the caller can tell "the provider did not say" from "the provider said no".
    """
    models: list[DiscoveredModel] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        model_id = raw.get("id") or raw.get("canonical_slug")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
        modalities = architecture.get("input_modalities")
        top = raw.get("top_provider") if isinstance(raw.get("top_provider"), dict) else {}
        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        reasoning = raw.get("reasoning") if isinstance(raw.get("reasoning"), dict) else {}
        efforts = reasoning.get("supported_efforts")
        params = raw.get("supported_parameters")
        input_price = _openrouter_price_per_million(pricing.get("prompt"))
        output_price = _openrouter_price_per_million(pricing.get("completion"))
        models.append(
            DiscoveredModel(
                id=model_id.strip(),
                name=str(raw.get("name") or model_id),
                context_length=_as_int(raw.get("context_length")),
                endpoint_context_length=_as_int(top.get("context_length")),
                endpoint_max_completion_tokens=_as_int(top.get("max_completion_tokens")),
                input_modalities=[str(m) for m in modalities] if isinstance(modalities, list) else [],
                supported_parameters=[str(p) for p in params] if isinstance(params, list) else [],
                reasoning_mandatory=bool(reasoning.get("mandatory", False)),
                reasoning_efforts=[str(e) for e in efforts] if isinstance(efforts, list) else [],
                reasoning_default_effort=str(reasoning["default_effort"]) if reasoning.get("default_effort") else None,
                input_price_per_million=input_price,
                output_price_per_million=output_price,
                input_cache_read_per_million=_openrouter_price_per_million(pricing.get("input_cache_read")),
                description=str(raw["description"]) if raw.get("description") else None,
                # Both axes must be zero. A model with a free input but paid
                # output is not free, and providers do label those ":free".
                is_free=input_price == 0 and output_price == 0,
                expires_at=_as_int(raw.get("expiration_date")),
            )
        )
    return models


def _parse_ollama(payload: dict[str, Any]) -> list[DiscoveredModel]:
    """Normalize Ollama's ``/api/tags`` (no price or capability data)."""
    models: list[DiscoveredModel] = []
    for raw in payload.get("models") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name") or raw.get("model")
        if not isinstance(name, str) or not name.strip():
            continue
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        families = details.get("families")
        modalities = ["text"]
        if isinstance(families, list) and "clip" in {str(f).lower() for f in families}:
            modalities.append("image")
        models.append(
            DiscoveredModel(
                id=name.strip(),
                name=name.strip(),
                input_modalities=modalities,
                description=str(raw["details"]) if isinstance(raw.get("details"), str) else None,
                is_free=True,
            )
        )
    return models


#: ``provider id -> (url template, parser, auth style)``. ``{base}`` is the
#: configured endpoint. Kept declarative so adding a provider is one row.
#:
#: A provider absent from this table still works: it falls back to
#: :data:`DEFAULT_ADAPTER`, because the overwhelming majority of gateways speak
#: the OpenAI ``GET {base}/models`` shape. That is deliberate — requiring a code
#: change per provider is exactly the coupling that made the previous hardcoded
#: list expensive to maintain.
ADAPTERS: dict[str, tuple[str, Any, str]] = {
    "openrouter": ("{base}/models", _parse_openrouter, "bearer"),
    "requesty": ("{base}/models", _parse_openrouter, "bearer"),
    "vercel_ai_gateway": ("{base}/models", _parse_openrouter, "bearer"),
    # LiteLLM exposes the same envelope under /model/info.
    "litellm": ("{base}/model/info", _parse_openrouter, "bearer"),
    "ollama": ("{base}/api/tags", _parse_ollama, "none"),
    "lmstudio": ("{base}/v1/models", _parse_openrouter, "bearer"),
}

#: Used for any catalog provider without a specific row. OpenAI-compatible
#: ``GET {base}/models`` returning ``{"data": [{"id": ...}]}``.
DEFAULT_ADAPTER: tuple[str, Any, str] = ("{base}/models", _parse_openrouter, "bearer")


def _adapter_for(provider: str) -> tuple[str, Any, str]:
    """Adapter triple for a provider, falling back to the OpenAI-compatible one."""
    return ADAPTERS.get(provider, DEFAULT_ADAPTER)


def supports_discovery(provider: str) -> bool:
    """Whether *provider* can be discovered.

    True for every declared adapter plus any other provider, because the default
    OpenAI-compatible probe is correct for a gateway that does not need a special
    path. Only a provider with no ``base_url`` in ``models.yaml`` cannot be
    discovered, and that is reported by :func:`known_providers` instead.
    """
    return provider in ADAPTERS or provider in _catalog_provider_ids()


def _catalog_provider_ids() -> set[str]:
    try:
        from alpha.config.models_catalog import get_models_catalog

        return {entry.id for entry in get_models_catalog().catalog}
    except Exception:
        return set()


def _resolve_endpoint(provider: str, base: str) -> str:
    return _adapter_for(provider)[0].format(base=base.rstrip("/"))


def _screen(url: str) -> None:
    """Apply the same egress policy that guards BYO model configuration.

    Discovery fetches an operator-supplied endpoint, so without this it would be
    an SSRF primitive against loopback, RFC1918, and cloud metadata. Raises
    ``ModelEndpointBlockedError`` (a ``ValueError``) for a refused endpoint.
    """
    from alpha.community.url_safety import assert_model_endpoint_url

    assert_model_endpoint_url(url)


def _headers(auth_style: str, api_key: str | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if auth_style == "bearer" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def fetch_models(provider: str, base_url: str, api_key: str | None = None) -> DiscoveryState:
    """Fetch and normalize one provider's catalog.

    Blocking and network-bound: callers on an event loop must use
    :func:`afetch_models`. Never raises — a failure is recorded on the returned
    state so one unreachable provider cannot break a settings page.
    """
    import httpx

    try:
        url = _resolve_endpoint(provider, base_url)
    except ValueError as exc:
        return _store(DiscoveryState(provider=provider, ok=False, error=str(exc), fetched_at=time.time()))

    try:
        _screen(url)
    except Exception as exc:
        return _store(DiscoveryState(provider=provider, ok=False, error=f"endpoint refused by egress policy: {exc}", fetched_at=time.time()))

    auth_style = _adapter_for(provider)[2]
    parser = _adapter_for(provider)[1]
    try:
        response = httpx.get(url, headers=_headers(auth_style, api_key), timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.info("Model discovery failed for provider '%s': %s", provider, type(exc).__name__)
        return _store(DiscoveryState(provider=provider, ok=False, error=f"{type(exc).__name__}: {exc}", fetched_at=time.time(), source_url=url))

    models = parser(payload if isinstance(payload, dict) else {})
    models.sort(key=lambda m: m.id)
    if len(models) > MAX_MODELS_PER_PROVIDER:
        logger.info("Provider '%s' reported %d models; capping at %d", provider, len(models), MAX_MODELS_PER_PROVIDER)
        models = models[:MAX_MODELS_PER_PROVIDER]
    return _store(
        DiscoveryState(
            provider=provider,
            models=[model.to_dict() for model in models],
            fetched_at=time.time(),
            ok=True,
            source_url=url,
        )
    )


async def afetch_models(provider: str, base_url: str, api_key: str | None = None) -> DiscoveryState:
    """Async wrapper so an event-loop caller never blocks on the network."""
    import asyncio

    return await asyncio.to_thread(fetch_models, provider, base_url, api_key)


def get_state(provider: str, *, ttl: float = DEFAULT_TTL_SECONDS) -> DiscoveryState:
    """Cached state for a provider, fetching only when stale.

    Blocking and network-bound: callers on an event loop must use
    :func:`aget_state` or :func:`afetch_models`.
    """
    state = _cached(provider)
    if state is not None and state.is_fresh(ttl):
        return state
    try:
        base, key = configured_endpoint(provider)
    except ValueError as exc:
        return _store(DiscoveryState(provider=provider, ok=False, error=str(exc), fetched_at=time.time()))
    return fetch_models(provider, base, key)


async def aget_state(provider: str, *, ttl: float = DEFAULT_TTL_SECONDS) -> DiscoveryState:
    """Async :func:`get_state`, so a Gateway request never blocks on discovery IO."""
    import asyncio

    return await asyncio.to_thread(get_state, provider, ttl=ttl)


def configured_endpoint(provider: str) -> tuple[str, str | None]:
    """Base URL and API key for a discovery provider, from ``models.yaml``.

    Raises ``ValueError`` when the provider is not declared under ``catalog:`` or
    has no ``base_url``, so a caller reports "not configured" instead of guessing
    an endpoint.
    """
    from alpha.config.models_catalog import get_models_catalog

    catalog = get_models_catalog()
    for entry in catalog.catalog:
        if entry.id != provider:
            continue
        if not entry.base_url:
            raise ValueError(f"provider '{provider}' has no base_url in models.yaml")
        key = None
        if entry.key_env:
            import os

            key = os.getenv(entry.key_env) or None
        return entry.base_url, key
    raise ValueError(f"provider '{provider}' is not declared under `catalog:` in models.yaml")


def known_providers() -> list[str]:
    """Provider ids declared in ``models.yaml`` that can be discovered.

    Any provider with a ``base_url`` qualifies, because the default adapter
    probes the OpenAI-compatible ``GET {base}/models`` shape that essentially
    every gateway implements. A provider without a ``base_url`` (pure
    native-SDK entries such as Bedrock or Vertex) is excluded rather than probed
    at a guessed URL.
    """
    from alpha.config.models_catalog import get_models_catalog

    try:
        catalog = get_models_catalog()
    except Exception:
        logger.debug("models.yaml unavailable; no discovery providers", exc_info=True)
        return []
    return [entry.id for entry in catalog.catalog if entry.base_url]
