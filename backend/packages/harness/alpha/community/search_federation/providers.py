"""Provider HTTP layer: one seam, four wireable adapters, no silent empties.

Discipline inherited from ``alpha.models.free_router.providers``:

* **One HTTP seam.** Every outbound call goes through the module-level
  :func:`request`. Tests stub that one function; nothing else touches the
  network. No real credential is ever a literal in this file.
* **Honest payloads.** A 2xx with an empty ``results`` list is returned as an
  empty list (the web genuinely had nothing). A 2xx we cannot parse, or one
  missing the results key, raises :class:`ProviderContractError` rather than
  pretending the answer was "no results".
* **Typed failures.** Every non-2xx and every transport error becomes a typed
  error from :mod:`.errors`, carrying the HTTP status so the federation can
  classify retryable vs deterministic.
* **Every call has a real timeout.** No call is made without one.

Credential handling
-------------------
Credentials come only from environment variables named in the spec. An unset
variable means the provider is *not configured* - :func:`credential` returns
``None`` and the federation skips the provider. There is no code path that
raises because a key is missing, and no fallback to a bundled default key.

Tavily keyless
--------------
``tavily_keyless`` sends the documented ``X-Tavily-Access-Mode: keyless``
header and no ``Authorization``. Note the vendor-documented precedence: if both
are present the API key wins and the keyless budget is not used, so this module
never sends both. When ``TAVILY_API_KEY`` is set the federation prefers the
keyed provider (it has the higher rate limit) and still keeps keyless as a
fallback.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from alpha.community.search_federation.budgets import (
    BUDGET_SPECS,
    TAVILY_AUTO_PARAMETERS_CREDIT_COST,
    TAVILY_EXTRACT_CREDITS_PER_URL_BASIC,
    TAVILY_SEARCH_CREDIT_COSTS,
    BudgetSpec,
    CreditCost,
)
from alpha.community.search_federation.errors import (
    ProviderAuthError,
    ProviderBudgetExhaustedError,
    ProviderContractError,
    ProviderRateLimitedError,
    ProviderRequestRejectedError,
    ProviderRetiredError,
    ProviderUnavailableError,
    SearchProviderError,
)
from alpha.community.search_federation.truth import assert_selectable

logger = logging.getLogger(__name__)

USER_AGENT = "Alpha-Search-Federation/1.0 (+keyless-and-free-tier-web-search)"
DEFAULT_TIMEOUT = 30.0
#: Tavily `advanced` searches and Exa `deep` fan out upstream; give them room.
SLOW_TIMEOUT = 60.0
TAVILY_KEYLESS_HEADER = "X-Tavily-Access-Mode"
TAVILY_KEYLESS_VALUE = "keyless"
TAVILY_BASE_URL = "https://api.tavily.com"
EXA_BASE_URL = "https://api.exa.ai"
SERPER_BASE_URL = "https://google.serper.dev"

#: Tavily search depths, cheapest first. ``advanced`` is deliberately not the
#: default: it costs 2x a credit.
TAVILY_SEARCH_DEPTHS: tuple[str, ...] = ("basic", "fast", "ultra-fast", "advanced")

#: Serper/Exa ``num``/``numResults`` above 10 changes the bill (Exa bills
#: $1/1k per result above 10) and rarely improves an agent's answer.
MAX_RESULTS_CAP = 20

#: Which modes each provider can serve, so routing never asks for a capability
#: the provider does not have.
MODE_WEB = "web"
MODE_SEMANTIC = "semantic"
MODE_NEWS = "news"
MODE_SIMILAR = "similar"


@dataclass(frozen=True)
class SearchProviderSpec:
    """Static description of one wireable search provider."""

    name: str
    base_url: str
    search_path: str
    #: Environment variables that must ALL be set for this provider to be usable.
    #: Empty tuple => keyless.
    auth_env: tuple[str, ...] = ()
    #: "bearer" | "x-api-key" | "x-keyless-header" | "none"
    auth_style: str = "none"
    #: Header name for ``auth_style == "x-api-key"``. Each vendor documents its
    #: own casing and we send what they document (headers are case-insensitive
    #: in HTTP, but a captured request that differs from the docs is a bad look).
    auth_header_name: str = "x-api-key"
    budget: BudgetSpec = field(default_factory=lambda: BUDGET_SPECS["duckduckgo"])
    supports: frozenset[str] = frozenset({MODE_WEB})
    #: Rank in the default chain; lower is tried first.
    default_rank: int = 99
    #: Human-readable reason this provider earns its rank.
    why: str = ""


PROVIDERS: dict[str, SearchProviderSpec] = {
    "tavily_keyless": SearchProviderSpec(
        name="tavily_keyless",
        base_url=TAVILY_BASE_URL,
        search_path="/search",
        auth_style="x-keyless-header",
        budget=BUDGET_SPECS["tavily_keyless"],
        supports=frozenset({MODE_WEB, MODE_NEWS, MODE_SEMANTIC}),
        default_rank=0,
        why="Best zero-setup result: LLM-shaped, scored, ranked results with no account and no key.",
    ),
    "tavily": SearchProviderSpec(
        name="tavily",
        base_url=TAVILY_BASE_URL,
        search_path="/search",
        auth_env=("TAVILY_API_KEY",),
        auth_style="bearer",
        budget=BUDGET_SPECS["tavily"],
        supports=frozenset({MODE_WEB, MODE_NEWS, MODE_SEMANTIC}),
        default_rank=1,
        why="Same engine as keyless with a 1,000 credit/month allowance and a 100 RPM development-key limit.",
    ),
    "exa": SearchProviderSpec(
        name="exa",
        base_url=EXA_BASE_URL,
        search_path="/search",
        auth_env=("EXA_API_KEY",),
        auth_style="x-api-key",
        budget=BUDGET_SPECS["exa"],
        supports=frozenset({MODE_WEB, MODE_SEMANTIC, MODE_SIMILAR, MODE_NEWS}),
        default_rank=2,
        why="Neural/semantic retrieval and find-similar, which none of the other providers offer.",
    ),
    "serper": SearchProviderSpec(
        name="serper",
        base_url=SERPER_BASE_URL,
        search_path="/search",
        auth_env=("SERPER_API_KEY",),
        auth_style="x-api-key",
        auth_header_name="X-API-KEY",
        budget=BUDGET_SPECS["serper"],
        supports=frozenset({MODE_WEB, MODE_NEWS}),
        default_rank=3,
        why="Cheapest raw Google SERP JSON; the only provider here with Google's own ranking for keyword queries.",
    ),
    "duckduckgo": SearchProviderSpec(
        name="duckduckgo",
        base_url="",
        search_path="",
        auth_style="none",
        budget=BUDGET_SPECS["duckduckgo"],
        supports=frozenset({MODE_WEB, MODE_NEWS}),
        default_rank=4,
        why="Keyless last-resort so search still answers when every HTTP provider is down or out of quota.",
    ),
}

#: Default attempt order, highest quality-per-unit-cost first.
PROVIDER_ORDER: tuple[str, ...] = tuple(sorted(PROVIDERS, key=lambda n: PROVIDERS[n].default_rank))


# ---------------------------------------------------------------------------
# HTTP seam
# ---------------------------------------------------------------------------

_CLIENT: httpx.Client | None = None


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
        )
    return _CLIENT


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Response:
    """The single module-level HTTP seam (tests stub this function)."""
    return _client().request(method, url, headers=headers, json=json_body, timeout=timeout)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def credential(spec: SearchProviderSpec, env: Mapping[str, str] | None = None) -> str | None:
    """Return this provider's credential, or ``None`` when it has none.

    ``None`` means "not usable right now" and is never an error: a keyless
    provider returns ``None`` too, and the caller distinguishes the two by
    looking at ``spec.auth_env``.
    """
    if not spec.auth_env:
        return None
    source = os.environ if env is None else env
    for name in spec.auth_env:
        value = (source.get(name) or "").strip()
        if not value:
            return None
    return (source.get(spec.auth_env[0]) or "").strip()


def missing_env(spec: SearchProviderSpec) -> tuple[str, ...]:
    """Which of this provider's env vars are unset (all of them, to be usable)."""
    if not spec.auth_env:
        return ()
    unset = tuple(name for name in spec.auth_env if not (os.environ.get(name) or "").strip())
    return unset or ()


