"""Honesty 6/6 — a completed delegation is visible somewhere an operator can read it.

The defect this pins
--------------------
A genuinely successful delegation — ``task`` called, a subagent executed, a
result recorded, tokens attributed to the subagent — produced **zero**
``subagent.start`` / ``subagent.step`` / ``subagent.end`` events on the parent
run. Nothing errored. The run finished ``success``. The event store simply had
no record of the work having happened, so every surface an operator could read
said the agent did nothing.

How it was found
----------------
By comparing the durable records against the answer: the assistant's own turn
contained a delegation, the checkpointed thread state contained a delegation
ledger entry, and the run's event stream contained no delegation at all.

What is authoritative today, and what is declared but not emitted
-----------------------------------------------------------------
This file states it rather than leaving it implied:

* **Authoritative durable surface: ``ThreadState.delegations``**, checkpointed
  and served by ``GET /api/threads/{id}/state``. It is written by
  ``DurableContextMiddleware`` on every model call, with no dependency on stream
  modes, provider, worker count or persistence backend, and it survives
  summarization because it is a separate state channel rather than message text.

* **Declared but conditionally emitted: the ``subagent.*`` run events.** The
  catalog (``runtime/events/catalog.py``) declares all three, the run-event
  contract documents them, and ``backend/docs/RUN_EVENT_STREAM.md`` lists them as
  the way a subtask card is backfilled. But the only writer is
  ``_SubagentEventBuffer`` in ``runtime/runs/worker.py``, and it is fed from
  exactly one place: ``_publish_stream_item(..., mode == "custom")``. A run whose
  requested stream modes do not include ``custom`` — the default, and what every
  IM channel and the LangGraph SDK default to — never feeds it. That is the
  mechanism behind "zero events on the parent run".

The tests below therefore assert the authoritative surface unconditionally, and
assert the conditional one *conditionally*, with the condition named. That way a
future change that wires the ledger from the run-event path (or vice versa)
fails a named test instead of quietly making the documentation wrong.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from _agent_e2e_helpers import FakeToolCallingModel
from langchain.agents import create_agent
from langchain.tools import InjectedToolCallId
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from alpha.agents.middlewares.durable_context_middleware import DurableContextMiddleware
from alpha.agents.thread_state import ThreadState, merge_delegations
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.subagents.status_contract import make_subagent_additional_kwargs

THREAD_ID = "honesty-delegation-thread"
DELEGATION_RESULT = "HONESTY_DELEGATION_SENTINEL"

#: Token usage the parent attributes to the subagent. Present so the test can
#: assert the "genuinely successful, tokens attributed" half of the reported
#: situation rather than a bare tool call.
SUBAGENT_USAGE = {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540}


@tool("task", parse_docstring=True)
def fake_task(
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """A `task` delegation that really succeeds.

    Stands in for ``alpha.tools.builtins.task_tool`` at exactly the boundary the
    delegation ledger reads: the terminal ``ToolMessage`` and the structured
    ``additional_kwargs`` that carry the subagent's status, result and token
    usage. Written as a real tool so ``create_agent`` drives it through the real
    ``ToolNode`` rather than a hand-fed message list.

    Args:
        description: short task label.
        prompt: full task instructions.
        subagent_type: which subagent type to use.
        tool_call_id: injected by ToolNode; binds the result to the call.
    """

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Task Succeeded. Result: {DELEGATION_RESULT}",
                    tool_call_id=tool_call_id,
                    name="task",
                    id=f"tm-{tool_call_id}",
                    additional_kwargs=make_subagent_additional_kwargs(
                        "completed",
                        result=DELEGATION_RESULT,
                        model_name="subagent-model",
                        token_usage=SUBAGENT_USAGE,
                    ),
                )
            ]
        }
    )


def _agent() -> Any:
    """A real ``create_agent`` graph: real state schema, real reducer, real middleware."""
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "research the auth scheme", "prompt": "find out how auth works", "subagent_type": "general-purpose"},
                        "id": "call_honesty_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="auth uses a bearer token"),
        ]
    )
    return create_agent(model=model, tools=[fake_task], middleware=[DurableContextMiddleware()], state_schema=ThreadState)


def _run_a_real_delegation() -> dict:
    """Execute the graph. No provider, no network, no sandbox."""
    return _agent().invoke({"messages": [HumanMessage(content="how does auth work?")]})


# ---------------------------------------------------------------------------
# 1. The authoritative surface: the checkpointed delegation ledger
# ---------------------------------------------------------------------------


def test_a_completed_delegation_is_recorded_in_the_thread_state_ledger():
    """The product's claim: the work happened. The durable record must say so.

    ``ThreadState.delegations`` is a real state channel with the real
    ``merge_delegations`` reducer; the entry is produced by
    ``DurableContextMiddleware`` from the real ``task`` tool's real terminal
    ``ToolMessage``. Nothing here is a fixture except the subagent itself, which
    is the one thing that genuinely needs a provider to be genuine.
    """
    result = _run_a_real_delegation()

    ledger = result["delegations"]
    assert len(ledger) == 1, f"expected exactly one delegation entry, got {ledger}"
    entry = ledger[0]

    assert entry["id"] == "call_honesty_1"
    assert entry["status"] == "completed", entry
    assert entry["subagent_type"] == "general-purpose"
    assert entry["description"] == "research the auth scheme"
    assert DELEGATION_RESULT in entry["result_brief"], entry
    # The digest is what a reader uses to tell two delegations apart.
    assert len(entry["result_sha256"]) == 64, entry
    assert entry["created_at"], "a ledger entry with no timestamp cannot be ordered against another"


def test_the_ledger_survives_the_merger_an_operator_reads_back():
    """The reducer is the read path's guarantee, not an implementation detail.

    ``merge_delegations`` is what LangGraph applies on every write, so a second
    turn must not drop or downgrade the entry the first turn produced.
    """
    first = _run_a_real_delegation()["delegations"]
    assert first, "no delegation to merge; the premise failed"

    merged = merge_delegations(first, first)
    assert [entry["id"] for entry in merged] == ["call_honesty_1"], merged
    assert merged[0]["status"] == "completed"

    # A later, non-terminal observation of the same delegation must not downgrade
    # the terminal status — otherwise a resumed run would show work as in-flight.
    downgraded = merge_delegations(first, [{**first[0], "status": "in_progress"}])
    assert downgraded[0]["status"] == "completed", "a terminal delegation was downgraded to in_progress"


def test_the_ledger_is_reachable_through_the_gateway_thread_state_route():
    """The operator's actual surface: ``GET /api/threads/{id}/state``.

    Driven with a real ``InMemorySaver`` checkpoint so the value read back is a
    real checkpointed channel, materialized by ``agent.get_state`` (what the
    Gateway's ``CheckpointStateAccessor`` wraps) and serialized by the real
    ``serialize_channel_values_for_api`` the route uses. This is the assertion
    that a completed delegation is discoverable without opening a debugger.
    """
    from langchain_core.runnables import RunnableConfig

    from alpha.runtime import serialize_channel_values_for_api

    checkpointer = InMemorySaver()
    agent = _agent()
    # The worker assigns the checkpointer onto the compiled graph before the run
    # (`runtime/runs/worker.py`); `get_state` reads it from there, not from the
    # per-call config, so the same assignment is made here.
    agent.checkpointer = checkpointer
    config: RunnableConfig = {"configurable": {"thread_id": THREAD_ID}}
    agent.invoke({"messages": [HumanMessage(content="how does auth work?")]}, config=config)

    snapshot = agent.get_state(config)
    assert snapshot.values, "the checkpoint carries no materialized state"
    # The Gateway's state route answers 404 when there is no `checkpoint_id`,
    # so the presence of a checkpoint is what makes the ledger reachable at all.
    checkpoint_id = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
    assert checkpoint_id, f"the run did not checkpoint, so GET /threads/{{id}}/state would 404: {snapshot.config}"

    values = serialize_channel_values_for_api(snapshot.values)
    delegations = values.get("delegations")
    assert delegations, f"the checkpointed thread state carries no delegation ledger: {sorted(values)}"

    entry = next(item for item in delegations if item["id"] == "call_honesty_1")
    assert entry["status"] == "completed", entry
    assert DELEGATION_RESULT in entry["result_brief"], entry


# ---------------------------------------------------------------------------
# 2. The declared-but-conditional surface: subagent.* run events
# ---------------------------------------------------------------------------


def _contract_event_types() -> set[str]:
    """Event types the shipped run-event contract declares.

    Read from the JSON rather than imported from a Python constant, because the
    promise under test is "the documentation says this exists".
    """
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    contract = json.loads((repo_root / "contracts" / "run_event_stream_contract.json").read_text(encoding="utf-8"))
    return {event["event_type"] for event in contract["events"]}


def test_the_subagent_lifecycle_events_are_declared_everywhere_they_are_documented():
    """The premise for the next two tests: these event types are real promises.

    A declared event that nothing can emit is a documentation lie; an emitted
    event that nothing declared is an undeclared contract. Both are checked.
    """
    from alpha.runtime.events.catalog import (
        SUBAGENT_END_EVENT,
        SUBAGENT_START_EVENT,
        SUBAGENT_STEP_EVENT,
    )

    contract_types = _contract_event_types()

    for definition in (SUBAGENT_START_EVENT, SUBAGENT_STEP_EVENT, SUBAGENT_END_EVENT):
        assert definition.event_type in contract_types, f"{definition.event_type} is emitted but not in the run-event contract"
        assert definition.category == "subagent", f"{definition.event_type} left the dedicated subagent category and would enter the thread message feed"

    assert {"subagent.start", "subagent.step", "subagent.end"} <= contract_types


def test_subagent_run_events_are_written_only_when_the_run_streams_the_custom_mode():
    """The mechanism behind "zero events on the parent run", named explicitly.

    ``_SubagentEventBuffer`` is the sole writer and ``_publish_stream_item`` is
    its sole feeder, gated on ``mode == "custom"``. So a run that does not
    request the ``custom`` stream mode — the **default** in
    ``alpha.runtime.stream_modes.normalize_stream_modes`` — cannot produce these
    rows however successful its delegation was. Pinned so that if the condition
    is ever removed the documentation in this file's header stops being a guess.
    """
    from alpha.runtime.runs.worker import _publish_stream_item
    from alpha.runtime.stream_modes import normalize_stream_modes

    assert normalize_stream_modes(None) == ["values"], "the default stream mode changed; re-read this file's header and the docs it cites"
    assert "custom" not in normalize_stream_modes(None)

    store = MemoryRunEventStore()
    chunk = {
        "type": "task_completed",
        "task_id": "call_honesty_1",
        "result": DELEGATION_RESULT,
        "usage": SUBAGENT_USAGE,
        "model_name": "subagent-model",
    }

    async def _drive(mode: str) -> list[dict]:
        from alpha.runtime.runs.worker import _SubagentEventBuffer

        published: list[tuple[str, Any]] = []
        buffer = _SubagentEventBuffer(store, THREAD_ID, "run-conditional")

        class _Bridge:
            async def publish(self, run_id, event, payload):  # noqa: ANN001 - test double for the stream bridge
                published.append((event, payload))

        await _publish_stream_item(
            bridge=_Bridge(),
            run_id="run-conditional",
            mode=mode,
            chunk=chunk,
            namespace=(),
            file_tool_chunk_batcher=None,
            subagent_events=buffer,
        )
        await buffer.flush()
        return await store.list_events(THREAD_ID, "run-conditional")

    without_custom = asyncio.run(_drive("values"))
    assert not [row for row in without_custom if row["event_type"] == "subagent.end"], "a non-custom stream now persists subagent events; the header of this file is out of date"

    with_custom = asyncio.run(_drive("custom"))
    ends = [row for row in with_custom if row["event_type"] == "subagent.end"]
    assert len(ends) == 1, f"the custom stream mode did not persist the terminal subagent event: {[r['event_type'] for r in with_custom]}"
    assert ends[0]["content"]["status"] == "completed", ends[0]["content"]
    assert ends[0]["content"]["usage"]["total_tokens"] == SUBAGENT_USAGE["total_tokens"], ends[0]["content"]
    assert ends[0]["metadata"]["task_id"] == "call_honesty_1", ends[0]["metadata"]


def test_a_completed_delegation_is_discoverable_without_the_conditional_surface():
    """The guarantee this file exists to state.

    A successful delegation performed through the real graph, with no
    ``subagent.*`` run event anywhere, is still discoverable through the
    checkpointed ledger. That is what makes the ledger authoritative and the
    run events a convenience, and it is the property a future refactor must not
    break.
    """
    store = MemoryRunEventStore()

    async def _no_events() -> list[dict]:
        return await store.list_events(THREAD_ID, "run-without-subagent-events")

    assert asyncio.run(_no_events()) == [], "the fixture run already had subagent events, so this file is not testing what it claims"

    result = _run_a_real_delegation()
    ledger = result["delegations"]

    assert any(entry["status"] == "completed" and DELEGATION_RESULT in (entry.get("result_brief") or "") for entry in ledger), (
        "a completed delegation is not discoverable on the authoritative surface; there would be no durable record an operator could read"
    )


def test_the_delegation_result_never_leaks_onto_the_wrong_surface():
    """The negative: the ledger must not become a second message feed.

    ``subagent.*`` events live in category ``subagent`` precisely so they stay
    out of ``list_messages`` (the thread's chat history) while remaining visible
    to ``list_events``. If a future change moved them, a subagent's internal tool
    outputs would start appearing as chat messages.
    """
    store = MemoryRunEventStore()

    async def _write() -> None:
        await store.put(
            thread_id=THREAD_ID,
            run_id="run-category",
            event_type="subagent.step",
            category="subagent",
            content={"task_id": "call_honesty_1", "message_index": 1, "kind": "tool", "text": "internal", "truncated": False, "tool_name": "bash"},
            metadata={"task_id": "call_honesty_1", "message_index": 1},
        )

    asyncio.run(_write())

    async def _read() -> tuple[list[dict], list[dict]]:
        return await store.list_messages(THREAD_ID), await store.list_events(THREAD_ID, "run-category")

    messages, events = asyncio.run(_read())
    assert not messages, f"a subagent step entered the thread message feed: {messages}"
    assert [row["event_type"] for row in events] == ["subagent.step"]
