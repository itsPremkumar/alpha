"""
Web Search Tool - Search the web using DuckDuckGo (no API key required).

Failure honesty
---------------
The one rule this module enforces: **a failed search is never reported as an
empty result set.** ``{"results": []}`` means "DuckDuckGo answered and had
nothing for this query"; anything that went wrong - unreachable engine, rate
limit, missing package - is reported as a typed ``error`` object carrying the
``kind`` vocabulary already used by :mod:`alpha.community.search_federation`
(``unavailable`` / ``rate_limited`` / ``not_configured``), so the model reads
the same failure words whichever search provider is configured. A model that
cannot tell "the web has nothing" from "the search blew up" will confidently
answer from an outage.

Result bound
------------
``max_results`` is model-controlled, so it is clamped to
:data:`MAX_RESULTS_HARD_CAP` before it reaches the engine. A tool call must not
be able to inject an arbitrary wall of third-party text into the context
window; the same bound the federated provider applies.
"""

import json
import logging

from langchain.tools import tool

from alpha.community.search_federation.errors import (
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from alpha.community.search_time_range import DDGS_TIMELIMIT_BY_TIME_RANGE, SearchTimeRange
from alpha.config import get_app_config

logger = logging.getLogger(__name__)

DEFAULT_BACKEND = "auto"
DEFAULT_REGION = "wt-wt"
DEFAULT_SAFESEARCH = "moderate"
DEFAULT_WIKIPEDIA_REGION = "us-en"

#: Provider identity used in every typed error this module reports.
PROVIDER = "duckduckgo"

#: Hard ceiling on model-supplied ``max_results``. Mirrors
#: ``alpha.community.search_federation.tools.MAX_RESULTS_HARD_CAP``; kept as a
#: local constant so the bundled keyless provider does not have to import the
#: whole federation to read one number.
MAX_RESULTS_HARD_CAP = 20
MIN_RESULTS = 1

#: Returned (not raised) when the engine answered and genuinely had nothing.
EMPTY_RESULT_NOTE = (
    "The DuckDuckGo engine answered this query and returned zero results. This is an empty "
    "result set, not a search failure: nothing on the web matched, so rephrase or try a "
    "different provider rather than retrying the same query."
)

#: Provider names the engine reports for the HTTP statuses that have a better
#: ``kind`` than the generic transport failure.
_RATE_LIMIT_MARKERS = ("rate limit", "too many requests", "429", "captcha", "blocked", "unusual traffic")

WIKIPEDIA_BACKENDS = {"auto", "all", "wikipedia"}
# ddgs 9.14.1: enabled text engines whose implementations honor ``timelimit``.
# Google and Bing also implement it but are disabled upstream in this release.
TIME_RANGE_CAPABLE_BACKENDS = ("brave", "duckduckgo", "yahoo")
DEFAULT_TIME_RANGE_BACKEND = ",".join(TIME_RANGE_CAPABLE_BACKENDS)
WIKIPEDIA_LANGUAGE_ALIASES = {
    "jp": "ja",
    "kr": "ko",
    "tzh": "zh",
    "wt": "en",
}


def _normalize_backend(backend: str | list[str] | tuple[str, ...] | None) -> str:
    if backend is None:
        return DEFAULT_BACKEND
    if isinstance(backend, (list, tuple)):
        return ",".join(str(part).strip() for part in backend if str(part).strip()) or DEFAULT_BACKEND
    return str(backend).strip() or DEFAULT_BACKEND


def _normalize_setting(value: str | None, default: str) -> str:
    return str(value).strip() if value else default


def _resolve_time_range_backend(backend: str | list[str] | tuple[str, ...] | None) -> str:
    """Exclude DDGS text backends that ignore the native time limit."""
    normalized_backend = _normalize_backend(backend)
    configured_backends = [part.strip().lower() for part in normalized_backend.split(",") if part.strip()]
    if any(part in {"auto", "all"} for part in configured_backends):
        return DEFAULT_TIME_RANGE_BACKEND

    supported_backends = [part for part in configured_backends if part in TIME_RANGE_CAPABLE_BACKENDS]
    excluded_backends = [part for part in configured_backends if part not in TIME_RANGE_CAPABLE_BACKENDS]
    if excluded_backends:
        logger.warning("Ignoring DDGS backends without time-range support: %s", ", ".join(excluded_backends))
    return ",".join(supported_backends) or DEFAULT_TIME_RANGE_BACKEND


def _backend_includes_wikipedia(backend: str | list[str] | tuple[str, ...] | None) -> bool:
    backend = _normalize_backend(backend)
    return any(part.strip().lower() in WIKIPEDIA_BACKENDS for part in backend.split(","))


def _contains_codepoint(query: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= ord(char) <= end for char in query for start, end in ranges)


def _infer_wikipedia_region(query: str) -> str:
    """Pick a valid Wikipedia language region when DDGS' worldwide region is used."""
    if _contains_codepoint(query, ((0x3040, 0x30FF), (0x31F0, 0x31FF))):
        return "jp-ja"
    if _contains_codepoint(query, ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F))):
        return "kr-ko"
    if _contains_codepoint(query, ((0x3400, 0x9FFF),)):
        return "cn-zh"
    if _contains_codepoint(query, ((0x0400, 0x04FF),)):
        return "ru-ru"
    if _contains_codepoint(query, ((0x0370, 0x03FF),)):
        return "gr-el"
    if _contains_codepoint(query, ((0x0590, 0x05FF),)):
        return "il-he"
    if _contains_codepoint(query, ((0x0600, 0x06FF),)):
        return "xa-ar"
    return DEFAULT_WIKIPEDIA_REGION


