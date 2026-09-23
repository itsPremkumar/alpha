"""Live research backends: real search and real fetch for DeepResearchEngine.

Historically the engine silently fell back to mock providers that fabricated
``example.org`` sources and canned fetch text, so every "verified citation"
in a deep research report was fiction unless the caller passed explicit
functions. Resolution happens here instead:

- :func:`default_search_fn` returns a real DuckDuckGo-backed search
  provider (``ddgs`` is a core harness dependency; no API key required).
- :func:`default_fetch_fn` returns a real HTTP fetcher (``httpx`` is a core
  dependency); non-2xx responses raise instead of returning invented text.
- If no backend can be loaded at all, the resolver raises ``RuntimeError``
  with an actionable message so dispatch/tools fail honestly instead of
  reporting mock results as research.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

SearchFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
FetchFn = Callable[[str], Awaitable[str]]


def default_search_fn() -> SearchFn:
    """Build the live DuckDuckGo search provider.

    Raises ``RuntimeError`` when the ``ddgs`` package is unavailable so a
    missing backend surfaces as an honest failure, never as mock sources.
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:  # pragma: no cover - ddgs is a core dependency
        raise RuntimeError(
            "No live search backend available: the `ddgs` package is not installed. "
            "Install it, configure a community search tool, or pass an explicit "
            "search_fn to run deep research."
        ) from exc

    async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
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
    """Build the live HTTP fetcher; HTTP errors raise (no invented content)."""
    import httpx

    async def fetch(url: str) -> str:
        headers = {"User-Agent": "alpha-deep-research/1.0 (+research-engine)"}
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30.0, headers=headers
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    return fetch
