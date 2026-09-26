"""Budget-aware, health-aware failover across the wireable search providers.

This is the *decision* layer between :mod:`.providers` (one HTTP call) and
:mod:`.tools` (the LangChain tool). It reuses the failover pattern already
established by ``alpha.models.free_router.catalog`` rather than inventing a
second style of routing:

* **Tri-state health.** ``healthy=True`` means a call really succeeded,
  ``False`` means a call really failed, ``None`` means unknown. Nothing
  infers health from "it has not been tried yet" or from a discovery step.
* **Cooldown = exponential backoff with jitter.** After ``n`` consecutive
  failures a provider is skipped for ``min(600s, 15s * 2**(n-1))`` seconds,
  +/-20% jitter. The constants are intentionally identical to
  ``alpha/models/free_router/catalog.py`` (``COOLDOWN_BASE_SECONDS`` etc.) and a
  test asserts they stay in sync by parsing that file, so the two routers cannot
  drift apart. They are *not* imported: pulling ``alpha.models`` into a search
  tool costs ~50s of import time, which would be paid on every cold start.
* **Budget exhaustion is a skip, not an error.** A provider whose allowance is
  spent is dropped from the plan with a typed reason. It is only an *error* when
  the provider itself tells us the balance is gone - and even then the ledger
  records it so the next search routes around it instead of paying for the same
  failure again.
* **Honest exhaustion.** If nothing can serve the request, this raises
  :class:`~.errors.SearchExhaustedError` carrying every ``(provider, label)``
  attempt and every ``(provider, reason)`` skip. It never degrades to
  "no results found": a real 200 with an empty result list is the only thing
  that produces an empty answer.

What is deliberately *not* here: no second catalog/discovery service, no
scoring model, no quality ranking of providers. Provider order is a static,
documented policy (:data:`MODE_CHAINS`), because a free anonymous search tier
does not produce the usage data a learned ranker would need, and a fabricated
one would be worse than none.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.community.search_federation import providers as provider_layer
from alpha.community.search_federation.budgets import BUDGET_SPECS, BudgetLedger, CreditCost
from alpha.community.search_federation.errors import (
    ProviderBudgetExhaustedError,
    ProviderNotConfiguredError,
    ProviderRequestRejectedError,
    SearchExhaustedError,
    SearchProviderError,
)
from alpha.community.search_federation.providers import (
    MODE_NEWS,
    MODE_SEMANTIC,
    MODE_SIMILAR,
    MODE_WEB,
    PROVIDER_ORDER,
    ProviderSearchResult,
    SearchHit,
)
from alpha.community.search_federation.truth import not_wired_report, status_of

logger = logging.getLogger(__name__)

# --- Cooldown policy (mirrors alpha/models/free_router/catalog.py) ----------
COOLDOWN_BASE_SECONDS = 15.0
COOLDOWN_MAX_SECONDS = 600.0
COOLDOWN_JITTER = 0.2

HEALTH_CACHE_FILENAME = "search_provider_health.json"

#: Which providers to try, per search mode. This is a static, documented policy:
#: a free tier does not emit the usage data a learned ranker needs, and inventing
#: one would be a fabricated ranking. ``auto`` is the default chain.
MODE_CHAINS: dict[str, tuple[str, ...]] = {
    # Default: best ranked/scored result per unit cost, keyless first.
    "auto": PROVIDER_ORDER,
    # Explicit web lookup: Google's own ranking first when a key exists.
    "web": ("tavily_keyless", "tavily", "serper", "exa", "duckduckgo"),
    # Conceptual / "papers like this" queries: Exa's neural index is the point.
    "semantic": ("exa", "tavily", "tavily_keyless", "duckduckgo"),
    # Recent events: Tavily's news topic, Exa's news category, Serper's /news,
    # DDG as the floor.
    "news": ("tavily_keyless", "tavily", "exa", "serper", "duckduckgo"),
    # Only Exa can do this; an empty chain here fails loudly instead of
    # silently substituting a keyword search that is not the same thing.
    "similar": ("exa",),
}

DEFAULT_SEARCH_DEPTH = "basic"
#: ``advanced`` doubles the Tavily credit cost, so it is opt-in per call/config.
DEFAULT_TIMEOUT = provider_layer.DEFAULT_TIMEOUT


@dataclass(frozen=True)
class SearchRequest:
    """One search the agent asked for."""

    query: str
    max_results: int = 5
    time_range: str | None = None
    #: One of MODE_CHAINS. ``auto`` uses the default chain.
    mode: str = "auto"
    #: Tavily ``search_depth``. Always sent explicitly - a defaulted depth is
    #: how a 1,000-credit month silently becomes 500 searches.
    search_depth: str = DEFAULT_SEARCH_DEPTH
    include_answer: bool = False
    #: Ask every eligible provider and merge the results (costs one call each).
    merge: bool = False
    #: Required when ``mode="similar"``.
    url: str | None = None
    timeout: float = DEFAULT_TIMEOUT


@dataclass
class ProviderRuntimeState:
    """Health/cooldown row for one provider (JSON-serializable)."""

    name: str
    healthy: bool | None = None
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str | None = None
    last_success: float | None = None
    last_latency_ms: float | None = None
    calls: int = 0
    failures: int = 0


@dataclass
class SearchOutcome:
    """What the agent gets back, including the honest story of the attempt."""

    query: str
    mode: str
    provider: str
    hits: list[SearchHit]
    #: Total cost across every provider actually called (merge mode: all of them).
    cost_amount: float = 0.0
    cost_unit: str = ""
    cost_reason: str = ""
    latency_ms: float = 0.0
    answer: str | None = None
    #: ``(provider, failure_label)`` for providers that were called and failed.
    attempts: list[tuple[str, str]] = field(default_factory=list)
    #: ``(provider, reason)`` for providers that were never called.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    per_provider: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    retry_after_seconds: float | None = None

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe payload for the tool. Contains no credentials, ever."""
        return {
            "query": self.query,
            "mode": self.mode,
            "provider": self.provider,
            "total_results": len(self.hits),
            "results": [hit.to_dict() for hit in self.hits],
            "answer": self.answer,
            "cost": {"amount": round(self.cost_amount, 6), "unit": self.cost_unit, "reason": self.cost_reason},
            "latency_ms": round(self.latency_ms, 1),
            "providers_called": self.per_provider,
            "skipped_providers": [{"provider": p, "reason": r} for p, r in self.skipped],
            "failed_attempts": [{"provider": p, "failure": label} for p, label in self.attempts],
            "notes": self.notes,
        }


