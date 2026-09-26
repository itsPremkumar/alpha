"""LangChain tools for the federated web-search layer.

Two tools, one underlying federation:

* :func:`web_search_tool` - query the chain (keyword, semantic, or news).
* :func:`web_find_similar_tool` - "pages like this URL" (Exa's differentiator).

Configuration (all optional, all read from the ``web_search`` tool entry)::

    - name: web_search
      group: web
      use: alpha.community.search_federation.tools:web_search_tool
      max_results: 5
      search_depth: basic        # basic|fast|ultra-fast|advanced  (advanced = 2x credits)
      mode: auto                 # auto|web|semantic|news
      merge: false               # true = query every eligible provider and union
      include_answer: false      # Tavily synthesised answer (no extra credit)
      timeout: 30
      # allow_unverified_providers: false
      # providers: [tavily_keyless, tavily, exa, serper, duckduckgo]

Credentials come **only** from the environment. ``TAVILY_API_KEY``,
``EXA_API_KEY`` and ``SERPER_API_KEY`` are read at call time; an unset variable
degrades to "that provider is skipped" and is reported as a skip reason in the
result payload. There is no code path that raises because a key is missing, and
no literal key anywhere in this package.

The error contract
------------------
On success the tool returns the search payload. On failure it returns a JSON
object with ``error.kind`` and, where relevant, ``error.attempts`` /
``error.skipped`` - never a bare "No results found". ``kind`` is one of:

``search_exhausted``   nothing could serve the query (all failed / no key /
                        out of quota). Retryable in principle; see
                        ``retry_after_seconds``.
``budget_exhausted``   the requested mode needs a provider whose allowance is
                        spent (e.g. ``similar`` with no Exa credit).
``request_rejected``    the request itself was invalid (bad mode, empty query).
``auth``               the configured key was rejected - fix the environment.
``unavailable``        transport failure or 5xx from a single provider; failover
                        already happened, so this only surfaces from ``similar``.

A genuinely empty result set is ``results: []`` with a note saying the provider
answered and had nothing - which is a different thing, and the agent can act on
the two differently.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.tools import tool

from alpha.community.search_federation.errors import SearchExhaustedError, SearchProviderError
from alpha.community.search_federation.federation import (
    DEFAULT_SEARCH_DEPTH,
    MODE_CHAINS,
    SearchFederation,
    SearchRequest,
    get_federation,
)
from alpha.community.search_federation.providers import TAVILY_SEARCH_DEPTHS
from alpha.community.search_federation.truth import not_wired_report
from alpha.community.search_time_range import SearchTimeRange

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5
DEFAULT_TIMEOUT = 30.0
#: Refuse to hand the model a wall of text from many providers by accident.
MAX_RESULTS_HARD_CAP = 20


def _tool_config() -> Any | None:
    try:
        from alpha.config import get_app_config

        return get_app_config().get_tool_config("web_search")
    except Exception as exc:  # noqa: BLE001 - config must never break a search
        logger.debug("web_search tool config unavailable (%s); using defaults", type(exc).__name__)
        return None


def _extra(config: Any | None) -> dict[str, Any]:
    if config is None:
        return {}
    extra = getattr(config, "model_extra", None)
    return extra if isinstance(extra, dict) else {}


def _coerce_int(value: Any, default: int, *, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _coerce_float(value: Any, default: float, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _build_federation() -> SearchFederation:
    """Read tool config and construct a federation for this call.

    A per-call instance (rather than the process singleton) keeps configured
    options - chain, depth, merge - explicit and testable. Health and budget
    state still persist to ``runtime_home()``, so cooldowns survive.
    """
    extra = _extra(_tool_config())
    search_depth = str(extra.get("search_depth") or DEFAULT_SEARCH_DEPTH).strip().lower()
    if search_depth not in TAVILY_SEARCH_DEPTHS:
        logger.warning("web_search search_depth %r is not a Tavily depth; using %r", search_depth, DEFAULT_SEARCH_DEPTH)
        search_depth = DEFAULT_SEARCH_DEPTH
    return SearchFederation(
        search_depth=search_depth,
        allow_unverified_providers=_coerce_bool(extra.get("allow_unverified_providers"), False),
    )


def _error_payload(exc: BaseException) -> dict[str, Any]:
    """JSON view of a failure. Never an exception dump, never a payload body."""
    if isinstance(exc, SearchExhaustedError):
        return {"error": exc.to_dict()}
    if isinstance(exc, SearchProviderError):
        return {"error": exc.to_dict(), "hint": _hint_for(exc)}
    return {"error": {"kind": "unavailable", "message": f"{type(exc).__name__}: {str(exc)[:200]}"}}


def _hint_for(exc: SearchProviderError) -> str | None:
    if exc.kind == "budget_exhausted":
        return "set the provider's API key in the environment, or wait for the monthly allowance to refresh"
    if exc.kind == "auth":
        return "the configured key was rejected; check the provider's API key environment variable"
    if exc.kind == "request_rejected":
        return "the request was malformed; check search_depth, mode and max_results"
    if exc.kind == "unavailable":
        return "every eligible provider failed; retry later or narrow the query"
    return None


@tool("web_search", parse_docstring=True)
def web_search_tool(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    time_range: SearchTimeRange | None = None,
    mode: str = "",
) -> str:
    """Search the web for current information and return ranked, cited results.

    Uses a federated chain of free and free-tier search providers. With no
    configuration at all it uses Tavily's keyless access mode, which needs no
    account and no API key, and falls back to keyless DuckDuckGo if Tavily is
    unreachable. Set TAVILY_API_KEY, EXA_API_KEY or SERPER_API_KEY in the
    environment to add higher-quality or higher-limit providers to the chain;
    each is picked up automatically and skipped cleanly when unset.

    Results are normalized across providers: title, url, content, provider,
    rank, score, published_date. The response also reports which provider
    answered, what the call cost against that provider's free allowance, and
    which providers were skipped and why.

    When the response contains `error`, the search did not run: `error.kind`
    is `search_exhausted` (no provider could serve the query - not "no results
    exist"), `budget_exhausted`, `request_rejected`, `auth`, or `unavailable`.

    Args:
        query: What to search for. Be specific; natural-language questions work well.
        max_results: Maximum number of results to return (1-20). Default is 5, or the configured `max_results`.
        time_range: Optional publication recency window. Use only when the request needs recent results.
        mode: Which kind of search to run. Leave empty to use the configured mode (default `auto`).
            `auto` uses the full chain in priority order. `web` favours raw web/Google ranking.
            `semantic` favours conceptual similarity (Exa first). `news` targets recent events.
            For pages similar to a given URL use the `web_find_similar` tool instead of a mode here.
    """
    extra = _extra(_tool_config())
    federation = _build_federation()
    try:
        # Building the request is inside the try on purpose: a bad config value
        # must be reported through the same JSON error contract as a bad
        # response, not raised past the tool boundary.
        request = SearchRequest(
            query=query,
            max_results=_coerce_int(
                extra.get("max_results", max_results), DEFAULT_MAX_RESULTS, low=1, high=MAX_RESULTS_HARD_CAP
            ),
            time_range=time_range,
            mode=str(mode or extra.get("mode") or "auto").strip().lower(),
            search_depth=str(extra.get("search_depth") or DEFAULT_SEARCH_DEPTH).strip().lower(),
            include_answer=_coerce_bool(extra.get("include_answer"), False),
            merge=_coerce_bool(extra.get("merge"), False),
            timeout=_coerce_float(extra.get("timeout", DEFAULT_TIMEOUT), DEFAULT_TIMEOUT, low=1.0, high=120.0),
        )
        outcome = federation.search(request)
    except (SearchExhaustedError, SearchProviderError) as exc:
        payload = _error_payload(exc)
        payload["query"] = query
        payload["mode"] = str(mode or extra.get("mode") or "auto")
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - the tool must return, not explode
        logger.exception("web_search federation failed unexpectedly")
        payload = _error_payload(exc)
        payload["query"] = query
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return json.dumps(outcome.to_payload(), indent=2, ensure_ascii=False)


@tool("web_find_similar", parse_docstring=True)
def web_find_similar_tool(url: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """Find web pages similar to a given URL.

    This is a genuinely different capability from keyword search: it retrieves
    pages semantically similar to the one you point it at, which is how you find
    alternatives, competitors, or further reading on a topic you already have a
    source for. It requires EXA_API_KEY in the environment; with the key unset
    the tool returns `error.kind = budget_exhausted` rather than silently
    substituting a keyword search that would answer a different question.

    Args:
        url: The full URL of the page to find similar pages for. Must include the scheme, e.g. https://example.com/article
        max_results: Maximum number of similar pages to return (1-20). Default is 5.
    """
    extra = _extra(_tool_config())
    federation = _build_federation()
    try:
        outcome = federation.find_similar(
            url,
            max_results=_coerce_int(
                extra.get("max_results", max_results), DEFAULT_MAX_RESULTS, low=1, high=MAX_RESULTS_HARD_CAP
            ),
            timeout=_coerce_float(extra.get("timeout", DEFAULT_TIMEOUT), DEFAULT_TIMEOUT, low=1.0, high=120.0),
        )
    except (SearchExhaustedError, SearchProviderError) as exc:
        payload = _error_payload(exc)
        payload["url"] = url
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - the tool must return, not explode
        logger.exception("web_find_similar federation failed unexpectedly")
        payload = _error_payload(exc)
        payload["url"] = url
        return json.dumps(payload, indent=2, ensure_ascii=False)
    payload = outcome.to_payload()
    payload["url"] = url
    return json.dumps(payload, indent=2, ensure_ascii=False)


@tool("web_search_providers", parse_docstring=True)
def web_search_providers_tool() -> str:
    """Report the state of every web-search provider: health, quota, and cost.

    Use this when a search failed or returned nothing and you need to know
    whether the problem is the query, the quota, or a provider outage - it
    distinguishes the three, and lists the providers that were deliberately
    left out (retired, contested, or blocked by licence) with the reason.

    Returns:
        A JSON object with `providers` (per-provider health and remaining
        allowance), `budgets` (allowance consumed vs documented), `mode_chains`,
        and `not_wired` (every provider considered and rejected, with evidence).
    """
    federation = get_federation()
    described = dict(federation.describe())
    described["not_wired"] = not_wired_report()
    described["tool_modes"] = sorted(MODE_CHAINS)
    described["credit_costs"] = {
        "tavily_search_basic": "1 credit (fast/ultra-fast also 1)",
        "tavily_search_advanced": "2 credits",
        "tavily_auto_parameters": "2 credits regardless of search_depth",
        "tavily_extract_basic": "1 credit per 5 successful URLs",
        "exa_search": "$0.007 base (<=10 results) + $0.001 per result above 10",
        "serper_search": "1 query ($1.00 per 1,000 after the free grant)",
        "tavily_keyless": "not billed; anonymous rate limit applies",
        "duckduckgo": "not billed; no account",
    }
    return json.dumps(described, indent=2, ensure_ascii=False)
