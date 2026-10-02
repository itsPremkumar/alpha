"""A dangling ``agent_name`` must degrade to a working run, not kill it.

End-to-end debugging found the whole failure chain behind
"The response stream was interrupted":

1. The Web UI binds roster-bot names as ``assistant_id`` on thread creation
   and run streaming (``ChatView.tsx`` -> ``assistant_id: activeBot?.name``),
   but bots live in ``.alpha/bots/roster.json`` while custom agents live in
   ``users/{user}/agents/{name}/`` — two separate namespaces with no sync.
2. ``build_run_config`` forwards any non-default ``assistant_id`` as
   ``agent_name`` into ``configurable`` and ``context``.
3. ``_assemble_lead_agent`` called ``load_agent_config(agent_name)``, which
   raises ``FileNotFoundError`` for a missing agent — aborting assembly, the
   run, and every state read of that thread (``resolve_thread_assistant_id``
   compiles the same graph for ``GET /threads/{id}/state``).

Contract pinned here, at the single chokepoint every path flows through:

- ``FileNotFoundError`` (agent directory/config absent — stale binding,
  deleted agent, or bot-name binding) does **not** fail the run: the
  ``agent_name`` is cleared from ``configurable``, ``context`` *and* the
  merged runtime ``cfg`` so no downstream reader (memory middleware,
  ``setup_agent``, the prompt's self-update section, ``update_agent``
  exposure) can claim a custom agent that does not exist.
- When the dangling name is a **roster bot**, the identity is re-homed to
  ``bot_name`` in the same carriers, so ``DynamicContextMiddleware``'s bot
  roster reminder still fires and the bot chat keeps its context.
- Only *missing* is recoverable: a corrupt config (``ValueError``) keeps
  failing loudly.
- A roster-registry failure never blocks the run (fail-safe lookup).
"""

import logging

import pytest

GHOST_AGENT = "e2e-ghost-agent-zzz9"
GHOST_BOT = "e2e-ghost-bot-zzz9"


