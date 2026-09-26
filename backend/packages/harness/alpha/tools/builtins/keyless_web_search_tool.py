"""Built-in keyless_web_search tool ported from AgentEye's keyless search engines.

Ported from ``agent_eye/search_engines.py`` (Agent Search Lite, MIT): an
httpx-first chain of keyless scrapers (DuckDuckGo HTML, DuckDuckGo via Jina
Reader, Google, Bing, Brave, StartPage, Yahoo, Ecosia) with a transparent
optional ``ddgs`` fallback. Zero new hard dependencies: ``httpx`` is already a
core harness dependency; ``ddgs`` stays optional and its absence is reported
as an honest attempt outcome naming the real package.

Honesty contract (never fabricated results):
- ``status: "ok"``         -- at least one endpoint answered and parsed results.
- ``status: "no_results"`` -- the search genuinely ran (an endpoint answered
  HTTP 200, or ``ddgs`` returned successfully) and yielded zero matches.
- ``status: "failed"``     -- no endpoint completed (transport/HTTP errors on
  every engine, or a required package is missing). ``missing_package`` names
  the real package when an import was the blocker.
Every attempt is disclosed in ``attempts`` so callers can audit the chain.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable
from typing import Any, NamedTuple

from langchain.tools import tool

try:
    # httpx is a core dependency; if it is genuinely missing, every call must
    # fail honestly naming the package instead of pretending to have searched.
    import httpx
except ImportError:  # pragma: no cover - normally impossible (core dep)
    httpx = None  # type: ignore[assignment]

GOOGLE_SEARCH = "https://www.google.com/search"
DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"
BING_SEARCH = "https://www.bing.com/search"
BRAVE_SEARCH = "https://search.brave.com/search"
START_PAGE = "https://www.startpage.com/sp/search"
YAHOO_SEARCH = "https://search.yahoo.com/search"
ECOSIA_SEARCH = "https://www.ecosia.org/search"

DEFAULT_TIMEOUT = 10.0
MAX_LIMIT = 20

# Local stand-in for AgentEye's ua_rotator (that module is not ported): a fixed
# keyless UA rotation picked by attempt index so behaviour stays deterministic.
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
)


class _Engine(NamedTuple):
    """One keyless search endpoint: URL template (``{query}`` substituted), per-call params, HTML/markdown parser, redirect flag."""

    name: str
    url: str
    params: Callable[[str, int], dict[str, Any] | None]
    parser: Callable[[str, int], list[dict[str, Any]]]
    follow_redirects: bool


def _strip_tags(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment).strip()


def _google_params(query: str, limit: int) -> dict[str, Any]:
    return {"q": query, "num": limit, "hl": "en"}


def _ddg_params(query: str, limit: int) -> dict[str, Any]:
    return {"q": query, "kl": "us-en"}


def _no_params(query: str, limit: int) -> dict[str, Any] | None:
    return None


def _bing_params(query: str, limit: int) -> dict[str, Any]:
    return {"q": query, "count": limit}


def _brave_params(query: str, limit: int) -> dict[str, Any]:
    return {"q": query, "source": "web"}


def _startpage_params(query: str, limit: int) -> dict[str, Any]:
    return {"query": query, "cat": "web", "page": 1}


def _yahoo_params(query: str, limit: int) -> dict[str, Any]:
    return {"p": query, "b": 1, "pz": limit}


def _ecosia_params(query: str, limit: int) -> dict[str, Any]:
    return {"q": query}


# ---------------------------------------------------------------------------
# Parsers (ports of the AgentEye per-engine HTML/markdown parsers)
# ---------------------------------------------------------------------------


def _parse_ddg_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse DuckDuckGo's html.duckduckgo.com result anchors."""
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    results: list[dict[str, Any]] = []
    for url, title, snippet in pattern.findall(html)[:limit]:
        title_text = _strip_tags(title)
        if url and title_text:
            results.append({"title": title_text, "url": url, "description": _strip_tags(snippet)[:300], "source": "duckduckgo", "position": len(results) + 1})
    return results


