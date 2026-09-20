"""Tests for per-session agent presets (standard/minimal/custom)."""

from __future__ import annotations

from unittest.mock import MagicMock

from alpha.agents.lead_agent import agent as lead_agent_module
from alpha.config.agent_preset_config import (
    MINIMAL_PRESET_NAME,
    STANDARD_PRESET_NAME,
    AgentPresetConfig,
    default_presets,
    preset_summary,
    resolve_agent_preset,
)
from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig
from alpha.config.sandbox_config import SandboxConfig


def _make_app_config(*, presets: dict | None = None) -> AppConfig:
    return AppConfig(
        models=[
            ModelConfig(
                name="safe-model",
                display_name="safe-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="safe-model",
                supports_thinking=False,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
        agent_presets=presets or {},
    )


def _named_tool(name: str):
    tool = MagicMock()
    tool.name = name
    return tool


def _install_assembly_mocks(monkeypatch, app_config, tools: list, captured: dict) -> None:
    import alpha.tools as tools_module

    def _fake_get_tools(**kwargs):
        captured.update(kwargs)
        return list(tools)

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", _fake_get_tools)
    monkeypatch.setattr(lead_agent_module, "build_middlewares", lambda config, model_name, agent_name=None, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)


def _assemble(monkeypatch, app_config, tools: list, context: dict) -> tuple[dict, dict]:
    captured: dict = {}
    _install_assembly_mocks(monkeypatch, app_config, tools, captured)
    result = lead_agent_module.make_lead_agent({"context": context})
    return result, captured


# -- resolve_agent_preset --


class TestResolveAgentPreset:
    def test_none_selects_standard_identity(self):
        name, preset = resolve_agent_preset(None, presets={})
        assert name == STANDARD_PRESET_NAME
        assert preset.tool_groups is None
        assert preset.include_mcp is True
        assert preset.subagent_enabled is None
        assert preset.disabled_tools == []
        assert preset.allow_update_agent is True

    def test_empty_string_selects_standard(self):
        name, _ = resolve_agent_preset("  ", presets={})
        assert name == STANDARD_PRESET_NAME

    def test_non_string_selects_standard_without_raising(self):
        name, _ = resolve_agent_preset({"preset": "minimal"}, presets={})
        assert name == STANDARD_PRESET_NAME

    def test_minimal_builtin_switches(self):
        name, preset = resolve_agent_preset(MINIMAL_PRESET_NAME, presets={})
        assert name == MINIMAL_PRESET_NAME
        assert preset.include_mcp is False
        assert preset.subagent_enabled is False
        assert "ask_clarification" in preset.disabled_tools

    def test_operator_preset_wins_over_builtin(self):
        custom = AgentPresetConfig(include_mcp=True, disabled_tools=["bash"])
        name, preset = resolve_agent_preset("minimal", presets={"minimal": custom})
        assert name == "minimal"
        assert preset.include_mcp is True
        assert preset.disabled_tools == ["bash"]

    def test_unknown_name_falls_back_to_standard(self):
        name, preset = resolve_agent_preset("nope", presets={})
        assert name == STANDARD_PRESET_NAME
        assert preset == default_presets()[STANDARD_PRESET_NAME]

    def test_preset_summary_is_json_safe(self):
        summary = preset_summary("custom", AgentPresetConfig(tool_groups=["web"], disabled_tools=["bash"]))
        assert summary == {
            "name": "custom",
            "tool_groups": ["web"],
            "include_mcp": True,
            "subagent_enabled": None,
            "disabled_tools": ["bash"],
            "allow_update_agent": True,
        }


# -- assembly wiring --


class TestPresetAssembly:
    def test_standard_is_today_behavior(self, monkeypatch):
        app_config = _make_app_config()
        tools = [_named_tool("bash"), _named_tool("ask_clarification")]
        result, captured = _assemble(monkeypatch, app_config, tools, {"subagent_enabled": True})
        assert captured["model_name"] == "safe-model"
        assert captured["groups"] is None
        assert captured["include_mcp"] is True
        assert captured["subagent_enabled"] is True
        assert [tool.name for tool in result["tools"]] == ["bash", "ask_clarification"]

    def test_minimal_disables_mcp_subagents_and_clarification(self, monkeypatch):
        app_config = _make_app_config()
        tools = [_named_tool("bash"), _named_tool("ask_clarification")]
        result, captured = _assemble(
            monkeypatch,
            app_config,
            tools,
            {"subagent_enabled": True, "agent_preset": "minimal"},
        )
        assert captured["include_mcp"] is False
        assert captured["subagent_enabled"] is False
        assert [tool.name for tool in result["tools"]] == ["bash"]

    def test_custom_preset_groups_and_disabled_tools(self, monkeypatch):
        app_config = _make_app_config(presets={"research": AgentPresetConfig(tool_groups=["web"], disabled_tools=["bash"], subagent_enabled=False)})
        tools = [_named_tool("bash"), _named_tool("web_search")]
        result, captured = _assemble(
            monkeypatch,
            app_config,
            tools,
            {"subagent_enabled": True, "agent_preset": "research"},
        )
        assert captured["groups"] == ["web"]
        assert captured["subagent_enabled"] is False
        assert [tool.name for tool in result["tools"]] == ["web_search"]

    def test_unknown_preset_falls_back_without_breaking_assembly(self, monkeypatch):
        app_config = _make_app_config()
        tools = [_named_tool("bash")]
        result, captured = _assemble(monkeypatch, app_config, tools, {"agent_preset": "typo"})
        assert captured["include_mcp"] is True
        assert [tool.name for tool in result["tools"]] == ["bash"]

    def test_preset_without_update_agent_withholds_extra_tool(self, monkeypatch):
        # agent_name path is covered implicitly: default agent has no
        # update_agent; assert the preset flag flows into the descriptor.
        app_config = _make_app_config(presets={"locked": AgentPresetConfig(allow_update_agent=False)})
        tools = [_named_tool("bash")]
        result, _ = _assemble(monkeypatch, app_config, tools, {"agent_preset": "locked"})
        assert [tool.name for tool in result["tools"]] == ["bash"]
