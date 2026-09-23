"""Free-LLM catalog: discovery, tri-state health, cooldowns, selection, failover.

This module owns the *decision layer* between :mod:`...providers` (the HTTP
layer) and :class:`alpha.models.free_router.chat_model.ChatFreeLLM`:

* **Single-flight refresh.** Concurrent :meth:`FreeLLMRouter.refresh` callers
  never start a second discovery pass: one thread performs it, everyone else
  either reuses the result or, on a cold cache, briefly waits for it. The
  very first refresh on an empty cache blocks (bounded by per-request HTTP
  timeouts) so the first chat does not race an empty catalog.

* **Health is tri-state and survives refreshes.** ``healthy=True`` means a
  real call succeeded, ``False`` means a chat call failed, ``None`` means
  *unknown / inconclusive* (never probed, or a probe failed — a failed probe
  is not proof the chat path is dead). A catalog re-discovery never rewrites
  chat health, failure counts, or cooldowns: only real outcomes do.

* **Cooldown = exponential backoff + jitter.** Consecutive chat failures put
  a provider in cooldown for ``min(600s, 15s * 2**(n-1))`` with +/-20%
  jitter. Selection skips cooled-down providers strictly; when *every*
  candidate is cooling down, chat raises an honest
  :class:`FreeLLMUnavailableError` with the earliest retry hint instead of
  hammering endpoints.

* **Honest failure.** When nothing can serve the request the router raises
  :class:`FreeLLMUnavailableError` (a ``ConnectionError`` subclass, so
  ``alpha.models.fallback`` treats it as retryable and continues the
  configured model fallback chain) carrying honest ``(provider, label)``
  attempt pairs — error classes/statuses only, never payloads.

Caches at ``runtime_home()/free_llm_catalog.json`` (atomic tmp + replace).
No API keys exist in this package: only documented anonymous constants in
``providers``.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.models.free_router import providers as provider_layer

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300.0
COOLDOWN_BASE_SECONDS = 15.0
COOLDOWN_MAX_SECONDS = 600.0
COOLDOWN_JITTER = 0.2
WAIT_ROUND_SECONDS = 5.0
MAX_WAIT_ROUNDS = 6
CACHE_FILENAME = "free_llm_catalog.json"
_MODELS_SHOWN_IN_API = 25


class FreeLLMUnavailableError(ConnectionError):
    """No free provider could serve the request right now.

    Subclasses ``ConnectionError`` so ``is_retryable_llm_error`` classifies
    it retryable and a configured fallback chain may continue. ``attempts``
    holds honest ``(provider, label)`` pairs where ``label`` is an error
    class plus HTTP status — never exception text or payloads.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: list[tuple[str, str]] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.retry_after_seconds = retry_after_seconds


@dataclass
class ProviderState:
    """Per-provider catalog + health state (JSON-serializable for the cache)."""

    name: str
    # Tri-state chat health: True=proven working, False=chat failed,
    # None=unknown/inconclusive (never probed, probe failed, discovery only).
    healthy: bool | None = None
    discovery_ok: bool | None = None
    models: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None  # most recent chat/probe failure
    discovery_error: str | None = None  # most recent catalog discovery failure
    last_checked: float | None = None  # epoch seconds
    last_success: float | None = None  # epoch seconds
    consecutive_failures: int = 0
    cooldown_until: float = 0.0  # epoch seconds; 0 = not cooling down
    latency_ms: float | None = None