def _parse_ddg_jina_results(text: str, limit: int) -> list[dict[str, Any]]:
    """Parse markdown results rendered by the Jina Reader proxy for DuckDuckGo."""
    pattern = re.compile(r"^## \[(.+?)\]\((.+?)\)$", re.MULTILINE)
    results: list[dict[str, Any]] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = pattern.match(line.strip())
        if not match:
            continue
        title, url = match.group(1), match.group(2)
        if "duckduckgo.com" in url and "/html/" in url:
            continue
        snippet = ""
        for follow in range(index + 1, min(index + 4, len(lines))):
            candidate = lines[follow].strip()
            if candidate and not candidate.startswith("[") and not candidate.startswith("!"):
                snippet = candidate
                break
        results.append({"title": title, "url": url, "description": snippet[:300], "source": "duckduckgo", "position": len(results) + 1})
        if len(results) >= limit:
            break
    return results


def _parse_google_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse Google SERP HTML (markup drifts often; two patterns, then fallbacks)."""
    patterns = [
        re.compile(
            r'<div class="[^"]*g[^"]*">.*?<a[^>]*href="(/url\?q=|)(https?://[^"&]+)[^"]*"[^>]*>.*?<h3[^>]*>(.*?)</h3>.*?<span[^>]*>(.*?)</span>',
            re.DOTALL | re.IGNORECASE,
        ),
        re.compile(
            r'<a[^>]*href="(/url\?q=|)(https?://[^"&]+)"[^>]*>.*?<div[^>]*class="[^"]*[^"]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE,
        ),
    ]
    seen_urls: set[str] = set()
    results: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.findall(html):
            if len(match) < 3:
                continue
            url = match[1] if match[1] else urllib.parse.unquote(match[0].replace("/url?q=", ""))
            title = _strip_tags(match[2] if len(match) > 2 else "")
            snippet = _strip_tags(match[3] if len(match) > 3 else "")
            if url and title and url not in seen_urls:
                seen_urls.add(url)
                results.append({"title": title, "url": url, "description": snippet[:300], "source": "google", "position": len(results) + 1})
                if len(results) >= limit:
                    break
        if results:
            break
    return results[:limit]


def _parse_bing_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse Bing's ``li.b_algo`` organic result blocks."""
    pattern = re.compile(r'<li class="b_algo">.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
    results: list[dict[str, Any]] = []
    for url, title, snippet in pattern.findall(html)[:limit]:
        title_text = _strip_tags(title)
        if url and title_text:
            results.append({"title": title_text, "url": url, "description": _strip_tags(snippet)[:300], "source": "bing", "position": len(results) + 1})
    return results


def _parse_brave_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse Brave Search HTML, resolving its ``/l/`` redirect links."""
    pattern = re.compile(
        r'<a[^>]*class="[^"]*[^"]*"[^>]*href="(/l/|https?://[^"]+)"[^>]*>(.*?)</a>.*?<div[^>]*class="[^"]*snippet[^"]*"[^>]*>(.*?)</div>',
        re.DOTALL | re.IGNORECASE,
    )
    results: list[dict[str, Any]] = []
    for url, title, snippet in pattern.findall(html)[:limit]:
        title_text = _strip_tags(title)
        if url and title_text:
            full_url = f"https://search.brave.com{url}" if url.startswith("/l/") else url
            results.append({"title": title_text, "url": full_url, "description": _strip_tags(snippet)[:300], "source": "brave", "position": len(results) + 1})
    return results


def _parse_startpage_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse StartPage result titles and descriptions."""
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result-title[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</p>',
        re.DOTALL | re.IGNORECASE,
    )
    results: list[dict[str, Any]] = []
    for url, title, snippet in pattern.findall(html)[:limit]:
        title_text = _strip_tags(title)
        if url and title_text:
            results.append({"title": title_text, "url": url, "description": _strip_tags(snippet)[:300], "source": "startpage", "position": len(results) + 1})
    return results


def _parse_yahoo_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse Yahoo Search result anchors and paragraph snippets."""
    pattern = re.compile(r'<a[^>]*class="[^"]*[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*class="[^"]*[^"]*"[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
    results: list[dict[str, Any]] = []
    for url, title, snippet in pattern.findall(html)[:limit]:
        title_text = _strip_tags(title)
        if url and title_text:
            results.append({"title": title_text, "url": url, "description": _strip_tags(snippet)[:300], "source": "yahoo", "position": len(results) + 1})
    return results


def _parse_ecosia_results(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse Ecosia's ``result-url``/``result-title`` blocks."""
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result-url[^"]*"[^>]*href="([^"]*)"[^>]*>.*?<h2[^>]*class="[^"]*result-title[^"]*">(.*?)</h2>.*?<p[^>]*class="[^"]*result-snippet[^"]*">(.*?)</p>',
        re.DOTALL | re.IGNORECASE,
    )
    results: list[dict[str, Any]] = []
    for url, title, snippet in pattern.findall(html)[:limit]:
        title_text = _strip_tags(title)
        if url and title_text:
            results.append({"title": title_text, "url": url, "description": _strip_tags(snippet)[:300], "source": "ecosia", "position": len(results) + 1})
    return results


