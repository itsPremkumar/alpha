"""The synchronous graph path must survive the real middleware stack.

The shipped bug this file exists to prevent
--------------------------------------------
``alpha --json`` drives ``AgentWorkspaceClient.stream()``, which is a
*synchronous* graph invocation. Three hooks in the real lead-agent stack were
overridden only in their ``async`` form, so every one of them took down the sync
path before a single token was produced:

    AutonomousCommandMiddleware.awrap_model_call   (no wrap_model_call)
    UserModelMiddleware.abefore_agent              (no before_agent)
    UserModelMiddleware.aafter_agent               (no after_agent)

``langchain.agents.create_agent`` adds a graph node for a hook when *either*
variant is overridden, then hands ``RunnableCallable`` a ``None`` for whichever
one is missing. The sync run therefore died with

    TypeError: No synchronous function provided to "abefore_agent"

(and, had it got that far, ``NotImplementedError: Synchronous implementation of
wrap_model_call is not available``). The async Gateway path never noticed.

Why a structural test is not enough on its own
-----------------------------------------------
Both halves matter, and neither substitutes for the other:

* the structural check walks the *real* assembled stack, so a future
  async-only middleware fails the suite at PR time instead of in the field;
* the behavioural check actually runs ``.stream()`` synchronously, because the
  structural check reads the same langchain source that produced the bug and
  could be fooled by a langchain change; the async path passing tells you
  nothing about the sync path, which is why every test here drives sync.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from alpha.agents.lead_agent.agent import build_middlewares
from alpha.agents.middlewares.autonomous_command_middleware import (
    AutonomousCommandMiddleware,
)
from alpha.agents.middlewares.user_model_middleware import UserModelMiddleware
from alpha.config.app_config import AppConfig
from alpha.config.sandbox_config import SandboxConfig

#: Every ``AgentMiddleware`` lifecycle hook that has an async twin. ``create_agent``
#: wires each pair independently, so each needs its own sync/async audit.
_LIFECYCLE_HOOK_PAIRS = (
    ("before_agent", "abefore_agent"),
    ("after_agent", "aafter_agent"),
    ("before_model", "abefore_model"),
    ("after_model", "aafter_model"),
    ("wrap_model_call", "awrap_model_call"),
    ("wrap_tool_call", "awrap_tool_call"),
)


def _real_stack() -> list[AgentMiddleware]:
    """The production lead-agent chain, assembled from a minimal AppConfig.

    Deliberately the real ``build_middlewares``, not a hand-picked subset: the
    bug only exists in composition, and a subset would let the next async-only
    middleware in undetected.
    """
    return build_middlewares(
        config={"configurable": {}},
        model_name="gpt-4o",
        app_config=AppConfig(sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider")),
    )


def _overrides(cls: type, name: str) -> bool:
    base = getattr(AgentMiddleware, name, None)
    return getattr(cls, name, None) is not base


# ---------------------------------------------------------------------------
# Structural: the assembled stack must be sync-complete
# ---------------------------------------------------------------------------


def test_real_middleware_stack_has_no_async_only_hooks():
    """The shipped failure, caught at assembly time instead of at request time.

    Only async-only is an error. Sync-only is survivable: langgraph's
    ``RunnableCallable.ainvoke`` falls back to the sync callable, so a sync-only
    hook still runs on the async path (blocking the loop, which is a performance
    concern, not a crash).
    """
    offenders: list[str] = []
    for middleware in _real_stack():
        cls = type(middleware)
        for sync_name, async_name in _LIFECYCLE_HOOK_PAIRS:
            if _overrides(cls, async_name) and not _overrides(cls, sync_name):
                offenders.append(f"{cls.__name__}.{async_name}")
    assert not offenders, (
        "async-only middleware hook(s) break the synchronous stream path "
        "(alpha --json / client.stream()): "
        + ", ".join(sorted(offenders))
    )


def test_stack_audit_covers_the_two_classes_the_shipped_bug_came_from():
    """Named so the regression reads as the bug it is, not an abstract warning."""
    stack = {type(m) for m in _real_stack()}
    assert AutonomousCommandMiddleware in stack
    assert UserModelMiddleware in stack


@pytest.mark.parametrize("sync_name,async_name", _LIFECYCLE_HOOK_PAIRS)
def test_each_paired_hook_actually_exists_on_the_base_class(sync_name, async_name):
    """Guards the audit table itself.

    If langchain renames or drops a hook, the loop above would silently stop
    checking it and the whole test would pass while checking nothing.
    """
    assert _overrides(AgentMiddleware, sync_name) is False
    assert callable(getattr(AgentMiddleware, async_name, None))


# ---------------------------------------------------------------------------
# Behavioural: the sync path runs, and runs the same logic
# ---------------------------------------------------------------------------


class _SyncFakeModel(FakeMessagesListChatModel):
    """Deterministic model with the no-op ``bind_tools`` ``create_agent`` needs."""

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self


def _agent(*middlewares: AgentMiddleware):
    model = _SyncFakeModel(responses=[AIMessage(content="ok")])
    return create_agent(model=model, tools=[], middleware=list(middlewares))


def test_sync_stream_runs_with_the_middlewares_that_broke_it():
    """The exact repro: synchronous ``stream()`` over the two offending middlewares.

    Before the fix this raised ``TypeError: No synchronous function provided to
    "abefore_agent"``. The assertions are about the run *completing*; a
    middleware that silently stopped participating would also pass, so the
    companion test below pins that the sync path still does the work.
    """
    agent = _agent(AutonomousCommandMiddleware(), UserModelMiddleware())
    events = list(agent.stream({"messages": [HumanMessage(content="hello there")]}, {"recursion_limit": 10}, stream_mode="values"))
    assert events, "sync stream produced no events at all"
    assert [m.content for m in events[-1]["messages"] if isinstance(m, AIMessage)] == ["ok"]


def test_sync_invoke_runs_with_the_middlewares_that_broke_it():
    """``invoke()`` is the same synchronous path by a different entry point."""
    agent = _agent(AutonomousCommandMiddleware(), UserModelMiddleware())
    result = agent.invoke({"messages": [HumanMessage(content="hello there")]}, {"recursion_limit": 10})
    assert result["messages"][-1].content == "ok"


def test_the_graph_actually_contains_nodes_for_every_hook_the_middlewares_override():
    """Completion is not enough: the hooks must be wired, not just survivable.

    A "fix" that deleted the async hooks would let the sync stream run while
    removing the feature. ``create_agent`` names a node after the hook, so the
    node set is the observable proof that both halves are wired.
    """
    agent = _agent(AutonomousCommandMiddleware(), UserModelMiddleware())
    node_names = set(agent.get_graph().nodes)
    assert "UserModelMiddleware.before_agent" in node_names
    assert "UserModelMiddleware.after_agent" in node_names
    # wrap_model_call composes into the shared model node rather than getting one
    # of its own, so its presence is asserted by the behavioural tests above.
    assert "model" in node_names


def test_user_model_middleware_defines_both_lifecycle_hooks():
    """Named for the two hooks the shipped run died on."""
    for hook in ("before_agent", "after_agent", "abefore_agent", "aafter_agent"):
        assert _overrides(UserModelMiddleware, hook), f"UserModelMiddleware must override {hook}"


def test_autonomous_command_middleware_defines_both_wrap_hooks():
    assert _overrides(AutonomousCommandMiddleware, "wrap_model_call")
    assert _overrides(AutonomousCommandMiddleware, "awrap_model_call")


def test_sync_wrap_model_call_injects_the_directive_the_async_path_injects():
    """The sync hook carries the feature, not just a pass-through.

    A "fix" that made ``wrap_model_call`` return ``handler(request)`` unchanged
    would satisfy every test above while silently disabling the feature on the
    sync path. This pins that the sync and async paths detect the same trigger
    and build the same directive.
    """
    middleware = AutonomousCommandMiddleware()
    captured: dict[str, Any] = {}

    def _handler(request: ModelRequest) -> ModelResponse:
        captured["messages"] = list(request.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    # A research-intent prompt matches the engine with high confidence.
    request = _request("Conduct deep research into the latest consensus protocols and compare solutions")
    result = middleware.wrap_model_call(request, _handler)

    injected = [
        m
        for m in captured["messages"]
        if isinstance(m, SystemMessage) and "autonomous_command_lifecycle_directive" in str(m.content)
    ]
    assert injected, "sync wrap_model_call injected no autonomous directive"
    assert "COMMAND IDENTIFIED" in str(injected[0].content)
    assert result.result[0].content == "ok"


def test_sync_and_async_wrap_model_call_agree():
    """Byte-parity between the two hooks on the same request.

    The two hooks share :meth:`AutonomousCommandMiddleware._directive_messages`;
    this is the regression net for that sharing actually holding.
    """
    import asyncio

    middleware = AutonomousCommandMiddleware()
    prompt = "Conduct deep research into the latest consensus protocols and compare solutions"

    sync_messages: list[Any] = []
    middleware.wrap_model_call(_request(prompt), lambda r: (sync_messages.extend(r.messages), ModelResponse(result=[AIMessage(content="ok")]))[1])

    async_messages: list[Any] = []

    async def _ahandler(r):
        async_messages.extend(r.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    asyncio.run(middleware.awrap_model_call(_request(prompt), _ahandler))

    assert [str(m.content) for m in sync_messages] == [str(m.content) for m in async_messages]
    assert len(sync_messages) > 1, "neither hook injected anything, so parity is vacuous"


def test_user_model_sync_path_is_a_passthrough_not_a_coroutine_block():
    """The sync hooks must not block on the provider's coroutines.

    The documented design decision is that personalisation runs on the async
    path only; driving it from sync would mean blocking on coroutines from a
    possibly-running loop. So the sync hooks are no-ops, and this pins that they
    are *cheap* no-ops rather than accidental ones.
    """
    middleware = UserModelMiddleware()
    assert middleware._active is False  # NullUserModelProvider by default
    assert middleware.before_agent({}, _runtime()) is None
    assert middleware.after_agent({}, _runtime()) is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _Runtime:
    def __init__(self, **context: Any) -> None:
        self.context = context


def _runtime() -> _Runtime:
    return _Runtime(thread_id="t1", run_id="r1")


class _Request:
    """Minimal ``ModelRequest`` stand-in exposing what the middleware touches."""

    def __init__(self, messages: list[Any], runtime: _Runtime) -> None:
        self.messages = messages
        self.runtime = runtime

    def override(self, *, messages: list[Any]) -> _Request:
        return _Request(messages, self.runtime)


def _request(user_text: str) -> _Request:
    return _Request([HumanMessage(content=user_text)], _runtime())
