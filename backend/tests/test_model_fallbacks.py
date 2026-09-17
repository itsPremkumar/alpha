"""Tests for provider failover chains (O1) and provider profiles (H6).

All tests are offline: scripted chat models stand in for provider clients and
faults are synthetic exception objects (duck-typed ``status_code``), so no
network, credentials, or provider SDKs are required.
"""

import logging
from typing import ClassVar

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from pydantic import ValidationError

from agent_workspace.config.app_config import AppConfig
from agent_workspace.config.model_config import ModelConfig, ProviderConfig
from agent_workspace.models import factory as factory_module
from agent_workspace.models.fallback import (
    FallbackChatModel,
    ModelFallbackExhaustedError,
    is_retryable_llm_error,
)


class FakeStatusError(Exception):
    """Duck-typed provider HTTP error (no SDK import needed)."""

    def __init__(self, status: int):
        super().__init__(f"fake provider error {status}")
        self.status_code = status


class ChattyStatusError(Exception):
    """Retryable error whose message looks secret-bearing (redaction probe)."""

    def __init__(self):
        super().__init__("upstream said key=sk-test-only-secret-xyz is invalid")
        self.status_code = 500


class ScriptedChatModel(factory_module.BaseChatModel):
    """Chat model replaying a class-level script.

    ``_SCRIPT`` items are exceptions (raised) or answer strings. Each
    ``use=`` test reference gets its own subclass, so scripts never leak
    across tests. ``_SEEN_KWARGS`` records every ``_generate`` call's kwargs
    so tests can prove bound tools travel into members.
    """

    _SCRIPT: ClassVar[list] = []
    _SEEN_KWARGS: ClassVar[list] = []
    stream_script: list | None = None
    temperature: float | None = None
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        from langchain_core.runnables import RunnableBinding

        return RunnableBinding(bound=self, kwargs={"tools": list(tools)})

    def _next_action(self):
        script = type(self)._SCRIPT
        action = script[min(self.calls, len(script) - 1)]
        self.calls += 1
        return action

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self)._SEEN_KWARGS.append(dict(kwargs))
        action = self._next_action()
        if isinstance(action, BaseException):
            raise action
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(action)))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        if self.stream_script is None:
            result = self._generate(messages, stop, run_manager, **kwargs)
            for generation in result.generations:
                yield ChatGenerationChunk(message=AIMessageChunk(content=generation.message.content))
            return
        for op in self.stream_script:
            kind, payload = op
            if kind == "chunk":
                yield ChatGenerationChunk(message=AIMessageChunk(content=payload))
            elif kind == "raise":
                raise payload
            else:  # pragma: no cover - test-only helper
                raise AssertionError(f"unknown stream op {kind!r}")


def _make_member(script, **fields) -> ScriptedChatModel:
    cls = type(
        "ScriptedMember",
        (ScriptedChatModel,),
        {"_SCRIPT": list(script), "__module__": __name__},
    )
    return cls(**fields)


def _messages() -> list[BaseMessage]:
    return [HumanMessage(content="hello")]


