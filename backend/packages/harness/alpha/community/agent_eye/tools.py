"""Alpha tools backed by the pinned AgentEye source library."""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache

from langchain.tools import tool

from alpha.community.agent_eye.provider import (
    AgentEyeCategory,
    AgentEyeSearchClient,
    AgentEyeSettings,
    get_settings,
    source_catalog,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def get_client(settings: AgentEyeSettings) -> AgentEyeSearchClient:
    """Cache clients by hot-reloadable settings, not across distinct policies."""

    return AgentEyeSearchClient(settings)


@tool("agent_eye_search", parse_docstring=True)
async def agent_eye_search_tool(
    query: str,
    category: AgentEyeCategory = "auto",
    backends: list[str] | None = None,
    max_results: int = 8,
) -> str:
    """Search many live internet sources through the free AgentEye backend library.

    Use this when ordinary web search needs broader coverage across academic,
    developer, package registry, government, scientific, knowledge, news, social,
    media, finance, or language resources. The operator controls which backends are
    allowed. Free does not mean unlimited: providers can rate-limit, reject scrapers,
    or change without notice. Verify important claims by fetching the original source.

    Args:
        query: A specific search query. Current facts should include the relevant year or date.
        category: Source family to use, or auto to select one from the query. Defaults to auto.
        backends: Optional explicit AgentEye backend names from agent_eye_sources. Must pass the operator allowlist.
        max_results: Maximum ranked results to return. The configured cap is always enforced.
    """

    settings = get_settings()
    effective_limit = max(1, min(int(max_results), settings.max_results))
    try:
        payload = await asyncio.to_thread(
            get_client(settings).search,
            query,
            max_results=effective_limit,
            category=category,
            backends=backends,
        )
    except Exception as exc:
        logger.warning("AgentEye search failed: %s", exc)
        payload = {
            "success": False,
            "query": query,
            "results": [],
            "errors": [{"backend": "agent_eye", "error": str(exc)[:500]}],
        }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@tool("agent_eye_sources", parse_docstring=True)
def agent_eye_sources_tool(category: AgentEyeCategory | None = None) -> str:
    """List live-source backends currently allowed by the AgentEye operator policy.

    Call this before agent_eye_search when you need a particular source family or want
    to inspect fragile providers. It performs no network requests.

    Args:
        category: Optional category to inspect instead of returning every source family.
    """

    catalog = source_catalog(get_settings())
    if category and category != "auto":
        catalog = {
            **catalog,
            "categories": {category: catalog["categories"].get(category, [])},
        }
    return json.dumps(catalog, indent=2, ensure_ascii=False)
