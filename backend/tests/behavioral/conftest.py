"""Shared fixtures for the behavioral evaluation suite.

Unlike the pure-function unit tests that dominate ``backend/tests``, these
tests exercise *behaviour*: a real ``langchain.agents.create_agent`` graph
driven by a scripted fake model, a real ``RunManager`` lifecycle, a real
swarm plan run through the coordinator, a real DAG schedule executed wave by
wave. The fakes here replace only the two boundaries that cannot run offline
— the LLM and the wall clock's ownership of a crashed worker — never the code
under test.

Everything in this module is deterministic and offline: no network, no API
keys, no sleeps longer than a tool-timeout budget.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from pydantic import PrivateAttr

from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from alpha.runtime.checkpoint.engine import CheckpointEngine
from alpha.runtime.runs.manager import RunManager
from alpha.runtime.runs.store.memory import MemoryRunStore


class ScriptedModel(FakeMessagesListChatModel):
    """A fake chat model that replays scripted turns and records each request.

    ``create_agent`` calls ``model.bind_tools(...)``; the upstream fake raises
    ``NotImplementedError`` there, so we return ``self`` — tool schemas are
    irrelevant when the model's output is scripted.

    ``seen_requests`` is the behavioural observability hook: a test can assert
    not just on the final state but on *what the model was shown* at each turn,
    which is how loop warnings, recovery hints, and delegation ledgers are
    actually verified.
    """

    _seen_requests: list[list[Any]] = PrivateAttr(default_factory=list)

    @property
    def seen_requests(self) -> list[list[Any]]:
        return self._seen_requests

    def bind_tools(  # type: ignore[override]
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Runnable:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        self._seen_requests.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def scripted_model(*responses: AIMessage) -> ScriptedModel:
    """Build a :class:`ScriptedModel` that emits *responses* in order."""
    return ScriptedModel(responses=list(responses))


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    """One scripted tool call in the wire shape models emit."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def turn_with_calls(*calls: dict[str, Any]) -> AIMessage:
    """An assistant turn that only makes tool calls (no final text yet)."""
    return AIMessage(content="", tool_calls=list(calls))


def final_turn(text: str) -> AIMessage:
    """An assistant turn with no tool calls — this terminates the agent loop."""
    return AIMessage(content=text)


def injected_warnings(request_messages: list[Any]) -> list[Any]:
    """Loop warnings the middleware injected into one outgoing model request."""
    from langchain_core.messages import HumanMessage

    return [m for m in request_messages if isinstance(m, HumanMessage) and m.name == "loop_warning"]


def tool_messages(result: dict[str, Any]) -> list[Any]:
    from langchain_core.messages import ToolMessage

    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


@pytest.fixture()
def tool_error_middleware() -> ToolErrorHandlingMiddleware:
    """ToolErrorHandlingMiddleware over a minimal offline app config.

    The real config object pulls in the whole model registry; the middleware
    only reads three fields off it, so a stand-in keeps the test hermetic
    while still running the production middleware unchanged.
    """
    return ToolErrorHandlingMiddleware(
        app_config=SimpleNamespace(
            summarization=SimpleNamespace(skill_file_read_tool_names=()),
            skills=SimpleNamespace(container_path="/nonexistent-skills-root"),
            tool_timeout=5.0,
        )
    )


@pytest.fixture()
def run_manager() -> RunManager:
    """A RunManager backed by the in-memory store (the test-mode default)."""
    return RunManager(store=MemoryRunStore())


@pytest.fixture()
def checkpoint_engine() -> CheckpointEngine:
    return CheckpointEngine()