def auth_headers(spec: SearchProviderSpec, key: str | None) -> dict[str, str]:
    """Build the auth header for this provider.

    Keyless and keyed Tavily are never combined: the vendor documents that a
    supplied API key takes precedence, so sending both would silently spend the
    *account's* credits while the caller believed it was on the free tier.
    """
    if spec.auth_style == "x-keyless-header":
        return {TAVILY_KEYLESS_HEADER: TAVILY_KEYLESS_VALUE}
    if not key:
        return {}
    if spec.auth_style == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if spec.auth_style == "x-api-key":
        return {spec.auth_header_name: key}
    return {}


# ---------------------------------------------------------------------------
# Credit costs
# ---------------------------------------------------------------------------


def tavily_credit_cost(
    *,
    search_depth: str,
    include_answer: bool = False,
    include_raw_content: bool = False,
    auto_parameters: bool = False,
) -> CreditCost:
    """Cost of one Tavily search, computed rather than assumed.

    ``search_depth`` is a required keyword argument on purpose. Tavily bills
    ``advanced`` at 2 credits and everything else at 1, and ``auto_parameters``
    bills 2 regardless of what you ask for, so a defaulted depth is a doubled
    bill. Making it impossible to omit is cheaper than a comment asking people
    not to.
    """
    if auto_parameters:
        return CreditCost(
            float(TAVILY_AUTO_PARAMETERS_CREDIT_COST),
            "tavily_credit",
            "auto_parameters=true is billed as advanced (2 credits) regardless of search_depth",
        )
    cost = TAVILY_SEARCH_CREDIT_COSTS.get(search_depth)
    if cost is None:
        raise ValueError(f"unknown Tavily search_depth {search_depth!r}; expected one of {TAVILY_SEARCH_DEPTHS}")
    reason = f"search_depth={search_depth} costs {cost} credit(s)"
    if include_answer:
        reason += "; include_answer is not billed separately by Tavily's published table"
    if include_raw_content:
        reason += "; include_raw_content is not billed separately by Tavily's published table"
    return CreditCost(float(cost), "tavily_credit", reason)