def _resolve_ddgs_region(query: str, region: str | None, backend: str | list[str] | tuple[str, ...] | None) -> str:
    """
    DDGS' wikipedia engine treats the second part of region as a Wikipedia
    subdomain. Its default worldwide region, wt-wt, becomes wt.wikipedia.org.
    """
    normalized_region = _normalize_setting(region, DEFAULT_REGION).lower()
    if not _backend_includes_wikipedia(backend):
        return normalized_region

    if normalized_region == DEFAULT_REGION:
        return _infer_wikipedia_region(query)

    if "-" not in normalized_region:
        return DEFAULT_WIKIPEDIA_REGION

    country, language = normalized_region.split("-", 1)
    return f"{country}-{WIKIPEDIA_LANGUAGE_ALIASES.get(language, language)}"


class DuckDuckGoSearchError(ProviderUnavailableError):
    """A DuckDuckGo search could not be performed.

    Subclasses the shared :class:`~alpha.community.search_federation.errors.ProviderUnavailableError`
    so it inherits that package's ``kind`` / ``to_dict()`` contract: every
    search provider in the repo reports failures in the same vocabulary, and
    the model reads the same failure words whichever provider is configured.

    The default ``kind`` is ``unavailable`` (transport failure). Two sibling
    classes below narrow it to the two other cases worth telling apart.
    Raised - never swallowed - by :func:`search_web` and :func:`_search_text`,
    so a caller can distinguish "the engine is unreachable / rate-limited /
    misconfigured" from "the engine answered and had nothing for this query".
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(PROVIDER, message, status_code=status_code)


class DuckDuckGoRateLimitedError(ProviderRateLimitedError, DuckDuckGoSearchError):
    """The engine answered, but refused the query as too frequent."""


class DuckDuckGoNotInstalledError(ProviderNotConfiguredError, DuckDuckGoSearchError):
    """The optional ``ddgs`` package is not installed.

    A *configuration* failure, not an empty answer and not an outage. It gets
    its own ``kind`` so the tool can tell the operator to install a package
    instead of telling the model the web had nothing.
    """


def _clamp_max_results(value: object, default: int = 5) -> int:
    """Clamp a model- or config-supplied ``max_results`` into ``[1, 20]``.

    ``max_results`` is chosen by the model, so without a ceiling a single tool
    call can ask the engine for an unbounded result set and inject a wall of
    third-party text into the context window. Junk falls back to *default*
    rather than to the cap, so a malformed value cannot silently become
    "give me everything".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_RESULTS, min(MAX_RESULTS_HARD_CAP, number))


