"""Bounded, multi-source search adapter for the pinned AgentEye project.

The upstream project exposes many public and scraped providers, but its
``AgentSearchLite`` orchestrator eagerly writes global config, duplicates dead
backends, and can amplify one query into thousands of requests. Alpha imports
only a curated set of AgentEye's fixed-endpoint backend functions and pure
ranker/extractor modules, then retains ownership of operator allowlists, hard
caps, isolated backend failures, URL normalization, and deduplication.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree as SafeET

from alpha.community.agent_eye.safe_fetch import fetch_public_text
from alpha.config import get_app_config

type AgentEyeCategory = Literal[
    "auto",
    "web",
    "academic",
    "code",
    "packages",
    "news",
    "social",
    "knowledge",
    "government",
    "science",
    "media",
    "finance",
    "language",
]

_MAX_QUERY_CHARS = 500
_MAX_RESULTS = 20
_MAX_SOURCES = 12
_MAX_SNIPPET_CHARS = 1_200
_MAX_ERROR_CHARS = 240
_SEARCH_WORKERS = 8
_SEARCH_QUEUE_DEPTH = 32
_SEARCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=_SEARCH_WORKERS,
    thread_name_prefix="agent-eye-search",
)
_SEARCH_SLOTS = BoundedSemaphore(_SEARCH_WORKERS + _SEARCH_QUEUE_DEPTH)
_BACKEND_NAMES = frozenset(
    {
        "anilist",
        "arxiv",
        "bitbucket",
        "bluesky",
        "boardgameatlas",
        "cocoapods",
        "conceptnet",
        "crates_io",
        "crossref",
        "datagov",
        "datamuse",
        "dbpedia",
        "ddgs",
        "dockerhub",
        "github",
        "gitlab",
        "go_pkg",
        "hackernews",
        "lemmy",
        "lobsters",
        "maven",
        "mastodon",
        "npm",
        "nuget",
        "openalex",
        "openlibrary",
        "osm",
        "packagist",
        "pubdev",
        "pubmed",
        "pypi",
        "rubygems",
        "semantic_scholar",
        "stackoverflow",
        "weather",
        "wikidata",
        "wikipedia",
        "wordnet",
        "yahoo_finance",
    }
)
_BACKEND_LOCKS = {backend: Lock() for backend in _BACKEND_NAMES}
_BACKEND_LAST_STARTED: dict[str, float] = {}
_BACKEND_MIN_INTERVAL_SECONDS = {
    "arxiv": 1.0,
    "crossref": 0.5,
    "github": 2.0,
    "openalex": 0.5,
    "osm": 1.0,
    "pubmed": 0.4,
    "semantic_scholar": 0.5,
    "wikipedia": 0.2,
}


def _wait_for_backend_turn(backend: str) -> None:
    """Space calls to rate-sensitive public APIs without blocking the event loop."""

    lock = _BACKEND_LOCKS.get(backend)
    if lock is None:
        return
    with lock:
        interval = _BACKEND_MIN_INTERVAL_SECONDS.get(backend, 0.0)
        if interval > 0:
            elapsed = time.monotonic() - _BACKEND_LAST_STARTED.get(backend, 0.0)
            if elapsed < interval:
                time.sleep(interval - elapsed)
        _BACKEND_LAST_STARTED[backend] = time.monotonic()


# Scraped search engines, unofficial package/media endpoints, and public
# volunteer social instances are useful but prone to markup, API, consent,
# login, and anti-bot changes. They remain explicit opt-ins.
FRAGILE_BACKENDS = frozenset(
    {
        "bing",
        "bitbucket",
        "bluesky",
        "boardgameatlas",
        "brave",
        "duckduckgo",
        "go_pkg",
        "google",
        "instagram",
        "lastfm",
        "lemmy",
        "linkedin",
        "mal",
        "mastodon",
        "mojeek",
        "pexels",
        "pixabay",
        "pypi",
        "qwant",
        "reddit",
        "startpage",
        "tiktok",
        "tmdb",
        "twitter",
        "unsplash",
        "x",
        "youtube",
        "yahoo_finance",
    }
)

CATEGORY_BACKENDS: dict[str, tuple[str, ...]] = {
    "web": ("ddgs", "wikipedia", "wikidata", "dbpedia"),
    "academic": (
        "arxiv",
        "pubmed",
        "semantic_scholar",
        "crossref",
        "openalex",
        "wikipedia",
    ),
    "code": (
        "github",
        "gitlab",
        "bitbucket",
        "stackoverflow",
        "hackernews",
        "ddgs",
    ),
    "packages": (
        "npm",
        "pypi",
        "dockerhub",
        "crates_io",
        "packagist",
        "go_pkg",
        "rubygems",
        "nuget",
        "maven",
        "cocoapods",
        "pubdev",
    ),
    "news": ("hackernews", "lobsters", "ddgs"),
    "social": ("bluesky", "mastodon", "lemmy", "lobsters"),
    "knowledge": ("osm", "wikidata", "dbpedia", "wikipedia"),
    "government": ("datagov",),
    "science": (
        "arxiv",
        "pubmed",
        "semantic_scholar",
        "crossref",
        "openalex",
        "weather",
    ),
    "media": ("openlibrary", "anilist", "boardgameatlas"),
    "finance": ("yahoo_finance",),
    "language": ("datamuse", "wordnet", "conceptnet"),
}

DEFAULT_ALLOWED_BACKENDS = tuple(
    dict.fromkeys(
        backend
        for category in (
            "web",
            "academic",
            "code",
            "packages",
            "news",
            "social",
            "knowledge",
            "government",
            "science",
            "media",
            "finance",
            "language",
        )
        for backend in CATEGORY_BACKENDS[category]
        if backend not in FRAGILE_BACKENDS
    )
)

_AUTO_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "academic",
        (
            "paper",
            "papers",
            "research",
            "study",
            "studies",
            "citation",
            "pubmed",
            "arxiv",
            "clinical",
            "medical",
            "scientific",
            "benchmark",
        ),
    ),
    (
        "code",
        (
            "code",
            "coding",
            "library",
            "framework",
            "package",
            "github",
            "gitlab",
            "python",
            "javascript",
            "typescript",
            "rust",
            "golang",
            "docker",
            "sdk",
            "api",
            "npm",
            "pypi",
        ),
    ),
    (
        "news",
        ("news", "latest", "today", "announcement", "current events", "press release"),
    ),
    (
        "social",
        (
            "reddit",
            "twitter",
            "mastodon",
            "bluesky",
            "linkedin",
            "community",
            "discussion",
            "opinion",
            "review",
        ),
    ),
    (
        "science",
        ("nasa", "earthquake", "volcano", "astronomy", "spacecraft", "seismic"),
    ),
    (
        "government",
        ("government", "policy", "regulation", "public data", "world bank", "law"),
    ),
    (
        "knowledge",
        ("location", "geography", "country", "city", "museum", "landmark", "map"),
    ),
    (
        "packages",
        ("dependency", "dependencies", "crate", "maven", "nuget", "rubygem", "pub.dev"),
    ),
    (
        "media",
        ("book", "books", "movie", "music", "anime", "manga", "board game"),
    ),
    ("finance", ("stock", "finance", "market", "earnings", "ticker", "shares")),
)


class AgentEyeUnavailableError(RuntimeError):
    """Raised when the pinned AgentEye package cannot be imported."""


@dataclass(frozen=True, slots=True)
class AgentEyeSettings:
    """Validated per-tool limits and provider policy."""

    allowed_backends: tuple[str, ...] = DEFAULT_ALLOWED_BACKENDS
    max_results: int = 8
    max_sources_per_query: int = 6
    timeout_seconds: float = 30.0
    allow_fragile_backends: bool = False
    use_for_deep_research: bool = True

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(str(backend).strip().lower() for backend in self.allowed_backends if str(backend).strip()))
        object.__setattr__(self, "allowed_backends", normalized or DEFAULT_ALLOWED_BACKENDS)
        object.__setattr__(self, "max_results", max(1, min(int(self.max_results), _MAX_RESULTS)))
        object.__setattr__(
            self,
            "max_sources_per_query",
            max(1, min(int(self.max_sources_per_query), _MAX_SOURCES)),
        )
        object.__setattr__(
            self,
            "timeout_seconds",
            max(2.0, min(float(self.timeout_seconds), 60.0)),
        )
        object.__setattr__(self, "allow_fragile_backends", bool(self.allow_fragile_backends))
        object.__setattr__(self, "use_for_deep_research", bool(self.use_for_deep_research))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_backend_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def settings_from_extra(extra: dict[str, Any] | None) -> AgentEyeSettings:
    """Build bounded settings from ``ToolConfig.model_extra``."""

    values = extra or {}
    return AgentEyeSettings(
        allowed_backends=_as_backend_tuple(values.get("allowed_backends")) or DEFAULT_ALLOWED_BACKENDS,
        max_results=_as_int(values.get("max_results"), 8),
        max_sources_per_query=_as_int(values.get("max_sources_per_query"), 6),
        timeout_seconds=_as_float(values.get("timeout_seconds"), 30.0),
        allow_fragile_backends=_as_bool(values.get("allow_fragile_backends"), False),
        use_for_deep_research=_as_bool(values.get("use_for_deep_research"), True),
    )


def get_settings() -> AgentEyeSettings:
    """Read the hot-reloadable ``agent_eye_search`` tool configuration."""

    config = get_app_config().get_tool_config("agent_eye_search")
    extra = dict(config.model_extra or {}) if config is not None else {}
    return settings_from_extra(extra)


def _contains_query_term(lowered_query: str, term: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered_query))


def resolve_category(query: str, requested: str = "auto") -> str:
    """Resolve AgentEye's broad ``auto`` mode to a bounded source family."""

    normalized = (requested or "auto").strip().lower()
    if normalized in CATEGORY_BACKENDS:
        return normalized
    if normalized != "auto":
        raise ValueError(f"Unknown AgentEye category: {requested}")
    lowered = query.lower()
    for category, terms in _AUTO_CATEGORY_RULES:
        if any(_contains_query_term(lowered, term) for term in terms):
            return category
    return "web"


