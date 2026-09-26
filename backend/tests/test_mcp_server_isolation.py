"""Per-server isolation in ``alpha.mcp.tools.get_mcp_tools``.

The comment above the fan-out states the contract: "one broken MCP server does
not prevent healthy servers from contributing their tools". Until this suite
existed that contract was *accidental* — it held only because the per-server
coroutine happened to wrap its whole body in ``except Exception``:

* a ``BaseException`` raised by one server's discovery (e.g. ``CancelledError``
  unwinding through the adapter's cancel scopes, or an ``asyncio.wait_for``
  elsewhere in the stack) made ``asyncio.gather`` re-raise straight out of
  ``get_mcp_tools`` and every healthy server's tools were discarded with it;
* any new statement added outside that ``try`` would have re-broke the contract
  with no test to notice.

The isolation is now structural: the gather is run with
``return_exceptions=True`` and every outcome is normalized by
``_tools_for_one_server``. These tests pin that at both levels — the fan-out
(``BaseException`` included) and the normalizer itself — plus the property that
made the old shape dangerous: per-server isolation must not turn a *caller*
cancellation into a silent success.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from alpha.config.extensions_config import ExtensionsConfig
from alpha.mcp.tools import _tools_for_one_server, get_mcp_tools

_HEALTHY_TOOL_NAME = "healthy_search"


class _Args(BaseModel):
    query: str = Field(..., description="query")


def _tool(name: str) -> StructuredTool:
    async def _call(query: str) -> str:
        return query

    return StructuredTool(name=name, description="Search", args_schema=_Args, coroutine=_call)


def _two_server_config() -> ExtensionsConfig:
    return ExtensionsConfig.model_validate(
        {
            "mcpServers": {
                "healthy": {"type": "stdio", "command": "uvx", "args": ["healthy-mcp"]},
                "broken": {"type": "stdio", "command": "uvx", "args": ["broken-mcp"]},
            }
        }
    )


def _two_server_connections() -> dict[str, dict]:
    return {
        "healthy": {"transport": "stdio", "command": "uvx", "args": ["healthy-mcp"]},
        "broken": {"transport": "stdio", "command": "uvx", "args": ["broken-mcp"]},
    }


def _client_cls(healthy_hook=None, broken_hook=None):
    """A ``MultiServerMCPClient`` stand-in driving each server independently.

    *broken_hook* is awaited for the ``broken`` server instead of returning
    tools; *healthy_hook* is awaited for the ``healthy`` server before it
    returns its tool (so a test can prove the healthy side actually ran).
    """

    class FakeClient:
        def __init__(self, connections, *, callbacks=None, tool_interceptors=None, tool_name_prefix=False) -> None:
            self.connections = connections
            self.callbacks = callbacks
            self.tool_interceptors = tool_interceptors or []
            self.tool_name_prefix = tool_name_prefix

        async def get_tools(self, *, server_name=None):
            if server_name == "broken":
                assert broken_hook is not None
                return await broken_hook()
            if healthy_hook is not None:
                await healthy_hook()
            return [_tool(_HEALTHY_TOOL_NAME)]

    return FakeClient


def _patches(client_cls):
    return (
        patch("alpha.mcp.tools.ExtensionsConfig.from_file", return_value=_two_server_config()),
        patch("alpha.mcp.tools.build_servers_config", return_value=_two_server_connections()),
        patch("alpha.mcp.tools.get_initial_oauth_headers", new_callable=AsyncMock, return_value={}),
        patch("alpha.mcp.tools.build_oauth_tool_interceptor", return_value=None),
        patch("langchain_mcp_adapters.client.MultiServerMCPClient", client_cls),
        patch("langchain_mcp_adapters.tools.load_mcp_tools", new_callable=AsyncMock),
        patch("alpha.mcp.tools._make_session_pool_tool", side_effect=lambda tool, *_a, **_k: tool),
    )


async def _raise(exception: BaseException):
    raise exception


def _drive(client_cls, *, caplog=None, timeout: float = 10.0):
    """Run ``get_mcp_tools`` under the given fake client, returning tool names."""

    async def _run() -> list[str]:
        contexts = _patches(client_cls)
        for context in contexts:
            context.start()
        try:
            tools = await asyncio.wait_for(get_mcp_tools(), timeout=timeout)
            return [tool.name for tool in tools]
        finally:
            for context in reversed(contexts):
                context.stop()

    if caplog is None:
        return asyncio.run(_run())
    with caplog.at_level(logging.WARNING, logger="alpha.mcp.tools"):
        return asyncio.run(_run())


# ── the fan-out: a broken server must not cost the healthy server its tools ──


def test_broken_server_exception_does_not_stop_healthy_server(caplog) -> None:
    """The documented case: an ordinary discovery failure is isolated."""

    async def broken():
        raise RuntimeError("server exploded during tools/list")

    assert _drive(_client_cls(broken_hook=broken), caplog=caplog) == [_HEALTHY_TOOL_NAME]
    assert any("broken" in record.getMessage() for record in caplog.records)


def test_broken_server_base_exception_does_not_stop_healthy_server(caplog) -> None:
    """A ``BaseException`` from one server must not discard the others' tools.

    This is the regression: ``except Exception`` inside the per-server coroutine
    cannot see ``CancelledError``, so pre-fix the whole gather unwound and
    ``get_mcp_tools`` raised instead of returning the healthy server's tools.
    """
    healthy_calls: list[str] = []

    async def broken():
        raise asyncio.CancelledError("discovery cancelled by an unrelated unwinding scope")

    async def healthy():
        healthy_calls.append("ran")

    assert _drive(_client_cls(healthy_hook=healthy, broken_hook=broken), caplog=caplog) == [_HEALTHY_TOOL_NAME]
    # The healthy side really executed, so the name above is not a constant.
    assert healthy_calls == ["ran"]


def test_broken_server_cancellation_is_reported_with_its_exception_type(caplog) -> None:
    """A ``CancelledError`` has an empty ``str()``; the log must still name the cause."""

    async def broken():
        raise asyncio.CancelledError()

    assert _drive(_client_cls(broken_hook=broken), caplog=caplog) == [_HEALTHY_TOOL_NAME]
    messages = [record.getMessage() for record in caplog.records]
    assert any("CancelledError" in message for message in messages), messages


# ── the normalizer: structural, not emergent ──


def test_tools_for_one_server_absorbs_any_base_exception() -> None:
    """Every exception shape, not just ``Exception``, becomes an empty list."""
    for exc in (
        RuntimeError("boom"),
        asyncio.CancelledError(),
        asyncio.CancelledError("named"),
        TimeoutError(),
        KeyboardInterrupt(),
        SystemExit(1),
    ):
        assert _tools_for_one_server("broken", exc) == []


def test_tools_for_one_server_passes_tools_through_unchanged() -> None:
    tools = [_tool("a"), _tool("b")]
    assert _tools_for_one_server("healthy", tools) == tools


def test_tools_for_one_server_copies_the_result_list(caplog) -> None:
    """The normalizer must not hand the gather's own list to the mutating loop below."""
    original = [_tool("a")]
    normalized = _tools_for_one_server("healthy", original)
    assert normalized == original
    assert normalized is not original


