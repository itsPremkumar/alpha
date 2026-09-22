"""Wiring tests for ``alpha.agents.memory.user_model``.

That package was complete -- provider ABC, a null implementation, and a
file-backed one -- with its own test suite and no production caller, so
``memory.user_model`` in config.yaml was a knob wired to nothing.
``UserModelMiddleware`` is the integration point. These tests pin both halves of
that: the default path must stay byte-identical, and an opted-in provider must
actually be driven through its lifecycle.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from alpha.agents.memory.user_model import (
    FileUserModelProvider,
    NullUserModelProvider,
    UserModelProvider,
)
from alpha.agents.middlewares.user_model_middleware import UserModelMiddleware


class _Runtime:
    def __init__(self, **context: Any) -> None:
        self.context = context


class _Request:
    """Minimal ``ModelRequest`` stand-in exposing what the middleware touches."""

    def __init__(self, messages: list[Any], runtime: _Runtime) -> None:
        self.messages = messages
        self.runtime = runtime
        self.overridden: list[Any] | None = None

    def override(self, *, messages: list[Any]) -> _Request:
        clone = _Request(messages, self.runtime)
        self.overridden = messages
        return clone


class _ToolRequest:
    """Minimal ``ToolCallRequest`` stand-in."""

    def __init__(self, tool_call: dict[str, Any], runtime: _Runtime) -> None:
        self.tool_call = tool_call
        self.runtime = runtime


class _RecordingProvider(UserModelProvider):
    """Records every lifecycle call so the wiring can be asserted."""

    def __init__(self, *, block: str | None = "USER MODEL CONTEXT", raise_on: str | None = None) -> None:
        self.block = block
        self.raise_on = raise_on
        self.calls: list[tuple[str, Any]] = []

    async def initialize(self, runtime: Any, app_config: Any) -> None:
        self.calls.append(("initialize", app_config))
        if self.raise_on == "initialize":
            raise RuntimeError("provider blew up")

    def system_prompt_block(self, runtime: Any) -> str | None:
        self.calls.append(("system_prompt_block", None))
        if self.raise_on == "system_prompt_block":
            raise RuntimeError("provider blew up")
        return self.block

    async def prefetch(self, runtime: Any) -> dict[str, Any]:
        self.calls.append(("prefetch", None))
        if self.raise_on == "prefetch":
            raise RuntimeError("provider blew up")
        return {"entries": []}

    async def sync_turn(self, runtime: Any, prefetched: dict[str, Any]) -> None:
        self.calls.append(("sync_turn", prefetched))

    async def handle_tool_call(self, runtime: Any, tool_name: str, tool_args: dict, tool_result: Any) -> None:
        self.calls.append(("handle_tool_call", (tool_name, tool_args, tool_result)))
        if self.raise_on == "handle_tool_call":
            raise RuntimeError("provider blew up")

    async def shutdown(self) -> None:
        self.calls.append(("shutdown", None))
        if self.raise_on == "shutdown":
            raise RuntimeError("provider blew up")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# -- the default path must be a no-op ----------------------------------------


def test_default_config_builds_the_null_provider(tmp_path):
    from alpha.config.memory_config import MemoryConfig, UserModelConfig

    memory = MemoryConfig(user_model=UserModelConfig(provider=None))
    middleware = UserModelMiddleware(_config_with(memory))

    assert isinstance(middleware._provider, NullUserModelProvider)
    assert middleware._active is False


def test_null_provider_leaves_the_request_byte_identical():
    middleware = UserModelMiddleware(provider=NullUserModelProvider())
    runtime = _Runtime(thread_id="t", run_id="r")
    request = _Request([{"role": "user", "content": "hi"}], runtime)

    async def handler(req):
        return "model-result"

    result = _run(middleware.awrap_model_call(request, handler))

    assert result == "model-result"
    # No override means not a single byte was added to the system channel.
    assert request.overridden is None


def test_sync_path_is_untouched_even_with_an_active_provider():
    """The sync hooks must exist and pass through, not raise.

    ``AgentMiddleware.wrap_model_call`` / ``.wrap_tool_call`` raise
    ``NotImplementedError`` when only the async variant is defined, so an
    async-only middleware would break every synchronous ``invoke()``. They have to
    be present and must not drive the provider (which would mean blocking on
    coroutines from a possibly-running loop).
    """
    provider = _RecordingProvider()
    middleware = UserModelMiddleware(provider=provider)
    runtime = _Runtime(thread_id="t", run_id="r")
    request = _Request([{"role": "user", "content": "hi"}], runtime)

    assert middleware.wrap_model_call(request, lambda req: "sync-result") == "sync-result"
    assert middleware.wrap_tool_call(_ToolRequest({"name": "bash", "args": {}}, runtime), lambda req: "sync-tool") == "sync-tool"

    assert provider.calls == []


# -- an opted-in provider is driven through its lifecycle ---------------------


def test_file_provider_is_built_from_config(tmp_path):
    from alpha.config.memory_config import MemoryConfig, UserModelConfig

    memory = MemoryConfig(user_model=UserModelConfig(provider="file", storage_path=str(tmp_path)))
    middleware = UserModelMiddleware(_config_with(memory))

    assert isinstance(middleware._provider, FileUserModelProvider)
    assert middleware._active is True


def test_model_call_prefetches_injects_and_syncs():
    from langchain_core.messages import SystemMessage

    provider = _RecordingProvider(block="USER MODEL CONTEXT")
    middleware = UserModelMiddleware(provider=provider)
    runtime = _Runtime(thread_id="t", run_id="r")
    request = _Request([SystemMessage(content="base prompt"), {"role": "user", "content": "hi"}], runtime)

    async def handler(req):
        return "model-result"

    result = _run(middleware.abefore_agent({}, runtime))
    result = _run(middleware.awrap_model_call(request, handler))

    assert result == "model-result"
    names = [name for name, _ in provider.calls]
    # initialize once, then the per-turn sequence in order.
    assert names == ["initialize", "prefetch", "system_prompt_block", "sync_turn"]

    injected = request.overridden
    assert injected is not None
    block = injected[1]
    # Its own SystemMessage, placed after the base prompt, never merged into it.
    assert isinstance(block, SystemMessage)
    assert block.content == "USER MODEL CONTEXT"
    assert injected[0].content == "base prompt"


def test_tool_calls_are_forwarded_to_the_provider():
    provider = _RecordingProvider()
    middleware = UserModelMiddleware(provider=provider)
    runtime = _Runtime(thread_id="t", run_id="r")
    tool_request = _ToolRequest({"name": "read_file", "args": {"path": "a.py"}, "id": "1"}, runtime)

    async def handler(req):
        return "tool-result"

    result = _run(middleware.awrap_tool_call(tool_request, handler))

    assert result == "tool-result"
    forwarded = [payload for name, payload in provider.calls if name == "handle_tool_call"]
    assert forwarded == [("read_file", {"path": "a.py"}, "tool-result")]


def test_shutdown_closes_the_run():
    provider = _RecordingProvider()
    middleware = UserModelMiddleware(provider=provider)
    runtime = _Runtime(thread_id="t", run_id="r")

    _run(middleware.abefore_agent({}, runtime))
    _run(middleware.aafter_agent({}, runtime))

    assert ("shutdown", None) in provider.calls


# -- a broken provider must never take down a run ----------------------------


@pytest.mark.parametrize("stage", ["initialize", "prefetch", "system_prompt_block", "handle_tool_call", "shutdown"])
def test_a_raising_provider_is_logged_and_ignored(stage):
    provider = _RecordingProvider(raise_on=stage)
    middleware = UserModelMiddleware(provider=provider)
    runtime = _Runtime(thread_id="t", run_id="r")

    async def handler(req):
        return "result"

    # None of these may raise, whatever the provider does.
    _run(middleware.abefore_agent({}, runtime))
    result = _run(middleware.awrap_model_call(_Request([{"role": "user"}], runtime), handler))
    assert result == "result"
    _run(middleware.awrap_tool_call(_ToolRequest({"name": "bash", "args": {}}, runtime), handler))
    _run(middleware.aafter_agent({}, runtime))


def test_an_unknown_provider_name_falls_back_to_null():
    from alpha.config.memory_config import MemoryConfig, UserModelConfig

    # ``create_user_model_provider`` raises ValueError for an unknown name;
    # agent assembly must survive a typo in config.yaml.
    memory = MemoryConfig(user_model=UserModelConfig(provider="does-not-exist"))
    middleware = UserModelMiddleware(_config_with(memory))

    assert isinstance(middleware._provider, NullUserModelProvider)
    assert middleware._active is False


def test_file_provider_without_storage_path_falls_back_to_null():
    from alpha.config.memory_config import MemoryConfig, UserModelConfig

    memory = MemoryConfig(user_model=UserModelConfig(provider="file", storage_path=None))
    middleware = UserModelMiddleware(_config_with(memory))

    assert isinstance(middleware._provider, NullUserModelProvider)


def _config_with(memory):
    """A config carrying only what ``UserModelMiddleware`` reads."""
    from types import SimpleNamespace

    return SimpleNamespace(memory=memory)