def _isolate_from_the_ambient_config(monkeypatch):
    """Mirror of ``TestLeadAgentAssembly._isolate_from_the_ambient_config``.

    Assembly falls back to ``get_app_config()``; without an owned config the
    test would only pass where a usable ``config.yaml`` happens to exist.
    ``create_chat_model`` / ``create_agent`` are stubbed so no provider or
    graph construction is reached.
    """
    from alpha.agents.lead_agent import agent as lead_agent_module
    from alpha.config.app_config import AppConfig
    from alpha.config.model_config import ModelConfig
    from alpha.config.sandbox_config import SandboxConfig

    app_config = AppConfig(
        models=[
            ModelConfig(
                name="missing-fallback-model",
                display_name="missing-fallback-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="missing-fallback-model",
                supports_thinking=False,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)
    return app_config


def _patch_missing_agent(monkeypatch):
    from alpha.agents.lead_agent import agent as lead_agent_module

    def _raise(name, *, user_id=None):
        raise FileNotFoundError(f"Agent directory not found: {name}")

    monkeypatch.setattr(lead_agent_module, "load_agent_config", _raise)


class _FakeBot:
    def __init__(self, name):
        self.name = name
        self.role = "qa"
        self.status = "active"


class _FakeRegistry:
    def __init__(self, names):
        self._names = list(names)

    def list_bots(self):
        return [_FakeBot(n) for n in self._names]


def test_missing_agent_degrades_to_the_default_lead(monkeypatch, caplog):
    """FileNotFoundError must not abort assembly; the identity is cleared."""
    from alpha.agents.lead_agent.agent import assemble_lead_agent

    _isolate_from_the_ambient_config(monkeypatch)
    _patch_missing_agent(monkeypatch)

    config = {
        "configurable": {"thread_id": "t-missing", "agent_name": GHOST_AGENT},
        "context": {"agent_name": GHOST_AGENT},
    }

    with caplog.at_level(logging.WARNING):
        assembly = assemble_lead_agent(config)

    assert assembly.graph is not None
    # No carrier may still claim the missing agent...
    assert "agent_name" not in config["configurable"]
    assert "agent_name" not in config["context"]
    # ...and the name was not quietly re-labelled as a bot.
    assert "bot_name" not in config["context"]
    assert "bot_name" not in config["configurable"]
    # The degradation is operator-visible and names the offender.
    assert any(GHOST_AGENT in r.message for r in caplog.records), "expected a warning naming the missing agent"


def test_missing_agent_clears_agent_name_from_configurable_only_carriers(monkeypatch):
    """The channel path seeds ``configurable`` only; that must clear too."""
    from alpha.agents.lead_agent.agent import assemble_lead_agent

    _isolate_from_the_ambient_config(monkeypatch)
    _patch_missing_agent(monkeypatch)

    config = {"configurable": {"thread_id": "t-chan", "agent_name": GHOST_AGENT}}

    assembly = assemble_lead_agent(config)

    assert assembly.graph is not None
    assert "agent_name" not in config["configurable"]
    assert "context" not in config or "bot_name" not in (config.get("context") or {})


def test_missing_roster_bot_rehomes_identity_to_bot_context(monkeypatch, caplog):
    """A bot bound as assistant_id keeps working *as that bot*.

    Bots are roster personas, not custom agents; when the dangling name is a
    roster bot the run must carry ``bot_name`` so DynamicContextMiddleware
    injects the bot roster reminder.
    """
    import alpha.bots.registry as registry_module
    from alpha.agents.lead_agent.agent import assemble_lead_agent

    _isolate_from_the_ambient_config(monkeypatch)
    _patch_missing_agent(monkeypatch)
    monkeypatch.setattr(registry_module, "get_bot_registry", lambda: _FakeRegistry([GHOST_BOT, "architect"]))

    config = {
        "configurable": {"thread_id": "t-bot", "agent_name": GHOST_BOT},
        "context": {"agent_name": GHOST_BOT},
    }

    with caplog.at_level(logging.INFO):
        assembly = assemble_lead_agent(config)

    assert assembly.graph is not None
    assert "agent_name" not in config["configurable"]
    assert "agent_name" not in config["context"]
    assert config["context"]["bot_name"] == GHOST_BOT
    assert config["configurable"]["bot_name"] == GHOST_BOT
    assert any("bot_name" in r.message for r in caplog.records), "expected the bot re-home to be logged"


def test_roster_lookup_failure_never_blocks_the_run(monkeypatch):
    """The registry is advisory: if it cannot be read, degrade to default."""
    import alpha.bots.registry as registry_module
    from alpha.agents.lead_agent.agent import assemble_lead_agent

    _isolate_from_the_ambient_config(monkeypatch)
    _patch_missing_agent(monkeypatch)

    def _boom():
        raise RuntimeError("roster unreadable")

    monkeypatch.setattr(registry_module, "get_bot_registry", _boom)

    config = {"configurable": {"thread_id": "t-roster-down", "agent_name": GHOST_AGENT}}

    assembly = assemble_lead_agent(config)

    assert assembly.graph is not None
    assert "agent_name" not in config["configurable"]
    assert "bot_name" not in config["configurable"]


def test_corrupt_agent_config_still_fails_loudly(monkeypatch):
    """Only *missing* is recoverable; a parse failure must not be masked."""
    from alpha.agents.lead_agent import agent as lead_agent_module
    from alpha.agents.lead_agent.agent import assemble_lead_agent

    _isolate_from_the_ambient_config(monkeypatch)

    def _raise(name, *, user_id=None):
        raise ValueError(f"Failed to parse agent config {name}")

    monkeypatch.setattr(lead_agent_module, "load_agent_config", _raise)

    config = {"configurable": {"thread_id": "t-corrupt", "agent_name": GHOST_AGENT}}

    with pytest.raises(ValueError, match="Failed to parse agent config"):
        assemble_lead_agent(config)


def test_existing_agent_identity_is_untouched(monkeypatch):
    """Success path regression: a resolvable agent keeps its identity."""
    from alpha.agents.lead_agent import agent as lead_agent_module
    from alpha.agents.lead_agent.agent import assemble_lead_agent
    from alpha.config.agents_config import AgentConfig

    _isolate_from_the_ambient_config(monkeypatch)
    existing = AgentConfig(name="finalis")
    monkeypatch.setattr(lead_agent_module, "load_agent_config", lambda name, *, user_id=None: existing)

    config = {
        "configurable": {"thread_id": "t-ok", "agent_name": "finalis"},
        "context": {"agent_name": "finalis"},
    }

    assembly = assemble_lead_agent(config)

    assert assembly.graph is not None
    assert config["configurable"]["agent_name"] == "finalis"
    assert config["context"]["agent_name"] == "finalis"
    assert "bot_name" not in config["context"]