# Keyless-first fallback chain: privacy-friendly DuckDuckGo endpoints lead,
# mainstream engines follow, the optional ddgs library closes the chain.
_ENGINES: tuple[_Engine, ...] = (
    _Engine("duckduckgo_html", DUCKDUCKGO_HTML, _ddg_params, _parse_ddg_results, False),
    _Engine("duckduckgo_jina", "https://r.jina.ai/https://html.duckduckgo.com/html/?q={query}", _no_params, _parse_ddg_jina_results, False),
    _Engine("google", GOOGLE_SEARCH, _google_params, _parse_google_results, True),
    _Engine("bing", BING_SEARCH, _bing_params, _parse_bing_results, False),
    _Engine("brave", BRAVE_SEARCH, _brave_params, _parse_brave_results, False),
    _Engine("startpage", START_PAGE, _startpage_params, _parse_startpage_results, False),
    _Engine("yahoo", YAHOO_SEARCH, _yahoo_params, _parse_yahoo_results, False),
    _Engine("ecosia", ECOSIA_SEARCH, _ecosia_params, _parse_ecosia_results, False),
)


def _fetch_engine(engine: _Engine, query: str, limit: int, timeout: float, language: str, attempt: int) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch and parse one endpoint. Returns ``(hits, error)``; ``error is None`` means the endpoint answered (hits may still be empty)."""
    url = engine.url.replace("{query}", urllib.parse.quote(query))
    headers = {
        "User-Agent": _USER_AGENTS[attempt % len(_USER_AGENTS)],
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": f"{language};q=0.9, en;q=0.8" if language else "en-US,en;q=0.9",
    }
    try:
        resp = httpx.get(url, params=engine.params(query, limit), headers=headers, timeout=timeout, follow_redirects=engine.follow_redirects)
        resp.raise_for_status()
    except Exception as exc:  # any transport/HTTP failure is one honest attempt failure
        return [], f"{type(exc).__name__}: {exc}"
    return engine.parser(resp.text, limit), None


def _ddgs_search(query: str, limit: int) -> tuple[str, list[dict[str, Any]], str]:
    """Last-resort fallback through the optional ``ddgs`` library (AgentEye's ``_ddgs_fallback``, ported).

    Returns ``(outcome, hits, detail)`` where outcome is one of ``ok`` (hits),
    ``empty`` (successful call, zero hits), ``error`` (library present but the
    call failed) or ``unavailable`` (package not installed -- detail names it).
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:
        return "unavailable", [], f"optional fallback package 'ddgs' is not installed (pip install ddgs): {exc}"
    try:
        with DDGS(timeout=10) as client:
            hits: list[dict[str, Any]] = []
            for index, hit in enumerate(client.text(query, max_results=limit)):
                if index >= limit:
                    break
                url = str(hit.get("href") or hit.get("url") or "")
                title = str(hit.get("title", ""))
                if not url or not title:
                    continue
                hits.append({"title": title, "url": url, "description": str(hit.get("body", ""))[:300], "source": "ddgs", "position": index + 1, "fallback_via": "ddgs"})
            return ("ok", hits, "") if hits else ("empty", [], "")
    except Exception as exc:  # library present, request failed
        return "error", [], f"{type(exc).__name__}: {exc}"


def _search_keyless(query: str, limit: int = 10, timeout: float = DEFAULT_TIMEOUT, language: str = "") -> dict[str, Any]:
    """Run the keyless fallback chain and return an honestly labelled payload (see module docstring for the status vocabulary)."""
    query = (query or "").strip()
    if not query:
        return {"status": "failed", "query": query, "engine": None, "results": [], "attempts": [], "error": "empty query: there is nothing to search for"}
    limit = max(1, min(int(limit), MAX_LIMIT))
    timeout = max(1.0, min(float(timeout), 30.0))
    if httpx is None:
        return {
            "status": "failed",
            "query": query,
            "engine": None,
            "results": [],
            "attempts": [],
            "error": "httpx is not importable; no request was attempted and no results are fabricated",
            "missing_package": "httpx",
        }
    attempts: list[dict[str, Any]] = []
    ran_clean = 0
    for index, engine in enumerate(_ENGINES):
        hits, error = _fetch_engine(engine, query, limit, timeout, language, index)
        if error is not None:
            attempts.append({"engine": engine.name, "outcome": "error", "error": error})
            continue
        if hits:
            attempts.append({"engine": engine.name, "outcome": "ok", "count": len(hits)})
            return {"status": "ok", "query": query, "engine": engine.name, "results": hits[:limit], "attempts": attempts}
        ran_clean += 1
        attempts.append({"engine": engine.name, "outcome": "empty", "note": "endpoint answered HTTP 200 but parsed zero results (markup drift or genuinely no match)"})
    ddgs_outcome, ddgs_hits, ddgs_detail = _ddgs_search(query, limit)
    if ddgs_outcome == "ok":
        attempts.append({"engine": "ddgs", "outcome": "ok", "count": len(ddgs_hits)})
        return {"status": "ok", "query": query, "engine": "ddgs", "results": ddgs_hits[:limit], "attempts": attempts, "notes": ["results served by the optional ddgs fallback library"]}
    if ddgs_outcome == "empty":
        ran_clean += 1
        attempts.append({"engine": "ddgs", "outcome": "empty", "note": "ddgs returned successfully with zero hits"})
    elif ddgs_outcome == "unavailable":
        attempts.append({"engine": "ddgs", "outcome": "unavailable", "detail": ddgs_detail})
    else:
        attempts.append({"engine": "ddgs", "outcome": "error", "error": ddgs_detail})
    if ran_clean:
        payload: dict[str, Any] = {"status": "no_results", "query": query, "engine": None, "results": [], "attempts": attempts}
        payload["notes"] = ["search ran: at least one endpoint answered, but zero results were found"] if ddgs_outcome != "unavailable" else ["search ran with zero results; the optional ddgs fallback was unavailable: " + ddgs_detail]
        return payload
    detail = "; ".join(f"{entry['engine']}: {entry.get('error', entry.get('detail', entry.get('outcome')))}" for entry in attempts)
    payload = {"status": "failed", "query": query, "engine": None, "results": [], "attempts": attempts, "error": f"all {len(attempts)} keyless attempts failed before any endpoint answered: {detail}"}
    if ddgs_outcome == "unavailable":
        payload["missing_package"] = "ddgs"
        payload["error"] += f"; last-resort fallback unavailable: {ddgs_detail}"
    return payload


@tool("keyless_web_search", parse_docstring=True)
def keyless_web_search(query: str, limit: int = 10, timeout: float = 10.0, language: str = "") -> str:
    """Search the web without any API key by falling back across keyless engines until one answers.

    Ports AgentEye's keyless chain (DuckDuckGo HTML, DuckDuckGo via Jina Reader,
    Google, Bing, Brave, StartPage, Yahoo, Ecosia, then the optional ddgs
    library). Returns JSON whose ``status`` is honestly one of:
    "ok" (parsed results in ``results``, serving ``engine``, full ``attempts``
    trail), "no_results" (the search ran and an endpoint answered but there
    were zero matches) or "failed" (nothing answered, or a package is missing
    -- ``error`` explains and ``missing_package`` names it). Failures never
    fabricate results.

    Args:
        query: The search query.
        limit: Maximum results to return (1-20).
        timeout: Per-endpoint HTTP timeout in seconds (1-30).
        language: Optional search language code (e.g. "ta", "hi"); empty uses each service's default.
    """
    return json.dumps(_search_keyless(query, limit=limit, timeout=timeout, language=language), indent=2)
