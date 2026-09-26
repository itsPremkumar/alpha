"""Contract tests for the pinned AgentEye live-research integration."""

from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from alpha.community.agent_eye import provider, safe_fetch
from alpha.community.agent_eye.provider import (
    AgentEyeSearchClient,
    AgentEyeSettings,
    configured_search_fn,
)
from alpha.community.agent_eye.tools import (
    agent_eye_search_tool,
    agent_eye_sources_tool,
)
from alpha.research import backends as research_backends


def _settings(**overrides: Any) -> AgentEyeSettings:
    values: dict[str, Any] = {
        "allowed_backends": ("github", "arxiv"),
        "max_results": 5,
        "max_sources_per_query": 4,
        "timeout_seconds": 2.0,
        "allow_fragile_backends": False,
        "use_for_deep_research": True,
    }
    values.update(overrides)
    return AgentEyeSettings(**values)


def test_curated_registry_uses_safe_agent_eye_modules_without_running_core_orchestrator() -> None:
    backends = provider._load_backend_registry()

    assert {"arxiv", "pubmed", "github", "wikipedia", "npm", "openlibrary", "datamuse"} <= set(backends)
    assert not ({"google", "bing", "reddit", "twitter", "nasa_apod", "wayback"} & set(backends))
    assert all(callable(function) for function in backends.values())


def test_search_uses_agent_eye_backends_deduplicates_and_ranks() -> None:
    backends = {
        "github": lambda query, limit: [
            {
                "title": "Alpha source",
                "url": f"https://example.com/alpha?utm_source={query}",
                "body": "Evidence about alpha from GitHub.",
            }
        ],
        "arxiv": lambda query, limit: {
            "results": [
                {
                    "name": "Alpha paper",
                    "link": "https://example.com/alpha",
                    "abstract": "Independent alpha evidence.",
                }
            ]
        },
    }
    client = AgentEyeSearchClient(
        _settings(),
        backends=backends,
        ranker=lambda results, query: [{**item, "relevance_score": 1.0, "position": index + 1} for index, item in enumerate(results)],
    )

    result = client.search(
        "alpha",
        max_results=3,
        category="academic",
        backends=["github", "arxiv"],
    )

    assert result["success"] is True
    assert result["resolved_category"] == "academic"
    assert result["backends_succeeded"] == ["arxiv", "github"]
    assert not result["errors"]
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://example.com/alpha"
    assert result["results"][0]["source_count"] == 2
    assert result["results"][0]["sources"] == ["arxiv", "github"]
    assert result["results"][0]["relevance_score"] == 1.0


def test_search_isolates_backend_failures_and_enforces_allowlist() -> None:
    backends = {
        "github": lambda query, limit: (_ for _ in ()).throw(RuntimeError("rate limited")),
        "arxiv": lambda query, limit: [{"title": "Paper", "url": "https://arxiv.org/example", "snippet": "Data"}],
    }
    client = AgentEyeSearchClient(_settings(), backends=backends, ranker=lambda items, query: items)

    result = client.search(
        "paper",
        max_results=3,
        category="academic",
        backends=["github", "arxiv"],
    )

    assert result["success"] is True
    assert result["backends_succeeded"] == ["arxiv"]
    assert result["errors"] == [{"backend": "github", "error": "rate limited"}]
    assert result["results"][0]["url"] == "https://arxiv.org/example"

    with pytest.raises(ValueError, match="not allowed"):
        client.search("paper", category="academic", backends=["google"])


def test_auto_category_selects_domain_specific_sources() -> None:
    assert provider.resolve_category("latest python framework release", "auto") == "code"
    assert provider.resolve_category("peer reviewed clinical study", "auto") == "academic"
    assert provider.resolve_category("earthquake volcanic activity", "auto") == "science"
    assert provider.resolve_category("api documentation", "auto") == "code"
    assert provider.resolve_category("capital allocation policy", "auto") == "government"
    assert provider.resolve_category("ordinary search phrase", "auto") == "web"


def test_configured_search_fn_requires_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider, "get_app_config", lambda: SimpleNamespace(get_tool_config=lambda name: None))
    assert configured_search_fn() is None

    disabled = SimpleNamespace(
        model_extra={"use_for_deep_research": False},
    )
    monkeypatch.setattr(
        provider,
        "get_app_config",
        lambda: SimpleNamespace(get_tool_config=lambda name: disabled),
    )
    assert configured_search_fn() is None


def test_agent_eye_tool_schemas_are_model_safe() -> None:
    schema = agent_eye_search_tool.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"query", "category", "backends", "max_results"}
    assert schema["required"] == ["query"]

    openai_schema = convert_to_openai_tool(agent_eye_search_tool)
    assert openai_schema["function"]["name"] == "agent_eye_search"
    assert "live internet" in openai_schema["function"]["description"].lower()

    source_schema = agent_eye_sources_tool.tool_call_schema.model_json_schema()
    assert set(source_schema["properties"]) == {"category"}
    assert not source_schema.get("required", [])


@pytest.mark.asyncio
async def test_agent_eye_search_tool_returns_structured_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        search=lambda query, max_results, category, backends: {
            "success": True,
            "query": query,
            "results": [{"title": "Result", "url": "https://example.com"}],
        }
    )
    monkeypatch.setattr("alpha.community.agent_eye.tools.get_client", lambda settings: client)
    monkeypatch.setattr(
        "alpha.community.agent_eye.tools.get_settings",
        lambda: _settings(max_results=3),
    )

    payload = await agent_eye_search_tool.ainvoke({"query": "live sources", "max_results": 99})

    assert '"success": true' in payload
    assert "Result" in payload
    assert agent_eye_search_tool.name == "agent_eye_search"
    assert "live internet" in agent_eye_search_tool.description.lower()


