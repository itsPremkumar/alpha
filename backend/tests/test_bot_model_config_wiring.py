"""Per-bot model config: profile, registry, factory chain, and Gateway routes.

The unit contract lives in ``test_bot_model_config.py``; this module proves the
wiring end to end:

- ``BotProfile`` carries the block, tolerates legacy/junk payloads, and only
  moves its capability epoch when the block is actually non-empty;
- ``BotRegistry.update_bot``/``clone_bot`` store and copy it (deep, so editing
  a clone cannot mutate the source);
- ``create_chat_model(fallbacks=...)`` *replaces* the primary's declared chain
  and still fails closed on unknown names and cycles;
- the Gateway routes validate against ``models[]``, report every issue in one
  response, and never persist an invalid block;
- the lead agent resolves the plan (request > bot.model_config > bot.model >
  custom agent > default) and hands the primary, the chain, the sampling and
  the effort rung to ``create_chat_model`` with their provenance in run
  metadata and the assembly descriptor.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry
from app.gateway.auth.models import User
from app.gateway.routers import bots as bots_router

# ── Profile ────────────────────────────────────────────────────────────────


def _profile(**kwargs) -> BotProfile:
    return BotProfile(name=kwargs.pop("name", "reviewer"), display_name="Reviewer", role="reviewer", soul="s", **kwargs)


def test_profile_defaults_to_empty_block():
    bot = _profile()
    assert bot.model_config == {}
    assert bot.resolved_model_config().is_empty


def test_profile_from_dict_normalises_a_non_dict_block():
    """An older writer (or a hand-edited file) must not crash the reader."""
    for junk in (None, "text", 42, ["list"]):
        bot = BotProfile.from_dict({"name": "b", "display_name": "B", "role": "r", "soul": "s", "model_config": junk})
        assert bot.model_config == {}


def test_epoch_is_stable_until_the_block_is_non_empty():
    """Adding the field must not churn existing profiles' epochs."""
    bot = _profile()
    before = bot.capability_fingerprint()

    bot.model_config = {}
    assert bot.capability_fingerprint() == before, "empty block must hash byte-identically"

    bot.model_config = {"primary": "flagship"}
    during = bot.capability_fingerprint()
    assert during != before, "a real model config is a capability change"

    bot.model_config = {}
    assert bot.capability_fingerprint() == before, "clearing must restore the original epoch"

    # Same content, different insertion order → same epoch (canonical JSON).
    bot.model_config = {"primary": "flagship", "sampling": {"temperature": 0.1}}
    first = bot.capability_fingerprint()
    bot.model_config = {"sampling": {"temperature": 0.1}, "primary": "flagship"}
    assert bot.capability_fingerprint() == first


def test_resolved_model_config_never_raises_on_junk():
    bot = _profile()
    bot.model_config = {"counsel": "broken", "mixture": 5, "fallbacks": "not-a-list"}
    cfg = bot.resolved_model_config()
    assert cfg.counsel is None and cfg.mixture is None and cfg.fallbacks == ()


# ── Registry ───────────────────────────────────────────────────────────────


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.registry as registry_mod

    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)
    yield BotRegistry(storage_path=tmp_path / "roster.json")
    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)


def test_update_bot_stores_and_clears_the_block(registry):
    registry.register(_profile(name="alpha"))

    updated = registry.update_bot("alpha", model_config={"primary": "flagship"})
    assert updated is not None and updated.model_config == {"primary": "flagship"}

    # ``None`` means "leave alone" — the convention every field follows.
    still = registry.update_bot("alpha", display_name="Renamed")
    assert still is not None and still.model_config == {"primary": "flagship"}

    # ``{}`` clears it.
    cleared = registry.update_bot("alpha", model_config={})
    assert cleared is not None and cleared.model_config == {}


def test_clone_bot_deep_copies_the_block(registry):
    source_cfg = {"primary": "flagship", "fallbacks": ["cheap"], "sampling": {"temperature": 0.1}}
    registry.register(_profile(name="source", model_config=source_cfg))
    clone = registry.clone_bot("source", "copy")

    assert clone.model_config == source_cfg
    clone.model_config["sampling"]["temperature"] = 0.9
    clone.model_config["fallbacks"].append("extra")
    # The source must be untouched: a clone shares no mutable state with it.
    assert registry.get_bot("source").model_config == source_cfg


def test_clone_can_override_the_plain_model_field(registry):
    registry.register(_profile(name="source", model="cheap", model_config={"primary": "flagship"}))
    clone = registry.clone_bot("source", "copy", model="local-model")
    assert clone.model == "local-model"
    assert clone.model_config == {"primary": "flagship"}