def _normalize_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    if not normalized:
        raise ValueError("Search query must not be empty")
    if len(normalized) > _MAX_QUERY_CHARS:
        raise ValueError(f"Search query exceeds {_MAX_QUERY_CHARS} characters")
    return normalized


def _canonicalize_url(url: str) -> str | None:
    candidate = str(url or "").strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(query, doseq=True),
            "",
        )
    )


def _first_string(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _walk_result_candidates(value: Any, *, depth: int = 0) -> Iterable[dict[str, Any]]:
    if depth > 4:
        return
    if isinstance(value, dict):
        url_keys = {"url", "href", "link", "source_url", "repository", "html_url", "id"}
        if any(key in value for key in url_keys):
            yield value
            return
        preferred = ("results", "data", "items", "hits", "docs", "documents", "value", "message")
        visited: set[int] = set()
        for key in preferred:
            nested = value.get(key)
            if nested is not None and id(nested) not in visited:
                visited.add(id(nested))
                yield from _walk_result_candidates(nested, depth=depth + 1)
        for nested in value.values():
            if nested is not None and id(nested) not in visited:
                visited.add(id(nested))
                yield from _walk_result_candidates(nested, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_result_candidates(nested, depth=depth + 1)


def _fallback_ranker(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = {term for term in re.findall(r"\w+", query.lower()) if len(term) > 1}
    ranked: list[dict[str, Any]] = []
    for item in results:
        haystack = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        overlap = sum(1 for term in terms if term in haystack)
        score = min(1.0, 0.2 + overlap / max(1, len(terms)) * 0.8)
        ranked.append({**item, "relevance_score": round(score, 3)})
    ranked.sort(key=lambda item: (-float(item.get("relevance_score", 0.0)), item.get("title", "")))
    for position, item in enumerate(ranked, 1):
        item["position"] = position
    return ranked


def _read_limited_response(response: httpx.Response, max_bytes: int = 2_000_000) -> bytes:
    declared = response.headers.get("content-length")
    if declared:
        try:
            declared_bytes = int(declared)
        except ValueError:
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > max_bytes:
            raise ValueError("AgentEye backend response exceeds the safety limit")
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise ValueError("AgentEye backend response exceeds the safety limit")
        body.extend(chunk)
    return bytes(body)


def _limited_get(client: httpx.Client, url: str, *, params: dict[str, Any] | None = None) -> bytes:
    with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        return _read_limited_response(response)


def _safe_arxiv_search(query: str, limit: int = 5) -> dict[str, Any]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max(1, min(limit, 10)),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    with httpx.Client(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        headers={"User-Agent": "Alpha-AgentEye/1.0"},
    ) as client:
        with client.stream("GET", "https://export.arxiv.org/api/query", params=params) as response:
            response.raise_for_status()
            body = _read_limited_response(response)
    root = SafeET.fromstring(body)
    namespace = "{http://www.w3.org/2005/Atom}"
    results: list[dict[str, Any]] = []
    for entry in root.findall(f"{namespace}entry"):
        title = (entry.findtext(f"{namespace}title") or "").strip()
        summary = (entry.findtext(f"{namespace}summary") or "").strip()
        url = (entry.findtext(f"{namespace}id") or "").strip()
        published = (entry.findtext(f"{namespace}published") or "").strip()
        if url:
            results.append(
                {
                    "title": re.sub(r"\s+", " ", title),
                    "url": url,
                    "snippet": re.sub(r"\s+", " ", summary),
                    "published": published,
                }
            )
    return {"success": True, "results": results}


def _safe_pubmed_search(query: str, limit: int = 5) -> dict[str, Any]:
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": max(1, min(limit, 10)),
        "sort": "relevance",
    }
    with httpx.Client(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        headers={"User-Agent": "Alpha-AgentEye/1.0"},
    ) as client:
        search_payload = json.loads(_limited_get(client, f"{base_url}esearch.fcgi", params=params))
        ids = search_payload.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return {"success": True, "results": []}
        summary_payload = json.loads(
            _limited_get(
                client,
                f"{base_url}esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            )
        )
    records = summary_payload.get("result", {})
    results: list[dict[str, Any]] = []
    for article_id in ids:
        record = records.get(article_id, {})
        if not isinstance(record, dict):
            continue
        article_ids = record.get("articleids", [])
        url = next(
            (str(item.get("value")) for item in article_ids if isinstance(item, dict) and item.get("idtype") == "pubmed"),
            f"https://pubmed.ncbi.nlm.nih.gov/{article_id}/",
        )
        results.append(
            {
                "title": record.get("title") or article_id,
                "url": url,
                "snippet": record.get("sorttitle") or record.get("fulljournalname") or "",
                "published": record.get("pubdate") or "",
            }
        )
    return {"success": True, "results": results}


def _load_backend_registry() -> dict[str, Callable[[str, int], Any]]:
    """Load curated AgentEye modules without constructing or invoking its core orchestrator.

    Upstream's ``AgentSearchLite`` eagerly writes a process-global config file,
    fans out across dead/duplicate backends, and carries an unbounded research
    path. Alpha therefore imports only fixed-endpoint backend functions and
    keeps admission, concurrency, timeout, and cache policy in this adapter.
    """

    try:
        from ddgs import DDGS

        from agent_eye.academic import wikipedia_search
        from agent_eye.academic_backends import (
            crossref_search,
            openalex_search,
            semantic_scholar_search,
        )
        from agent_eye.commerce_gov import datagov_search, weather_search
        from agent_eye.dev_backends import (
            bitbucket_search,
            dockerhub_search,
            gitlab_search,
            npm_search,
            pypi_search,
        )
        from agent_eye.extra_apis import (
            cocoapods_search,
            conceptnet_lookup,
            datamuse_words,
            maven_search,
            nuget_search,
            pubdev_search,
            rubygems_search,
            wordnet_lookup,
        )
        from agent_eye.knowledge_backends import dbpedia_search, osm_search, wikidata_search
        from agent_eye.media_backends import anilist_search, boardgameatlas_search, openlibrary_search
        from agent_eye.more_backends import (
            crates_io_search,
            go_pkg_search,
            lobsters_search,
            packagist_search,
            yahoo_finance_search,
        )
        from agent_eye.social import lemmy_search, stackoverflow_search
        from agent_eye.social_backends import mastodon_search
    except ImportError as exc:  # pragma: no cover - locked dependencies provide these
        raise AgentEyeUnavailableError("AgentEye or one of its curated runtime dependencies is unavailable; reinstall backend dependencies from backend/uv.lock.") from exc

    def ddgs_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
        return list(DDGS(timeout=10).text(query, max_results=max(1, min(limit, 10))) or [])

    def github_search(query: str, limit: int = 5) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Alpha-AgentEye/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(
            timeout=httpx.Timeout(12.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            payload = json.loads(
                _limited_get(
                    client,
                    "https://api.github.com/search/repositories",
                    params={"q": query, "per_page": max(1, min(limit, 10))},
                )
            )
        return {
            "success": True,
            "results": [
                {
                    "title": item.get("full_name") or item.get("name") or "",
                    "url": item.get("html_url") or "",
                    "snippet": item.get("description") or "",
                    "published": item.get("updated_at") or "",
                }
                for item in payload.get("items", [])
                if isinstance(item, dict)
            ],
        }

    def hackernews_search(query: str, limit: int = 5) -> dict[str, Any]:
        with httpx.Client(
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={"User-Agent": "Alpha-AgentEye/1.0"},
        ) as client:
            payload = json.loads(
                _limited_get(
                    client,
                    "https://hn.algolia.com/api/v1/search",
                    params={
                        "query": query,
                        "tags": "(story,comment)",
                        "hitsPerPage": max(1, min(limit, 10)),
                    },
                )
            )
        results: list[dict[str, Any]] = []
        for hit in payload.get("hits", []):
            if not isinstance(hit, dict):
                continue
            url = hit.get("url") or hit.get("story_url")
            if not url and hit.get("objectID"):
                url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            if url:
                results.append(
                    {
                        "title": hit.get("title") or hit.get("story_title") or "Hacker News result",
                        "url": url,
                        "snippet": hit.get("story_text") or hit.get("comment_text") or "",
                        "published": hit.get("created_at") or "",
                    }
                )
        return {"success": True, "results": results}

    def bluesky_search(query: str, limit: int = 5) -> dict[str, Any]:
        with httpx.Client(
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={"User-Agent": "Alpha-AgentEye/1.0"},
        ) as client:
            payload = json.loads(
                _limited_get(
                    client,
                    "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                    params={"q": query, "limit": max(1, min(limit, 25))},
                )
            )
        results: list[dict[str, Any]] = []
        for post in payload.get("posts", []):
            if not isinstance(post, dict):
                continue
            record = post.get("record") if isinstance(post.get("record"), dict) else {}
            author = post.get("author") if isinstance(post.get("author"), dict) else {}
            uri = str(post.get("uri") or "")
            rkey = uri.rsplit("/", 1)[-1] if uri else ""
            handle = author.get("handle") or "unknown.bsky.social"
            results.append(
                {
                    "title": f"@{handle}: {str(record.get('text') or 'Bluesky post')[:120]}",
                    "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else "",
                    "snippet": str(record.get("text") or ""),
                    "published": post.get("indexedAt") or "",
                }
            )
        return {"success": True, "results": results}

    def bounded_osm(query: str, limit: int = 5) -> Any:
        # Nominatim's public usage policy allows one result per request.
        return osm_search(query, 1)

    def bounded_weather(query: str, limit: int = 5) -> Any:
        return weather_search(query, 1)

    def datamuse(query: str, limit: int = 5) -> Any:
        return datamuse_words(query, max_results=max(1, min(limit, 10)))

    def wordnet(query: str, limit: int = 5) -> Any:
        return wordnet_lookup(query)

    def conceptnet(query: str, limit: int = 5) -> Any:
        return conceptnet_lookup(query, "en", max(1, min(limit, 10)))

    def lemmy(query: str, limit: int = 5) -> Any:
        instance = os.getenv("AGENT_EYE_LEMMY_INSTANCE", "").strip() or "lemmy.world"
        return lemmy_search(query, max(1, min(limit, 10)), instance=instance)

    def mastodon(query: str, limit: int = 5) -> Any:
        instance = os.getenv("AGENT_EYE_MASTODON_INSTANCE", "").strip()
        if not instance:
            return None
        return mastodon_search(query, max(1, min(limit, 10)), instance=instance)

    registry: dict[str, Callable[[str, int], Any]] = {
        "ddgs": ddgs_search,
        "github": github_search,
        "hackernews": hackernews_search,
        "bluesky": bluesky_search,
        "wikipedia": wikipedia_search,
        "arxiv": _safe_arxiv_search,
        "pubmed": _safe_pubmed_search,
        "semantic_scholar": semantic_scholar_search,
        "crossref": crossref_search,
        "openalex": openalex_search,
        "wikidata": wikidata_search,
        "dbpedia": dbpedia_search,
        "osm": bounded_osm,
        "stackoverflow": stackoverflow_search,
        "gitlab": gitlab_search,
        "bitbucket": bitbucket_search,
        "npm": npm_search,
        "pypi": pypi_search,
        "dockerhub": dockerhub_search,
        "crates_io": crates_io_search,
        "packagist": packagist_search,
        "go_pkg": go_pkg_search,
        "rubygems": rubygems_search,
        "nuget": nuget_search,
        "maven": maven_search,
        "cocoapods": cocoapods_search,
        "pubdev": pubdev_search,
        "lobsters": lobsters_search,
        "lemmy": lemmy,
        "mastodon": mastodon,
        "datagov": datagov_search,
        "weather": bounded_weather,
        "openlibrary": openlibrary_search,
        "anilist": anilist_search,
        "boardgameatlas": boardgameatlas_search,
        "yahoo_finance": yahoo_finance_search,
        "datamuse": datamuse,
        "wordnet": wordnet,
        "conceptnet": conceptnet,
    }
    return registry


def _load_ranker() -> Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]:
    try:
        from agent_eye.ranking import rank_results
    except ImportError:  # pragma: no cover - dependency is pinned in the app
        return _fallback_ranker
    return rank_results


class AgentEyeSearchClient:
    """Synchronous bounded fan-out, safe to call through ``asyncio.to_thread``."""

    def __init__(
        self,
        settings: AgentEyeSettings,
        *,
        backends: dict[str, Callable[[str, int], Any]] | None = None,
        ranker: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.settings = settings
        self._backends = backends
        self._rate_limit_enabled = backends is None
        self._ranker = ranker

    @property
    def backends(self) -> dict[str, Callable[[str, int], Any]]:
        if self._backends is None:
            self._backends = _load_backend_registry()
        return self._backends

    @property
    def ranker(self) -> Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]:
        if self._ranker is None:
            self._ranker = _load_ranker()
        return self._ranker

    def _select_backends(
        self,
        query: str,
        category: str,
        requested: list[str] | tuple[str, ...] | None,
    ) -> list[str]:
        allowed = set(self.settings.allowed_backends)
        if requested:
            if len(requested) > _MAX_SOURCES:
                raise ValueError(f"At most {_MAX_SOURCES} explicit AgentEye backends may be requested")
            selected = [str(item).strip().lower() for item in requested if str(item).strip()]
            if any(len(item) > 64 for item in selected):
                raise ValueError("AgentEye backend names must be 64 characters or fewer")
            disallowed = [item for item in selected if item not in allowed]
            if disallowed:
                raise ValueError("AgentEye backend(s) not allowed by operator policy: " + ", ".join(sorted(set(disallowed))))
            fragile = [item for item in selected if item in FRAGILE_BACKENDS and not self.settings.allow_fragile_backends]
            if fragile:
                raise ValueError("Fragile AgentEye backend(s) require allow_fragile_backends=true: " + ", ".join(sorted(set(fragile))))
        else:
            resolved = resolve_category(query, category)
            selected = [item for item in CATEGORY_BACKENDS[resolved] if item in allowed]
        return list(dict.fromkeys(selected))[: self.settings.max_sources_per_query]

    def _run_backend(self, backend: str, function: Callable[[str, int], Any], query: str, limit: int) -> Any:
        def invoke() -> Any:
            try:
                if self._rate_limit_enabled:
                    _wait_for_backend_turn(backend)
                return function(query, limit)
            finally:
                _SEARCH_SLOTS.release()

        if not _SEARCH_SLOTS.acquire(blocking=False):
            raise RuntimeError("AgentEye search worker queue is full")
        try:
            return _SEARCH_EXECUTOR.submit(invoke)
        except Exception:
            _SEARCH_SLOTS.release()
            raise

    def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        category: str = "auto",
        backends: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Search selected AgentEye backends and return a bounded stable shape."""

        normalized_query = _normalize_query(query)
        resolved_category = resolve_category(normalized_query, category)
        result_limit = max(1, min(int(max_results), self.settings.max_results))
        selected = self._select_backends(normalized_query, category, backends)
        if not selected:
            return {
                "success": False,
                "query": normalized_query,
                "resolved_category": resolved_category,
                "results": [],
                "backends_requested": [],
                "backends_succeeded": [],
                "errors": [{"backend": "policy", "error": "No allowed backends selected"}],
            }

        available = self.backends
        futures: dict[Future[Any], str] = {}
        errors: list[dict[str, str]] = []
        per_backend_limit = max(3, min(result_limit, 8))
        for backend in selected:
            function = available.get(backend)
            if function is None:
                errors.append({"backend": backend, "error": "Backend unavailable in pinned AgentEye revision"})
                continue
            try:
                future = self._run_backend(backend, function, normalized_query, per_backend_limit)
            except Exception as exc:
                errors.append({"backend": backend, "error": str(exc)[:_MAX_ERROR_CHARS]})
                continue
            futures[future] = backend

        done, pending = wait(tuple(futures), timeout=self.settings.timeout_seconds)
        payloads: list[tuple[str, Any]] = []
        for future, backend in futures.items():
            if future in pending:
                if future.cancel():
                    _SEARCH_SLOTS.release()
                errors.append({"backend": backend, "error": "Timed out"})
                continue
            try:
                payload = future.result()
                if isinstance(payload, dict) and payload.get("success") is False:
                    message = str(payload.get("error") or payload.get("message") or "Backend reported failure")
                    errors.append({"backend": backend, "error": message[:_MAX_ERROR_CHARS]})
                else:
                    payloads.append((backend, payload))
            except Exception as exc:
                errors.append({"backend": backend, "error": str(exc)[:_MAX_ERROR_CHARS]})

        deduped: dict[str, dict[str, Any]] = {}
        for backend, payload in payloads:
            for raw in _walk_result_candidates(payload):
                url = _canonicalize_url(
                    _first_string(
                        raw,
                        ("url", "href", "link", "source_url", "repository", "html_url", "id"),
                    )
                )
                if url is None:
                    continue
                title = _first_string(raw, ("title", "name", "heading", "label")) or url
                snippet = _first_string(
                    raw,
                    ("snippet", "body", "description", "abstract", "summary", "text", "content"),
                )
                published = _first_string(raw, ("published", "published_at", "date", "updated_at"))
                provider_source = _first_string(raw, ("source", "provider", "site"))
                item = deduped.setdefault(
                    url,
                    {
                        "title": title[:300],
                        "url": url,
                        "snippet": snippet[:_MAX_SNIPPET_CHARS],
                        "source": backend,
                        "provider_source": provider_source[:120],
                        "published": published[:120],
                        "sources": [],
                    },
                )
                if backend not in item["sources"]:
                    item["sources"].append(backend)
                if len(item["snippet"]) < _MAX_SNIPPET_CHARS and snippet:
                    item["snippet"] = snippet[:_MAX_SNIPPET_CHARS]

        normalized_results = list(deduped.values())
        for item in normalized_results:
            item["sources"] = sorted(set(item["sources"]))
            item["source_count"] = len(item["sources"])
            if not item["provider_source"]:
                item.pop("provider_source", None)

        try:
            ranked = self.ranker(normalized_results, normalized_query)
            if not isinstance(ranked, list):
                raise TypeError("AgentEye ranker returned a non-list result")
        except Exception:
            ranked = _fallback_ranker(normalized_results, normalized_query)
        ranked.sort(
            key=lambda item: (
                -float(item.get("relevance_score", 0.0)),
                -int(item.get("source_count", 1)),
                float(item.get("position", math.inf)),
            )
        )
        for position, item in enumerate(ranked, 1):
            item["position"] = position
        ranked = ranked[:result_limit]

        succeeded = sorted({backend for backend, _ in payloads})
        errors.sort(key=lambda item: (item["backend"], item["error"]))
        return {
            "success": bool(ranked),
            "query": normalized_query,
            "resolved_category": resolved_category,
            "results": ranked,
            "backends_requested": selected,
            "backends_succeeded": succeeded,
            "errors": errors,
            "warnings": [
                "Free providers are rate-limited and may change without notice.",
                "Search results are discovery evidence; verify important claims against the source.",
            ],
        }


def source_catalog(settings: AgentEyeSettings) -> dict[str, Any]:
    """Return the operator-approved AgentEye source catalog without network I/O."""

    allowed = set(settings.allowed_backends)
    try:
        loaded_backends = sorted(_load_backend_registry())
        runtime_status: dict[str, Any] = {
            "available": True,
            "loaded_backends": len(loaded_backends),
        }
    except AgentEyeUnavailableError as exc:
        runtime_status = {
            "available": False,
            "loaded_backends": 0,
            "error": str(exc),
        }
    return {
        "categories": {category: [backend for backend in backends if backend in allowed] for category, backends in CATEGORY_BACKENDS.items()},
        "allowed_backends": sorted(allowed),
        "fragile_backends": sorted(allowed & FRAGILE_BACKENDS),
        "runtime_status": runtime_status,
        "limits": {
            "max_results": settings.max_results,
            "max_sources_per_query": settings.max_sources_per_query,
            "timeout_seconds": settings.timeout_seconds,
        },
        "notes": [
            "No API key does not mean unlimited or guaranteed availability.",
            "Public APIs and scraped sites enforce their own rate limits and terms.",
            "Fragile scrapers require allow_fragile_backends=true in config.yaml.",
        ],
    }


def configured_search_fn() -> Callable[[str, int], Any] | None:
    """Return the configured AgentEye async search provider, if enabled."""

    config = get_app_config().get_tool_config("agent_eye_search")
    if config is None:
        return None
    settings = settings_from_extra(dict(config.model_extra or {}))
    if not settings.use_for_deep_research:
        return None
    client = AgentEyeSearchClient(settings)

    async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        try:
            payload = await asyncio.to_thread(
                client.search,
                query,
                max_results=max_results,
                category="auto",
            )
        except (AgentEyeUnavailableError, ValueError):
            return []
        return list(payload.get("results", []))

    return search


async def extract_public_html(
    url: str,
    *,
    max_chars: int = 12_000,
    timeout_seconds: float = 20.0,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    """Fetch through Alpha's SSRF guard, then use AgentEye's smart extractor."""

    fetched = await fetch_public_text(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    bounded_chars = max(1_000, min(int(max_chars), 50_000))
    try:
        from agent_eye.extractors import smart_extract
    except ImportError:
        return {
            "url": fetched.final_url,
            "title": "",
            "content": fetched.content[:bounded_chars],
            "structured_data": None,
            "metadata": {},
            "extraction_method": "raw-html-fallback",
        }
    extracted = await asyncio.to_thread(smart_extract, fetched.content, fetched.final_url, bounded_chars)
    extracted["url"] = fetched.final_url
    extracted["requested_url"] = fetched.requested_url
    extracted["content"] = str(extracted.get("content") or "")[:bounded_chars]
    return extracted