@tool
def _plus_one(value: int) -> int:
    """Add one."""
    return value + 1


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_statuses(status):
    assert is_retryable_llm_error(FakeStatusError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_http_statuses(status):
    assert is_retryable_llm_error(FakeStatusError(status)) is False


def test_retryable_transport_errors():
    assert is_retryable_llm_error(TimeoutError("timed out")) is True
    assert is_retryable_llm_error(ConnectionError("reset")) is True

    class APITimeoutError(Exception):
        pass

    assert is_retryable_llm_error(APITimeoutError("slow")) is True


def test_non_retryable_generic_errors():
    assert is_retryable_llm_error(ValueError("bad request shape")) is False
    assert is_retryable_llm_error(RuntimeError("content blocked")) is False


def test_base_exceptions_are_never_swallowed():
    wrapper = FallbackChatModel(
        instances=[_make_member([KeyboardInterrupt()])],
        model_names=["primary"],
    )
    with pytest.raises(KeyboardInterrupt):
        wrapper.invoke(_messages())


# ---------------------------------------------------------------------------
# FallbackChatModel behaviour
# ---------------------------------------------------------------------------


def test_first_success_serves_without_touching_backups():
    backup = _make_member(["backup-answer"])
    wrapper = FallbackChatModel(
        instances=[_make_member(["primary-answer"]), backup],
        model_names=["primary", "backup"],
    )
    result = wrapper.invoke(_messages())
    assert result.content == "primary-answer"
    assert backup.calls == 0
    assert wrapper.get_last_effective_model() == "primary"


def test_retryable_failure_fails_over_to_backup():
    wrapper = FallbackChatModel(
        instances=[_make_member([FakeStatusError(500)]), _make_member(["backup-answer"])],
        model_names=["primary", "backup"],
    )
    result = wrapper.invoke(_messages())
    assert result.content == "backup-answer"
    assert wrapper.get_last_effective_model() == "backup"


def test_non_retryable_failure_raises_without_failover():
    backup = _make_member(["backup-answer"])
    wrapper = FallbackChatModel(
        instances=[_make_member([FakeStatusError(400)]), backup],
        model_names=["primary", "backup"],
    )
    with pytest.raises(FakeStatusError):
        wrapper.invoke(_messages())
    assert backup.calls == 0


def test_exhausted_chain_reports_attempts_without_secrets(caplog):
    wrapper = FallbackChatModel(
        instances=[_make_member([ChattyStatusError()]), _make_member([FakeStatusError(429)])],
        model_names=["primary-db", "backup-db"],
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ModelFallbackExhaustedError) as exc_info:
            wrapper.invoke(_messages())
    message = str(exc_info.value)
    assert "primary-db" in message and "backup-db" in message
    assert [name for name, _ in exc_info.value.attempts] == ["primary-db", "backup-db"]
    assert "sk-test-only-secret-xyz" not in message
    assert "sk-test-only-secret-xyz" not in caplog.text


def test_streaming_fails_over_to_backup():
    wrapper = FallbackChatModel(
        instances=[_make_member([FakeStatusError(503)]), _make_member(["streamed-backup"])],
        model_names=["primary", "backup"],
    )
    chunks = list(wrapper.stream(_messages()))
    assert "".join(chunk.content for chunk in chunks) == "streamed-backup"
    assert wrapper.get_last_effective_model() == "backup"


def test_mid_stream_failure_restarts_on_backup():
    primary = _make_member([])
    primary.stream_script = [("chunk", "partial-"), ("raise", FakeStatusError(500))]
    wrapper = FallbackChatModel(
        instances=[primary, _make_member(["full-backup"])],
        model_names=["primary", "backup"],
    )
    chunks = list(wrapper.stream(_messages()))
    assert "".join(chunk.content for chunk in chunks) == "partial-full-backup"


def test_bind_tools_compatible():
    ScriptedChatModel._SEEN_KWARGS.clear()
    wrapper = FallbackChatModel(
        instances=[_make_member(["bound-answer"])],
        model_names=["primary"],
    )
    bound = wrapper.bind_tools([_plus_one])
    assert bound.invoke(_messages()).content == "bound-answer"
    # Bound tools travel inside the member binding, reaching _generate kwargs.
    assert any("tools" in seen for seen in ScriptedChatModel._SEEN_KWARGS)


def test_bind_tools_failover_keeps_tools():
    ScriptedChatModel._SEEN_KWARGS.clear()
    primary_cls = type(
        "ScriptedFailing",
        (ScriptedChatModel,),
        {"_SCRIPT": [FakeStatusError(500)], "__module__": __name__},
    )
    backup_cls = type(
        "ScriptedServing",
        (ScriptedChatModel,),
        {"_SCRIPT": ["served-with-tools"], "__module__": __name__},
    )
    wrapper = FallbackChatModel(
        instances=[primary_cls(), backup_cls()],
        model_names=["primary", "backup"],
    )
    bound = wrapper.bind_tools([_plus_one])
    assert bound.invoke(_messages()).content == "served-with-tools"
    assert bound.get_last_effective_model() == "backup"
    assert any("tools" in seen for seen in backup_cls._SEEN_KWARGS)


def test_constructor_rejects_mismatched_inputs():
    with pytest.raises(ValueError, match="1:1"):
        FallbackChatModel(instances=[_make_member(["x"])], model_names=[])


# ---------------------------------------------------------------------------
# Factory integration (monkeypatched model classes, real AppConfig)
# ---------------------------------------------------------------------------


def _model_entry(name: str, use: str = "test:Scripted", **overrides) -> ModelConfig:
    return ModelConfig(name=name, model=f"{name}-deployment", use=use, **overrides)


def _app_config(models: list[ModelConfig], providers: dict | None = None) -> AppConfig:
    return AppConfig(
        sandbox={"use": "test:DummySandbox"},
        models=models,
        providers=providers or {},
    )


@pytest.fixture
def scripted_classes(monkeypatch):
    """Route each `use="test:*"` reference at a fresh scripted subclass."""

    def _resolve(use: str, base):
        return type(
            f"Scripted_{use.replace(':', '_')}",
            (ScriptedChatModel,),
            {"__module__": __name__},
        )

    monkeypatch.setattr(factory_module, "resolve_class", _resolve)
    monkeypatch.setattr(factory_module, "build_tracing_callbacks", lambda: [])


def test_single_model_returns_plain_instance(scripted_classes):
    config = _app_config([_model_entry("solo")])
    instance = factory_module.create_chat_model("solo", app_config=config, attach_tracing=False)
    assert type(instance).__name__ == "Scripted_test_Scripted"
    assert not isinstance(instance, FallbackChatModel)


def test_factory_failover_chain_serves_backup(scripted_classes, monkeypatch):
    config = _app_config(
        [
            _model_entry("primary", use="test:Primary", fallbacks=["backup"]),
            _model_entry("backup", use="test:Backup"),
        ]
    )
    seen: dict[str, type] = {}

    def _resolve(use: str, base):
        if use not in seen:
            seen[use] = type(f"Scripted_{use.replace(':', '_')}", (ScriptedChatModel,), {"__module__": __name__})
        return seen[use]

    monkeypatch.setattr(factory_module, "resolve_class", _resolve)

    instance = factory_module.create_chat_model("primary", app_config=config, attach_tracing=False)
    assert isinstance(instance, FallbackChatModel)
    assert instance.fallback_model_names == ["primary", "backup"]
    # Drive the failure deterministically: primary raises, backup answers.
    seen["test:Primary"]._SCRIPT = [FakeStatusError(500)]
    seen["test:Backup"]._SCRIPT = ["backup served"]
    assert instance.invoke(_messages()).content == "backup served"
    assert instance.get_last_effective_model() == "backup"


def test_factory_unknown_fallback_name_is_actionable(scripted_classes):
    config = _app_config([_model_entry("primary", fallbacks=["ghost"])])
    with pytest.raises(ValueError, match="unknown fallback model 'ghost'"):
        factory_module.create_chat_model("primary", app_config=config, attach_tracing=False)


def test_factory_cycle_is_rejected(scripted_classes):
    config = _app_config(
        [
            _model_entry("aaa", fallbacks=["bbb"]),
            _model_entry("bbb", fallbacks=["aaa"]),
        ]
    )
    with pytest.raises(ValueError, match="[Cc]ycle"):
        factory_module.create_chat_model("aaa", app_config=config, attach_tracing=False)


def test_factory_fallback_list_capped_at_five():
    with pytest.raises(ValidationError, match="fallbacks"):
        _model_entry("primary", fallbacks=["a", "b", "c", "d", "e", "f"])


def test_factory_thinking_skips_unsupported_members(scripted_classes, caplog):
    config = _app_config(
        [
            _model_entry("thinker", supports_thinking=True, fallbacks=["plain"]),
            _model_entry("plain", supports_thinking=False),
        ]
    )
    with caplog.at_level(logging.WARNING):
        instance = factory_module.create_chat_model("thinker", thinking_enabled=True, app_config=config, attach_tracing=False)
    # Only one capable member remains: plain client, identical to pre-chain behaviour.
    assert not isinstance(instance, FallbackChatModel)
    assert any("plain" in record.message for record in caplog.records)


def test_factory_thinking_with_no_capable_member_raises(scripted_classes):
    config = _app_config([_model_entry("plain", supports_thinking=False)])
    with pytest.raises(ValueError, match="does not support thinking"):
        factory_module.create_chat_model("plain", thinking_enabled=True, app_config=config, attach_tracing=False)


def test_factory_overrides_apply_to_every_member(scripted_classes, monkeypatch):
    config = _app_config(
        [
            _model_entry("primary", use="test:P1", fallbacks=["backup"]),
            _model_entry("backup", use="test:P2"),
        ]
    )
    built: list = []
    real_resolve = factory_module.resolve_class

    def _resolve(use: str, base):
        cls = real_resolve(use, base)

        orig_init = cls.__init__

        def __init__(self, **data):
            built.append(data)
            orig_init(self, **data)

        return type(f"Recording_{use.replace(':', '_')}", (cls,), {"__init__": __init__, "__module__": __name__})

    monkeypatch.setattr(factory_module, "resolve_class", _resolve)
    instance = factory_module.create_chat_model(
        "primary",
        app_config=config,
        attach_tracing=False,
        model_overrides={"temperature": 0.1},
    )
    assert isinstance(instance, FallbackChatModel)
    assert len(built) == 2
    assert all(kwargs.get("temperature") == 0.1 for kwargs in built)


def test_factory_missing_use_without_provider_is_actionable(scripted_classes):
    entry = _model_entry("bare")
    entry.use = None
    config = _app_config([entry])
    with pytest.raises(ValueError, match="neither `use` nor a provider"):
        factory_module.create_chat_model("bare", app_config=config, attach_tracing=False)


# ---------------------------------------------------------------------------
# Provider profiles
# ---------------------------------------------------------------------------


def test_provider_defaults_merge_under_model_keys():
    config = _app_config(
        [ModelConfig(name="m", model="m-dep", provider="p", timeout=30)],
        providers={
            "p": ProviderConfig(
                name="p",
                use="test:Scripted",
                timeout=600,
                api_key="provider-key",
            )
        },
    )
    entry = config.get_model_config("m")
    assert entry is not None
    settings = factory_module._effective_model_settings(entry, config)
    assert settings["timeout"] == 30
    assert settings["api_key"] == "provider-key"
    assert settings["model"] == "m-dep"
    assert "name" not in settings and "provider" not in settings
    assert factory_module._resolve_effective_use(entry, config) == "test:Scripted"


def test_provider_missing_use_falls_back_to_none():
    config = _app_config(
        [ModelConfig(name="m", model="m-dep", use="test:Direct", provider="p")],
        providers={"p": ProviderConfig(name="p", use="test:FromProvider")},
    )
    entry = config.get_model_config("m")
    assert entry is not None
    # Model-level `use` wins over the provider default.
    assert factory_module._resolve_effective_use(entry, config) == "test:Direct"


def test_unknown_provider_reference_is_actionable():
    config = _app_config([ModelConfig(name="m", model="m-dep", provider="ghost")])
    entry = config.get_model_config("m")
    assert entry is not None
    with pytest.raises(ValueError, match="unknown provider 'ghost'"):
        factory_module._effective_model_settings(entry, config)