@pytest.mark.live
def test_agent_eye_academic_sources_live_smoke() -> None:
    import os

    if os.getenv("AGENT_WORKSPACE_RUN_LIVE_TESTS") != "1":
        pytest.skip("Set AGENT_WORKSPACE_RUN_LIVE_TESTS=1 for live provider checks")

    client = AgentEyeSearchClient(
        AgentEyeSettings(
            allowed_backends=("arxiv", "pubmed", "semantic_scholar", "crossref"),
            max_results=5,
            max_sources_per_query=4,
            timeout_seconds=30,
        )
    )
    payload = client.search(
        "retrieval augmented generation evaluation",
        max_results=5,
        category="academic",
    )

    assert payload["success"] is True
    assert payload["results"]
    assert payload["backends_succeeded"]


def test_source_catalog_reports_missing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider,
        "_load_backend_registry",
        lambda: (_ for _ in ()).throw(provider.AgentEyeUnavailableError("missing test runtime")),
    )
    catalog = provider.source_catalog(_settings())
    assert catalog["runtime_status"] == {
        "available": False,
        "loaded_backends": 0,
        "error": "missing test runtime",
    }


def test_agent_eye_sources_tool_lists_configured_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alpha.community.agent_eye.tools.get_settings", _settings)
    payload = agent_eye_sources_tool.invoke({})
    assert "academic" in payload
    assert "github" in payload
    assert '"available": true' in payload
    assert "rate limits" in payload.lower()


@pytest.mark.asyncio
async def test_deep_research_search_prefers_agent_eye_and_keeps_ddgs_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def agent_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return [{"title": "AgentEye", "url": "https://example.com/agent-eye", "snippet": "live"}]

    class _DDGS:
        def __init__(self, *args: Any, **kwargs: Any):
            raise AssertionError("DDGS fallback should not run when AgentEye returns results")

    monkeypatch.setattr(research_backends, "configured_agent_eye_search_fn", lambda: agent_search)
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=_DDGS))

    search = research_backends.default_search_fn()
    results = await search("query", 3)
    assert results[0]["url"] == "https://example.com/agent-eye"

    async def empty_agent_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return []

    class _FallbackDDGS:
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def text(self, query: str, max_results: int = 5, **kwargs: Any) -> list[dict[str, str]]:
            return [{"title": "DDGS", "href": "https://example.com/ddgs", "body": "fallback"}]

    monkeypatch.setattr(research_backends, "configured_agent_eye_search_fn", lambda: empty_agent_search)
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=_FallbackDDGS))
    fallback_search = research_backends.default_search_fn()
    fallback_results = await fallback_search("query", 3)
    assert fallback_results[0]["url"] == "https://example.com/ddgs"


@pytest.mark.asyncio
async def test_safe_fetch_revalidates_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    class _Response:
        def __init__(self, status_code: int, location: str | None = None):
            self.status_code = status_code
            self.headers = {"location": location} if location else {}
            self.encoding = "utf-8"

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            if False:
                yield b""

    class _Stream:
        def __init__(self, response: _Response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str):
            requested.append(url)
            return _Stream(_Response(302, "http://127.0.0.1/private"))

    def validate(url: str, **kwargs: Any) -> str | None:
        if "127.0.0.1" in url:
            return "Error: private address"
        return None

    monkeypatch.setattr(safe_fetch, "validate_public_http_url", validate)
    monkeypatch.setattr(safe_fetch.httpx, "AsyncClient", lambda **kwargs: _Client())

    with pytest.raises(ValueError, match="private address"):
        await safe_fetch.fetch_public_text("https://example.com/start", max_bytes=1024)

    assert requested == ["https://example.com/start"]


@pytest.mark.asyncio
async def test_safe_fetch_rejects_embedded_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        safe_fetch,
        "validate_public_http_url",
        lambda url, **kwargs: pytest.fail("credential-bearing URLs must be rejected before DNS/HTTP"),
    )
    monkeypatch.setattr(
        safe_fetch.httpx,
        "AsyncClient",
        lambda **kwargs: pytest.fail("credential-bearing URLs must not open an HTTP client"),
    )

    with pytest.raises(ValueError, match="credentials"):
        await safe_fetch.fetch_public_text("https://user:secret@example.com/private")


@pytest.mark.asyncio
async def test_safe_fetch_caps_response_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        encoding = "utf-8"

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b"x" * 1024
            yield b"y"

    class _Stream:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str):
            return _Stream()

    monkeypatch.setattr(safe_fetch, "validate_public_http_url", lambda url, **kwargs: None)
    monkeypatch.setattr(safe_fetch.httpx, "AsyncClient", lambda **kwargs: _Client())

    with pytest.raises(ValueError, match="exceeds"):
        await safe_fetch.fetch_public_text("https://example.com", max_bytes=1024)


def test_event_loop_is_not_blocked_by_sync_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """The async tool boundary must offload AgentEye's synchronous fan-out."""

    event_loop_thread = threading.get_ident()
    search_thread: list[int] = []

    def search(query: str, max_results: int, category: str, backends: list[str] | None) -> dict[str, object]:
        search_thread.append(threading.get_ident())
        return {"success": True, "query": query, "results": []}

    async def run() -> str:
        return await agent_eye_search_tool.ainvoke({"query": "test"})

    client = SimpleNamespace(search=search)
    monkeypatch.setattr("alpha.community.agent_eye.tools.get_client", lambda settings: client)
    monkeypatch.setattr("alpha.community.agent_eye.tools.get_settings", _settings)
    assert '"success": true' in asyncio.run(run())
    assert search_thread and search_thread[0] != event_loop_thread
