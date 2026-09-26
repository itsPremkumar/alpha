"""``AgentWorkspaceClient`` must be runnable with zero configuration.

The shipped bug this file exists to prevent
--------------------------------------------
``AgentWorkspaceClient.__init__`` defaulted ``thinking_enabled=True``, while
``alpha.models.factory.create_chat_model`` fails closed when thinking is
requested for a model that declares ``supports_thinking: false``
(``ValueError: Model <name> does not support thinking...``). Both models in the
shipped ``config.yaml`` declare ``supports_thinking: false``, so the default
constructed client -- which is exactly what ``alpha.tui.session.open_session``
builds for ``alpha --json`` -- could not complete a single turn.

Why the default is ``False`` (not "the raise is wrong")
-------------------------------------------------------
The raise is right and stays. It is the fail-closed guard for an *explicit*
misconfiguration: an operator who asks for thinking on a model that cannot do it
should be told, not silently given something else. The evidence is that
``False`` is the intended default everywhere else in the product:

* ``create_chat_model(thinking_enabled: bool = False)`` -- the factory's own
  default is already opt-in;
* ``ModelConfig.supports_thinking`` defaults to ``False``;
* every non-interactive call site passes it explicitly
  (``summarization_middleware``, ``title_middleware``, ``security_scanner``,
  ``subagents/executor``, ``runtime/goal``, ``utils/oneshot_llm`` -- all
  ``thinking_enabled=False``);
* the canonical Gateway assembly path
  (``agents/lead_agent/agent.py:1065``) already treats "thinking requested on a
  non-thinking model" as a *downgrade with a warning*, not a failure.

So the client default was the single outlier, and it is the one that made the
product unrunnable.
"""

from __future__ import annotations

import pytest

from alpha.client import AgentWorkspaceClient
from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.models import factory as factory_module
from alpha.models.factory import create_chat_model

_NON_THINKING_MODEL = ModelConfig(
    name="no-think",
    display_name="No Think",
    description=None,
    use="langchain_openai:ChatOpenAI",
    model="no-think-1",
    api_key="test-key-not-a-secret",
    supports_thinking=False,
    supports_reasoning_effort=False,
    supports_vision=False,
)


def _app_config(*models: ModelConfig) -> AppConfig:
    return AppConfig(
        models=list(models) or [_NON_THINKING_MODEL],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )


# ---------------------------------------------------------------------------
# The default itself
# ---------------------------------------------------------------------------


def test_client_default_thinking_is_false(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr("alpha.client.get_app_config", lambda: _app_config())
        client = AgentWorkspaceClient()
    assert client._thinking_enabled is False


def test_runnable_config_default_thinking_is_false(monkeypatch):
    """The per-run config must agree with the constructor default.

    Two separate ``True`` defaults existed. Fixing only the constructor would
    leave the second one to re-break any caller that supplies its own
    ``RunnableConfig`` without the key.
    """
    with monkeypatch.context() as m:
        m.setattr("alpha.client.get_app_config", lambda: _app_config())
        client = AgentWorkspaceClient()
    config = client._get_runnable_config("thread-1")
    assert config["configurable"]["thinking_enabled"] is False


def test_explicit_true_is_still_honoured(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr("alpha.client.get_app_config", lambda: _app_config())
        client = AgentWorkspaceClient(thinking_enabled=True)
    assert client._thinking_enabled is True
    assert client._get_runnable_config("t")["configurable"]["thinking_enabled"] is True


def test_per_run_override_still_wins(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr("alpha.client.get_app_config", lambda: _app_config())
        client = AgentWorkspaceClient()
    assert client._get_runnable_config("t", thinking_enabled=True)["configurable"]["thinking_enabled"] is True


# ---------------------------------------------------------------------------
# A supports_thinking:false model is usable with zero configuration
# ---------------------------------------------------------------------------


def test_zero_configuration_client_builds_a_non_thinking_model(monkeypatch):
    """The end-to-end shape of the bug, without a network call.

    The shipped crash was at agent-build time, so patching the *model
    construction* (not the request) is the faithful reproduction: it asserts the
    client resolves to a built client instead of raising.
    """
    captured: dict[str, object] = {}

    def _fake_create(name=None, thinking_enabled=False, **kwargs):
        captured["name"] = name
        captured["thinking_enabled"] = thinking_enabled
        return object()

    with monkeypatch.context() as m:
        m.setattr("alpha.client.get_app_config", lambda: _app_config())
        m.setattr("alpha.client.create_chat_model", _fake_create)
        m.setattr("alpha.client.build_middlewares", lambda *a, **k: [])
        client = AgentWorkspaceClient()
        client._ensure_agent(client._get_runnable_config("t"))

    assert captured["thinking_enabled"] is False, "a default client still requests thinking"


def test_ensure_agent_without_the_thinking_key_resolves_to_false(monkeypatch):
    """A hand-built RunnableConfig with no ``thinking_enabled`` must not imply True.

    ``_ensure_agent`` reads the flag straight off ``configurable``; the
    constructor default does not protect a caller that supplies its own config.
    """
    captured: dict[str, object] = {}

    def _fake_create(name=None, thinking_enabled=False, **kwargs):
        captured["thinking_enabled"] = thinking_enabled
        return object()

    with monkeypatch.context() as m:
        m.setattr("alpha.client.get_app_config", lambda: _app_config())
        m.setattr("alpha.client.create_chat_model", _fake_create)
        m.setattr("alpha.client.build_middlewares", lambda *a, **k: [])
        client = AgentWorkspaceClient()
        client._ensure_agent({"configurable": {"thread_id": "t"}})

    assert captured["thinking_enabled"] is False


def test_factory_still_fails_closed_on_explicit_thinking_for_a_non_thinking_model():
    """The guard the default was tripping over is preserved, not deleted.

    A silently-ignored explicit request is worse than a loud failure: the
    operator would never learn their thinking setting did nothing.
    """
    with pytest.raises(ValueError, match="does not support thinking"):
        create_chat_model(
            "no-think",
            thinking_enabled=True,
            attach_tracing=False,
            app_config=_app_config(),
        )


def test_factory_default_is_still_opt_in():
    """``create_chat_model``'s own default, pinned so the two cannot drift apart."""
    import inspect

    signature = inspect.signature(factory_module.create_chat_model)
    assert signature.parameters["thinking_enabled"].default is False


def test_supported_thinking_model_still_builds_with_explicit_true():
    """Opt-in still works: a model that declares support must be usable with True."""
    thinking_model = _NON_THINKING_MODEL.model_copy(
        update={
            "name": "thinker",
            "display_name": "Thinker",
            "model": "thinker-1",
            "supports_thinking": True,
        }
    )
    built = create_chat_model("thinker", thinking_enabled=True, attach_tracing=False, app_config=_app_config(thinking_model))
    assert built is not None


def test_shipped_config_models_declare_supports_thinking_false():
    """Why the default had to change: the shipped models are all non-thinking.

    Reads the operator's real ``config.yaml`` when present. Skipped, not failed,
    without one -- a test that cannot see the shipped config proves nothing about
    it, but it also has nothing to assert.
    """
    from pathlib import Path

    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "config.yaml"
    if not config_path.exists():
        pytest.skip("no config.yaml in this checkout; the shipped-model assertion has nothing to read")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    models = data.get("models") or []
    assert models, "shipped config.yaml declares no models"
    non_thinking = [m["name"] for m in models if not m.get("supports_thinking")]
    assert len(non_thinking) == len(models), (
        "shipped config.yaml now declares at least one thinking model, so the "
        f"default-thinking argument no longer holds (non-thinking: {non_thinking})"
    )