@dataclass
class FreeChatResult:
    """Honest result of one routed chat call."""

    text: str
    provider: str
    model_id: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    latency_ms: float = 0.0


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _failure_label(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        response_status = getattr(getattr(exc, "response", None), "status_code", None)
        status = response_status if isinstance(response_status, int) and not isinstance(response_status, bool) else None
    return f"{type(exc).__name__}(status={status})" if status is not None else type(exc).__name__


class FreeLLMRouter:
    """Catalog/health/cooldown state plus the routed chat call.

    All provider-HTTP traffic goes through the module-level
    ``providers.request`` seam; tests stub that one function (plus inject
    ``clock``/``cache_path``) and exercise this logic for real.
    """

    def __init__(
        self,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        cache_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        layer: Any = provider_layer,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._layer = layer
        self._cache_path = cache_path
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._refreshing = False
        self._loaded = False
        self._last_refresh: float = 0.0
        self._states: dict[str, ProviderState] = {
            name: ProviderState(name=name) for name in layer.PROVIDER_ORDER
        }
        self._load_cache()

    # ------------------------------------------------------------------
    # Cache persistence (atomic; corrupt input degrades honestly)
    # ------------------------------------------------------------------

    @property
    def cache_path(self) -> Path:
        if self._cache_path is None:
            self._cache_path = runtime_home() / CACHE_FILENAME
        return self._cache_path

    def _load_cache(self) -> None:
        try:
            raw = self.cache_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            # Corrupt cache never blocks startup: start empty + say why.
            logger.warning("Free-LLM catalog cache unreadable (%s); starting empty", type(exc).__name__)
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
            logger.warning("Free-LLM catalog cache has an unexpected shape; starting empty")
            return
        for name, entry in payload["providers"].items():
            if name not in self._states or not isinstance(entry, dict):
                continue
            state = self._states[name]
            try:
                state.healthy = entry.get("healthy")
                state.discovery_ok = entry.get("discovery_ok")
                state.models = [m for m in entry.get("models", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
                state.last_error = entry.get("last_error")
                state.discovery_error = entry.get("discovery_error")
                state.last_checked = entry.get("last_checked")
                state.last_success = entry.get("last_success")
                state.consecutive_failures = int(entry.get("consecutive_failures") or 0)
                state.cooldown_until = float(entry.get("cooldown_until") or 0.0)
                state.latency_ms = entry.get("latency_ms")
            except (TypeError, ValueError):
                logger.warning("Free-LLM catalog cache entry %r malformed; keeping defaults", name)
        updated_at = payload.get("updated_at")
        if isinstance(updated_at, (int, float)):
            self._loaded = True
            self._last_refresh = float(updated_at)

    def _save_cache(self) -> None:
        with self._lock:
            payload = {
                "updated_at": self._last_refresh,
                "ttl_seconds": self._ttl,
                "providers": {name: asdict(state) for name, state in self._states.items()},
            }
        path = self.cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            # A read-only runtime dir must not break chat: cache is optional.
            logger.warning("Could not persist free-LLM catalog cache: %s", type(exc).__name__)

    # ------------------------------------------------------------------
    # Discovery refresh (single-flight)
    # ------------------------------------------------------------------

    @property
    def ttl(self) -> float:
        return self._ttl

    @property
    def last_refresh(self) -> float:
        with self._lock:
            return self._last_refresh

    def _discover_all(self) -> None:
        """Fetch every provider's catalog concurrently; record honest results."""
        from concurrent.futures import ThreadPoolExecutor

        names = list(self._layer.PROVIDER_ORDER)
        results: dict[str, provider_layer.DiscoveryResult] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
            future_map = {
                pool.submit(self._safe_discover, self._layer.PROVIDERS[name]): name
                for name in names
            }
            for future, name in future_map.items():
                results[name] = future.result()
        self._merge_discovery(results)

    def _safe_discover(self, spec: Any) -> provider_layer.DiscoveryResult:
        try:
            return self._layer.discover(spec)
        except Exception as exc:  # noqa: BLE001 - one bad provider must not sink the refresh
            return provider_layer.DiscoveryResult(ok=False, models=[], error=f"{type(exc).__name__}: {str(exc)[:200]}")

    def _merge_discovery(self, results: dict[str, provider_layer.DiscoveryResult]) -> None:
        now = self._clock()
        with self._lock:
            for name, result in results.items():
                state = self._states.get(name)
                if state is None:
                    continue
                state.last_checked = now
                state.discovery_ok = result.ok
                state.latency_ms = result.latency_ms
                if result.ok:
                    # Catalog refresh never rewrites chat health/cooldowns.
                    state.models = list(result.models)
                    state.discovery_error = None
                else:
                    # Honest failure: keep last-known models (stale), record why.
                    state.discovery_error = result.error or "discovery failed"
            self._last_refresh = now
            self._loaded = True

    def refresh(self, *, force: bool = False) -> bool:
        """Refresh the catalog; returns True iff this call performed the work.

        Single-flight: while a refresh is in flight, other callers return
        ``False`` immediately — *except* on a cold, never-loaded cache, where
        they briefly wait (bounded) so the first chat does not race an empty
        catalog.
        """
        with self._lock:
            if self._refreshing:
                if self._loaded and not force:
                    return False
                # Cold cache: briefly wait for the in-flight pass so the first
                # chat does not race an empty catalog (bounded wait).
                rounds = 0
                while self._refreshing and rounds < MAX_WAIT_ROUNDS:
                    self._cond.wait(timeout=WAIT_ROUND_SECONDS)
                    rounds += 1
                if self._refreshing:
                    # Still busy after the bound: proceed with what we have;
                    # an empty catalog surfaces as an honest no-candidates error.
                    return False
                if self._loaded and not force:
                    return False  # the in-flight pass satisfied this caller
            if self._loaded and not force and (self._clock() - self._last_refresh) < self._ttl:
                return False
            self._refreshing = True
        try:
            self._discover_all()
        finally:
            with self._lock:
                self._refreshing = False
                self._cond.notify_all()
        self._save_cache()
        return True

    # ------------------------------------------------------------------
    # Health marks (the ONLY writers of healthy/cooldown/failure fields)
    # ------------------------------------------------------------------

    def mark_success(self, name: str, *, latency_ms: float | None = None) -> None:
        with self._lock:
            state = self._states.get(name)
            if state is None:
                return
            state.healthy = True
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            state.last_success = self._clock()
            state.last_error = None
        self._save_cache()  # persist health/cooldown across restarts (lock released above)

    def mark_failure(self, name: str, label: str, *, inconclusive: bool = False) -> None:
        """Record a failed call. ``inconclusive=True`` (probe failure) keeps
        tri-state ``None`` unless the provider was already proven healthy —
        a failed probe is never proof the chat path is dead."""
        with self._lock:
            state = self._states.get(name)
            if state is None:
                return
            now = self._clock()
            state.last_error = label
            if inconclusive:
                if state.healthy is not True:
                    state.healthy = None
                return
            state.healthy = False
            state.consecutive_failures += 1
            window = min(COOLDOWN_MAX_SECONDS, COOLDOWN_BASE_SECONDS * (2 ** (state.consecutive_failures - 1)))
            jitter = 1.0 + random.uniform(-COOLDOWN_JITTER, COOLDOWN_JITTER)
            state.cooldown_until = now + min(COOLDOWN_MAX_SECONDS, window * jitter)
        self._save_cache()  # persist health/cooldown across restarts (lock released above)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _rank_key(self, name: str, now: float) -> tuple[int, int]:
        state = self._states[name]
        healthy_rank = 0 if state.healthy is True else (1 if state.healthy is None else 2)
        order_rank = list(self._layer.PROVIDER_ORDER).index(name)
        return (healthy_rank, order_rank)

    def _pick_model(self, spec: Any, state: ProviderState, requested: str) -> str | None:
        if not state.models:
            return None
        if requested and requested != "auto":
            for entry in state.models:
                if entry.get("id") == requested:
                    return requested
            return None  # honest: this provider does not offer the model
        documented = set(spec.documented_models)
        for entry in state.models:
            if entry.get("id") in documented:
                return str(entry["id"])
        first = state.models[0].get("id")
        return str(first) if first else None

    def candidates(self, *, model: str = "auto") -> list[tuple[Any, str]]:
        """Ordered ``(spec, model_id)`` pairs a chat call may attempt.

        Skips cooling-down providers strictly. Raises
        :class:`FreeLLMUnavailableError` with an honest reason when nothing
        is attemptable (all cooling down / requested model offered nowhere /
        no discovered models at all).
        """
        if not self._loaded or (self._clock() - self._last_refresh) >= self._ttl:
            self.refresh()
        now = self._clock()
        pairs: list[tuple[Any, str]] = []
        cooldown_skips = 0
        with self._lock:
            ordered = sorted(self._states, key=lambda n: self._rank_key(n, now))
            for name in ordered:
                state = self._states[name]
                if state.cooldown_until > now:
                    cooldown_skips += 1
                    continue
                if not state.models:
                    continue
                spec = self._layer.PROVIDERS[name]
                mid = self._pick_model(spec, state, model)
                if mid is None:
                    continue
                pairs.append((spec, mid))
        if pairs:
            return pairs
        total_with_models = sum(1 for s in self._states.values() if s.models)
        if total_with_models and cooldown_skips == total_with_models:
            with self._lock:
                earliest = min(
                    (s.cooldown_until for s in self._states.values() if s.models and s.cooldown_until > now),
                    default=now,
                )
            raise FreeLLMUnavailableError(
                f"all {cooldown_skips} free provider candidate(s) are cooling down after failures",
                retry_after_seconds=max(0.0, earliest - now),
            )
        if model and model != "auto":
            raise FreeLLMUnavailableError(
                f"model '{model}' is not offered by any reachable free provider"
            )
        raise FreeLLMUnavailableError(
            "no free provider candidates: discovery has not succeeded for any provider yet"
        )

    # ------------------------------------------------------------------
    # Routed chat (per-provider failover with honest marks)
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> FreeChatResult:
        """Try eligible providers in ranked order; honest error if none serve."""
        pairs = self.candidates(model=model)
        attempts: list[tuple[str, str]] = []
        for spec, mid in pairs:
            start = time.perf_counter()
            try:
                result = self._layer.chat_completion(
                    spec,
                    mid,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout if timeout is not None else provider_layer.DEFAULT_TIMEOUT,
                    extra_body=extra,
                )
            except Exception as exc:  # noqa: BLE001 - classified below; BaseExceptions propagate
                label = _failure_label(exc)
                attempts.append((spec.name, label))
                self.mark_failure(spec.name, label)
                logger.warning("Free provider '%s' chat attempt failed: %s", spec.name, label)
                continue
            latency = (time.perf_counter() - start) * 1000.0
            self.mark_success(spec.name, latency_ms=latency)
            return FreeChatResult(
                text=result.text,
                provider=spec.name,
                model_id=mid,
                tool_calls=result.tool_calls,
                usage=result.usage,
                latency_ms=latency,
            )
        cooling = [name for name, state in self._states.items() if state.cooldown_until > self._clock()]
        raise FreeLLMUnavailableError(
            f"all {len(attempts)} free provider attempt(s) failed ({len(cooling)} now cooling down)",
            attempts=attempts,
        )

    # ------------------------------------------------------------------
    # Probing (optional liveness; failures stay inconclusive)
    # ------------------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Small liveness probe per attemptable provider; honest tri-state."""
        summary: dict[str, Any] = {}
        for spec, mid in self._candidates_for_probe():
            probe_result = self._layer.health_probe(spec, mid)
            if probe_result.ok:
                self.mark_success(spec.name, latency_ms=probe_result.latency_ms)
            else:
                self.mark_failure(spec.name, probe_result.error or "probe failed", inconclusive=True)
            summary[spec.name] = {
                "ok": probe_result.ok,
                "latency_ms": round(probe_result.latency_ms, 1),
                "error": probe_result.error,
                "note": "failed probe is inconclusive; chat health unchanged unless previously proven",
            }
        self._save_cache()
        return summary

    def _candidates_for_probe(self) -> list[tuple[Any, str]]:
        """Probe set: providers with models, not currently cooling down."""
        now = self._clock()
        out: list[tuple[Any, str]] = []
        with self._lock:
            for name in self._layer.PROVIDER_ORDER:
                state = self._states[name]
                if not state.models or state.cooldown_until > now:
                    continue
                mid = self._pick_model(self._layer.PROVIDERS[name], state, "auto")
                if mid:
                    out.append((self._layer.PROVIDERS[name], mid))
        return out

    # ------------------------------------------------------------------
    # API view (honest disclosure, never fabricated availability)
    # ------------------------------------------------------------------

    def catalog_dict(self) -> dict[str, Any]:
        now = self._clock()
        providers_view: list[dict[str, Any]] = []
        with self._lock:
            for name in self._layer.PROVIDER_ORDER:
                state = self._states[name]
                spec = self._layer.PROVIDERS[name]
                cooling = state.cooldown_until > now
                providers_view.append(
                    {
                        "name": name,
                        "base_url": spec.base_url,
                        "healthy": state.healthy,  # tri-state: true/false/null(unknown)
                        "discovery_ok": state.discovery_ok,
                        "last_error": state.last_error,
                        "discovery_error": state.discovery_error,
                        "last_checked": _iso(state.last_checked),
                        "last_success": _iso(state.last_success),
                        "consecutive_failures": state.consecutive_failures,
                        "cooldown_until": _iso(state.cooldown_until) if cooling else None,
                        "latency_ms": state.latency_ms,
                        "model_count": len(state.models),
                        "models": state.models[:_MODELS_SHOWN_IN_API],
                        "models_truncated": len(state.models) > _MODELS_SHOWN_IN_API,
                        "source_labels": sorted({str(m.get("source", "catalog")) for m in state.models})
                        if state.models
                        else [],
                    }
                )
        try:
            eligible = [f"{spec.name}:{mid}" for spec, mid in self._candidates_unrefreshed()]
        except FreeLLMUnavailableError:
            eligible = []
        return {
            "source": "alpha-free-llm-router",
            "selection_method": "provider order ranked by tri-state health (true > unknown > false); "
            "no quality scoring — free anonymous gateways only",
            "health_values": "true=call succeeded, false=chat call failed, null=unknown/inconclusive",
            "refreshed_at": _iso(self._last_refresh) if self._loaded else None,
            "ttl_seconds": self._ttl,
            "providers": providers_view,
            "eligible_candidates": eligible,
            "disclaimer": "Reachability and free pricing on anonymous gateways change at any time; "
            "no availability guarantee. Do not send secrets to anonymous endpoints.",
        }

    def _candidates_unrefreshed(self) -> list[tuple[Any, str]]:
        """candidates() without triggering a network refresh (API view only)."""
        now = self._clock()
        pairs: list[tuple[Any, str]] = []
        with self._lock:
            for name in sorted(self._states, key=lambda n: self._rank_key(n, now)):
                state = self._states[name]
                if state.cooldown_until > now or not state.models:
                    continue
                mid = self._pick_model(self._layer.PROVIDERS[name], state, "auto")
                if mid:
                    pairs.append((self._layer.PROVIDERS[name], mid))
        return pairs


# ---------------------------------------------------------------------------
# Process singleton
# ---------------------------------------------------------------------------

_SINGLETON: FreeLLMRouter | None = None
_SINGLETON_LOCK = threading.Lock()


def get_free_router() -> FreeLLMRouter:
    """Process-wide router singleton (tests replace/reset it)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = FreeLLMRouter()
        return _SINGLETON


def reset_free_router() -> None:
    """Drop the singleton (tests / explicit re-initialization)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None
