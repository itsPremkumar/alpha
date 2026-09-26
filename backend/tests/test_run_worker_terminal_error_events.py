"""A run that errored must say so on its own stream, with a stable code.

``RunStatus.error`` is durable state; a stream subscriber never sees it. The
worker flipped the durable status to ``error`` in several places and published
nothing, so a failed run ended as a clean ``end`` frame and read as a success.
These tests pin the contract for every terminal error path:

* one ``error`` event per run, carrying a stable machine-readable ``code`` plus
  ``run_id`` / ``thread_id`` / ``correlation_id`` / ``trace_id``;
* published *before* ``publish_end`` closes the stream;
* a produced-output scan that could not run is recorded as ``unverified``, not
  persisted as an empty, passing receipt;
* a run that genuinely succeeded publishes no ``error`` event at all.

The SSE ``end`` frame itself is synthesized by the gateway from
``StreamBridge.publish_end``, which takes only ``run_id`` and carries no status.
Making the ``end`` frame carry the terminal status is therefore a gateway-side
requirement, tracked in ``app/gateway/services.py`` by another workstream.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.runs.manager import MODEL_FAILURE_RECOVERY_REASON, RunManager
from alpha.runtime.runs.schemas import RunStatus
from alpha.runtime.runs.worker import (
    ERROR_CODE_DELIVERY_INCOMPLETE,
    ERROR_CODE_DELIVERY_RECEIPT_FAILED,
    ERROR_CODE_MODEL_FAILURE,
    ERROR_CODE_RUN_EXCEPTION,
    ERROR_CODE_WORKSPACE_SNAPSHOT_FAILED,
    RunContext,
    run_agent,
)


class _RecordingBridge:
    """A ``StreamBridge`` stand-in that keeps a single ordered call log.

    Ordering is the point: the terminal ``error`` frame must reach subscribers
    before ``publish_end`` closes the stream, and the gateway's ``end`` frame is
    the last thing a client acts on.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def publish(self, run_id, event, data):
        self.events.append((event, data))

    async def publish_end(self, run_id):
        self.events.append(("end", None))

    async def cleanup(self, run_id, *args, **kwargs):
        self.events.append(("cleanup", None))

    @property
    def error_frames(self) -> list[dict]:
        return [data for event, data in self.events if event == "error"]

    @property
    def event_names(self) -> list[str]:
        return [event for event, _ in self.events]


def _assert_coded_error_frame(frame: dict, *, code: str, run_id: str, thread_id: str) -> None:
    """Every terminal error frame must be machine-correlatable, not just prose."""
    assert frame["code"] == code
    assert frame["status"] == RunStatus.error.value
    assert frame["run_id"] == run_id
    assert frame["thread_id"] == thread_id
    assert frame["correlation_id"] == run_id
    assert isinstance(frame["trace_id"], str) and frame["trace_id"]


def _no_op_agent():
    class NoOpAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": [AIMessage(content="SESSION SUMMARY")]}

    return NoOpAgent()


def _presenting_agent(path: str):
    class PresentingAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            journal = config["context"]["__run_journal"]
            journal._remember_current_run_tool_calls(
                AIMessage(content="", tool_calls=[{"id": "call_1", "name": "present_files", "args": {}}]),
                caller="lead_agent",
            )
            journal.on_tool_end(
                Command(
                    update={
                        "artifacts": [path],
                        "messages": [ToolMessage("Successfully presented files", tool_call_id="call_1")],
                    }
                ),
                run_id=uuid4(),
            )
            yield {"messages": []}

    return PresentingAgent()


# ---------------------------------------------------------------------------
# A succeeded run stays silent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_successful_run_publishes_no_error_event():
    """The negative control: honesty must not become "everything is an error"."""
    run_manager = RunManager()
    record = await run_manager.create("thread-ok")
    bridge = _RecordingBridge()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()),
        agent_factory=lambda *, config: _no_op_agent(),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.success
    assert bridge.error_frames == []


# ---------------------------------------------------------------------------
# Delivery gate: produced output was never presented
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delivery_gate_failure_publishes_a_coded_error_event(monkeypatch):
    run_manager = RunManager()
    record = await run_manager.create("thread-delivery")
    bridge = _RecordingBridge()
    store = MemoryRunEventStore()
    monkeypatch.setattr(
        "alpha.runtime.runs.worker._produced_output_paths",
        AsyncMock(return_value=["/mnt/user-data/outputs/report.md"]),
    )

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=lambda *, config: _no_op_agent(),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.error
    assert len(bridge.error_frames) == 1
    _assert_coded_error_frame(
        bridge.error_frames[0],
        code=ERROR_CODE_DELIVERY_INCOMPLETE,
        run_id=record.run_id,
        thread_id="thread-delivery",
    )
    assert bridge.error_frames[0]["message"] == "Artifact delivery incomplete: no produced output artifact was presented"
    assert bridge.event_names.index("error") < bridge.event_names.index("end")


