"""Bounded/redaction and integration proofs for discovery telemetry."""

from __future__ import annotations

from types import SimpleNamespace

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from alpha.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from alpha.agents.thread_state import ThreadState
from alpha.runtime.secret_context import SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY
from alpha.tools.builtins.tool_search import build_deferred_tool_setup
from alpha.tools.builtins.tool_search_tool import catalog_tool_call, catalog_tool_describe, catalog_tool_search
from alpha.tools.mcp_metadata import tag_mcp_tool
from alpha.tools.search.catalog import UniversalToolCatalog
from alpha.tools.tool_discovery_metrics import (
    ToolDiscoveryMetricsStore,
    get_tool_discovery_snapshot,
    get_tool_discovery_status,
    record_deferred_tool_search,
    record_tool_discovery_operation,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_store_is_lru_ttl_bounded_saturated_and_honest_when_absent(monkeypatch) -> None:
    clock = _Clock()
    store = ToolDiscoveryMetricsStore(max_sessions=2, retention_seconds=10.0, clock=clock)

    absent = store.status("never-recorded")
    assert absent["available"] is False
    assert absent["reason"] == "no_counters_recorded"
    assert "search" not in absent
    assert absent["raw_queries_retained"] is False
    assert absent["persistence"] == "process_memory_only"

    assert store.record("raw-session-one", "search") is True
    store.record("raw-session-one", "describe")
    store.record("raw-session-one", "promote")
    status = store.status("raw-session-one")
    assert status["available"] is True
    assert (status["search"], status["describe"], status["promote"]) == (1, 1, 1)
    assert status["session_key"] != "raw-session-one"
    assert len(status["session_key"]) == 16
    assert "raw-session-one" not in repr(status)

    store.record("raw-session-two", "search")
    assert store.counters("raw-session-one") is not None  # refresh LRU recency
    store.record("raw-session-three", "search")
    assert store.counters("raw-session-two") is None

    monkeypatch.setattr("alpha.tools.tool_discovery_metrics.MAX_COUNTER_VALUE", 2)
    store.record("raw-session-one", "search")
    store.record("raw-session-one", "search")
    assert store.counters("raw-session-one").search == 2

    clock.advance(10.0)
    assert store.counters("raw-session-one") is None
    assert store.status()["available"] is False


def test_unscoped_operation_is_not_fabricated_into_a_default_session() -> None:
    assert record_tool_discovery_operation("search") is False


def test_three_registered_catalog_tools_record_real_per_session_stages(monkeypatch) -> None:
    session_id = "catalog-telemetry-session-4f7b"
    query_marker = "RAW_QUERY_MUST_NOT_BE_RETAINED"

    @tool
    def catalog_calculator(value: int) -> int:
        """Double an integer."""
        return value * 2

    catalog = UniversalToolCatalog()
    catalog.register_tool("catalog_calculator", catalog_calculator, description="Double an integer.")
    monkeypatch.setattr("alpha.tools.builtins.tool_search_tool.get_universal_catalog", lambda: catalog)

    class RecordingModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = RecordingModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "catalog_tool_search", "args": {"query": query_marker}, "id": "search", "type": "tool_call"},
                        {"name": "catalog_tool_describe", "args": {"tool_name": "catalog_calculator"}, "id": "describe", "type": "tool_call"},
                        {"name": "catalog_tool_call", "args": {"tool_name": "catalog_calculator", "arguments": {"value": 4}}, "id": "promote", "type": "tool_call"},
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    graph = create_agent(
        model=model,
        tools=[catalog_tool_search, catalog_tool_describe, catalog_tool_call],
    )

    graph.invoke({"messages": [HumanMessage(content="use the calculator")]}, context={"thread_id": session_id})

    status = get_tool_discovery_status(session_id)
    assert status["available"] is True
    assert (status["search"], status["describe"], status["promote"]) == (1, 1, 1)
    assert status["retention_seconds"] == 3600.0
    assert status["max_sessions"] == 256
    retained = repr(get_tool_discovery_snapshot())
    assert session_id not in retained
    assert query_marker not in retained


def test_deferred_search_records_effective_policy_filtered_promotion() -> None:
    session_id = "policy-telemetry-session-91ac"

    @tool
    def allowed_target(value: str) -> str:
        """Return a value."""
        return value

    setup = build_deferred_tool_setup([tag_mcp_tool(allowed_target)], enabled=True)

    class RecordingModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = RecordingModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "tool_search",
                            "args": {"query": "select:allowed_target"},
                            "id": "search",
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
        tools=[allowed_target, setup.tool_search_tool],
        middleware=[DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)],
        state_schema=ThreadState,
    )

    graph.invoke({"messages": [HumanMessage(content="use it")]}, context={"thread_id": session_id})

    status = get_tool_discovery_status(session_id)
    assert (status["search"], status["describe"], status["promote"]) == (1, 1, 1)

    denied_session = "policy-denied-telemetry-session-3ab2"
    runtime = SimpleNamespace(
        context={
            "thread_id": denied_session,
            SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY: {"allowed_names": []},
        }
    )
    assert record_deferred_tool_search(runtime, ["allowed_target"]) is True
    denied_status = get_tool_discovery_status(denied_session)
    assert (denied_status["search"], denied_status["describe"]) == (1, 1)
    assert denied_status["promote"] == 0
