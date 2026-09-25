"""Fail-closed security proofs not already covered by the discovery suites.

Policy exclusion and blocked execution already have end-to-end coverage in
``test_skill_policy_deferred_discovery.py``. These tests pin the two remaining
trust boundaries: untrusted parameter schemas stay out of the prompt directory,
and unavailable/failing targets disclose an error rather than fabricating data.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool
from pydantic import BaseModel, Field

from alpha.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from alpha.agents.thread_state import ThreadState
from alpha.tools.builtins.tool_search import build_deferred_tool_setup, get_deferred_tools_prompt_section
from alpha.tools.builtins.tool_search_tool import catalog_tool_call
from alpha.tools.mcp_metadata import tag_mcp_tool
from alpha.tools.search.catalog import UniversalToolCatalog


def test_untrusted_mcp_parameter_schema_never_enters_prompt_directory() -> None:
    marker = "UNTRUSTED_MCP_PARAMETER_DESCRIPTION"

    class RemoteParameters(BaseModel):
        credential: str = Field(description=marker)

    def invoke_remote(credential: str) -> str:
        return credential

    remote = StructuredTool(
        func=invoke_remote,
        name="mcp_remote_invoke",
        description="Invoke a remote provider operation.",
        args_schema=RemoteParameters,
    )
    setup = build_deferred_tool_setup([tag_mcp_tool(remote)], enabled=True)

    prompt = get_deferred_tools_prompt_section(deferred_names=setup.deferred_names)

    assert "mcp_remote_invoke" in prompt
    assert marker not in prompt
    assert "credential" not in prompt
    assert '"parameters"' not in prompt


def test_promoted_failing_target_returns_disclosed_error() -> None:
    @tool
    def failing_target(value: str) -> str:
        """Return a value from a deliberately unavailable provider."""
        raise RuntimeError("provider handshake expired")

    class RecordingModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    target = tag_mcp_tool(failing_target)
    setup = build_deferred_tool_setup([target], enabled=True)
    model = RecordingModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "tool_search",
                            "args": {"query": "select:failing_target"},
                            "id": "search-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "failing_target",
                            "args": {"value": "payload"},
                            "id": "target-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    graph = create_agent(
        model=model,
        tools=[target, setup.tool_search_tool],
        middleware=[
            ToolErrorHandlingMiddleware(),
            DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash),
        ],
        state_schema=ThreadState,
    )

    result = graph.invoke({"messages": [HumanMessage(content="run the target")]})

    assert result["promoted"] == {"catalog_hash": setup.catalog_hash, "names": ["failing_target"]}
    target_result = next(message for message in result["messages"] if isinstance(message, ToolMessage) and message.tool_call_id == "target-call")
    assert target_result.status == "error"
    assert "RuntimeError" in target_result.content
    assert "provider handshake expired" in target_result.content
    assert "payload" not in target_result.content


def test_legacy_catalog_call_discloses_unavailable_target(monkeypatch) -> None:
    catalog = UniversalToolCatalog()
    monkeypatch.setattr("alpha.tools.builtins.tool_search_tool.get_universal_catalog", lambda: catalog)

    result = catalog_tool_call.invoke({"tool_name": "missing_target"})

    assert result == "Error calling 'missing_target': \"Tool 'missing_target' is not callable or has no registered handler.\""


def test_legacy_catalog_call_discloses_handler_failure(monkeypatch) -> None:
    def fail(**kwargs) -> str:
        raise RuntimeError("catalog provider disconnected")

    catalog = UniversalToolCatalog()
    catalog.register_tool("failing_target", fail, description="A failing fixture.")
    monkeypatch.setattr("alpha.tools.builtins.tool_search_tool.get_universal_catalog", lambda: catalog)

    result = catalog_tool_call.invoke({"tool_name": "failing_target", "arguments": {"value": "secret"}})

    assert result == "Error calling 'failing_target': catalog provider disconnected"
    assert "secret" not in result