def tavily_extract_credit_cost(url_count: int, *, extract_depth: str = "basic") -> CreditCost:
    """Cost of one Tavily Extract call (1 credit per 5 *successful* URLs)."""
    per_url = TAVILY_EXTRACT_CREDITS_PER_URL_BASIC * (2 if extract_depth == "advanced" else 1)
    return CreditCost(
        per_url * max(0, url_count),
        "tavily_credit",
        f"extract depth={extract_depth}: {per_url:g} credit per successful URL (1 credit per 5 URLs basic)",
    )


def exa_credit_cost(num_results: int, *, with_summary: bool = False) -> CreditCost:
    """Cost of one Exa search in USD, per Exa's published price list.

    Base price covers the first 10 results; every result above 10 adds
    $1/1k, and an AI page summary adds $1/1k pages.
    """
    base = 7.0 / 1000.0
    extra_results = max(0, num_results - 10) * (1.0 / 1000.0)
    summaries = (1.0 / 1000.0) * num_results if with_summary else 0.0
    total = base + extra_results + summaries
    parts = [f"base ${base:g} (<=10 results)"]
    if extra_results:
        parts.append(f"${extra_results:g} for {num_results - 10} result(s) above 10")
    if summaries:
        parts.append(f"${summaries:g} for {num_results} AI page summaries")
    return CreditCost(total, "usd", "; ".join(parts))


def serper_credit_cost(num_queries: int = 1) -> CreditCost:
    """Cost of Serper queries (the ledger unit is 'query', price is $1.00/1k)."""
    return CreditCost(
        float(num_queries),
        "query",
        "1 Serper credit per query ($1.00 per 1,000 credits; the free grant is 2,500 queries, one-time)",
    )


def nominal_cost(spec: SearchProviderSpec, **kwargs: Any) -> CreditCost:
    """Predicted cost of one call to ``spec`` under the given parameters."""
    if spec.name in {"tavily", "tavily_keyless"}:
        return tavily_credit_cost(
            search_depth=kwargs.get("search_depth", "basic"),
            include_answer=bool(kwargs.get("include_answer")),
            include_raw_content=bool(kwargs.get("include_raw_content")),
            auto_parameters=bool(kwargs.get("auto_parameters")),
        )
    if spec.name == "exa":
        return exa_credit_cost(int(kwargs.get("num_results", 5)))
    if spec.name == "serper":
        return serper_credit_cost()
    return CreditCost(0.0, spec.budget.unit, "unmetered")


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