# ── Factory: fallback chain override ───────────────────────────────────────


def _chain(monkeypatch, **kwargs):
    """Call ``_resolve_chain_configs`` against a fake config of three models."""
    from alpha.models import factory as factory_mod

    class _Cfg:
        def __init__(self):
            self._by_name = {}

        def add(self, name, fallbacks=()):
            from alpha.config.model_config import ModelConfig

            entry = ModelConfig(name=name, model=name, use="langchain_openai:ChatOpenAI")
            entry.fallbacks = list(fallbacks)
            self._by_name[name] = entry
            return self

        def get_model_config(self, name):
            return self._by_name.get(name)

    config = _Cfg().add("primary", fallbacks=["declared-b"]).add("declared-b").add("bot-b").add("bot-c")
    return [m.name for m in factory_mod._resolve_chain_configs("primary", config, **kwargs)]


def test_override_replaces_the_declared_chain():
    assert _chain(None) == ["primary", "declared-b"], "no override → declared chain"
    assert _chain(None, fallbacks=["bot-b", "bot-c"]) == ["primary", "bot-b", "bot-c"], "override replaces, not appends"


def test_override_keeps_transitive_expansion_and_dedupe():
    from alpha.models import factory as factory_mod

    class _Cfg:
        def __init__(self):
            from alpha.config.model_config import ModelConfig

            self._by_name = {
                "primary": ModelConfig(name="primary", model="primary", use="langchain_openai:ChatOpenAI"),
                "b": ModelConfig(name="b", model="b", use="langchain_openai:ChatOpenAI"),
                "c": ModelConfig(name="c", model="c", use="langchain_openai:ChatOpenAI"),
            }
            self._by_name["b"].fallbacks = ["c"]
            # The override names c first and b second: b's own chain re-adds c,
            # which must collapse rather than duplicate.
            self._by_name["primary"].fallbacks = ["ignored"]

        def get_model_config(self, name):
            return self._by_name.get(name)

    names = [m.name for m in factory_mod._resolve_chain_configs("primary", _Cfg(), fallbacks=["c", "b"])]
    assert names == ["primary", "c", "b"]


def test_override_still_fails_closed_on_unknown_and_cycles():
    from alpha.models import factory as factory_mod

    class _Cfg:
        def __init__(self):
            from alpha.config.model_config import ModelConfig

            self._by_name = {
                "primary": ModelConfig(name="primary", model="primary", use="langchain_openai:ChatOpenAI"),
                "a": ModelConfig(name="a", model="a", use="langchain_openai:ChatOpenAI"),
            }
            # ``a`` declares ``primary`` as its fallback: an override naming
            # ``a`` would close the loop primary → a → primary.
            self._by_name["a"].fallbacks = ["primary"]

        def get_model_config(self, name):
            return self._by_name.get(name)

    with pytest.raises(ValueError, match="unknown fallback model"):
        factory_mod._resolve_chain_configs("primary", _Cfg(), fallbacks=["ghost"])
    with pytest.raises(ValueError, match="cycle"):
        factory_mod._resolve_chain_configs("primary", _Cfg(), fallbacks=["a"])


# ── Gateway routes ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_bot_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.ephemeral as ephemeral_mod
    import alpha.bots.health as health_mod
    import alpha.bots.registry as registry_mod

    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)
    monkeypatch.setattr(ephemeral_mod, "_ephemeral_manager", None)
    monkeypatch.setattr(health_mod, "_global_monitor", None)
    yield
    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)


def _build_app() -> FastAPI:
    def _admin() -> User:
        return User(email="admin@example.com", password_hash="x", system_role="admin", id=uuid4())

    app = make_authed_test_app(user_factory=_admin)
    app.include_router(bots_router.router)
    return app


@pytest.fixture()
def client(monkeypatch):
    # A known model set so validation has something to check names against.
    monkeypatch.setattr(
        "app.gateway.routers.bots._known_model_names",
        lambda: {"flagship", "cheap", "local-model"},
    )
    with TestClient(_build_app()) as test_client:
        yield test_client


def _ensure(client: TestClient, name: str) -> None:
    resp = client.post(f"/api/bots/{name}/ensure", json={"display_name": name.title(), "role": "Reviewer"})
    assert resp.status_code == 200, resp.text


def test_get_returns_empty_config_and_full_detail(client):
    _ensure(client, "reviewer")
    resp = client.get("/api/bots/reviewer/model-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config"] == {}
    assert body["valid"] is True and body["issues"] == []
    resolved = body["resolved"]
    assert "precedence" in resolved and "limits" in resolved
    assert resolved["limits"]["max_fallbacks"] == 5
    assert resolved["plan"]["primary_source"] in {"default", "bot.model"}
    # The picker's option set is the validated `models[]` names, sorted.
    assert body["known_models"] == ["cheap", "flagship", "local-model"]


