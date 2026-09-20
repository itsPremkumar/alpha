"""Tests for task category presets (O1 intent routing)."""

import asyncio
import importlib
from enum import Enum
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from alpha.config.app_config import AppConfig
from alpha.config.subagents_config import SubagentCategoryConfig, SubagentsAppConfig
from alpha.subagents.categories import (
    CategoryResolutionError,
    apply_category,
    get_category,
    list_category_names,
)
from alpha.subagents.config import SubagentConfig

task_tool_module = importlib.import_module("alpha.tools.builtins.task_tool")


class FakeSubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _make_base_config(**overrides) -> SubagentConfig:
    values = {
        "name": "general-purpose",
        "description": "General helper",
        "system_prompt": "Base system prompt",
        "max_turns": 50,
        "timeout_seconds": 10,
    }
    values.update(overrides)
    return SubagentConfig(**values)


def _app_config_with_categories(categories: dict, *model_names: str) -> AppConfig:
    return AppConfig(
        sandbox={"use": "test:DummySandbox"},
        models=[{"name": name, "model": f"{name}-deployment", "use": "test:Scripted"} for name in model_names],
        subagents={"categories": categories},
    )


def _wrap_subagents_config(categories: dict) -> SimpleNamespace:
    return SimpleNamespace(subagents=SubagentsAppConfig(categories=categories))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_builtin_names_present():
    assert {"general", "research", "quick"} <= set(list_category_names(None))


def test_general_is_identity_noop():
    base = _make_base_config()
    resolution = apply_category(base, "general", app_config=None)
    assert resolution.config_overrides == {}
    assert resolution.prompt_suffix == ""


def test_research_overrides_and_suffix():
    base = _make_base_config()
    resolution = apply_category(base, "research", app_config=None)
    assert resolution.config_overrides["max_turns"] == 100
    assert "operator configuration" in resolution.prompt_suffix
    assert "research" in resolution.prompt_suffix


def test_unknown_category_lists_available():
    with pytest.raises(CategoryResolutionError, match="Unknown task category 'nope'"):
        apply_category(_make_base_config(), "nope", app_config=None)
    try:
        apply_category(_make_base_config(), "nope", app_config=None)
    except CategoryResolutionError as exc:
        assert "general" in str(exc) and "research" in str(exc)


def test_operator_category_overrides_builtin():
    custom = SubagentCategoryConfig(
        description="Custom research",
        max_turns=7,
        prompt_append="Custom guidance.",
    )
    app_config = _wrap_subagents_config({"research": custom})
    assert get_category("research", app_config) is custom
    resolution = apply_category(_make_base_config(), "research", app_config=app_config)
    assert resolution.config_overrides["max_turns"] == 7
    assert "Custom guidance." in resolution.prompt_suffix


def test_requires_models_gate():
    gated = SubagentCategoryConfig(
        description="Gated",
        requires_models=["deep-model"],
    )
    app_config = _wrap_subagents_config({"gated": gated})
    with pytest.raises(CategoryResolutionError, match="no application"):
        apply_category(_make_base_config(), "gated", app_config=app_config)


def test_requires_models_gate_passes_when_configured():
    gated = SubagentCategoryConfig(description="Gated", requires_models=["deep-model"])
    app_config = _app_config_with_categories({"gated": gated}, "deep-model")
    resolution = apply_category(_make_base_config(), "gated", app_config=app_config)
    assert resolution.config_overrides == {}


def test_models_chain_selects_first_configured():
    chained = SubagentCategoryConfig(
        description="Chained",
        models=["preferred", "fallback"],
    )
    app_config = _app_config_with_categories({"chained": chained}, "fallback", "preferred")
    resolution = apply_category(_make_base_config(), "chained", app_config=app_config)
    assert resolution.config_overrides["model"] == "preferred"


def test_models_chain_unknown_entry_fails_closed():
    chained = SubagentCategoryConfig(description="Chained", models=["ghost"])
    app_config = _app_config_with_categories({"chained": chained}, "unrelated-model")
    with pytest.raises(CategoryResolutionError, match="no configured model"):
        apply_category(_make_base_config(), "chained", app_config=app_config)


def test_models_chain_needs_model_configuration():
    # Operator category visible, but this runtime exposes no model lookup
    # (e.g. a bare SubagentsAppConfig): fail closed, do not guess.
    chained = SubagentCategoryConfig(description="Chained", models=["anything"])
    app_config = _wrap_subagents_config({"chained": chained})
    with pytest.raises(CategoryResolutionError, match="no application"):
        apply_category(_make_base_config(), "chained", app_config=app_config)


