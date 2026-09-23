"""Tests for the keyless free-LLM router (providers + catalog + ChatFreeLLM).

Design under test (single seam): every provider HTTP call goes through
``alpha.models.free_router.providers.request`` — tests stub ONLY that
function and exercise discovery, health/cooldown, selection, failover,
the LangChain model, and the gateway endpoint for real.

Workspace isolation: the autouse fixture points ``AGENT_WORKSPACE_HOME`` at
a per-test tmp dir (the repo's test env does not isolate it globally), so
the router's catalog cache never touches the live ``.agent-workspace``.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.models import factory as factory_module
from alpha.models.fallback import FallbackChatModel, is_retryable_llm_error
from alpha.models.free_router import PROVIDER_ORDER, ProviderError
from alpha.models.free_router import providers as providers_mod
from alpha.models.free_router.catalog import (
    COOLDOWN_MAX_SECONDS,
    FreeLLMRouter,
    FreeLLMUnavailableError,
    get_free_router,
    reset_free_router,
)
from alpha.models.free_router.chat_model import ChatFreeLLM

# ---------------------------------------------------------------------------
# Fixtures + stub helpers (the ONLY seam: providers.request)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "agent-workspace"))
    reset_free_router()
    yield
    reset_free_router()


class _Handler:
    """URL-routed stub for providers.request that records every call."""

    def __init__(self, *, models=None, chat=None, block_release: threading.Event | None = None):
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self._models = models  # callable(url) -> Response, or a Response
        self._chat = chat
        self._block_release = block_release

    def __call__(self, method, url, *, headers=None, params=None, json_body=None, timeout=None):
        with self._lock:
            self.calls.append({"method": method, "url": url, "json": json_body})
        is_chat = "chat/completions" in url or url.endswith("/openai") or "generate/text" in url
        if self._block_release is not None and not is_chat:
            self._block_release.wait(10)
        source = self._chat if is_chat else self._models
        if source is None:
            return models_ok()
        return source(url, json_body) if callable(source) else source

    def count(self, needle: str) -> int:
        with self._lock:
            return sum(1 for c in self.calls if needle in c["url"])


FREE_ENTRY = {"id": "free-1", "name": "Free One", "pricing": {"input": "0", "output": "0"}}
PAID_ENTRY = {"id": "paid-1", "name": "Paid One", "pricing": {"input": "1", "output": "5"}}
EMBED_ENTRY = {"id": "text-embedding-3", "pricing": {"input": "0", "output": "0"}}


def models_ok(url: str = "", body=None) -> httpx.Response:
    return httpx.Response(200, json=body or {"data": [FREE_ENTRY, PAID_ENTRY, EMBED_ENTRY]})


def models_http_500(url: str = "", body=None) -> httpx.Response:
    return httpx.Response(500, text="SECRET-BODY-SNIPPET")


def chat_ok(text: str = "hello from free", usage=None, tool_calls=None) -> httpx.Response:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    payload: dict = {"choices": [{"message": message}]}
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload)


def chat_http_500(url: str = "", body=None) -> httpx.Response:
    return httpx.Response(500, text="SECRET-BODY-SNIPPET")


def chat_empty_content(url: str = "", body=None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": ""}}]})


def install(monkeypatch, **kwargs) -> _Handler:
    handler = _Handler(**kwargs)
    monkeypatch.setattr(providers_mod, "request", handler)
    return handler


def make_router(tmp_path, *, clock=None, ttl=300.0) -> FreeLLMRouter:
    return FreeLLMRouter(
        ttl=ttl,
        cache_path=tmp_path / "catalog.json",
        clock=(lambda: clock[0]) if clock is not None else (lambda: 1_000_000.0),
    )


# ---------------------------------------------------------------------------
# Discovery: real free/paid/non-text filtering through the HTTP seam
# ---------------------------------------------------------------------------


def test_discovery_keeps_free_drops_paid_and_non_text(tmp_path, monkeypatch):
    handler = install(monkeypatch)
    router = make_router(tmp_path)
    assert router.refresh() is True

    view = router.catalog_dict()
    by_name = {p["name"]: p for p in view["providers"]}
    vireonix = by_name["vireonix"]
    assert vireonix["discovery_ok"] is True
    # paid model filtered by price, embedding model filtered as non-text:
    assert [m["id"] for m in vireonix["models"] if m["source"] == "catalog"] == ["free-1"]
    # documented anonymous ids are appended explicitly labeled as such:
    assert {"id": "auto", "source": "documented"} in vireonix["models"]
    assert handler.count("vireonix.ai/v1/models") == 1
    # documented-only provider needs no HTTP for its catalog:
    assert by_name["cehpoint"]["source_labels"] == ["documented"]
    assert by_name["cehpoint"]["healthy"] is None  # never contacted: unknown, not "up"


def test_discovery_failure_is_honest_not_availability_claims(tmp_path, monkeypatch):
    install(monkeypatch, models=models_http_500)
    router = make_router(tmp_path)
    router.refresh()

    view = router.catalog_dict()
    assert view["refreshed_at"] is not None
    for provider in view["providers"]:
        if provider["name"] == "cehpoint":  # documented-only: no HTTP, no error
            assert provider["discovery_ok"] is True
            continue
        assert provider["discovery_ok"] is False
        assert "HTTP 500" in provider["discovery_error"]
        assert provider["healthy"] is None  # discovery failure never fakes health


def test_refresh_single_flight_and_ttl_reuse(tmp_path, monkeypatch):
    release = threading.Event()
    handler = install(monkeypatch, block_release=release)
    clock = [1_000_000.0]
    router = make_router(tmp_path, clock=clock, ttl=300.0)

    assert router.refresh() is True
    first_pass = handler.count("/models")
    # cehpoint has no catalog endpoint (documented-only): 6 of 7 fetch HTTP.
    assert first_pass == len(PROVIDER_ORDER) - 1
    # Within TTL: no work, honest False.
    assert router.refresh() is False
    assert handler.count("/models") == first_pass

    # Stale: a refresh starts; a concurrent caller (loaded cache) returns
    # immediately instead of starting a second pass.
    clock[0] += 1000.0
    done: list[bool] = []
    worker = threading.Thread(target=lambda: done.append(router.refresh()))
    worker.start()
    for _ in range(400):  # bounded wait (~20s) for the worker's discovery
        if handler.count("/models") > first_pass:
            break
        time.sleep(0.05)
    else:
        pytest.fail("refresh worker never started discovery")
    assert router.refresh() is False  # single-flight: not this caller's job
    release.set()
    worker.join(10)
    assert done == [True]
    # Exactly one extra pass — never two (single-flight holds under contention).
    assert handler.count("/models") == first_pass * 2


def test_health_merge_preserves_chat_health_across_discovery_refresh(tmp_path, monkeypatch):
    handler = install(monkeypatch)
    clock = [1_000_000.0]
    router = make_router(tmp_path, clock=clock)
    router.refresh()
    router.mark_success("vireonix", latency_ms=12.0)

    handler._models = models_http_500
    clock[0] += 1000.0
    router.refresh(force=True)

    view = {p["name"]: p for p in router.catalog_dict()["providers"]}
    vireonix = view["vireonix"]
    assert vireonix["healthy"] is True  # chat health survives catalog refresh
    assert vireonix["discovery_ok"] is False
    assert "HTTP 500" in vireonix["discovery_error"]
    # stale models kept (catalog entries survive a failed re-discovery):
    assert [m["id"] for m in vireonix["models"] if m["source"] == "catalog"] == ["free-1"]


# ---------------------------------------------------------------------------
# Tri-state health + cooldown backoff (fake clock, real arithmetic)
# ---------------------------------------------------------------------------


def test_health_is_tri_state(tmp_path):
    clock = [1_000_000.0]
    router = make_router(tmp_path, clock=clock)
    name = "vireonix"

    assert {p["name"]: p for p in router.catalog_dict()["providers"]}[name]["healthy"] is None
    router.mark_failure(name, "probe timeout", inconclusive=True)
    assert {p["name"]: p for p in router.catalog_dict()["providers"]}[name]["healthy"] is None
    router.mark_failure(name, "ProviderError(status=500)")
    assert {p["name"]: p for p in router.catalog_dict()["providers"]}[name]["healthy"] is False
    router.mark_success(name)
    assert {p["name"]: p for p in router.catalog_dict()["providers"]}[name]["healthy"] is True
    assert router.catalog_dict()["providers"][0]["consecutive_failures"] == 0


def test_cooldown_exponential_backoff_with_jitter_and_cap(tmp_path):
    clock = [1_000_000.0]
    router = make_router(tmp_path, clock=clock)
    name = "vireonix"

    router.mark_failure(name, "E1")
    first = router._states[name].cooldown_until - clock[0]
    assert 12.0 <= first <= 18.0  # 15s +/- 20% jitter

    router.mark_failure(name, "E2")
    second = router._states[name].cooldown_until - clock[0]
    assert 24.0 <= second <= 36.0  # 30s +/- 20% jitter

    for i in range(12):
        router.mark_failure(name, f"E{i}")
    capped = router._states[name].cooldown_until - clock[0]
    assert capped <= COOLDOWN_MAX_SECONDS
    assert capped >= COOLDOWN_MAX_SECONDS * 0.8 - 1e-6

    clock[0] += COOLDOWN_MAX_SECONDS + 1.0
    assert all(s.cooldown_until <= clock[0] for s in router._states.values())
    # Public view reports the ISO cooldown only while it is still active:
    view = {p["name"]: p for p in router.catalog_dict()["providers"]}
    assert view[name]["cooldown_until"] is None


# ---------------------------------------------------------------------------
# Selection + routed chat failover (honest attempts, no payload leaks)
# ---------------------------------------------------------------------------


def test_chat_fails_over_and_marks_providers(tmp_path, monkeypatch):
    def chat_route(url, body):
        if "vireonix.ai" in url:
            return chat_http_500()
        return chat_ok()

    handler = install(monkeypatch, chat=chat_route)
    clock = [1_000_000.0]
    router = make_router(tmp_path, clock=clock)

    result = router.chat([{"role": "user", "content": "hi"}])
    assert result.text == "hello from free"
    assert result.provider == "blockrun"  # vireonix failed, next in order served
    assert result.model_id == "free-1"

    view = {p["name"]: p for p in router.catalog_dict()["providers"]}
    assert view["vireonix"]["healthy"] is False
    assert view["vireonix"]["cooldown_until"] is not None  # cooling down now
    assert view["vireonix"]["last_error"] == "ProviderError(status=500)"
    assert view["blockrun"]["healthy"] is True

    # Second chat: vireonix is cooling down and must not be attempted again.
    router.chat([{"role": "user", "content": "again"}])
    assert handler.count("vireonix.ai/v1/chat/completions") == 1
    assert handler.count("blockrun.ai/api/v1/chat/completions") == 2


def test_all_fail_raises_retryable_honest_error_without_body_leak(tmp_path, monkeypatch):
    handler = install(monkeypatch, chat=chat_http_500)
    router = make_router(tmp_path)

    with pytest.raises(FreeLLMUnavailableError) as excinfo:
        router.chat([{"role": "user", "content": "hi"}])

    exc = excinfo.value
    assert isinstance(exc, ConnectionError)
    assert is_retryable_llm_error(exc)  # fallback chain may continue
    assert len(exc.attempts) == len(PROVIDER_ORDER)  # every provider was tried
    for provider, label in exc.attempts:
        assert "status=500" in label
        assert "SECRET-BODY-SNIPPET" not in label  # labels never carry payloads
    chat_hits = handler.count("chat/completions") + handler.count("/openai") + handler.count("generate/text")
    assert chat_hits == len(PROVIDER_ORDER)  # one real HTTP attempt per provider


def test_empty_content_is_a_failure_never_fake_success(tmp_path, monkeypatch):
    install(monkeypatch, chat=chat_empty_content)
    router = make_router(tmp_path)
    with pytest.raises(FreeLLMUnavailableError) as excinfo:
        router.chat([{"role": "user", "content": "hi"}])
    assert len(excinfo.value.attempts) == len(PROVIDER_ORDER)


def test_all_cooling_down_reports_retry_after(tmp_path, monkeypatch):
    install(monkeypatch, chat=chat_http_500)
    clock = [1_000_000.0]
    router = make_router(tmp_path, clock=clock)

    # Discovery succeeds (models_ok default) so every provider has
    # candidates; one routed chat fails them all into cooldown.
    with pytest.raises(FreeLLMUnavailableError):
        router.chat([{"role": "user", "content": "hi"}])

    # Every provider is now cooling down: a fresh router state must NOT
    # hammer them; it reports an honest cooldown error with a retry hint.
    with pytest.raises(FreeLLMUnavailableError) as excinfo:
        router.candidates()
    assert "cooling down" in str(excinfo.value)
    assert excinfo.value.retry_after_seconds is not None
    assert excinfo.value.retry_after_seconds > 0


def test_no_candidates_when_discovery_never_succeeded(tmp_path, monkeypatch):
    install(monkeypatch, models=models_http_500)
    router = make_router(tmp_path)
    with pytest.raises(FreeLLMUnavailableError) as excinfo:
        router.chat([{"role": "user", "content": "hi"}])
    # cehpoint's documented candidates keep it attemptable even with no
    # catalog endpoint — so the error must carry those honest attempts.
    assert "no free provider candidates" in str(excinfo.value) or excinfo.value.attempts
    assert is_retryable_llm_error(excinfo.value)


# ---------------------------------------------------------------------------
# Cache persistence + corruption
# ---------------------------------------------------------------------------


def test_cache_roundtrip_persists_health_and_skips_http(tmp_path, monkeypatch):
    handler = install(monkeypatch)
    clock = [1_000_000.0]
    router1 = make_router(tmp_path, clock=clock)
    router1.refresh()
    router1.mark_failure("vireonix", "ProviderError(status=500)")

    router2 = make_router(tmp_path, clock=clock)
    view = {p["name"]: p for p in router2.catalog_dict()["providers"]}
    assert view["vireonix"]["healthy"] is False
    assert view["vireonix"]["consecutive_failures"] == 1
    assert view["vireonix"]["cooldown_until"] is not None
    assert [m["id"] for m in view["vireonix"]["models"] if m["source"] == "catalog"] == ["free-1"]
    # Cache is fresh (same clock): router2's refresh performs no NEW HTTP.
    before = handler.count("/models")
    assert router2.refresh() is False
    assert handler.count("/models") == before


def test_corrupt_cache_starts_empty_honestly(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{definitely not json", encoding="utf-8")
    router = FreeLLMRouter(cache_path=path, clock=lambda: 1_000_000.0)
    view = router.catalog_dict()
    # Corrupt cache never crashes startup: everything reads back as unknown.
    assert all(p["healthy"] is None for p in view["providers"])
    assert all(p["discovery_ok"] is None for p in view["providers"])


# ---------------------------------------------------------------------------
# ProviderError / error classification for the fallback chain
# ---------------------------------------------------------------------------


def test_error_classification_matches_fallback_semantics():
    assert is_retryable_llm_error(ProviderError("p", "x", status_code=429))
    assert is_retryable_llm_error(ProviderError("p", "x", status_code=503))
    assert not is_retryable_llm_error(ProviderError("p", "x", status_code=400))
    assert not is_retryable_llm_error(ProviderError("p", "x", status_code=401))
    assert is_retryable_llm_error(FreeLLMUnavailableError("all failed"))
    assert is_retryable_llm_error(httpx.ConnectError("down"))


# ---------------------------------------------------------------------------
# ChatFreeLLM (LangChain integration through the same seam)
# ---------------------------------------------------------------------------


def _fresh_model(**kwargs) -> ChatFreeLLM:
    kwargs.setdefault("model", "auto")
    kwargs.setdefault("max_tokens", 64)
    return ChatFreeLLM(**kwargs)


def test_chatfreellm_invoke_returns_honest_message_and_usage(tmp_path, monkeypatch):
    install(
        monkeypatch,
        chat=chat_ok(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    )
    model = _fresh_model(temperature=0.2)
    message = model.invoke([SystemMessage(content="s"), HumanMessage(content="hi")])

    assert message.content == "hello from free"
    assert message.response_metadata["free_llm_provider"] == "vireonix"
    assert message.usage_metadata == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_chatfreellm_usage_omitted_when_provider_returns_none(tmp_path, monkeypatch):
    install(monkeypatch, chat=chat_ok(usage=None))
    message = _fresh_model().invoke([HumanMessage(content="hi")])
    assert message.usage_metadata is None  # never invent token counts


def test_chatfreellm_parses_tool_calls_and_sends_bound_tools(tmp_path, monkeypatch):
    tool_response = chat_ok(
        text="",
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "alpha"}'},
            }
        ],
    )
    handler = install(monkeypatch, chat=tool_response)

    tools = [{"name": "search", "description": "search things", "parameters": {"type": "object"}}]
    bound = _fresh_model().bind_tools(tools, tool_choice="auto")
    message = bound.invoke([HumanMessage(content="find alpha")])

    assert message.tool_calls == [
        {"name": "search", "args": {"query": "alpha"}, "id": "c1", "type": "tool_call"}
    ]
    chat_bodies = [c["json"] for c in handler.calls if "chat/completions" in c["url"] or c["url"].endswith("/openai")]
    assert chat_bodies, "no chat call observed"
    assert any(body and "tools" in body for body in chat_bodies)
    assert any(body and body.get("tool_choice") == "auto" for body in chat_bodies)


def test_chatfreellm_invalid_tool_call_is_not_crashed_or_dropped(tmp_path, monkeypatch):
    tool_response = chat_ok(
        text="",
        tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{broken"}}
        ],
    )
    install(monkeypatch, chat=tool_response)
    message = _fresh_model().invoke([HumanMessage(content="hi")])
    assert message.tool_calls == []
    assert len(message.invalid_tool_calls) == 1
    assert message.invalid_tool_calls[0]["args"] == "{broken"
    assert message.invalid_tool_calls[0]["type"] == "invalid_tool_call"


def test_chatfreellm_stream_single_chunk_fires_token_callback(tmp_path, monkeypatch):
    install(monkeypatch, chat=chat_ok(text="streamed"))

    class Collector(BaseCallbackHandler):
        def __init__(self):
            self.tokens: list[str] = []

        def on_llm_new_token(self, token, **kwargs):
            self.tokens.append(token)

    collector = Collector()
    model = _fresh_model()
    chunks = list(model.stream([HumanMessage(content="hi")], config={"callbacks": [collector]}))

    assert chunks  # exactly one *content* chunk (providers do not stream);
    # langchain may append a trailing metadata/usage chunk with empty content.
    text = "".join(c.content for c in chunks if isinstance(c.content, str))
    assert text == "streamed"
    # Real content tokens arrive exactly once; langchain additionally emits a
    # token callback for its trailing empty metadata chunk — allow empties,
    # never allow duplicated or missing content.
    assert [t for t in collector.tokens if t] == ["streamed"]
    assert chunks[0].response_metadata["free_llm_provider"] == "vireonix"


def test_chatfreellm_async_generate_and_stream(tmp_path, monkeypatch):
    install(monkeypatch, chat=chat_ok(text="async-hello"))

    model = _fresh_model()

    async def run():
        message = await model.ainvoke([HumanMessage(content="hi")])
        chunks = [c async for c in model.astream([HumanMessage(content="hi")])]
        return message, chunks

    message, chunks = asyncio.run(run())
    assert message.content == "async-hello"
    assert chunks
    text = "".join(c.content for c in chunks if isinstance(c.content, str))
    assert text == "async-hello"  # one content chunk, no duplicated/mangled text


def test_chatfreellm_all_free_down_is_retryable_and_chain_serves_backup(tmp_path, monkeypatch):
    install(monkeypatch, chat=chat_http_500)

    class BackupModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "backup"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.messages import AIMessage

            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="backup-served"))])

        def bind_tools(self, tools, tool_choice=None, **kwargs):
            return self

    free = _fresh_model()
    chain = FallbackChatModel(instances=[free, BackupModel()], model_names=["alpha-free", "backup"])
    result = chain.invoke([HumanMessage(content="hi")])
    assert result.content == "backup-served"  # honest failover, not a fabricated free reply

    # bind_tools must travel across the whole chain without breaking it.
    bound_chain = chain.bind_tools([{"name": "search", "description": "d", "parameters": {"type": "object"}}])
    assert isinstance(bound_chain, FallbackChatModel)
    assert bound_chain.invoke([HumanMessage(content="hi")]).content == "backup-served"


def test_factory_builds_chatfreellm_from_config_entry(tmp_path, monkeypatch):
    app_config = AppConfig(
        models=[
            ModelConfig(
                name="alpha-free",
                display_name="Alpha Free",
                description="keyless",
                use="alpha.models.free_router:ChatFreeLLM",
                model="auto",
                max_tokens=64,
                supports_thinking=False,
                supports_reasoning_effort=False,
                when_thinking_enabled=None,
                when_thinking_disabled=None,
                thinking=None,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )
    built = factory_module.create_chat_model("alpha-free", app_config=app_config, attach_tracing=False)
    assert isinstance(built, ChatFreeLLM)
    assert isinstance(built, BaseChatModel)
    assert built.model == "auto"
    assert built.max_tokens == 64
    assert built._llm_type == "free-llm-router"


# ---------------------------------------------------------------------------
# Gateway endpoint (existing models router, additive route)
# ---------------------------------------------------------------------------


def test_free_catalog_route_registered_on_models_router():
    from app.gateway.routers.models import router as models_router

    paths = [route.path for route in models_router.routes]
    assert "/api/models/free/catalog" in paths


def test_free_catalog_endpoint_returns_honest_view(tmp_path, monkeypatch):
    install(monkeypatch)
    reset_free_router()  # fixture already did; keep intent explicit

    from app.gateway.routers.models import free_llm_catalog

    view = asyncio.run(free_llm_catalog(refresh=True))
    assert view["source"] == "alpha-free-llm-router"
    assert view["health_values"].startswith("true=")
    assert "no quality scoring" in view["selection_method"]
    assert len(view["providers"]) == len(PROVIDER_ORDER)
    assert all(p["healthy"] is None or isinstance(p["healthy"], bool) for p in view["providers"])
    assert isinstance(view["eligible_candidates"], list)
    assert "disclaimer" in view


def test_free_catalog_endpoint_wires_refresh_and_probe(monkeypatch):
    import alpha.models.free_router as package_api

    class FakeRouter:
        def __init__(self):
            self.refreshed = False
            self.probed = False

        def refresh(self, *, force=False):
            self.refreshed = force
            return True

        def probe(self):
            self.probed = True
            return {"vireonix": {"ok": True, "latency_ms": 1.0, "error": None, "note": "n"}}

        def catalog_dict(self):
            return {"source": "alpha-free-llm-router", "refreshed": self.refreshed}

    fake = FakeRouter()
    monkeypatch.setattr(package_api, "get_free_router", lambda: fake)

    from app.gateway.routers.models import free_llm_catalog

    view = asyncio.run(free_llm_catalog(refresh=True, probe=True))
    assert fake.refreshed is True
    assert fake.probed is True
    assert view["probes"]["vireonix"]["ok"] is True


def test_singleton_lifecycle():
    reset_free_router()
    first = get_free_router()
    assert get_free_router() is first
    reset_free_router()
    second = get_free_router()
    assert second is not first
    reset_free_router()
