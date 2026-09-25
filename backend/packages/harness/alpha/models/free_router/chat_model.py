"""``ChatFreeLLM`` — Alpha chat model backed by the keyless free-LLM router.

A normal ``BaseChatModel`` member of Alpha's model factory/fallback chains:

* **No key.** Construction needs no credentials; every request is routed by
  :class:`alpha.models.free_router.catalog.FreeLLMRouter` across the
  anonymous provider set with per-provider failover.
* **Tools.** :meth:`bind_tools` converts tools to their OpenAI schema once
  (``convert_to_openai_tool``) and attaches them to every request; returned
  provider ``tool_calls`` are parsed into LangChain ``tool_calls`` with real
  ``args`` dicts. A provider that returns neither content nor tool calls is a
  provider failure, never an empty "success".
* **Sync + async + streaming.** ``_generate`` owns the real call (``async``
  variants wrap it with ``asyncio.to_thread``); providers do not stream, so
  ``_stream``/``_astream`` emit exactly one chunk carrying the complete
  message (content, tool calls, usage) and fire the token callback so UI
  streaming still works.
* **Honest metadata.** ``usage_metadata`` is built only from a real provider
  ``usage`` payload (absent -> omitted, never invented); the serving
  provider's name is exposed in ``response_metadata`` for observability; a
  router outage raises ``FreeLLMUnavailableError`` (a ``ConnectionError``)
  so the configured fallback chain can continue.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Iterator
from typing import Any

from langchain.chat_models import BaseChatModel
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict

from alpha.models.free_router.catalog import FreeChatResult, get_free_router


def _usage_to_metadata(usage: Any) -> dict[str, Any] | None:
    """Map a real provider usage payload to LangChain ``UsageMetadata``.

    Returns ``None`` when the payload is absent or not numeric — an
    invented token count would poison cost accounting, so we never synthesize
    one. Unknown totals fall back to input+output when both are present.
    """
    if not isinstance(usage, dict):
        return None
    try:
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        total_raw = usage.get("total_tokens")
        total_tokens = int(total_raw) if total_raw is not None else input_tokens + output_tokens
    except (TypeError, ValueError):
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}


def _invalid_tool_call(name: Any, arguments: Any, call: dict[str, Any]) -> dict[str, Any]:
    """Canonical ``InvalidToolCall`` shape for an unparseable provider call."""
    raw_args = arguments if isinstance(arguments, str) else str(arguments)
    tool_id = call.get("id")
    return {
        "name": name if isinstance(name, str) else None,
        "args": raw_args,
        "id": tool_id if isinstance(tool_id, str) else None,
        "type": "invalid_tool_call",
    }


def _normalize_tool_calls(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split provider ``tool_calls`` into parsed LangChain calls + invalid.

    Provider arguments arrive as a JSON *string*; parsing failures become
    ``invalid_tool_calls`` (honest) instead of crashing or being dropped.
    """
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for call in raw:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name")
        if not isinstance(name, str) or not name:
            invalid.append(_invalid_tool_call(name, function.get("arguments", call.get("args", "")), call))
            continue
        arguments = function.get("arguments", call.get("args", {}))
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                invalid.append(_invalid_tool_call(name, arguments, call))
                continue
        elif isinstance(arguments, dict):
            args = arguments
        else:
            invalid.append(_invalid_tool_call(name, arguments, call))
            continue
        if not isinstance(args, dict):
            invalid.append(_invalid_tool_call(name, arguments, call))
            continue
        tool_id = call.get("id") or function.get("id") or f"free-{name}-{len(valid)}"
        valid.append({"name": name, "args": args, "id": tool_id, "type": "tool_call"})
    return valid, invalid


class ChatFreeLLM(BaseChatModel):
    """Keyless free-LLM router chat model (see module docstring)."""

    model: str = "auto"
    target_provider: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_timeout: float | None = None
    # Set by bind_tools (converted OpenAI schemas travel on the instance).
    bound_tools: list[dict[str, Any]] | None = None
    bound_tool_choice: str | dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "free-llm-router"

    def bind_tools(self, tools, tool_choice=None, **kwargs: Any) -> ChatFreeLLM:
        """Convert tools to OpenAI schema and carry them on a copy."""
        del kwargs  # accepted for BaseChatModel parity; not forwarded upstream
        converted = [convert_to_openai_tool(tool) for tool in tools]
        return self.model_copy(
            update={"bound_tools": converted, "bound_tool_choice": tool_choice or None}
        )

    def _extra_body(self, stop: list[str] | None) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self.bound_tools:
            extra["tools"] = self.bound_tools
            if self.bound_tool_choice is not None:
                extra["tool_choice"] = self.bound_tool_choice
        if stop:
            extra["stop"] = list(stop)
        return extra

    def _result_to_message(self, result: FreeChatResult) -> AIMessage:
        tool_calls, invalid = _normalize_tool_calls(result.tool_calls)
        message = AIMessage(
            content=result.text,
            tool_calls=tool_calls,
            invalid_tool_calls=invalid,
            usage_metadata=_usage_to_metadata(result.usage),
            response_metadata={
                "free_llm_provider": result.provider,
                "free_llm_model": result.model_id,
                "latency_ms": round(result.latency_ms, 1),
            },
        )
        return message

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager, kwargs  # provider payloads are built explicitly below
        router = get_free_router()
        payload = convert_to_openai_messages(messages)
        routed = router.chat(
            payload,
            model=self.model,
            target_provider=self.target_provider,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.request_timeout,
            extra=self._extra_body(stop),
        )
        message = self._result_to_message(routed)
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _to_chunk(message: AIMessage) -> AIMessageChunk:
        """Single chunk carrying the complete message (providers do not stream)."""
        return AIMessageChunk(
            content=message.content,
            additional_kwargs=message.additional_kwargs,
            response_metadata=message.response_metadata,
            usage_metadata=message.usage_metadata,
            id=message.id,
            tool_calls=message.tool_calls,
            invalid_tool_calls=message.invalid_tool_calls,
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        result = self._generate(messages, stop=stop, run_manager=None, **kwargs)
        message = result.generations[0].message
        chunk = self._to_chunk(message)
        if run_manager is not None:
            run_manager.on_llm_new_token(
                message.content if isinstance(message.content, str) else "",
                chunk=chunk,
            )
        yield ChatGenerationChunk(message=chunk)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        return await asyncio.to_thread(self._generate, messages, stop, None, **kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        result = await asyncio.to_thread(self._generate, messages, stop, None, **kwargs)
        message = result.generations[0].message
        chunk = self._to_chunk(message)
        if run_manager is not None:
            outcome = run_manager.on_llm_new_token(
                message.content if isinstance(message.content, str) else "",
                chunk=chunk,
            )
            if inspect.isawaitable(outcome):
                await outcome
        yield ChatGenerationChunk(message=chunk)