@pytest.mark.anyio
async def test_the_delivery_receipt_failure_publishes_a_coded_error_event(monkeypatch):
    class FailingReceiptStore(MemoryRunEventStore):
        async def put_if_absent(self, **kwargs):
            raise RuntimeError("event store unavailable")

    run_manager = RunManager()
    record = await run_manager.create("thread-receipt")
    bridge = _RecordingBridge()
    path = "/mnt/user-data/outputs/report.md"
    monkeypatch.setattr(
        "alpha.runtime.runs.worker._produced_output_paths",
        AsyncMock(return_value=[path]),
    )

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=FailingReceiptStore()),
        agent_factory=lambda *, config: _presenting_agent(path),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.error
    assert len(bridge.error_frames) == 1
    _assert_coded_error_frame(
        bridge.error_frames[0],
        code=ERROR_CODE_DELIVERY_RECEIPT_FAILED,
        run_id=record.run_id,
        thread_id="thread-receipt",
    )
    assert bridge.event_names.index("error") < bridge.event_names.index("end")


# ---------------------------------------------------------------------------
# Model failure fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_llm_error_fallback_publishes_a_coded_error_event():
    run_manager = RunManager()
    record = await run_manager.create("thread-llm")
    bridge = _RecordingBridge()

    class FallbackAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {
                "messages": [
                    AIMessage(
                        content="The configured LLM provider is temporarily unavailable after multiple retries.",
                        additional_kwargs={
                            "agent_workspace_error_fallback": True,
                            "error_type": "APIConnectionError",
                            "error_reason": "transient",
                            "error_detail": "Connection error.",
                        },
                    )
                ]
            }

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()),
        agent_factory=lambda *, config: FallbackAgent(),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.error
    assert record.stop_reason == MODEL_FAILURE_RECOVERY_REASON
    assert len(bridge.error_frames) == 1
    _assert_coded_error_frame(
        bridge.error_frames[0],
        code=ERROR_CODE_MODEL_FAILURE,
        run_id=record.run_id,
        thread_id="thread-llm",
    )
    assert bridge.error_frames[0]["message"] == "Connection error."
    assert bridge.error_frames[0]["stop_reason"] == MODEL_FAILURE_RECOVERY_REASON


# ---------------------------------------------------------------------------
# Unhandled exception
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unhandled_run_exception_publishes_a_coded_error_event():
    run_manager = RunManager()
    record = await run_manager.create("thread-boom")
    bridge = _RecordingBridge()

    class ExplodingAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise RuntimeError("agent exploded")
            if False:  # pragma: no cover - keep this an async generator
                yield

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()),
        agent_factory=lambda *, config: ExplodingAgent(),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.error
    assert len(bridge.error_frames) == 1, "the one-terminal-error guard must hold"
    _assert_coded_error_frame(
        bridge.error_frames[0],
        code=ERROR_CODE_RUN_EXCEPTION,
        run_id=record.run_id,
        thread_id="thread-boom",
    )
    frame = bridge.error_frames[0]
    assert frame["message"] == "agent exploded"
    assert frame["name"] == "RuntimeError"
    assert bridge.event_names.index("error") < bridge.event_names.index("end")


# ---------------------------------------------------------------------------
# A scan that could not run must not be persisted as a passing receipt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_unrunnable_output_scan_is_recorded_as_unverified(monkeypatch):
    """``_produced_output_paths`` returning ``None`` means *unverified*.

    Collapsing that to ``[]`` persisted a receipt whose verdict was "nothing was
    produced" -- a fabricated pass for a run that may well have produced files.
    """
    run_manager = RunManager()
    record = await run_manager.create("thread-scan")
    store = MemoryRunEventStore()
    path = "/mnt/user-data/outputs/report.md"
    monkeypatch.setattr(
        "alpha.runtime.runs.worker._produced_output_paths",
        AsyncMock(return_value=None),
    )

    await run_agent(
        _RecordingBridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=lambda *, config: _presenting_agent(path),
        graph_input={},
        config={},
    )

    events = await store.list_events("thread-scan", record.run_id)
    delivery = [e for e in events if e["event_type"] == "run.delivery"]
    assert len(delivery) == 1
    content = delivery[0]["content"]

    # The receipt is the durable record of the uncertainty.
    assert content["stage"] == "unverified"
    assert content["verification"] == {
        "source": ERROR_CODE_WORKSPACE_SNAPSHOT_FAILED,
        "requirement": "produced_output_scan",
        "satisfied": None,
    }
    # ...and no verdict is fabricated in either direction.
    assert "produced_paths" not in content
    assert "satisfied" not in content
    assert content["verification"]["satisfied"] is not True
    assert content["verification"]["satisfied"] is not False