#: Exa's machine-readable error tags that mean "your money/allowance is gone",
#: as opposed to "your request was bad". Checked before the status code because
#: Exa returns some of these with a 400.
EXA_BUDGET_TAGS = frozenset({"NO_MORE_CREDITS", "API_KEY_BUDGET_EXCEEDED", "TEAM_BUDGET_EXCEEDED"})
EXA_RATE_TAGS = frozenset({"RATE_LIMIT_EXCEEDED", "SNAPSHOT_RATE_LIMIT_EXCEEDED"})

#: Tavily uses non-standard statuses for account limits.
TAVILY_BUDGET_STATUSES = frozenset({432, 433})


def _body_snippet(response: httpx.Response, limit: int = 200) -> str:
    return (response.text or "").strip().replace("\n", " ")[:limit]


def _error_text(response: httpx.Response) -> str:
    """Best-effort human message from an error body, without echoing a payload.

    Providers put their error under different keys; we read the short string
    fields only, so a stack trace or an echoed request body never reaches agent
    context.
    """
    try:
        payload = json.loads(response.text)
    except (ValueError, TypeError):
        return _body_snippet(response, 160)
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            for key in ("error", "message"):
                value = detail.get(key)
                if isinstance(value, str) and value:
                    return value[:200]
        for key in ("error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value[:200]
    return _body_snippet(response, 160)


def _exa_tag(response: httpx.Response) -> str | None:
    try:
        payload = json.loads(response.text)
    except (ValueError, TypeError):
        return None
    tag = payload.get("tag") if isinstance(payload, dict) else None
    return tag if isinstance(tag, str) and tag else None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After") if response.headers else None
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def raise_for_status(provider: str, response: httpx.Response) -> None:
    """Map a non-2xx response to the typed error the federation understands.

    Status beats message text on purpose: the vendor docs say to branch on the
    status and header rather than matching error strings, which change.
    """
    status = response.status_code
    message = _error_text(response)
    retry_after = _retry_after(response)
    if status in {401, 403}:
        raise ProviderAuthError(provider, message, status_code=status, retry_after_seconds=retry_after)
    if status == 410:
        raise ProviderRetiredError(provider, message, status_code=status)
    if status == 429:
        raise ProviderRateLimitedError(provider, message, status_code=status, retry_after_seconds=retry_after)
    if status == 402 or status in TAVILY_BUDGET_STATUSES:
        raise ProviderBudgetExhaustedError(provider, message, status_code=status, retry_after_seconds=retry_after)
    if 400 <= status < 500:
        raise ProviderRequestRejectedError(provider, message, status_code=status)
    raise ProviderUnavailableError(provider, message, status_code=status, retry_after_seconds=retry_after)


def _call(
    spec: SearchProviderSpec,
    *,
    path: str,
    body: dict[str, Any],
    key: str | None,
    timeout: float,
) -> dict[str, Any]:
    """POST JSON, map every failure to a typed error, return the parsed object."""
    url = f"{spec.base_url}{path}"
    try:
        response = request("POST", url, headers=auth_headers(spec, key), json_body=body, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailableError(spec.name, f"read timeout after {timeout:g}s: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(spec.name, f"transport failure: {type(exc).__name__}: {str(exc)[:120]}") from exc

    tag = _exa_tag(response) if spec.name == "exa" else None
    if tag in EXA_BUDGET_TAGS:
        raise ProviderBudgetExhaustedError(spec.name, f"exhausted: {tag}", status_code=response.status_code)
    if tag in EXA_RATE_TAGS:
        raise ProviderRateLimitedError(spec.name, f"rate limited: {tag}", status_code=response.status_code, retry_after_seconds=_retry_after(response))
    if response.status_code >= 400:
        raise_for_status(spec.name, response)
    try:
        payload = json.loads(response.text)
    except (ValueError, TypeError) as exc:
        raise ProviderContractError(spec.name, f"non-JSON response: {type(exc).__name__}", status_code=response.status_code) from exc
    if not isinstance(payload, dict):
        raise ProviderContractError(spec.name, f"expected a JSON object, got {type(payload).__name__}", status_code=response.status_code)
    return payload


# ---------------------------------------------------------------------------
# Normalized result
# ---------------------------------------------------------------------------


@dataclass
class SearchHit:
    """One normalized result, identical in shape across every provider."""

    title: str
    url: str
    content: str
    provider: str
    rank: int = 0
    score: float | None = None
    published_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "provider": self.provider,
            "rank": self.rank,
            "score": self.score,
            "published_date": self.published_date,
        }


@dataclass
class ProviderSearchResult:
    """One provider's answer plus the honest cost/latency of getting it."""

    provider: str
    hits: list[SearchHit]
    cost: CreditCost
    latency_ms: float
    answer: str | None = None
    #: What the provider itself reported it cost, when it reports anything.
    #: For Tavily this is ``usage.credits`` from the response body.
    reported_cost: float | None = None
    reported_cost_unit: str | None = None
    request_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "result_count": len(self.hits),
            "answer": self.answer,
            "predicted_cost": {"amount": self.cost.amount, "unit": self.cost.unit, "reason": self.cost.reason},
            "reported_cost": (
                None
                if self.reported_cost is None
                else {"amount": self.reported_cost, "unit": self.reported_cost_unit}
            ),
            "latency_ms": round(self.latency_ms, 1),
            "request_id": self.request_id,
            "notes": self.notes,
        }


def _text(value: Any, limit: int = 4000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _tavily_body(
    query: str,
    *,
    max_results: int,
    search_depth: str,
    time_range: str | None,
    topic: str,
    include_answer: bool,
    include_raw_content: bool,
) -> dict[str, Any]:
    """Build a Tavily /search body with the credit-relevant fields always set.

    ``search_depth`` and ``auto_parameters`` are sent explicitly on every call.
    Leaving either to the server default is what makes a 1,000-credit month
    disappear in 500 searches, so there is no code path that omits them.
    """
    if search_depth not in TAVILY_SEARCH_DEPTHS:
        raise ValueError(f"search_depth must be one of {TAVILY_SEARCH_DEPTHS}, got {search_depth!r}")
    body: dict[str, Any] = {
        "query": query,
        "max_results": max(0, min(MAX_RESULTS_CAP, max_results)),
        "search_depth": search_depth,
        "auto_parameters": False,
        "include_usage": True,
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
    }
    if time_range:
        # Tavily accepts the same day/week/month/year words DDGS uses.
        body["time_range"] = time_range
    if topic in {MODE_NEWS, "news", "finance"}:
        body["topic"] = "news" if topic == MODE_NEWS else "finance"
    return body


def _tavily_results(
    spec: SearchProviderSpec,
    payload: dict[str, Any],
    *,
    cost: CreditCost,
    started: float,
) -> ProviderSearchResult:
    if "results" not in payload:
        raise ProviderContractError(spec.name, "response has no 'results' key")
    raw_results = payload.get("results")
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise ProviderContractError(spec.name, f"'results' should be a list, got {type(raw_results).__name__}")
    hits: list[SearchHit] = []
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"), 2000)
        if not url:
            continue
        score = item.get("score")
        hits.append(
            SearchHit(
                title=_text(item.get("title"), 500),
                url=url,
                content=_text(item.get("content")),
                provider=spec.name,
                rank=index,
                score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                published_date=_text(item.get("published_date"), 64) or None,
            )
        )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    reported = usage.get("credits")
    notes: list[str] = []
    if not isinstance(reported, (int, float)) or isinstance(reported, bool):
        reported = None
        notes.append("provider did not report usage.credits")
    if payload.get("auto_parameters") is not None:
        notes.append(f"provider resolved auto_parameters: {payload.get('auto_parameters')}")
    return ProviderSearchResult(
        provider=spec.name,
        hits=hits,
        cost=cost,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        answer=_text(payload.get("answer"), 4000) or None,
        reported_cost=float(reported) if reported is not None else None,
        reported_cost_unit="tavily_credit" if reported is not None else None,
        request_id=_text(payload.get("request_id"), 64) or None,
        notes=notes,
    )


def tavily_search(
    query: str,
    *,
    key: str | None,
    max_results: int = 5,
    search_depth: str = "basic",
    time_range: str | None = None,
    topic: str = MODE_WEB,
    include_answer: bool = False,
    include_raw_content: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProviderSearchResult:
    """One Tavily ``/search`` call. ``key=None`` means documented keyless access."""
    name = "tavily" if key else "tavily_keyless"
    spec = PROVIDERS[name]
    assert_selectable(name)
    cost = tavily_credit_cost(search_depth=search_depth, include_answer=include_answer, include_raw_content=include_raw_content)
    body = _tavily_body(
        query,
        max_results=max_results,
        search_depth=search_depth,
        time_range=time_range,
        topic=topic,
        include_answer=include_answer,
        include_raw_content=include_raw_content,
    )
    started = time.perf_counter()
    payload = _call(spec, path=spec.search_path, body=body, key=key, timeout=timeout)
    return _tavily_results(spec, payload, cost=cost, started=started)


def tavily_extract(
    urls: list[str],
    *,
    key: str | None = None,
    extract_depth: str = "basic",
    timeout: float = SLOW_TIMEOUT,
) -> ProviderSearchResult:
    """One Tavily ``/extract`` call. Keyless and keyed are both documented."""
    name = "tavily" if key else "tavily_keyless"
    spec = PROVIDERS[name]
    assert_selectable(name)
    cost = tavily_extract_credit_cost(len(urls), extract_depth=extract_depth)
    started = time.perf_counter()
    payload = _call(
        spec,
        path="/extract",
        body={"urls": list(urls), "extract_depth": extract_depth, "include_usage": True},
        key=key,
        timeout=timeout,
    )
    notes: list[str] = []
    if "results" not in payload:
        raise ProviderContractError(spec.name, "response has no 'results' key")
    failed = payload.get("failed_results")
    if isinstance(failed, list) and failed:
        reasons = sorted({_text(f.get("error"), 80) for f in failed if isinstance(f, dict) and f.get("error")})
        notes.append(f"{len(failed)} URL(s) failed to extract: {', '.join(reasons[:3])}")
    raw_results = payload.get("results")
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise ProviderContractError(spec.name, f"'results' should be a list, got {type(raw_results).__name__}")
    hits: list[SearchHit] = []
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"), 2000)
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title"), 500),
                url=url,
                content=_text(item.get("raw_content") or item.get("content")),
                provider=spec.name,
                rank=index,
            )
        )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    reported = usage.get("credits")
    return ProviderSearchResult(
        provider=spec.name,
        hits=hits,
        cost=cost,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        reported_cost=float(reported) if isinstance(reported, (int, float)) and not isinstance(reported, bool) else None,
        reported_cost_unit="tavily_credit" if isinstance(reported, (int, float)) and not isinstance(reported, bool) else None,
        request_id=_text(payload.get("request_id"), 64) or None,
        notes=notes,
    )


def exa_search(
    query: str,
    *,
    key: str,
    max_results: int = 5,
    search_type: str = "auto",
    time_range: str | None = None,
    topic: str = MODE_WEB,
    include_highlights: bool = True,
    highlights_max_characters: int = 1200,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProviderSearchResult:
    """One Exa ``/search`` call (neural/semantic retrieval)."""
    spec = PROVIDERS["exa"]
    assert_selectable(spec.name)
    if not key:
        raise ProviderAuthError(spec.name, "EXA_API_KEY is unset", status_code=None)
    num_results = max(1, min(100, max_results))
    body: dict[str, Any] = {
        "query": query,
        "numResults": num_results,
        "type": search_type,
        "contents": {"highlights": {"maxCharacters": highlights_max_characters}} if include_highlights else None,
    }
    if time_range:
        # Exa filters by absolute publish date, not a relative window; map the
        # longest supported window so "recent" still means something.
        days = {"day": 1, "week": 7, "month": 31, "year": 366}.get(time_range)
        if days:
            body["startPublishedDate"] = _iso_days_ago(days)
    if topic == MODE_NEWS:
        # Exa's documented news category, which is a real index rather than a
        # keyword heuristic - the reason it is worth calling for news mode.
        body["category"] = "news"
    if body["contents"] is None:
        del body["contents"]
    cost = exa_credit_cost(num_results)
    started = time.perf_counter()
    payload = _call(spec, path=spec.search_path, body=body, key=key, timeout=timeout)
    if "results" not in payload:
        raise ProviderContractError(spec.name, "response has no 'results' key")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise ProviderContractError(spec.name, f"'results' should be a list, got {type(raw_results).__name__}")
    hits: list[SearchHit] = []
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"), 2000)
        if not url:
            continue
        highlights = item.get("highlights")
        content = "\n".join(_text(h) for h in highlights if isinstance(h, str)) if isinstance(highlights, list) else ""
        if not content:
            content = _text(item.get("summary")) or _text(item.get("text"), 2000)
        score = item.get("score")
        hits.append(
            SearchHit(
                title=_text(item.get("title"), 500),
                url=url,
                content=content,
                provider=spec.name,
                rank=index,
                score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                published_date=_text(item.get("publishedDate"), 64) or None,
            )
        )
    notes: list[str] = []
    cost_dollars = payload.get("costDollars")
    reported: float | None = None
    if isinstance(cost_dollars, dict) and isinstance(cost_dollars.get("total"), (int, float)):
        reported = float(cost_dollars["total"])
        notes.append("provider reported costDollars.total")
    if payload.get("resolvedSearchType"):
        notes.append(f"provider resolved search type: {payload.get('resolvedSearchType')}")
    return ProviderSearchResult(
        provider=spec.name,
        hits=hits,
        cost=cost,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        reported_cost=reported,
        reported_cost_unit="usd" if reported is not None else None,
        request_id=_text(payload.get("requestId"), 64) or None,
        notes=notes,
    )


def exa_find_similar(
    url: str,
    *,
    key: str,
    max_results: int = 5,
    include_highlights: bool = True,
    highlights_max_characters: int = 1200,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProviderSearchResult:
    """Exa ``/findSimilar``: pages like this URL. The one capability nothing else here has."""
    spec = PROVIDERS["exa"]
    assert_selectable(spec.name)
    if not key:
        raise ProviderAuthError(spec.name, "EXA_API_KEY is unset", status_code=None)
    num_results = max(1, min(100, max_results))
    body: dict[str, Any] = {"url": url, "numResults": num_results}
    if include_highlights:
        body["contents"] = {"highlights": {"maxCharacters": highlights_max_characters}}
    cost = exa_credit_cost(num_results)
    started = time.perf_counter()
    payload = _call(spec, path="/findSimilar", body=body, key=key, timeout=timeout)
    if "results" not in payload:
        raise ProviderContractError(spec.name, "response has no 'results' key")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise ProviderContractError(spec.name, f"'results' should be a list, got {type(raw_results).__name__}")
    hits: list[SearchHit] = []
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        hit_url = _text(item.get("url"), 2000)
        if not hit_url:
            continue
        highlights = item.get("highlights")
        content = "\n".join(_text(h) for h in highlights if isinstance(h, str)) if isinstance(highlights, list) else ""
        hits.append(
            SearchHit(
                title=_text(item.get("title"), 500),
                url=hit_url,
                content=content or _text(item.get("text"), 2000),
                provider=spec.name,
                rank=index,
                published_date=_text(item.get("publishedDate"), 64) or None,
            )
        )
    return ProviderSearchResult(
        provider=spec.name,
        hits=hits,
        cost=cost,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        request_id=_text(payload.get("requestId"), 64) or None,
    )


def _iso_days_ago(days: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def serper_search(
    query: str,
    *,
    key: str,
    max_results: int = 5,
    time_range: str | None = None,
    topic: str = MODE_WEB,
    gl: str | None = None,
    hl: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProviderSearchResult:
    """One Serper Google SERP call. ``topic="news"`` hits the /news endpoint."""
    spec = PROVIDERS["serper"]
    assert_selectable(spec.name)
    if not key:
        raise ProviderAuthError(spec.name, "SERPER_API_KEY is unset", status_code=None)
    path = "/news" if topic == MODE_NEWS else "/search"
    body: dict[str, Any] = {"q": query, "num": max(1, min(100, max_results))}
    if time_range:
        # Serper's own vocabulary is tbs=qdr:/qdr:w and so on.
        body["tbs"] = f"qdr:{ {'day': 'd', 'week': 'w', 'month': 'm', 'year': 'y'}.get(time_range, 'd') }"
    if gl:
        body["gl"] = gl
    if hl:
        body["hl"] = hl
    cost = serper_credit_cost()
    started = time.perf_counter()
    payload = _call(spec, path=path, body=body, key=key, timeout=timeout)
    if "organic" not in payload:
        raise ProviderContractError(spec.name, "response has no 'organic' key")
    raw_results = payload.get("organic")
    if not isinstance(raw_results, list):
        raise ProviderContractError(spec.name, f"'organic' should be a list, got {type(raw_results).__name__}")
    hits: list[SearchHit] = []
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        url = _text(item.get("link"), 2000)
        if not url:
            continue
        hits.append(
            SearchHit(
                title=_text(item.get("title"), 500),
                url=url,
                content=_text(item.get("snippet")),
                provider=spec.name,
                rank=index,
                published_date=_text(item.get("date"), 64) or None,
            )
        )
    notes: list[str] = []
    for extra in ("answerBox", "knowledgeGraph"):
        if payload.get(extra):
            notes.append(f"response also contained a {extra} block, not included in results")
    reported = payload.get("credits")
    return ProviderSearchResult(
        provider=spec.name,
        hits=hits,
        cost=cost,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        reported_cost=float(reported) if isinstance(reported, (int, float)) and not isinstance(reported, bool) else None,
        reported_cost_unit="query" if isinstance(reported, (int, float)) and not isinstance(reported, bool) else None,
        notes=notes,
    )


def duckduckgo_search(
    query: str,
    *,
    max_results: int = 5,
    time_range: str | None = None,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    backend: str = "auto",
    timeout: float = DEFAULT_TIMEOUT,
) -> ProviderSearchResult:
    """Keyless last-resort search through the already-bundled ddgs client.

    Delegates to ``alpha.community.ddg_search.search_web`` (the honest wrapper
    that raises instead of returning an empty list on failure), so a DDG outage
    still becomes a typed error here.
    """
    from alpha.community.ddg_search.tools import DuckDuckGoSearchError, search_web

    spec = PROVIDERS["duckduckgo"]
    assert_selectable(spec.name)
    started = time.perf_counter()
    try:
        rows = search_web(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            backend=backend,
            time_range=time_range,
            timeout=timeout,
        )
    except DuckDuckGoSearchError as exc:
        raise ProviderUnavailableError(spec.name, str(exc)) from exc
    hits = [
        SearchHit(
            title=_text(row.get("title"), 500),
            url=_text(row.get("href") or row.get("link"), 2000),
            content=_text(row.get("body") or row.get("snippet")),
            provider=spec.name,
            rank=index,
        )
        for index, row in enumerate(rows, start=1)
        if isinstance(row, dict) and _text(row.get("href") or row.get("link"), 2000)
    ]
    return ProviderSearchResult(
        provider=spec.name,
        hits=hits,
        cost=nominal_cost(spec),
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


#: name -> callable(**kwargs) -> ProviderSearchResult, as used by the federation.
ADAPTERS = {
    "tavily_keyless": tavily_search,
    "tavily": tavily_search,
    "exa": exa_search,
    "serper": serper_search,
    "duckduckgo": duckduckgo_search,
}


def spec_for(name: str) -> SearchProviderSpec:
    """Look up a spec, refusing anything not in the wireable set."""
    spec = PROVIDERS.get(name)
    if spec is None:
        assert_selectable(name)  # raises the typed refusal (retired / unverified)
        raise ProviderContractError(name, "selectable provider with no adapter spec")
    return spec


__all__ = [
    "ADAPTERS",
    "BUDGET_SPECS",
    "DEFAULT_TIMEOUT",
    "EXA_BASE_URL",
    "MODE_NEWS",
    "MODE_SEMANTIC",
    "MODE_SIMILAR",
    "MODE_WEB",
    "PROVIDERS",
    "PROVIDER_ORDER",
    "SERPER_BASE_URL",
    "SLOW_TIMEOUT",
    "TAVILY_BASE_URL",
    "TAVILY_SEARCH_DEPTHS",
    "BudgetSpec",
    "CreditCost",
    "ProviderSearchResult",
    "SearchHit",
    "SearchProviderSpec",
    "SearchProviderError",
    "assert_selectable",
    "auth_headers",
    "credential",
    "duckduckgo_search",
    "exa_credit_cost",
    "exa_find_similar",
    "exa_search",
    "missing_env",
    "nominal_cost",
    "raise_for_status",
    "request",
    "serper_credit_cost",
    "serper_search",
    "spec_for",
    "tavily_credit_cost",
    "tavily_extract",
    "tavily_extract_credit_cost",
    "tavily_search",
]