def test_disallowed_tools_union_is_sticky():
    base = _make_base_config(disallowed_tools=["task"])
    extra = SubagentCategoryConfig(description="Extra", disallowed_tools=["bash"])
    app_config = _wrap_subagents_config({"extra": extra})
    resolution = apply_category(base, "extra", app_config=app_config)
    assert sorted(resolution.config_overrides["disallowed_tools"]) == ["bash", "task"]


def test_category_cannot_lift_task_denial():
    # Even a category that denies nothing keeps the base denial: nested
    # delegation can never be re-enabled through a preset.
    base = _make_base_config(disallowed_tools=["task"])
    resolution = apply_category(base, "research", app_config=None)
    assert "disallowed_tools" not in resolution.config_overrides
    assert base.disallowed_tools == ["task"]


def test_explicit_overrides_win():
    base = _make_base_config(skills=["a"], tools=["read_file"], max_turns=50, timeout_seconds=10)
    preset = SubagentCategoryConfig(
        description="Preset",
        skills=["b"],
        tools=["bash"],
        max_turns=5,
        timeout_seconds=60,
    )
    app_config = _wrap_subagents_config({"preset": preset})
    resolution = apply_category(base, "preset", app_config=app_config)
    assert resolution.config_overrides["skills"] == ["b"]
    assert resolution.config_overrides["tools"] == ["bash"]
    assert resolution.config_overrides["max_turns"] == 5
    assert resolution.config_overrides["timeout_seconds"] == 60


# ---------------------------------------------------------------------------
# task_tool integration (DummyExecutor harness)
# ---------------------------------------------------------------------------


def _make_runtime(*, app_config=None) -> SimpleNamespace:
    context = {"thread_id": "thread-1"}
    if app_config is not None:
        context["app_config"] = app_config
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context=context,
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )


def _make_result(status, *, result=None, error=None) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        ai_messages=[],
        result=result,
        error=error,
        stop_reason=None,
        token_usage_records=[],
        usage_reported=False,
        tool_receipts=None,
        bash_executions=None,
    )


def _run_task_tool(**kwargs):
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


def _patch_executor_harness(monkeypatch, captured, base_config=None):
    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            captured["prompt"] = prompt
            return task_id or "generated-task-id"

    async def _no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(
        task_tool_module,
        "get_subagent_config",
        lambda *args, **kwargs: base_config or _make_base_config(),
    )
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("alpha.tools.get_available_tools", lambda **kwargs: [])
    return captured


def test_task_tool_unknown_category_fails_closed(monkeypatch):
    captured: dict = {}
    _patch_executor_harness(monkeypatch, captured)
    runtime = _make_runtime()
    result = _run_task_tool(
        runtime=runtime,
        prompt="p",
        subagent_type="general-purpose",
        tool_call_id="tc-cat-unknown",
        category="nope",
    )
    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert "Unknown task category 'nope'" in message.content
    assert "executor_kwargs" not in captured


def test_task_tool_applies_research_category(monkeypatch):
    captured: dict = {}
    _patch_executor_harness(monkeypatch, captured)
    runtime = _make_runtime()
    result = _run_task_tool(
        runtime=runtime,
        prompt="do research",
        subagent_type="general-purpose",
        tool_call_id="tc-cat-research",
        category="research",
    )
    assert isinstance(result, Command)
    executor_config = captured["executor_kwargs"]["config"]
    assert executor_config.max_turns == 100
    assert "operator configuration" in captured["prompt"]
    assert captured["prompt"].startswith("do research")
    # Loop guard preserved: the child still cannot delegate.
    assert "task" in (executor_config.disallowed_tools or [])


def test_task_tool_category_model_override(monkeypatch):
    custom = SubagentCategoryConfig(description="Deep", models=["deep-model"])
    app_config = _app_config_with_categories({"deep": custom}, "deep-model")
    captured: dict = {}
    _patch_executor_harness(monkeypatch, captured)
    runtime = _make_runtime(app_config=app_config)
    result = _run_task_tool(
        runtime=runtime,
        prompt="p",
        subagent_type="general-purpose",
        tool_call_id="tc-cat-model",
        category="deep",
    )
    assert isinstance(result, Command)
    assert captured["executor_kwargs"]["config"].model == "deep-model"


def test_task_tool_default_category_is_transparent(monkeypatch):
    captured: dict = {}
    _patch_executor_harness(monkeypatch, captured)
    runtime = _make_runtime()
    result = _run_task_tool(
        runtime=runtime,
        prompt="plain task",
        subagent_type="general-purpose",
        tool_call_id="tc-cat-default",
    )
    assert isinstance(result, Command)
    assert captured["prompt"] == "plain task"
    assert captured["executor_kwargs"]["config"].max_turns == 50