def test_put_validates_stores_and_reports_every_issue(client):
    _ensure(client, "reviewer")

    bad = client.put(
        "/api/bots/reviewer/model-config",
        json={"config": {"primary": "ghost", "fallbacks": ["ghost"], "sampling": {"api_key": "sk-x"}}},
    )
    assert bad.status_code == 422, bad.text
    issues = bad.json()["detail"]["issues"]
    codes = {i["code"] for i in issues}
    assert "unknown_model" in codes
    assert "secret_key" in codes
    # Nothing invalid was persisted.
    assert client.get("/api/bots/reviewer/model-config").json()["config"] == {}

    good = client.put(
        "/api/bots/reviewer/model-config",
        json={"config": {"primary": "flagship", "fallbacks": ["cheap", "local-model"], "sampling": {"temperature": 0.2}}},
    )
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["valid"] is True
    assert body["config"]["primary"] == "flagship"
    assert body["resolved"]["plan"]["primary_source"] == "bot.model_config"
    assert body["resolved"]["plan"]["fallbacks"] == ["cheap", "local-model"]

    # It is visible from the ordinary profile read too (the detail panel's source).
    profile = client.get("/api/bots/reviewer").json()
    assert profile["model_config"]["primary"] == "flagship"

    cleared = client.delete("/api/bots/reviewer/model-config")
    assert cleared.status_code == 200 and cleared.json()["cleared"] is True
    assert client.get("/api/bots/reviewer/model-config").json()["config"] == {}