def _iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _failure_label(exc: BaseException) -> str:
    """Honest label for an unexpected exception: class (+ status) only."""
    if isinstance(exc, SearchProviderError):
        return exc.failure_label
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return f"{type(exc).__name__}(status={status})"
    return type(exc).__name__


class SearchFederation:
    """Ranked, budget-aware failover over the wireable search providers."""

    def __init__(
        self,
        *,
        layer: Any = provider_layer,
        clock: Callable[[], float] = time.time,
        health_path: Path | str | None = None,
        budget_path: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        allow_unverified_providers: bool = False,
        search_depth: str = DEFAULT_SEARCH_DEPTH,
        chains: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._layer = layer
        self._clock = clock
        self._health_path = Path(health_path) if health_path is not None else None
        self._allow_unverified = allow_unverified_providers
        self._search_depth = search_depth
        self._env = env
        # Routing order is a documented policy, overridable per instance so an
        # operator can reorder or extend it without editing the default table.
        self._chains: dict[str, tuple[str, ...]] = dict(chains) if chains else dict(MODE_CHAINS)
        self._lock = threading.Lock()
        self._state: dict[str, ProviderRuntimeState] = {
            name: ProviderRuntimeState(name=name) for name in layer.PROVIDER_ORDER
        }
        # ``layer.BUDGET_SPECS`` so a test can inject a provider with its own
        # allowance without touching the process-wide table.
        self.budgets = BudgetLedger(specs=dict(getattr(layer, "BUDGET_SPECS", BUDGET_SPECS)), path=budget_path, clock=clock)
        self._load_health()

    # ------------------------------------------------------------------
    # Health persistence
    # ------------------------------------------------------------------

    @property
    def health_path(self) -> Path:
        if self._health_path is None:
            from alpha.config.runtime_paths import runtime_home

            self._health_path = runtime_home() / HEALTH_CACHE_FILENAME
        return self._health_path

    def _env_map(self) -> Mapping[str, str]:
        return os.environ if self._env is None else self._env

    def _load_health(self) -> None:
        try:
            payload = json.loads(self.health_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("search provider health cache unreadable (%s); starting empty", type(exc).__name__)
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
            logger.warning("search provider health cache has an unexpected shape; starting empty")
            return
        for name, entry in payload["providers"].items():
            state = self._state.get(name)
            if state is None or not isinstance(entry, dict):
                continue
            try:
                healthy = entry.get("healthy")
                state.healthy = healthy if isinstance(healthy, bool) else None
                state.consecutive_failures = int(entry.get("consecutive_failures") or 0)
                state.cooldown_until = float(entry.get("cooldown_until") or 0.0)
                state.last_error = entry.get("last_error")
                state.last_success = entry.get("last_success")
                state.last_latency_ms = entry.get("last_latency_ms")
                state.calls = int(entry.get("calls") or 0)
                state.failures = int(entry.get("failures") or 0)
            except (TypeError, ValueError):
                logger.warning("search provider health entry %r malformed; keeping defaults", name)

    def _save_health(self) -> None:
        with self._lock:
            payload = {
                "updated_at": self._clock(),
                "cooldown_policy": {
                    "base_seconds": COOLDOWN_BASE_SECONDS,
                    "max_seconds": COOLDOWN_MAX_SECONDS,
                    "jitter": COOLDOWN_JITTER,
                    "mirrors": "alpha/models/free_router/catalog.py",
                },
                "providers": {name: asdict(state) for name, state in self._state.items()},
            }
        try:
            self.health_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.health_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.health_path)
        except OSError as exc:
            # Health caching is an optimization; never break search over it.
            logger.warning("could not persist search provider health: %s", type(exc).__name__)

    # ------------------------------------------------------------------
    # Health marks (the only writers of healthy/cooldown/failure fields)
    # ------------------------------------------------------------------

    def mark_success(self, name: str, *, latency_ms: float) -> None:
        with self._lock:
            state = self._state.get(name)
            if state is None:
                return
            state.healthy = True
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            state.last_success = self._clock()
            state.last_latency_ms = latency_ms
            state.last_error = None
            state.calls += 1
        self._save_health()

    def mark_failure(self, name: str, label: str) -> None:
        """Record a real failure: cool the provider down with backoff + jitter."""
        with self._lock:
            state = self._state.get(name)
            if state is None:
                return
            now = self._clock()
            state.healthy = False
            state.last_error = label
            state.consecutive_failures += 1
            state.calls += 1
            state.failures += 1
            window = min(COOLDOWN_MAX_SECONDS, COOLDOWN_BASE_SECONDS * (2 ** (state.consecutive_failures - 1)))
            jitter = 1.0 + random.uniform(-COOLDOWN_JITTER, COOLDOWN_JITTER)
            state.cooldown_until = now + min(COOLDOWN_MAX_SECONDS, window * jitter)
        self._save_health()

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def chain_for(self, mode: str) -> tuple[str, ...]:
        """Ordered provider names for a search mode (never empty by accident)."""
        chain = self._chains.get(mode)
        if chain is None:
            raise ProviderRequestRejectedError(
                "router",
                f"unknown search mode {mode!r}; expected one of {sorted(self._chains)}",
                status_code=None,
            )
        return tuple(name for name in chain if name in self._state)

    @staticmethod
    def can_serve(spec: Any, mode: str) -> bool:
        """Can this provider serve this mode?

        ``auto`` means "whatever this provider is good at", so a provider
        qualifies if it supports at least one ordinary mode. ``similar`` is
        deliberately excluded: find-similar is a different question, and
        answering it with a keyword search would be a lie.
        """
        if mode == "auto":
            return bool(spec.supports & {MODE_WEB, MODE_SEMANTIC, MODE_NEWS})
        return mode in spec.supports

    def _nominal_cost(self, name: str, request: SearchRequest) -> CreditCost:
        """Predicted cost of this call, in the provider's own unit."""
        spec = self._layer.PROVIDERS[name]
        if name in {"tavily", "tavily_keyless"}:
            return self._layer.tavily_credit_cost(search_depth=request.search_depth, include_answer=request.include_answer)
        if name == "exa":
            return self._layer.exa_credit_cost(request.max_results)
        if name == "serper":
            return self._layer.serper_credit_cost()
        return self._layer.nominal_cost(spec)

    def plan(self, request: SearchRequest) -> tuple[list[str], list[tuple[str, str]]]:
        """Ordered attemptable providers plus the honest reasons for each skip."""
        chain = self.chain_for(request.mode)
        now = self._clock()
        env = self._env_map()
        plan: list[str] = []
        skipped: list[tuple[str, str]] = []
        for name in chain:
            spec = self._layer.PROVIDERS.get(name)
            if spec is None:
                skipped.append((name, "no adapter registered"))
                continue
            if not self.can_serve(spec, request.mode):
                skipped.append((name, f"provider cannot serve mode={request.mode!r}"))
                continue
            try:
                status = status_of(name)
            except SearchProviderError as exc:
                skipped.append((name, exc.kind))
                continue
            if not status.selectable and not self._allow_unverified:
                skipped.append((name, f"{status.availability}: {status.summary.split('.')[0]}"))
                continue
            if spec.auth_env:
                unset = tuple(var for var in spec.auth_env if not (env.get(var) or "").strip())
                if unset:
                    skipped.append((name, f"not configured: set {' or '.join(unset)}"))
                    continue
            with self._lock:
                state = self._state.get(name)
                cooldown_until = state.cooldown_until if state else 0.0
            if cooldown_until > now:
                skipped.append((name, f"cooling down for another {cooldown_until - now:.0f}s after failures"))
                continue
            cost = self._nominal_cost(name, request)
            ok, reason = self.budgets.can_spend(name, cost)
            if not ok:
                skipped.append((name, f"budget: {reason}"))
                continue
            plan.append(name)
        return plan, skipped

    # ------------------------------------------------------------------
    # Calling one provider
    # ------------------------------------------------------------------

    def _call(self, name: str, request: SearchRequest) -> ProviderSearchResult:
        key = self._layer.credential(self._layer.PROVIDERS[name], env=self._env_map())
        timeout = request.timeout
        if request.mode == MODE_SIMILAR:
            return self._layer.exa_find_similar(
                str(request.url),
                key=key or "",
                max_results=request.max_results,
                timeout=timeout,
            )
        if name in {"tavily", "tavily_keyless"}:
            return self._layer.tavily_search(
                request.query,
                key=key,
                max_results=request.max_results,
                search_depth=request.search_depth,
                time_range=request.time_range,
                topic=MODE_NEWS if request.mode == MODE_NEWS else MODE_WEB,
                include_answer=request.include_answer,
                timeout=timeout,
            )
        if name == "exa":
            return self._layer.exa_search(
                request.query,
                key=key or "",
                max_results=request.max_results,
                time_range=request.time_range,
                topic=MODE_NEWS if request.mode == MODE_NEWS else MODE_WEB,
                timeout=timeout,
            )
        if name == "serper":
            return self._layer.serper_search(
                request.query,
                key=key or "",
                max_results=request.max_results,
                time_range=request.time_range,
                topic=MODE_NEWS if request.mode == MODE_NEWS else MODE_WEB,
                timeout=timeout,
            )
        if name == "duckduckgo":
            return self._layer.duckduckgo_search(
                request.query,
                max_results=request.max_results,
                time_range=request.time_range,
                timeout=timeout,
            )
        raise ProviderRequestRejectedError(name, "no adapter for this provider", status_code=None)

    def _settle(self, result: ProviderSearchResult) -> tuple[float, str, str]:
        """Charge the ledger and return (amount, unit, reason) actually charged.

        The provider's own reported figure wins when it is present and in the
        ledger's unit: our prediction is a model of their billing, theirs is the
        bill. When the units do not line up we refuse to guess, charge the
        prediction instead, and say so in the reason.
        """
        predicted = result.cost
        expected_unit = self.budgets.unit_for(result.provider)
        if (
            result.reported_cost is not None
            and result.reported_cost_unit
            and result.reported_cost_unit == expected_unit
        ):
            charged = CreditCost(
                float(result.reported_cost),
                str(result.reported_cost_unit),
                f"provider-reported ({predicted.reason})",
            )
        else:
            charged = predicted
        if charged.amount > 0:
            self.budgets.record(result.provider, charged)
        return charged.amount, charged.unit, charged.reason

    # ------------------------------------------------------------------
    # Routed search
    # ------------------------------------------------------------------

    def search(self, request: SearchRequest) -> SearchOutcome:
        """Try the planned providers in order; raise honestly if none can serve.

        The distinction this method exists to keep straight:

        * a provider that **answered with zero results** is a real answer - it
          is kept, but only as a last resort, and the payload says so;
        * a provider that **failed** is recorded as a typed attempt and the
          search moves on;
        * if **nothing** produced a usable answer, the caller gets
          :class:`SearchExhaustedError`, never ``{"results": []}``.
        """
        # Caller mistakes are validated *before* planning. If a missing url or an
        # empty query were discovered inside the provider loop, the honest
        # request-rejection would be recorded as a provider failure and then
        # masked as "everything is exhausted" - telling the agent to retry a
        # request that can never work.
        if not request.query.strip() and request.mode != MODE_SIMILAR:
            raise ProviderRequestRejectedError("router", "query must not be empty", status_code=None)
        if request.mode == MODE_SIMILAR and not (request.url or "").strip():
            raise ProviderRequestRejectedError("router", "mode='similar' requires a url to compare against", status_code=None)
        plan, skipped = self.plan(request)
        if not plan:
            raise SearchExhaustedError(
                self._exhausted_message(request, skipped),
                attempts=[],
                skipped=list(skipped),
                retry_after_seconds=self._earliest_retry(skipped),
            )

        attempts: list[tuple[str, str]] = []
        merged: list[SearchHit] = []
        seen_urls: set[str] = set()
        total_cost = 0.0
        cost_unit = ""
        cost_reason_parts: list[str] = []
        per_provider: list[dict[str, Any]] = []
        answer: str | None = None
        winner: str | None = None
        #: Set when a provider answered successfully with an empty result set.
        empty_winner: str | None = None
        empty_answer: str | None = None
        latency_ms = 0.0

        for name in plan:
            if not request.merge and winner is not None:
                skipped.append((name, "not needed: an earlier provider already answered"))
                continue
            if request.merge and winner is not None and len(merged) >= request.max_results:
                skipped.append((name, "not needed: merge already reached max_results"))
                continue
            try:
                result = self._call(name, request)
            except SearchProviderError as exc:
                attempts.append((name, exc.failure_label))
                self.mark_failure(name, exc.failure_label)
                if isinstance(exc, ProviderBudgetExhaustedError):
                    # The provider says its balance is gone: stop paying for it.
                    self.budgets.mark_provider_exhausted(name, exc.failure_label)
                    skipped.append((name, "budget exhausted (provider-reported)"))
                logger.warning("search provider %r failed: %s", name, exc.failure_label)
                continue
            except Exception as exc:  # noqa: BLE001 - one bad adapter must not sink the search
                label = _failure_label(exc)
                attempts.append((name, label))
                self.mark_failure(name, label)
                logger.warning("search provider %r raised an unmapped error: %s", name, label)
                continue

            self.mark_success(name, latency_ms=result.latency_ms)
            amount, unit, reason = self._settle(result)
            total_cost += amount
            cost_unit = cost_unit or unit
            cost_reason_parts.append(f"{name}: {reason}")
            latency_ms = result.latency_ms
            per_provider.append(result.to_dict())
            if answer is None and result.answer:
                answer = result.answer

            if winner is None:
                if result.hits:
                    winner = name
                    merged = list(result.hits)
                    seen_urls = {hit.url for hit in merged}
                    if not request.merge:
                        # Keep walking so the remaining candidates are reported
                        # as "not needed" rather than silently vanishing from
                        # the payload; the guard at the top of the loop stops
                        # them making a call.
                        continue
                else:
                    # Genuinely nothing on the web, from a provider that did
                    # answer. Remembered as the fallback answer, but keep going:
                    # a lower-ranked provider may still know something.
                    empty_winner = empty_winner or name
                    if empty_answer is None and result.answer:
                        empty_answer = result.answer
                    continue
            if request.merge:
                for hit in result.hits:
                    if hit.url in seen_urls:
                        continue
                    seen_urls.add(hit.url)
                    merged.append(hit)

        if winner is None and empty_winner is not None:
            # A provider really did answer - with nothing. That is a result, not
            # a failure, but the agent must be able to tell it apart from a
            # healthy non-empty answer, so it is labelled as such.
            winner = empty_winner
            answer = answer or empty_answer

        if winner is None:
            raise SearchExhaustedError(
                self._exhausted_message(request, skipped, attempts),
                attempts=attempts,
                skipped=list(skipped),
                retry_after_seconds=self._earliest_retry(skipped),
            )

        truncated = merged[: request.max_results]
        for index, hit in enumerate(truncated, start=1):
            hit.rank = index
        notes: list[str] = []
        if request.merge:
            notes.append(f"merge mode: {len(per_provider)} provider call(s) were billed for this one search")
        if not truncated:
            notes.append(f"{winner} answered successfully with zero results; this is not a provider failure")
        if attempts:
            notes.append(f"{len(attempts)} provider call(s) failed before this answer was produced")
        return SearchOutcome(
            query=request.query,
            mode=request.mode,
            provider=winner,
            hits=truncated,
            cost_amount=total_cost,
            cost_unit=cost_unit,
            cost_reason=" | ".join(cost_reason_parts),
            latency_ms=latency_ms,
            answer=answer,
            attempts=attempts,
            skipped=skipped,
            per_provider=per_provider,
            notes=notes,
        )

    def find_similar(self, url: str, *, max_results: int = 5, timeout: float = DEFAULT_TIMEOUT) -> SearchOutcome:
        """Pages like ``url``. Exa-only; fails loudly rather than substituting a keyword search."""
        return self.search(SearchRequest(query="", max_results=max_results, mode=MODE_SIMILAR, url=url, timeout=timeout))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _exhausted_message(
        self,
        request: SearchRequest,
        skipped: list[tuple[str, str]],
        attempts: list[tuple[str, str]] | None = None,
    ) -> str:
        parts = [f"no search provider could serve mode={request.mode!r}"]
        if attempts:
            parts.append(f"{len(attempts)} provider call(s) failed: {', '.join(f'{p}={label}' for p, label in attempts)}")
        if skipped:
            parts.append(f"{len(skipped)} provider(s) skipped: {', '.join(f'{p} ({reason})' for p, reason in skipped)}")
        parts.append("this is a provider/quota failure, not 'no results on the web'")
        return "; ".join(parts)

    def _earliest_retry(self, skipped: list[tuple[str, str]]) -> float | None:
        now = self._clock()
        waits: list[float] = []
        with self._lock:
            for name, reason in skipped:
                if not reason.startswith("cooling down"):
                    continue
                state = self._state.get(name)
                if state is not None and state.cooldown_until > now:
                    waits.append(state.cooldown_until - now)
        return min(waits) if waits else None

    def describe(self) -> dict[str, Any]:
        """Honest operator view: health, budget, credit cost, and what was left out."""
        now = self._clock()
        env = self._env_map()
        providers_view: list[dict[str, Any]] = []
        with self._lock:
            states = {name: asdict(state) for name, state in self._state.items()}
        for name in PROVIDER_ORDER:
            spec = self._layer.PROVIDERS[name]
            state = states.get(name, {})
            unset = tuple(var for var in spec.auth_env if not (env.get(var) or "").strip())
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            providers_view.append(
                {
                    "name": name,
                    "configured": bool(spec.auth_env) and not unset,
                    "keyless": not spec.auth_env,
                    "missing_env": list(unset),
                    "healthy": state.get("healthy"),
                    "consecutive_failures": state.get("consecutive_failures"),
                    "cooldown_until": _iso(cooldown_until) if cooldown_until > now else None,
                    "last_error": state.get("last_error"),
                    "last_success": _iso(state.get("last_success")),
                    "last_latency_ms": state.get("last_latency_ms"),
                    "calls": state.get("calls"),
                    "failures": state.get("failures"),
                    "supports": sorted(spec.supports),
                    "why_this_rank": spec.why,
                }
            )
        return {
            "source": "alpha.community.search_federation",
            "default_order": list(PROVIDER_ORDER),
            "mode_chains": {mode: list(chain) for mode, chain in self._chains.items()},
            "health_values": "true=call succeeded, false=call failed, null=unknown",
            "cooldown_policy": {
                "base_seconds": COOLDOWN_BASE_SECONDS,
                "max_seconds": COOLDOWN_MAX_SECONDS,
                "jitter": COOLDOWN_JITTER,
                "mirrors": "alpha/models/free_router/catalog.py",
            },
            "search_depth": self._search_depth,
            "providers": providers_view,
            "budgets": self.budgets.snapshot(),
            "not_wired": not_wired_report(),
        }


# ---------------------------------------------------------------------------
# Process singleton
# ---------------------------------------------------------------------------

_SINGLETON: SearchFederation | None = None
_SINGLETON_LOCK = threading.Lock()


def get_federation() -> SearchFederation:
    """Process-wide federation singleton (tests replace/reset it)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = SearchFederation()
        return _SINGLETON


def reset_federation() -> None:
    """Drop the singleton (tests / explicit re-initialization)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None


def available_providers() -> list[dict[str, Any]]:
    """Structured provider list for tooling/diagnostics."""
    return get_federation().describe()["providers"]


__all__ = [
    "COOLDOWN_BASE_SECONDS",
    "COOLDOWN_JITTER",
    "COOLDOWN_MAX_SECONDS",
    "MODE_CHAINS",
    "ProviderNotConfiguredError",
    "ProviderRuntimeState",
    "SearchFederation",
    "SearchOutcome",
    "SearchRequest",
    "available_providers",
    "get_federation",
    "reset_federation",
]
