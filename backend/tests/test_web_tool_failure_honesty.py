"""Failure honesty, result bounding, SSRF screening and time bounding for the bundled web tools.

Every test in this file exists because a live probe of the shipped code produced
a wrong or dangerous answer. The shape of the assertions is deliberate:

* a *network failure* must be a typed ``error`` object, never an empty result
  list - an empty list reads to a model as "nothing exists on the web", and that
  lie propagates straight into the answer it writes;
* a *genuine* empty result set must still be reportable, and must be
  distinguishable from every failure;
* ``max_results`` is chosen by the model, so it must be bounded;
* ``web_fetch`` must screen the URL through the shared guard every other fetch
  provider already uses;
* ``web_fetch`` must be bounded in *wall-clock* time, not just per-chunk.

All URLs used here are public IP literals or syntactically invalid, so the suite
needs no DNS and no network: the only "network" it performs is the slow-drip
upstream it builds in-process.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from alpha.community.ddg_search import tools as ddg_tools
from alpha.community.image_search import tools as image_tools
from alpha.community.jina_ai import tools as jina_tools
from alpha.community.jina_ai.jina_client import JinaClient
from alpha.community.search_federation.errors import SearchProviderError

# A public address, written as a literal so the SSRF screen decides it offline.
PUBLIC_URL = "https://93.184.216.34/page"

PRIVATE_IP_URLS = [
    "http://127.0.0.1:8080/admin",
    "http://127.1.2.3/admin",
    "http://[::1]:9000/",
    "http://[fd00::1]/",
    "http://10.0.0.5/internal",
    "http://192.168.1.10/router",
    "http://172.16.4.4/host",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://localhost:8088/",
]


def _tool_config(extra: dict | None) -> MagicMock:
    cfg = MagicMock()
    if extra is None:
        cfg.get_tool_config.return_value = None
    else:
        entry = MagicMock()
        entry.model_extra = extra
        cfg.get_tool_config.return_value = entry
    return cfg


def _response(status: int, text: str) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("POST", "https://r.jina.ai/"))


class _RecordingDDGS:
    """A DDGS stand-in whose call/constructor behaviour the test dictates."""

    def __init__(self, text=None, images=None, error: Exception | None = None, ctor_error: Exception | None = None):
        self._text = text
        self._images = images
        self._error = error
        self._ctor_error = ctor_error
        self.calls: list[dict] = []
        self.timeout: int | None = None

    def __call__(self, timeout: int):  # DDGS is constructed, not subclassed
        if self._ctor_error is not None:
            raise self._ctor_error
        self.timeout = timeout
        return self

    def text(self, query: str, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if self._error is not None:
            raise self._error
        return list(self._text or [])

    def images(self, query: str, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if self._error is not None:
            raise self._error
        return list(self._images or [])


# ---------------------------------------------------------------------------
# web_search (community/ddg_search)
# ---------------------------------------------------------------------------


def test_web_search_transport_failure_is_a_typed_error_not_an_empty_result_set():
    """The core contract: a failure must never be laundered into "no results"."""
    engine = _RecordingDDGS(error=RuntimeError("all DDG backends are down"))
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(ddg_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(ddg_tools.web_search_tool.invoke({"query": "anything"}))

    assert payload["error"]["kind"] == "unavailable"
    assert payload["error"]["provider"] == "duckduckgo"
    assert "results" not in payload, "a failed search must not carry a results list"
    assert "not evidence that the web has nothing" in payload["hint"]
    assert "No results found" not in json.dumps(payload)


def test_web_search_missing_package_is_reported_as_not_configured():
    """An uninstalled optional package is an operator problem, not an empty web."""
    real_import = __import__

    def blocked(name, *args, **kwargs):
        if name == "ddgs" or name.startswith("ddgs."):
            raise ImportError("No module named 'ddgs'")
        return real_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", blocked),
        patch.object(ddg_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(ddg_tools.web_search_tool.invoke({"query": "anything"}))

    assert payload["error"]["kind"] == "not_configured"
    assert "pip install ddgs" in payload["hint"]
    assert "results" not in payload


@pytest.mark.parametrize(
    ("message", "expected_kind"),
    [
        ("Too Many Requests", "rate_limited"),
        ("rate limit exceeded", "rate_limited"),
        ("unusual traffic from your computer", "rate_limited"),
        ("connection reset by peer", "unavailable"),
        ("Name or service not known", "unavailable"),
    ],
)
def test_web_search_engine_errors_map_onto_the_shared_kind_vocabulary(message, expected_kind):
    engine = _RecordingDDGS(error=RuntimeError(message))
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(ddg_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(ddg_tools.web_search_tool.invoke({"query": "q"}))

    assert payload["error"]["kind"] == expected_kind
    assert payload["error"]["message"].startswith("RuntimeError:")


def test_web_search_genuine_empty_result_set_stays_reportable_and_distinguishable():
    """The engine answered and had nothing. That is a real answer, and it must not
    look like any of the failure payloads above."""
    engine = _RecordingDDGS(text=[])
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(ddg_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(ddg_tools.web_search_tool.invoke({"query": "obscure nonsense token 8f3a2b91c7"}))

    assert payload["results"] == []
    assert payload["total_results"] == 0
    assert "error" not in payload
    assert "not a search failure" in payload["note"]


def test_search_text_propagates_engine_failure_instead_of_returning_empty():
    """``_search_text`` is the function the tool calls; the old implementation
    swallowed the typed error and handed the tool ``[]``."""
    engine = _RecordingDDGS(error=RuntimeError("boom"))
    with patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}):
        with pytest.raises(ddg_tools.DuckDuckGoSearchError) as excinfo:
            ddg_tools._search_text("q")

    assert excinfo.value.kind == "unavailable"
    assert isinstance(excinfo.value, SearchProviderError)
    assert excinfo.value.to_dict()["provider"] == "duckduckgo"


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (100000, 20),
        (10**9, 20),
        (1, 1),
        (0, 1),
        (-50, 1),
        (7, 7),
    ],
)
def test_web_search_clamps_model_supplied_max_results(requested, expected):
    """``max_results`` is chosen by the model; without a ceiling one tool call can
    inject an arbitrary wall of third-party text into the context window."""
    engine = _RecordingDDGS(text=[])
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(ddg_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        ddg_tools.web_search_tool.invoke({"query": "q", "max_results": requested})

    assert engine.calls[0]["max_results"] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Operator config is free-form YAML, so every shape below really happens.
        ("12", 12),
        (None, 5),
        ("not-a-number", 5),
        (True, 5),
        (object(), 5),
    ],
)
def test_clamp_max_results_degrades_junk_to_the_default_not_to_the_cap(value, expected):
    """A malformed config value must neither silently become "give me everything"
    nor raise out of the tool."""
    assert ddg_tools._clamp_max_results(value) == expected


def test_web_search_config_max_results_is_also_clamped():
    engine = _RecordingDDGS(text=[])
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(ddg_tools, "get_app_config", return_value=_tool_config({"max_results": 5000})),
    ):
        ddg_tools.web_search_tool.invoke({"query": "q", "max_results": 5})

    assert engine.calls[0]["max_results"] == ddg_tools.MAX_RESULTS_HARD_CAP


# ---------------------------------------------------------------------------
# image_search (community/image_search)
# ---------------------------------------------------------------------------


def test_image_search_transport_failure_is_a_typed_error_not_an_empty_result_set():
    engine = _RecordingDDGS(error=RuntimeError("simulated transport failure"))
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(image_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(image_tools.image_search_tool.invoke({"query": "cat"}))

    assert payload["error"]["kind"] == "unavailable"
    assert "results" not in payload
    assert "No images found" not in json.dumps(payload)


def test_image_search_session_construction_failure_does_not_escape_the_tool():
    """``DDGS(...)`` used to be constructed outside the try, so a construction
    failure propagated a raw exception straight out of the tool boundary."""
    engine = _RecordingDDGS(ctor_error=ValueError("bad constructor args"))
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(image_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(image_tools.image_search_tool.invoke({"query": "cat"}))

    assert payload["error"]["kind"] == "unavailable"
    assert "could not open a DuckDuckGo session" in payload["error"]["message"]


def test_image_search_missing_package_is_reported_as_not_configured():
    real_import = __import__

    def blocked(name, *args, **kwargs):
        if name == "ddgs" or name.startswith("ddgs."):
            raise ImportError("No module named 'ddgs'")
        return real_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", blocked),
        patch.object(image_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(image_tools.image_search_tool.invoke({"query": "cat"}))

    assert payload["error"]["kind"] == "not_configured"
    assert "results" not in payload


def test_image_search_rate_limit_is_typed_as_rate_limited():
    engine = _RecordingDDGS(error=RuntimeError("429 Too Many Requests"))
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(image_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(image_tools.image_search_tool.invoke({"query": "cat"}))

    assert payload["error"]["kind"] == "rate_limited"
    assert "rate-limiting" in payload["hint"]


def test_image_search_genuine_empty_result_set_stays_reportable():
    engine = _RecordingDDGS(images=[])
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(image_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        payload = json.loads(image_tools.image_search_tool.invoke({"query": "obscure nonsense token 8f3a2b91c7"}))

    assert payload["results"] == []
    assert "error" not in payload
    assert "not a search failure" in payload["note"]


def test_image_search_clamps_model_supplied_max_results():
    engine = _RecordingDDGS(images=[])
    with (
        patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=engine)}),
        patch.object(image_tools, "get_app_config", return_value=_tool_config(None)),
    ):
        image_tools.image_search_tool.invoke({"query": "q", "max_results": 100000})

    assert engine.calls[0]["max_results"] == ddg_tools.MAX_RESULTS_HARD_CAP


# ---------------------------------------------------------------------------
# web_fetch: SSRF screening (community/jina_ai)
# ---------------------------------------------------------------------------


def _fetch(url: str, extra: dict | None = None, crawl: AsyncMock | None = None) -> str:
    crawl = crawl or AsyncMock(return_value="<html><body><p>body</p></body></html>")
    with (
        patch.object(jina_tools, "get_app_config", return_value=_tool_config(extra)),
        patch.object(jina_tools.JinaClient, "crawl", crawl),
    ):
        return asyncio.run(jina_tools.web_fetch_tool.ainvoke({"url": url}))


@pytest.mark.parametrize("url", PRIVATE_IP_URLS)
def test_web_fetch_refuses_private_loopback_and_metadata_urls(url):
    """Every other fetch provider screens through ``validate_public_http_url``
    (browserless, crawl4ai, fastcrw, browser_automation). The Jina-backed
    web_fetch did not, so these all reached the remote reader."""
    crawl = AsyncMock(return_value="<html><body><p>should never be read</p></body></html>")
    result = _fetch(url, crawl=crawl)

    assert result.startswith("Error: Refusing to fetch")
    crawl.assert_not_called(), "a refused URL must cost no third-party request"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://93.184.216.34/", "ftp://93.184.216.34/x", "not-a-url", ""])
def test_web_fetch_refuses_non_http_schemes(url):
    crawl = AsyncMock(return_value="<html><body><p>should never be read</p></body></html>")
    result = _fetch(url, crawl=crawl)

    assert result.startswith("Error: Only http:// and https:// URLs are supported")
    crawl.assert_not_called()


def test_web_fetch_allows_a_private_target_only_when_the_operator_opts_in():
    """The same escape hatch fastcrw documents, so a self-hosted deployment is
    not broken by the screen."""
    crawl = AsyncMock(return_value="<html><body><p>internal</p></body></html>")
    result = _fetch("http://10.0.0.5/internal", extra={"allow_private_addresses": True}, crawl=crawl)

    crawl.assert_awaited_once()
    assert "internal" in result


def test_web_fetch_still_fetches_a_public_url():
    crawl = AsyncMock(
        return_value="<html><head><title>T</title></head><body><article><p>public body text here</p></article></body></html>"
    )
    result = _fetch(PUBLIC_URL, crawl=crawl)

    crawl.assert_awaited_once()
    assert "public body text here" in result


# ---------------------------------------------------------------------------
# web_fetch: output bounding
# ---------------------------------------------------------------------------


def _long_page(sentences: int) -> str:
    body = "".join(f"<p>sentence number {i} with a little filler text</p>" for i in range(sentences))
    return f"<html><head><title>Long</title></head><body><article>{body}</article></body></html>"


def test_web_fetch_marks_a_truncated_page():
    """A silently clipped page reads to the model as a complete page, so the cut
    has to be visible in the output."""
    crawl = AsyncMock(return_value=_long_page(4000))
    result = _fetch(PUBLIC_URL, crawl=crawl)

    marker = jina_tools.TRUNCATION_MARKER.format(limit=jina_tools.MAX_CONTENT_CHARS)
    assert result.endswith(marker)
    assert len(result) == jina_tools.MAX_CONTENT_CHARS + len(marker)


def test_web_fetch_does_not_mark_a_short_page():
    crawl = AsyncMock(return_value="<html><head><title>T</title></head><body><article><p>short body</p></article></body></html>")
    result = _fetch(PUBLIC_URL, crawl=crawl)

    assert "short body" in result
    assert "truncated" not in result


# ---------------------------------------------------------------------------
# web_fetch: real time bounding
# ---------------------------------------------------------------------------


def _slow_drip(chunks: int, seconds_per_chunk: float):
    async def drip(self, url, **kwargs):
        for _ in range(chunks):  # every chunk is a well-formed, in-budget read
            await asyncio.sleep(seconds_per_chunk)
        return httpx.Response(200, text="<html><body><p>never delivered</p></body></html>", request=httpx.Request("POST", url))

    return drip


def test_web_fetch_is_bounded_by_a_total_time_budget_not_just_per_chunk_timeouts():
    """httpx's ``timeout`` is per-operation: a chunk every 2s satisfies it forever.
    Measured live, a ``timeout: 10`` config produced a 130s call. The total
    budget must cut that off and say so."""
    configured = 1
    budget = configured + 5

    with patch.object(httpx.AsyncClient, "post", _slow_drip(chunks=15, seconds_per_chunk=2.0)):
        started = time.perf_counter()
        result = asyncio.run(JinaClient().crawl(PUBLIC_URL, timeout=configured))
        elapsed = time.perf_counter() - started

    assert result.startswith("Error:")
    assert f"exceeded its {budget}s total time budget" in result
    assert "never delivered" not in result
    assert elapsed < 20, f"unbounded drip ran for {elapsed:.1f}s; the {budget}s budget did not apply"


def test_web_fetch_client_binds_a_slow_drip_through_the_tool():
    """The same bound must hold when the tool drives the client, i.e. the budget
    is a property of the client and not of one call site."""
    configured = 1
    budget = configured + 5

    with (
        patch.object(jina_tools, "get_app_config", return_value=_tool_config({"timeout": configured})),
        patch.object(httpx.AsyncClient, "post", _slow_drip(chunks=15, seconds_per_chunk=2.0)),
    ):
        started = time.perf_counter()
        result = asyncio.run(jina_tools.web_fetch_tool.ainvoke({"url": PUBLIC_URL}))
        elapsed = time.perf_counter() - started

    assert "never delivered" not in result
    assert f"exceeded its {budget}s total time budget" in result
    assert elapsed < 20, f"unbounded drip ran for {elapsed:.1f}s"


def test_web_fetch_bounds_the_upstream_error_body():
    """The error body is third-party content; an unbounded copy of it lands in a
    log line and, through the tool, in the model context."""

    async def fail(self, url, **kwargs):
        return _response(500, "REFUSED " + "A" * 200_000)

    with patch.object(httpx.AsyncClient, "post", fail):
        result = asyncio.run(JinaClient().crawl(PUBLIC_URL, timeout=5))

    assert result.startswith("Error: Jina API returned status 500:")
    assert "truncated" in result
    assert len(result) < 1200, f"upstream error body was {len(result)} chars"


def test_web_fetch_error_body_is_intact_when_it_is_short():
    async def fail(self, url, **kwargs):
        return _response(429, "Rate limited")

    with patch.object(httpx.AsyncClient, "post", fail):
        result = asyncio.run(JinaClient().crawl(PUBLIC_URL, timeout=5))

    assert result == "Error: Jina API returned status 429: Rate limited"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (0, 10),
        (-1, 10),
        (-99, 10),
        ("0", 10),
        ("-5", 10),
        (None, 10),
        (True, 10),
        ("not-a-number", 10),
        (1, 1),
        (30, 30),
        (10**9, 120),
    ],
)
def test_web_fetch_timeout_config_is_clamped(configured, expected):
    """``timeout: 0`` reaches httpx as "fail immediately" (measured: ConnectTimeout
    on the first request), not as "no limit" - so the setting that looks like it
    disables the timeout silently disabled the tool."""
    assert jina_tools._coerce_timeout(configured, 10) == expected


def test_web_fetch_passes_the_clamped_timeout_to_the_client():
    crawl = AsyncMock(return_value="<html><head><title>T</title></head><body><article><p>ok</p></article></body></html>")
    _fetch(PUBLIC_URL, extra={"timeout": 10**9}, crawl=crawl)

    assert crawl.await_args.kwargs["timeout"] == 120


# ---------------------------------------------------------------------------
# web_fetch: the bearer token never leaves the reader host
# ---------------------------------------------------------------------------


def test_jina_bearer_token_is_only_attached_to_the_reader_endpoint(monkeypatch):
    """The reader URL is a module constant, so the key cannot be aimed at a host
    the user supplied. Pin that, because it is the only thing standing between
    JINA_API_KEY and a redirect/override."""
    from alpha.community.jina_ai import jina_client

    seen: dict = {}

    async def capture(self, url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs["headers"]
        return httpx.Response(200, text="ok", request=httpx.Request("POST", url))

    monkeypatch.setenv("JINA_API_KEY", "secret-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", capture)

    result = asyncio.run(jina_client.JinaClient().crawl(PUBLIC_URL))

    assert result == "ok"
    assert seen["url"] == jina_client.READER_URL == "https://r.jina.ai/"
    assert seen["headers"]["Authorization"] == "Bearer secret-key"
    assert str(PUBLIC_URL) not in json.dumps(seen["headers"])


def test_jina_sends_no_authorization_header_without_a_key(monkeypatch):
    from alpha.community.jina_ai import jina_client

    seen: dict = {}

    async def capture(self, url, **kwargs):
        seen["headers"] = kwargs["headers"]
        return httpx.Response(200, text="ok", request=httpx.Request("POST", url))

    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "post", capture)

    asyncio.run(jina_client.JinaClient().crawl(PUBLIC_URL))

    assert "Authorization" not in seen["headers"]


def test_user_supplied_url_cannot_inject_request_headers():
    """The URL travels in a JSON body and every header is a static literal, so a
    URL carrying CR/LF or header-looking text is inert."""
    seen: dict = {}

    async def capture(self, url, **kwargs):
        seen["headers"] = kwargs["headers"]
        seen["data"] = kwargs["json"]
        return httpx.Response(200, text="ok", request=httpx.Request("POST", url))

    hostile = "https://93.184.216.34/x\r\nX-Injected: 1\r\n\r\nGET /admin"
    with patch.object(httpx.AsyncClient, "post", capture):
        asyncio.run(JinaClient().crawl(hostile, timeout=5))

    assert "X-Injected" not in seen["headers"]
    assert seen["data"] == {"url": hostile}