def test_preview_never_saves(client):
    _ensure(client, "reviewer")
    resp = client.post(
        "/api/bots/reviewer/model-config/preview",
        json={"config": {"primary": "cheap"}, "bot_model": "local-model"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["resolved"]["plan"]["primary"] == "cheap"
    assert body["resolved"]["plan"]["primary_source"] == "bot.model_config"
    # Nothing persisted by the preview.
    assert client.get("/api/bots/reviewer/model-config").json()["config"] == {}


def test_routes_404_for_unknown_bot(client):
    assert client.get("/api/bots/nope/model-config").status_code == 404
    assert client.put("/api/bots/nope/model-config", json={"config": {}}).status_code == 404
    assert client.delete("/api/bots/nope/model-config").status_code == 404


def test_model_config_survives_clone_route(client):
    _ensure(client, "reviewer")
    client.put("/api/bots/reviewer/model-config", json={"config": {"primary": "flagship", "fallbacks": ["cheap"]}})
    clone = client.post("/api/bots/clone1/clone", json={"source": "reviewer"})
    assert clone.status_code == 201, clone.text
    assert clone.json()["model_config"] == {"primary": "flagship", "fallbacks": ["cheap"]}


# ── Lead agent: plan → create_chat_model ───────────────────────────────────


def _lead_app_config():
    from alpha.config.app_config import AppConfig
    from alpha.config.loop_detection_config import LoopDetectionConfig
    from alpha.config.model_config import ModelConfig
    from alpha.config.sandbox_config import SandboxConfig

    def _m(name: str):
        return ModelConfig(
            name=name,
            display_name=name,
            description=None,
            use="langchain_openai:ChatOpenAI",
            model=name,
            supports_thinking=False,
            supports_vision=False,
        )

    return AppConfig(
        models=[_m("flagship"), _m("cheap"), _m("safe-model")],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
        loop_detection=LoopDetectionConfig(),
    )


def _run_lead(monkeypatch, run_config, bot=None):
    """Assemble the lead agent against a one-bot roster; capture the factory call.

    Returns the captured ``create_chat_model`` kwargs plus the run metadata the
    assembly stamped onto ``run_config`` and the descriptor's ``model_plan``.
    """
    import alpha.bots.registry as registry_mod
    import alpha.tools as tools_module
    from alpha.agents.lead_agent import agent as lead_agent_module

    app_config = _lead_app_config()

    class _Roster:
        def get_bot(self, name):
            return bot if bot is not None and name == bot.name else None

        def list_bots(self):
            return [bot] if bot is not None else []

    monkeypatch.setattr(registry_mod, "get_bot_registry", lambda: _Roster())
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    # The assembly descriptor is only built when an observer wants it; fake one
    # so the ``model_plan`` block can be asserted (notification stays a no-op —
    # the fake registers no actual observer).
    monkeypatch.setattr(
        "alpha.extensions.get_agent_build_extensions",
        lambda: SimpleNamespace(has_agent_assembly_observers=True, agent_assembly_observers=(), app_store=None),
    )
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "_load_enabled_available_skills", lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "build_middlewares", lambda config, model_name, agent_name=None, **kwargs: [])

    captured: dict[str, object] = {}

    def _fake_create_chat_model(*, name, thinking_enabled, reasoning_effort=None, app_config=None, attach_tracing=True, model_overrides=None, retries_orchestrated=False, fallbacks=None):
        captured.update(
            name=name,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            model_overrides=model_overrides,
            fallbacks=fallbacks,
        )
        return object()

    monkeypatch.setattr(lead_agent_module, "create_chat_model", _fake_create_chat_model)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    assembly = lead_agent_module._assemble_lead_agent(run_config, app_config=app_config)
    captured["metadata"] = dict(run_config.get("metadata") or {})
    captured["model_plan"] = assembly.descriptor.effective_policies["model_plan"]
    return captured


def test_lead_agent_applies_the_bot_block_to_the_factory(monkeypatch):
    bot = _profile(name="helper", model="cheap", model_config={"primary": "flagship", "fallbacks": ["cheap"], "sampling": {"temperature": 0.2}})
    captured = _run_lead(monkeypatch, {"context": {"bot_name": "helper"}}, bot=bot)

    assert captured["name"] == "flagship"
    assert tuple(captured["fallbacks"] or ()) == ("cheap",), "bot fallbacks must reach the factory as the chain override"
    assert captured["model_overrides"] == {"temperature": 0.2}

    meta = captured["metadata"]
    assert meta["model_source"] == "bot.model_config"
    assert list(meta["model_fallbacks"]) == ["cheap"]
    assert meta["model_fallbacks_source"] == "bot.model_config"
    # The descriptor carries the same plan so an inspector can answer "why
    # this model?" without re-deriving the precedence.
    assert captured["model_plan"]["primary"] == "flagship"
    assert captured["model_plan"]["primary_source"] == "bot.model_config"


def test_request_model_beats_the_bot_block(monkeypatch):
    bot = _profile(name="helper", model="cheap", model_config={"primary": "flagship"})
    captured = _run_lead(monkeypatch, {"context": {"bot_name": "helper", "model_name": "safe-model"}}, bot=bot)

    assert captured["name"] == "safe-model"
    assert captured["metadata"]["model_source"] == "request"
    assert captured["model_plan"]["primary_source"] == "request"


def test_bot_block_beats_the_plain_bot_model(monkeypatch):
    bot = _profile(name="helper", model="cheap", model_config={"primary": "flagship"})
    captured = _run_lead(monkeypatch, {"context": {"bot_name": "helper"}}, bot=bot)

    assert captured["name"] == "flagship"
    assert captured["metadata"]["model_source"] == "bot.model_config"


def test_invalid_block_is_ignored_loudly_and_the_plain_model_applies(monkeypatch, caplog):
    """A block written before validation (or a model deleted from
    ``config.yaml`` since) must not steer the run — but degrading must be
    loud, and the bot's plain ``model`` still applies."""
    bot = _profile(name="helper", model="cheap", model_config={"primary": "ghost"})

    with caplog.at_level("ERROR"):
        captured = _run_lead(monkeypatch, {"context": {"bot_name": "helper"}}, bot=bot)

    assert captured["name"] == "cheap"
    assert captured["metadata"]["model_source"] == "bot.model"
    assert "invalid model_config" in caplog.text
    assert "ghost" in caplog.text, "the log must name the offending model"


def test_bot_effort_travels_through_the_dedicated_kwarg(monkeypatch):
    bot = _profile(name="helper", model_config={"primary": "flagship", "sampling": {"reasoning_effort": "high", "temperature": 0.7}})
    captured = _run_lead(monkeypatch, {"context": {"bot_name": "helper"}}, bot=bot)

    assert captured["name"] == "flagship"
    assert captured["reasoning_effort"] == "high"
    # One resolution owner for the rung: it must not also appear inside the
    # constructor-override merge.
    assert "reasoning_effort" not in (captured["model_overrides"] or {})
    assert captured["model_overrides"]["temperature"] == 0.7
    assert captured["metadata"]["reasoning_effort"] == "high"


def test_without_a_bot_block_the_global_default_applies(monkeypatch):
    captured = _run_lead(monkeypatch, {"context": {}}, bot=None)

    assert captured["name"] == "flagship", "first models[] entry is the default"
    assert captured["metadata"]["model_source"] == "default"
    assert captured["fallbacks"] is None, "no bot chain → the primary's own declared chain"
