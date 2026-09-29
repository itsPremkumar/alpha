"""End-to-end proof of the ``subagent.*`` run-event emitter (delegation path).

``contracts/run_event_stream_contract.json`` declares three ``subagent`` event
types. This module answers the only question that matters about a declared
event type: **does a genuine delegation actually write it?**

Everything here is the real path. The parent node delegates through the real
``SubagentExecutor`` (a scripted child graph, no network), emits ``task_*``
custom events the way ``tools/builtins/task_tool.py`` does via the root-graph
``get_stream_writer()``, and the real ``alpha.runtime.runs.worker.run_agent``
consumes the stream and hands ``custom`` frames to the real
``_SubagentEventBuffer`` writing into a real ``MemoryRunEventStore``.

Two results are pinned:

1. With ``custom`` in the run's stream modes the three lifecycle events land on
   the **parent** run's event stream with the provider ``tool_call_id`` as
   ``metadata.task_id`` — so the contract is currently true, and the emitter is
   not missing.

2. Without ``custom`` the same delegation writes **none** of them. That is the
   real, reproducible defect: the contract states an unconditional promise while
   the only production emitter is reachable solely through a stream mode the
   caller chooses (``runtime/runs/worker.py`` builds ``lg_modes`` from the
   request, and ``normalize_stream_modes(None)`` is ``["values"]``). A run
   started without ``stream_mode: custom`` — the Gateway default, and the whole
   IM-channel manager's ``["messages-tuple", "values"]`` — records a real
   delegation and zero ``subagent.*`` events.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.runs import worker
from alpha.runtime.runs.manager import RunRecord, RunStartOutcome
from alpha.runtime.runs.schemas import DisconnectMode, RunStatus
from alpha.runtime.stream_bridge.memory import MemoryStreamBridge

THREAD_ID = "thread-subagent-event-e2e"

#: Event types the contract promises for delegation.
SUBAGENT_LIFECYCLE_EVENT_TYPES = ("subagent.start", "subagent.step", "subagent.end")


@pytest.fixture
def real_executor_module():
    """Swap the conftest MagicMock for the real subagent executor module.

    Mirrors ``tests/test_worker_stream_subgraph_namespace.py``: conftest mocks
    ``alpha.subagents.executor`` to break a package-init import cycle, and by
    the time this fixture runs the real module is safe to import.
    """
    original = sys.modules.get("alpha.subagents.executor")
    sys.modules.pop("alpha.subagents.executor", None)
    subagents_pkg = sys.modules.get("alpha.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")

    module = importlib.import_module("alpha.subagents.executor")
    # Hermetic in CI (no config.yaml) — same defaults as test_subagent_executor.
    module.get_app_config = lambda: SimpleNamespace(tool_search=SimpleNamespace(enabled=False))
    module.build_tracing_callbacks = lambda: []
    yield module

    if original is not None:
        sys.modules["alpha.subagents.executor"] = original
    else:
        sys.modules.pop("alpha.subagents.executor", None)
    subagents_pkg = sys.modules.get("alpha.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")


class _RecordingBridge(MemoryStreamBridge):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, object]] = []

    async def publish(self, run_id: str, event: str, payload: object) -> None:
        self.published.append((event, payload))
        await super().publish(run_id, event, payload)


class _RunManager:
    """Minimal RunManager stand-in: run lifecycle ownership stays with the real one."""

    def __init__(self, record: RunRecord) -> None:
        self.record = record

    async def try_start(self, _run_id):
        self.record.status = RunStatus.running
        return RunStartOutcome.started

    async def wait_for_prior_finalizing(self, *_args, **_kwargs):
        return None

    async def set_status(self, _run_id, status, **_kwargs):
        self.record.status = status

    async def set_status_if_not_cancelled(self, _run_id, status, **kwargs):
        await self.set_status(_run_id, status, **kwargs)
        return None

    async def update_model_name(self, *_args, **_kwargs):
        return None

    async def update_run_completion(self, *_args, **_kwargs):
        return None

    async def update_finalizing_progress(self, *_args, **_kwargs):
        return None

    async def has_later_started_run(self, *_args, **_kwargs):
        return False

    async def set_finalizing(self, *_args, **_kwargs):
        return None

    async def cleanup(self, *_args, **_kwargs):
        return None


def _scripted_child_graph():
    """A child that produces one AI turn, one tool output, then a final answer."""
    from langgraph.graph import END, START, MessagesState, StateGraph

    builder = StateGraph(MessagesState)
    builder.add_node(
        "child_model",
        lambda _state: {
            "messages": [
                AIMessage(
                    content="",
                    id="child-ai",
                    tool_calls=[{"name": "child_tool", "args": {}, "id": "child-tool-call", "type": "tool_call"}],
                )
            ]
        },
    )
    builder.add_node(
        "child_tool",
        lambda _state: {"messages": [ToolMessage(content="child tool output", name="child_tool", tool_call_id="child-tool-call", id="child-tool")]},
    )
    builder.add_node(
        "child_final",
        lambda _state: {"messages": [AIMessage(content="child final answer", id="child-final")]},
    )
    builder.add_edge(START, "child_model")
    builder.add_edge("child_model", "child_tool")
    builder.add_edge("child_tool", "child_final")
    builder.add_edge("child_final", END)
    return builder.compile(checkpointer=False)


def _delegating_parent_graph(executor_module, monkeypatch, *, tool_call_id: str = "call_subagent_e2e"):
    """Parent graph that delegates and emits ``task_*`` exactly like task_tool.py.

    The provider ``tool_call_id`` is the correlation key the contract names
    ``task_id`` — ``subagents/AGENTS.md`` "Identity split" makes that explicit.
    """
    from langgraph.config import get_stream_writer
    from langgraph.graph import END, START, MessagesState, StateGraph

    from alpha.subagents.config import SubagentConfig

    child_graph = _scripted_child_graph()
    executor = executor_module.SubagentExecutor(
        config=SubagentConfig(
            name="general-purpose",
            description="End-to-end run-event emitter test agent",
            system_prompt="You are an end-to-end run-event emitter test agent.",
            max_turns=5,
            timeout_seconds=30,
        ),
        tools=[],
        parent_model="test-model",
        thread_id=THREAD_ID,
        trace_id="trace-subagent-event-e2e",
    )

    async def build_initial_state(task):
        return ({"messages": [HumanMessage(content=task, id="child-task")]}, [], None)

    monkeypatch.setattr(executor, "_build_initial_state", build_initial_state)
    monkeypatch.setattr(executor, "_create_agent", lambda *_args, **_kwargs: child_graph)

    async def delegate(_state):
        writer = get_stream_writer()
        execution_id = executor.execute_async("run the delegated child graph", task_id=tool_call_id)
        writer({"type": "task_started", "task_id": tool_call_id, "description": "delegate to the child", "model_name": "test-model"})
        emitted = 0
        result = None
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                result = executor_module.get_background_task_result(execution_id)
                if result is None:
                    pytest.fail("delegated subagent vanished from the background registry")
                # Mirror task_tool.py's poll loop: one task_running per new
                # captured subagent message, so the persisted rows are the same
                # set a real delegation produces.
                messages = result.ai_messages or []
                for index in range(emitted, len(messages)):
                    writer(
                        {
                            "type": "task_running",
                            "task_id": tool_call_id,
                            "message": messages[index],
                            "message_index": index + 1,
                            "total_messages": len(messages),
                            "model_name": "test-model",
                        }
                    )
                emitted = len(messages)
                if result.status.is_terminal:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail("delegated subagent did not complete")
                await asyncio.sleep(0.001)
            assert result.status.value == "completed", f"delegation failed: {result.error}"
            writer(
                {
                    "type": "task_completed",
                    "task_id": tool_call_id,
                    "result": result.result,
                    "model_name": "test-model",
                }
            )
        finally:
            executor_module.cleanup_background_task(execution_id)
        return {"messages": [AIMessage(content="parent final answer", id="parent-final")]}

    builder = StateGraph(MessagesState)
    builder.add_node("delegate", delegate)
    builder.add_edge(START, "delegate")
    builder.add_edge("delegate", END)
    return builder.compile()


async def _run_delegation(executor_module, monkeypatch, *, stream_modes: list[str], run_id: str) -> tuple[RunRecord, MemoryRunEventStore]:
    graph = _delegating_parent_graph(executor_module, monkeypatch)
    store = MemoryRunEventStore()
    bridge = _RecordingBridge()
    record = RunRecord(
        run_id=run_id,
        thread_id=THREAD_ID,
        assistant_id="lead-agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        model_name="test-model",
    )
    record.abort_event = asyncio.Event()

    await worker.run_agent(
        bridge,
        _RunManager(record),
        record,
        ctx=worker.RunContext(checkpointer=InMemorySaver(), event_store=store),
        agent_factory=lambda config: graph,
        graph_input={"messages": [HumanMessage(content="delegate to the subagent")]},
        config={"configurable": {"thread_id": THREAD_ID}},
        stream_modes=stream_modes,
        stream_subgraphs=False,
    )
    return record, store


@pytest.mark.asyncio
async def test_genuine_delegation_publishes_the_three_contracted_lifecycle_events(real_executor_module, monkeypatch):
    """The contract's promise is TRUE on a run that subscribes to ``custom``."""
    run_id = "run-e2e-custom"
    record, store = await _run_delegation(real_executor_module, monkeypatch, stream_modes=["values", "custom"], run_id=run_id)
    assert record.status == RunStatus.success

    events = await store.list_events(THREAD_ID, run_id, event_types=list(SUBAGENT_LIFECYCLE_EVENT_TYPES))
    types = [event["event_type"] for event in events]

    # Non-vacuous: the delegation really happened before the assertion. The
    # scripted child captures three steps (assistant tool-call turn, tool
    # output, final answer), so a real delegation persists three subagent.step
    # rows between the two lifecycle rows.
    assert types == ["subagent.start", *["subagent.step"] * 3, "subagent.end"], types

    # Parent linkage: these rows belong to the PARENT run, and the provider
    # tool_call_id is the correlation key the contract names task_id.
    for event in events:
        assert event["run_id"] == run_id
        assert event["thread_id"] == THREAD_ID
        assert event["category"] == "subagent"
        assert event["metadata"]["task_id"] == "call_subagent_e2e"

    start, end = events[0], events[-1]
    assert start["content"]["description"] == "delegate to the child"
    assert end["content"]["status"] == "completed"
    assert "child final answer" in end["content"]["result"]

    steps = events[1:-1]
    assert [step["content"]["message_index"] for step in steps] == [1, 2, 3]
    assert [step["content"]["kind"] for step in steps] == ["ai", "tool", "ai"]
    assert steps[0]["content"]["tool_calls"][0]["name"] == "child_tool"
    assert steps[1]["content"]["tool_name"] == "child_tool"
    assert steps[1]["content"]["text"] == "child tool output"
    assert steps[2]["content"]["text"] == "child final answer"

    # The dedicated category keeps them out of the thread message feed.
    assert await store.list_messages(THREAD_ID) == []