def test_tools_for_one_server_logs_the_server_name_and_type(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="alpha.mcp.tools"):
        assert _tools_for_one_server("rogue-server", asyncio.CancelledError()) == []
    assert any("rogue-server" in record.getMessage() and "CancelledError" in record.getMessage() for record in caplog.records)


# ── the property that makes the old shape dangerous: caller cancellation ──


def test_cancelling_the_caller_still_aborts_the_whole_load() -> None:
    """Per-server isolation must not convert an aborted load into a silent success.

    ``return_exceptions=True`` collects a *child's* failure. It must not collect
    the *caller's* cancellation: ``get_mcp_tools`` awaiting a cancelled gather
    must still raise ``CancelledError`` so ``initialize_mcp_tools`` can unwind.
    """

    async def scenario() -> None:
        started = asyncio.Event()

        async def broken():
            started.set()
            await asyncio.sleep(60)

        async def healthy():
            started.set()
            await asyncio.sleep(60)

        contexts = _patches(_client_cls(healthy_hook=healthy, broken_hook=broken))
        for context in contexts:
            context.start()
        try:
            load = asyncio.create_task(get_mcp_tools())
            await asyncio.wait_for(started.wait(), timeout=5)
            load.cancel()
            with pytest.raises(asyncio.CancelledError):
                await load
        finally:
            for context in reversed(contexts):
                context.stop()

    asyncio.run(scenario())


def test_base_exception_raised_outside_the_per_server_try_is_still_isolated() -> None:
    """A per-server failure the inner handler cannot see must still be isolated.

    The inner ``except Exception`` in ``load_server_tools`` happens to wrap the
    whole body *today*, so the fan-out only bites for exception shapes it cannot
    catch. This drives the "new code path" case concretely: the fake client
    raises a ``BaseException`` **synchronously**, from the ``client.get_tools(...)``
    call itself rather than from the coroutine it returns. The inner
    ``except Exception`` cannot see it, so the only thing keeping the healthy
    server's tools alive is ``return_exceptions=True`` plus
    ``_tools_for_one_server``.
    """

    class SyncRaisingClient:
        def __init__(self, connections, *, callbacks=None, tool_interceptors=None, tool_name_prefix=False) -> None:
            self.connections = connections
            self.callbacks = callbacks
            self.tool_interceptors = tool_interceptors or []
            self.tool_name_prefix = tool_name_prefix

        def get_tools(self, *, server_name=None):
            if server_name == "broken":
                raise asyncio.CancelledError("raised before any await, outside any inner try")
            return _healthy_coroutine()

    def _healthy_coroutine():
        async def _get():
            return [_tool(_HEALTHY_TOOL_NAME)]

        return _get()

    assert _drive(SyncRaisingClient) == [_HEALTHY_TOOL_NAME]
