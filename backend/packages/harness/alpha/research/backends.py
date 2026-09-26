"""Live research backends: real search and real fetch for DeepResearchEngine.

Historically the engine silently fell back to mock providers that fabricated
``example.org`` sources and canned fetch text, so every "verified citation"
in a deep research report was fiction unless the caller passed explicit
functions. Resolution happens here instead:

- When the ``agent_eye_search`` tool is configured for research, its bounded
  multi-source provider is tried first and DuckDuckGo remains an honest fallback.
- :func:`default_fetch_fn` fetches through Alpha's public-URL/redirect guard and
  uses AgentEye's HTML/structured-data extractor, with the bounded raw response
  as a dependency-missing fallback.
- If no search backend can be loaded, the resolver raises ``RuntimeError`` with
  an actionable message instead of reporting mock results as research.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

SearchFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
FetchFn = Callable[[str], Awaitable[str]]

logger = logging.getLogger(__name__)


def configured_agent_eye_search_fn() -> SearchFn | None:
    """Return the opt-in AgentEye research provider without importing it eagerly."""

    try:
        from alpha.community.agent_eye.provider import configured_search_fn
    except ImportError:  # pragma: no cover - provider ships with the harness
        return None
    return configured_search_fn()


def default_search_fn() -> SearchFn:
    """Build the configured AgentEye + DuckDuckGo live search provider.

    AgentEye is used only when its ``agent_eye_search`` tool configuration opts
    into deep research. DuckDuckGo remains a real, keyless fallback; no mock
    provider is silently substituted.
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:  # pragma: no cover - ddgs is a core dependency
        raise RuntimeError("No live search backend available: install the locked `ddgs` dependency, configure AgentEye live research, or pass an explicit search_fn.") from exc

    agent_eye_search = configured_agent_eye_search_fn()

    async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        if agent_eye_search is not None:
            try:
                results = await agent_eye_search(query, max_results)
                if results:
                    return results
            except Exception:
                logger.warning("Configured AgentEye search failed; trying DuckDuckGo", exc_info=True)

        import asyncio

        def _run() -> list[dict[str, Any]]:
            # ddgs is synchronous; run it off the event loop.
            results = DDGS().text(query, max_results=max_results) or []
            out: list[dict[str, Any]] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                url = item.get("href") or item.get("url") or ""
                if not url:
                    continue
                out.append(
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(url),
                        "snippet": str(item.get("body") or item.get("snippet") or ""),
                    }
                )
            return out

        return await asyncio.to_thread(_run)

    return search


def default_fetch_fn() -> FetchFn:
    """Build the SSRF-screened, size-bounded live research fetcher."""

    async def fetch(url: str) -> str:
        from alpha.community.agent_eye.provider import extract_public_html

        extracted = await extract_public_html(
            url,
            max_chars=50_000,
            timeout_seconds=30.0,
            max_bytes=2_000_000,
        )
        content = str(extracted.get("content") or "").strip()
        if not content:
            raise ValueError(f"No readable content extracted from {url}")
        return content

    return fetch
