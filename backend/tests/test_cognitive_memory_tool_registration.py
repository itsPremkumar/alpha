import logging
from types import SimpleNamespace

import pytest

from agent_workspace.config.app_config import AppConfig
from agent_workspace.diagnostics.invariants import InvariantError, verify_agent_assembly
from agent_workspace.tools.builtins.cognitive_memory_tool import cognitive_memory_tool
from agent_workspace.tools.tools import BUILTIN_TOOLS, SUBAGENT_TOOLS, get_available_tools


@pytest.mark.parametrize("subagent_enabled", [False, True])
def test_cognitive_memory_tool_registration(caplog, subagent_enabled):
    config = AppConfig(sandbox={"use": "agent_workspace.sandbox.local:LocalSandboxProvider"})
    with caplog.at_level(logging.WARNING, logger="agent_workspace.tools.tools"):
        tools = get_available_tools(include_mcp=False, subagent_enabled=subagent_enabled, app_config=config)

    assert cognitive_memory_tool in BUILTIN_TOOLS
    assert cognitive_memory_tool.name not in {tool.name for tool in SUBAGENT_TOOLS}
    assert [tool for tool in tools if tool.name == cognitive_memory_tool.name] == [cognitive_memory_tool]
    assert not any("Duplicate tool name" in record.getMessage() and cognitive_memory_tool.name in record.getMessage() for record in caplog.records)
    assert "unique-tool-names" in verify_agent_assembly(tools=tools, middlewares=[])


@pytest.mark.parametrize("duplicate", [False, True])
def test_cognitive_memory_tool_complete_assembly(monkeypatch, duplicate):
    from agent_workspace.agents.lead_agent.agent import _complete_assembly

    monkeypatch.setattr("agent_workspace.extensions.get_agent_build_extensions", lambda: SimpleNamespace(has_agent_assembly_observers=False))
    config = AppConfig(sandbox={"use": "agent_workspace.sandbox.local:LocalSandboxProvider"})
    tools = get_available_tools(include_mcp=False, app_config=config)
    if duplicate:
        tools.append(cognitive_memory_tool)
    graph = object()
    kwargs = {
        "config": {},
        "graph": graph,
        "namespace": "test",
        "agent_name": "lead",
        "requested_model": None,
        "effective_model": "test-model",
        "model_config": None,
        "thinking_enabled": False,
        "reasoning_effort": None,
        "rendered_base_prompt": "",
        "tools": tools,
        "middlewares": [],
        "deferred_names": frozenset(),
        "enabled_skills": [],
        "effective_policies": {},
    }
    if duplicate:
        with pytest.raises(InvariantError, match="cognitive_memory_tool") as exc_info:
            _complete_assembly(**kwargs)
        assert exc_info.value.check_name == "unique-tool-names"
    else:
        assert _complete_assembly(**kwargs).graph is graph


@pytest.mark.no_auto_user
@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["alice", "bob", None])
async def test_subagent_cognitive_memory_inherits_owner_and_fails_closed(tmp_path, monkeypatch, user_id):
    import importlib.util
    import json
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock

    from langchain_core.messages import AIMessage

    from agent_workspace.config.paths import Paths
    from agent_workspace.memory.cognitive import engine
    from agent_workspace.subagents.config import SubagentConfig

    monkeypatch.setattr(engine, "get_paths", lambda: Paths(tmp_path))
    monkeypatch.setattr(engine, "_owner_systems", {})
    cognitive_memory_tool.func(action="store_belief", runtime=SimpleNamespace(context={"user_id": "alice"}), subject="PrivateAlice", predicate="likes", object_val="tea")
    source = Path(__file__).parents[1] / "packages/harness/agent_workspace/subagents/executor.py"
    spec = importlib.util.spec_from_file_location("_cognitive_memory_test_executor", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "build_tracing_callbacks", lambda: [])
    monkeypatch.setattr(module, "inject_langfuse_metadata", lambda *args, **kwargs: None)
    config = AppConfig(sandbox={"use": "agent_workspace.sandbox.local:LocalSandboxProvider"})
    tools = get_available_tools(include_mcp=False, subagent_enabled=False, app_config=config)
    executor = module.SubagentExecutor(
        config=SubagentConfig(name="memory-test", description="Memory test", system_prompt="Memory test"),
        tools=tools,
        app_config=config,
        parent_model="test-model",
        user_id=user_id,
        extensions=SimpleNamespace(needs_task_store=False, has_task_lifecycle=False),
    )
    assert [tool for tool in executor.tools if tool.name == cognitive_memory_tool.name] == [cognitive_memory_tool]
    captured = {}

    class Agent:
        async def astream(self, state, *, config, context, stream_mode):
            captured.update(context)
            response = cognitive_memory_tool.func(action="recall", runtime=SimpleNamespace(context=context), query="PrivateAlice")
            yield {"messages": [AIMessage(content=response)]}

    monkeypatch.setattr(executor, "_build_initial_state", AsyncMock(return_value=({}, executor.tools, None)))
    monkeypatch.setattr(executor, "_create_agent", lambda *args, **kwargs: Agent())
    result = await executor._aexecute_admitted("Recall owner memory")
    assert captured["is_subagent"] is True
    assert captured["user_id"] == user_id
    if user_id is None:
        assert result.status is module.SubagentStatus.FAILED
        assert "requires an owner" in result.error
        assert {key[0] for key in engine._owner_systems} == {"alice"}
    else:
        assert result.status is module.SubagentStatus.COMPLETED, result.error
        recalled = json.loads(result.result)
        assert ("PrivateAlice" in json.dumps(recalled["results"])) is (user_id == "alice")