@pytest.mark.asyncio
async def test_delegation_without_custom_stream_mode_publishes_none(real_executor_module, monkeypatch):
    """The real defect: the only emitter is gated on a caller-chosen stream mode.

    ``normalize_stream_modes(None)`` is ``["values"]``, so a run started without
    ``stream_mode: custom`` (the Gateway default, and every IM channel) records
    a genuine delegation and none of the three contracted event types.
    """
    from alpha.runtime.stream_modes import normalize_stream_modes

    assert normalize_stream_modes(None) == ["values"]

    run_id = "run-e2e-no-custom"
    record, store = await _run_delegation(real_executor_module, monkeypatch, stream_modes=["values"], run_id=run_id)
    assert record.status == RunStatus.success

    assert await store.list_events(THREAD_ID, run_id, event_types=list(SUBAGENT_LIFECYCLE_EVENT_TYPES)) == []


@pytest.mark.asyncio
async def test_subagent_events_are_fetchable_by_task_id_for_a_single_delegation(real_executor_module, monkeypatch):
    """``list_events(task_id=...)`` is how the subtask card backfills its history."""
    run_id = "run-e2e-by-task"
    _record, store = await _run_delegation(real_executor_module, monkeypatch, stream_modes=["custom"], run_id=run_id)

    events = await store.list_events(
        THREAD_ID,
        run_id,
        task_id="call_subagent_e2e",
        event_types=list(SUBAGENT_LIFECYCLE_EVENT_TYPES),
    )

    assert [event["event_type"] for event in events] == [
        "subagent.start",
        *["subagent.step"] * 3,
        "subagent.end",
    ]
    # A different delegation in the same run is filtered out by task_id.
    assert await store.list_events(THREAD_ID, run_id, task_id="call_other", event_types=list(SUBAGENT_LIFECYCLE_EVENT_TYPES)) == []
