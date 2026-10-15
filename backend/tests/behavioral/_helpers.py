"""Shared builders for the behavioural evaluation suite.

Lives in a sibling module (not ``conftest``) so test files import it directly,
mirroring the ``tests/monocle/_helpers.py`` convention — ``conftest`` is not
importable as a module under pytest's default import mode.

Unlike the pure-function unit tests that dominate ``backend/tests``, the
behavioural suite exercises *behaviour*: a real ``langchain.agents.create_agent``
graph driven by a scripted fake model, a real ``RunManager`` lifecycle, a real
swarm plan run through the coordinator, a real DAG schedule executed wave by
wave. The fakes replace only the two boundaries that cannot run offline — the
LLM and the wall-clock ownership of a crashed worker — never the code under
test.

Everything here is deterministic and offline: no network, no API keys.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from pydantic import PrivateAttr

sys.path.insert(0, str(Path(__file__).resolve().parent))

__all__ = [
    "ScriptedModel",
    "final_turn",
    "injected_warnings",
    "scripted_model",
    "tool_call",
    "tool_messages",
    "turn_with_calls",
]


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
    _bound_tool_counts: list[int] = PrivateAttr(default_factory=list)

    @property
    def seen_requests(self) -> list[list[Any]]:
        return self._seen_requests

    @property
    def bound_tool_counts(self) -> list[int]:
        """Tool schema counts bound for each model request."""
        return self._bound_tool_counts

    def bind_tools(  # type: ignore[override]
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Runnable:
        self._bound_tool_counts.append(len(tools))
        return self

    def bind(self, **kwargs: Any):
        """Record a no-tools model binding used for finalization calls."""
        self._bound_tool_counts.append(0)
        return super().bind(**kwargs)

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


def injected_warnings(request_messages: list[Any]) -> list[HumanMessage]:
    """Loop warnings the middleware injected into one outgoing model request."""
    return [m for m in request_messages if isinstance(m, HumanMessage) and m.name == "loop_warning"]


def tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    """Every ToolMessage in a completed graph result, in order."""
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]
