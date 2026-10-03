"""The LangGraph tool path must never await a tool call forever.

The 2026-10-02 multi-file e2e run hung inside ``tool.ainvoke ->
run_in_executor`` after a self-heal retry and sat in ``running`` until manual
cancel: the discovery runtime budgets its own calls, MCP has
``tool_call_timeout``, the sandbox caps ``bash`` at 600 s — but the plain
ToolNode path had no budget at all. ``ToolErrorHandlingMiddleware`` owns tool
exceptions, so it owns the budget too: exceeding it becomes an ordinary
tool-timeout error ToolMessage the existing recovery ladder classifies and
retries (``recovery.policies`` maps "tool"+"timeout" to the ``tool_timeout``
RetryPolicy; ``autonomy_truth`` marks it retryable). ``0`` disables the
budget — a YAML ``null`` cannot, the config loader drops nulls so the default
applies (``_drop_null_config_sections``).
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _app_config(**kwargs):
    """Minimal valid AppConfig (sandbox is the only required field)."""
    from alpha.config.app_config import AppConfig
    from alpha.config.sandbox_config import SandboxConfig

    return AppConfig(
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
        **kwargs,
    )


def _make_request(name: str = "slow_tool", tool_call_id: str = "call-1") -> ToolCallRequest:
    runtime = MagicMock()
    runtime.context = {"thread_id": "t-test"}
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": tool_call_id},
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )


async def test_hung_tool_is_cancelled_at_the_budget_and_reported_as_tool_timeout():
    from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

    middleware = ToolErrorHandlingMiddleware(app_config=_app_config(tool_timeout=0.05))

    async def hung(_request):
        await asyncio.sleep(30)  # a tool that never returns
        return ToolMessage(content="never reached", tool_call_id="call-1")

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await middleware.awrap_tool_call(_make_request(), hung)
    elapsed = loop.time() - started

    assert elapsed < 5.0, f"the budget did not bound the call (took {elapsed:.1f}s)"
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "slow_tool" in result.content
    assert "ToolCallTimeoutError" in result.content
    assert "30" not in result.content.split("budget")[0], "the hung call must not report as completed"

    # The recovery ladder keys off this exact classification: a tool timeout
    # is a bounded, retryable failure — not a model timeout, not unknown.
    from alpha.recovery.policies import classify_failure

    assert classify_failure(result.content) == "tool_timeout"

    # The autonomy planner metadata attached to the ToolMessage must mark it
    # retryable (its classifier buckets timeouts with bounded backoff).
    recovery = (result.additional_kwargs or {}).get("alpha_autonomy_recovery")
    assert recovery is not None, "autonomy recovery metadata must be attached"
    assert recovery["retryable"] is True


async def test_control_flow_bubbles_are_never_swallowed_by_the_budget():
    """Interrupt/pause signals (GraphBubbleUp) must pass through untouched."""
    from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

    middleware = ToolErrorHandlingMiddleware(app_config=_app_config(tool_timeout=30))

    async def interrupting(_request):
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        await middleware.awrap_tool_call(_make_request(), interrupting)


async def test_disabled_budget_passes_calls_through_unbounded():
    from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

    middleware = ToolErrorHandlingMiddleware(app_config=_app_config(tool_timeout=0))
    assert middleware._tool_timeout == 0

    async def quick(_request):
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = await middleware.awrap_tool_call(_make_request(), quick)
    assert getattr(result, "content", None) == "ok"
    assert getattr(result, "status", None) != "error"


def test_default_budget_matches_the_sandbox_command_cap():
    from alpha.config.app_config import DEFAULT_TOOL_TIMEOUT_SECONDS

    assert DEFAULT_TOOL_TIMEOUT_SECONDS == 600.0
    assert _app_config().tool_timeout == DEFAULT_TOOL_TIMEOUT_SECONDS