def _classify_engine_error(exc: Exception) -> DuckDuckGoSearchError:
    """Map a raw engine exception onto the typed-error vocabulary.

    Only the shape of the message is inspected, and only to pick between
    ``rate_limited`` and ``unavailable`` - the two cases where the model should
    retry later rather than assume the web is empty. The message is truncated
    here so an upstream body can never ride into agent context whole.
    """
    detail = f"{type(exc).__name__}: {str(exc)[:200]}"
    lowered = detail.lower()
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return DuckDuckGoRateLimitedError(detail)
    return DuckDuckGoSearchError(detail)


def search_web(
    query: str,
    max_results: int = 5,
    region: str | None = DEFAULT_REGION,
    safesearch: str | None = DEFAULT_SAFESEARCH,
    backend: str | list[str] | tuple[str, ...] | None = DEFAULT_BACKEND,
    time_range: SearchTimeRange | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Execute a keyless DuckDuckGo text search, raising on failure.

    Returns an empty list **only** when the engine genuinely had no results.

    Args:
        query: Search keywords
        max_results: Maximum number of results
        region: Search region
        safesearch: Safe search level
        backend: DDGS backend(s), e.g. "auto", "duckduckgo", or "duckduckgo,brave"
        time_range: Optional relative publication/update window
        timeout: Per-request timeout in seconds (rounded up to whole seconds)

    Raises:
        DuckDuckGoNotInstalledError: The ``ddgs`` package is missing.
        DuckDuckGoSearchError: The engine could not be reached, or refused the
            query. Never raised for a genuinely empty result set.
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise DuckDuckGoNotInstalledError("ddgs library not installed. Run: pip install ddgs") from exc

    seconds = max(1, int(timeout)) if isinstance(timeout, (int, float)) else 30
    try:
        ddgs = DDGS(timeout=seconds)
    except Exception as exc:
        raise _classify_engine_error(exc) from exc

    try:
        resolved_backend = _resolve_time_range_backend(backend) if time_range is not None else _normalize_backend(backend)
        search_kwargs: dict[str, object] = {
            "region": _resolve_ddgs_region(query, region, resolved_backend),
            "safesearch": _normalize_setting(safesearch, DEFAULT_SAFESEARCH),
            "max_results": _clamp_max_results(max_results),
            "backend": resolved_backend,
        }
        if time_range is not None:
            search_kwargs["timelimit"] = DDGS_TIMELIMIT_BY_TIME_RANGE[time_range]
        results = ddgs.text(query, **search_kwargs)
        return list(results) if results else []

    except DuckDuckGoSearchError:
        raise
    except Exception as exc:
        raise _classify_engine_error(exc) from exc


def _search_text(
    query: str,
    max_results: int = 5,
    region: str | None = DEFAULT_REGION,
    safesearch: str | None = DEFAULT_SAFESEARCH,
    backend: str | list[str] | tuple[str, ...] | None = DEFAULT_BACKEND,
    time_range: SearchTimeRange | None = None,
) -> list[dict]:
    """
    Execute text search using DuckDuckGo.

    Historical name kept because the bundled ``web_search`` tool calls it (and
    callers patch it in tests). It used to swallow
    :class:`DuckDuckGoSearchError` and return ``[]``, which is precisely the
    bug this module now forbids: a transport failure reached the model as
    "no results found", i.e. as a claim that the web had nothing. It now
    propagates the typed error like :func:`search_web`.

    Args:
        query: Search keywords
        max_results: Maximum number of results
        region: Search region
        safesearch: Safe search level
        backend: DDGS backend(s), e.g. "auto", "duckduckgo", or "duckduckgo,brave"
        time_range: Optional relative publication/update window

    Returns:
        List of search results; empty **only** when the engine answered with none.

    Raises:
        DuckDuckGoSearchError: See :func:`search_web`.
    """
    return search_web(
        query,
        max_results=max_results,
        region=region,
        safesearch=safesearch,
        backend=backend,
        time_range=time_range,
    )


@tool("web_search", parse_docstring=True)
def web_search_tool(
    query: str,
    max_results: int = 5,
    time_range: SearchTimeRange | None = None,
) -> str:
    """Search the web for information. Use this tool to find current information, news, articles, and facts from the internet.

    An empty `results` list means the search engine answered and had nothing for
    this query. When the search did not run at all the response instead carries
    an `error` object whose `kind` is one of `unavailable` (the engine could not
    be reached - retry later or use another provider), `rate_limited` (retry
    later) or `not_configured` (the optional `ddgs` package is not installed).
    Never report an `error` as "nothing exists on the web".

    Args:
        query: Search keywords describing what you want to find. Be specific for better results.
        max_results: Maximum number of results to return (1-20). Default is 5.
        time_range: Optional relative publication/update window. Use only when the request requires recent results.
    """
    config = get_app_config().get_tool_config("web_search")
    region = DEFAULT_REGION
    safesearch = DEFAULT_SAFESEARCH
    backend = DEFAULT_BACKEND

    if config is not None:
        # Override tool call defaults from config if set.
        max_results = config.model_extra.get("max_results", max_results)
        region = config.model_extra.get("region", region)
        safesearch = config.model_extra.get("safesearch", safesearch)
        backend = config.model_extra.get("backend", backend)

    max_results = _clamp_max_results(max_results)

    try:
        results = _search_text(
            query=query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            backend=backend,
            time_range=time_range,
        )
    except DuckDuckGoSearchError as exc:
        # The search did not run. Report the typed failure; never a result list.
        logger.error("DuckDuckGo search failed: %s", exc)
        payload: dict[str, object] = {"error": exc.to_dict(), "query": query}
        hint = _hint_for(exc)
        if hint:
            payload["hint"] = hint
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - a tool must return, not explode
        logger.exception("DuckDuckGo search failed unexpectedly")
        return json.dumps(
            {
                "error": {
                    "kind": "unavailable",
                    "provider": PROVIDER,
                    "message": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "status_code": None,
                    "retry_after_seconds": None,
                },
                "query": query,
                "hint": "the search did not run; this is not evidence that the web has nothing on the topic",
            },
            indent=2,
            ensure_ascii=False,
        )

    if not results:
        # The engine answered and had nothing. Distinct from every failure above.
        return json.dumps(
            {"query": query, "total_results": 0, "results": [], "note": EMPTY_RESULT_NOTE},
            indent=2,
            ensure_ascii=False,
        )

    normalized_results = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", r.get("link", "")),
            "content": r.get("body", r.get("snippet", "")),
        }
        for r in results
    ]

    output = {
        "query": query,
        "total_results": len(normalized_results),
        "results": normalized_results,
    }

    return json.dumps(output, indent=2, ensure_ascii=False)


def _hint_for(exc: DuckDuckGoSearchError) -> str:
    """One actionable sentence per failure kind, so the model knows its next move."""
    if isinstance(exc, DuckDuckGoNotInstalledError):
        return "install the optional package with `pip install ddgs`, or configure a different web_search provider"
    if isinstance(exc, DuckDuckGoRateLimitedError):
        return "the engine is rate-limiting this client; wait before retrying, or configure a different web_search provider"
    return "the search engine could not be reached; this is not evidence that the web has nothing on the topic"
