"""Hermetic provider-payload regression for Alpha's deferred MCP discovery.

The synthetic graph uses the production ``create_agent`` + ``DeferredToolFilterMiddleware``
path, captures the exact tools passed to ``bind_tools``, and serializes the same
OpenAI function schemas used by the runtime. No provider, network, credentials,
or model response is involved.

``token_count`` is deliberately a deterministic lexical count, not a provider
tokenizer estimate. Byte counts are exact UTF-8 bytes for this normalized
OpenAI-style request. On base ``c9b11f8`` the fixture measured 80 tools at
38,728 bytes / 11,583 lexical tokens directly versus 2,517 bytes / 452 tokens
deferred (93.5% smaller). One tool measured 650 / 207 directly versus 1,016 /
294 deferred (56.3% larger). The second result is intentional: deferral has a
fixed discovery and directory overhead and is not claimed to help every catalog.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_function

from alpha.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from alpha.tools.builtins.tool_search import assemble_deferred_tools, get_deferred_tools_prompt_section
from alpha.tools.mcp_metadata import tag_mcp_tool

_LARGE_CATALOG_SIZE = 80
_SMALL_CATALOG_SIZE = 1
_USER_MESSAGE = "Use the appropriate synthetic tool."


@dataclass(frozen=True)
class PayloadMeasurement:
    bytes: int
    tokens: int
    tool_count: int


def _synthetic_tool(index: int) -> StructuredTool:
    def run(resource: str, mode: str = "standard", limit: int = 25) -> str:
        return f"{resource}:{mode}:{limit}"

    description = (
        f"Synthetic external capability {index:03d}. Read or update a named external resource "
        "with an explicit operating mode, bounded result limit, and a stable machine-readable response. "
        "This description intentionally carries realistic provider-schema payload weight."
    )
    return tag_mcp_tool(
        StructuredTool.from_function(
            func=run,
            name=f"mcp_synthetic_{index:03d}",
            description=description,
        )
    )


def _capture_runtime_bound_tools(*, deferred: bool, tools: list[StructuredTool]) -> tuple[list[object], str]:
    final_tools, setup = assemble_deferred_tools(tools, enabled=deferred)
    bound_tools: list[object] = []

    class RecordingModel(GenericFakeChatModel):
        def bind_tools(self, candidate_tools, **kwargs):
            bound_tools.extend(candidate_tools)
            return self

    model = RecordingModel(messages=iter([AIMessage(content="done")]))
    middleware = [DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)] if deferred else []
    system_prompt = "You are a synthetic tool-request payload fixture."
    if deferred:
        system_prompt += "\n\n" + get_deferred_tools_prompt_section(deferred_names=setup.deferred_names)

    graph = create_agent(
        model=model,
        tools=final_tools,
        middleware=middleware,
        system_prompt=system_prompt,
    )
    graph.invoke({"messages": [HumanMessage(content=_USER_MESSAGE)]})
    return bound_tools, system_prompt


def _measurement_payload(bound_tools: list[object], system_prompt: str) -> dict[str, object]:
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _USER_MESSAGE},
        ],
        "tools": [convert_to_openai_function(candidate) for candidate in bound_tools],
    }


def _measure(catalog_size: int, *, deferred: bool) -> PayloadMeasurement:
    candidates = [_synthetic_tool(index) for index in range(catalog_size)]
    bound_tools, system_prompt = _capture_runtime_bound_tools(deferred=deferred, tools=candidates)
    serialized = json.dumps(
        _measurement_payload(bound_tools, system_prompt),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token_count = len(re.findall(r"\w+|[^\w\s]", serialized.decode("utf-8"), flags=re.UNICODE))
    return PayloadMeasurement(bytes=len(serialized), tokens=token_count, tool_count=len(bound_tools))


def test_large_catalog_defers_payload_but_small_catalog_costs_more() -> None:
    """Large: >=80% smaller. Small: honestly larger (deferral overhead)."""
    large_direct = _measure(_LARGE_CATALOG_SIZE, deferred=False)
    large_deferred = _measure(_LARGE_CATALOG_SIZE, deferred=True)
    small_direct = _measure(_SMALL_CATALOG_SIZE, deferred=False)
    small_deferred = _measure(_SMALL_CATALOG_SIZE, deferred=True)

    large_reduction = 1.0 - (large_deferred.bytes / large_direct.bytes)
    small_delta = small_deferred.bytes - small_direct.bytes

    assert large_direct.tool_count == _LARGE_CATALOG_SIZE
    assert large_deferred.tool_count == 1
    assert large_deferred.bytes <= large_direct.bytes * 0.20
    assert large_deferred.tokens <= large_direct.tokens * 0.20
    assert large_reduction >= 0.80

    assert small_direct.tool_count == small_deferred.tool_count == 1
    assert small_deferred.bytes > small_direct.bytes
    assert small_deferred.tokens > small_direct.tokens
    assert small_delta > 0
