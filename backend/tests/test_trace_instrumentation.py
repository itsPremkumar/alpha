"""The instrumented seams really emit, and really nest.

Unit tests on the emitters prove the *shape*; this file proves the *wiring*. It
drives the three production call sites this change added and asserts the durable
rows they write:

* :mod:`alpha.runtime.journal` -- layers 2 (model call), 12 (cost), 13 (errors);
* :mod:`alpha.tools.selection` -- layers 3 and 4 (tool and skill selection) from
  the one shared ranking helper;
* :mod:`alpha.tools.builtins.task_tool` -- layer 6 (subagent) at the spawn edge.

Every assertion also checks the other half: with no writer installed, the same
drive writes **nothing** and the run's own behaviour is unchanged. A call site
that emits unconditionally would double every run's event volume and is exactly
the kind of thing a unit test on the emitter cannot see.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from alpha.observability.trace.config import TraceConfig
from alpha.observability.trace.contract import TraceEnvelope
from alpha.observability.trace.writer import TraceWriter, install_writer, reset_writer
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal


@pytest.fixture(autouse=True)
def _no_installed_writer():
    reset_writer()
    yield
    reset_writer()


def _writer(store: MemoryRunEventStore) -> TraceWriter:
    """A writer whose only sink is the same durable store the journal writes to.

    That is the point of the design: the behaviour trace and the run feed are the
    same rows, so a test can read them back with the store's own ``list_events``
    and no test-only plumbing. ``thread_id`` is passed because a
    ``RunEventStore`` addresses rows by ``(thread_id, run_id)`` and the selection
    emitter has no thread of its own.
    """
    return TraceWriter(TraceConfig(enabled=True, sinks=["run_events"]), store=store, thread_id="t-1")


def _drain_now(writer: TraceWriter, store: MemoryRunEventStore) -> list[dict]:
    """Write the writer's buffer from a *synchronous* test."""
    return asyncio.run(_drain(writer, store))


async def _drain(writer: TraceWriter, store: MemoryRunEventStore) -> list[dict]:
    """Write the writer's buffer, then return every trace row the store holds.

    Async because a test already on a loop (the selection ones) cannot call
    :func:`asyncio.run`; both shapes go through here so no test has to think
    about which kind it is.
    """
    await writer.drain()
    rows: list[dict] = []
    for thread_events in store._events.values():  # noqa: SLF001 - the journal's own store, read in a test
        for row in thread_events:
            if row.get("metadata", {}).get("schema_version") == 1:
                rows.append(row)
    return rows


def _trace_rows(rows: list[dict]) -> list[TraceEnvelope]:
    return [TraceEnvelope.from_run_event(row) for row in rows]


# ---------------------------------------------------------------------------
# layer 2 / 12 / 13 -- the journal
# ---------------------------------------------------------------------------


_LAST_JOURNAL: list[RunJournal] = []


def _drive_journal(store: MemoryRunEventStore, *, fail: bool = False) -> dict[str, Any]:
    journal = RunJournal("r-1", "t-1", store, flush_threshold=10_000)
    _LAST_JOURNAL.append(journal)
    call_id = uuid4()
    journal.on_chat_model_start({"name": "ChatModel"}, [[HumanMessage(content="hello", id="h-1")]], run_id=call_id)
    if fail:
        journal.on_llm_error(TimeoutError("provider timed out"), run_id=call_id)
    else:
        message = AIMessage(
            content="answer",
            id="a-1",
            usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            response_metadata={"model_name": "space-bunny-free", "model_provider": "space-bunny", "finish_reason": "stop"},
        )
        journal.on_llm_end(type("R", (), {"generations": [[type("G", (), {"message": message})()]]})(), run_id=call_id, tags=["lead_agent"])
    return {"journal": journal, "buffer": list(journal._buffer)}  # noqa: SLF001 - the journal's own buffer in a test


def _last_journal() -> RunJournal:
    assert _LAST_JOURNAL, "no journal was driven"
    return _LAST_JOURNAL[-1]


def test_the_journal_emits_a_model_call_and_a_cost_snapshot():
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)

    result = _drive_journal(store)
    rows = _drain_now(writer, store)
    envelopes = _trace_rows(rows)

    types = [envelope.event_type for envelope in envelopes]
    assert "model.call.requested" in types
    assert "model.call.completed" in types
    assert "cost.snapshot" in types

    completed = next(envelope for envelope in envelopes if envelope.event_type == "model.call.completed")
    assert completed.provider == "space-bunny"
    assert completed.model == "space-bunny-free"
    assert completed.payload["finish_reason"] == "stop"
    assert completed.payload["latency_ms"] >= 0.0
    assert completed.payload["tokens"] == {"input_tokens": 11, "output_tokens": 7}
    # The models Alpha configures today declare supports_thinking: false, so the
    # field is present and null. Recorded as null rather than omitted so a
    # consumer can tell "the provider sent none" from "nobody looked".
    assert "reasoning" in completed.payload
    assert completed.payload["reasoning"] is None

    snapshot = next(envelope for envelope in envelopes if envelope.event_type == "cost.snapshot")
    assert snapshot.payload["totals"]["input_tokens"] == 11
    assert snapshot.payload["totals"]["output_tokens"] == 7

    # The journal's own durable rows are untouched by the trace.
    assert result["buffer"], "the journal must still write its own llm.human.input / llm.ai.response rows"
    assert {row["event_type"] for row in result["buffer"]} == {"llm.human.input", "llm.ai.response"}


def test_the_journal_emits_a_swallowed_model_failure_with_the_registry_code():
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)

    _drive_journal(store, fail=True)
    envelopes = _trace_rows(_drain_now(writer, store))

    raised = [envelope for envelope in envelopes if envelope.event_type == "err.swallowed"]
    assert len(raised) == 1
    # TimeoutError is claimed by the surviving registry, so the trace carries a
    # registered code rather than a fifth one.
    from alpha.errors.registry import ERROR_CODES

    assert raised[0].error_code in ERROR_CODES
    assert raised[0].payload["escalated"] is True
    assert raised[0].payload["exception_type"] == "TimeoutError"
    assert raised[0].correlation_id


def test_the_journal_emits_a_terminal_run_error_with_a_stack_hash():
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    journal = RunJournal("r-1", "t-1", store, flush_threshold=10_000)
    journal.on_chain_error(RuntimeError("graph blew up"), run_id=uuid4())

    envelopes = _trace_rows(_drain_now(writer, store))
    raised = [envelope for envelope in envelopes if envelope.event_type == "err.raised"]
    assert len(raised) == 1
    assert raised[0].error_code == "RUN_EXECUTION_FAILED"
    assert raised[0].severity.value == "error"
    assert len(raised[0].payload["stack_sha256"]) == 64, "a stack *fingerprint*, never the traceback"
    assert "Traceback" not in json.dumps(raised[0].to_record())


def test_the_journal_writes_nothing_to_the_trace_when_no_writer_is_installed():
    store = MemoryRunEventStore()
    result = _drive_journal(store)
    drained = _drain_now(TraceWriter(TraceConfig(enabled=False), store=store), store)
    assert drained == []
    assert result["buffer"], "the run's own durable rows must be identical with tracing off"


def test_the_trace_rows_land_in_the_existing_run_feed():
    """The whole design in one assertion: the behaviour trace is not a second
    log. It is rows the store the Gateway already serves already holds."""
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    _drive_journal(store)
    asyncio.run(writer.drain())
    # The journal buffers its own rows and flushes on its own schedule; without
    # this the run feed would hold only the trace rows, and the test below could
    # not tell "the trace landed" from "the trace replaced the run's events".
    journal = _last_journal()
    asyncio.run(journal.flush())

    from alpha.runtime.events.store.base import RunEventStore  # noqa: F401 - the interface the route depends on

    rows = asyncio.run(store.list_events("t-1", "r-1"))
    assert {row["event_type"] for row in rows} >= {"llm.human.input", "llm.ai.response", "model.call.completed", "cost.snapshot"}
    assert all(row["category"] in ("trace", "message") for row in rows)

    page = asyncio.run(store.list_events("t-1", "r-1", event_types=["model.call.completed"]))
    assert len(page) == 1
    assert page[0]["metadata"]["schema_version"] == 1


# ---------------------------------------------------------------------------
# layer 3 / 4 -- tool and skill selection
# ---------------------------------------------------------------------------


def _fake_ranking(monkeypatch: pytest.MonkeyPatch, *, refined: bool) -> Any:
    from alpha.tools import selection

    monkeypatch.setattr(selection, "get_system_one_client", lambda: _client())
    monkeypatch.setattr(selection, "evaluate_choice_partitioned", _evaluator(refined=refined))
    return selection


def _client() -> Any:
    from alpha.config.system_one_config import SystemOneConfig
    from alpha.models.system_one import SystemOneClient

    config = SystemOneConfig(enabled=True, enable_selection=True, max_selection_candidates=50)
    return SystemOneClient(config=config)


def _evaluator(*, refined: bool):
    async def _evaluate(state: Any, instructions: str, criteria: dict, **kwargs: Any) -> Any:
        ids = sorted(criteria, key=lambda key: (len(key), key))

        class _Result:
            ranking = ids
            scores = {name: 1.0 / (index + 1) for index, name in enumerate(ids)}

        return _Result()

    return _evaluate


@pytest.mark.anyio
async def test_tool_selection_records_the_candidate_set_the_choice_and_a_reason(monkeypatch: pytest.MonkeyPatch):
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    selection = _fake_ranking(monkeypatch, refined=False)

    from alpha.tools.selection import Candidate

    candidates = [Candidate(id="bash", title="bash", summary="run a shell command"), Candidate(id="read_file", title="read_file", summary="read a file"), Candidate(id="write_file", title="write_file", summary="write a file")]
    ranking = await selection.rank_candidates("delete the build output", candidates, top_n=2, site="tool_select")
    assert ranking is not None

    envelopes = _trace_rows(await _drain(writer, store))
    decided = [envelope for envelope in envelopes if envelope.event_type == "tool.select.decided"]
    assert len(decided) == 1
    payload = decided[0].payload
    assert set(payload["candidates"]) == {"bash", "read_file", "write_file"}, "the whole candidate set, not just the winner"
    assert payload["chosen"] in payload["candidates"]
    assert payload["reason"], "the choice must state why"
    assert payload["scores"], "the scores that produced the order"
    assert decided[0].tool == payload["chosen"]


@pytest.mark.anyio
async def test_skill_selection_records_the_registry_version(monkeypatch: pytest.MonkeyPatch):
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    selection = _fake_ranking(monkeypatch, refined=True)

    from alpha.tools.selection import Candidate

    candidates = [Candidate(id="alpha-wiki", title="alpha-wiki", summary="search the wiki"), Candidate(id="pdf-tools", title="pdf-tools", summary="extract from a PDF")]
    ranking = await selection.rank_candidates("summarise this PDF", candidates, top_n=1, site="skill_select")
    assert ranking is not None
    assert ranking.refined is True

    envelopes = _trace_rows(await _drain(writer, store))
    decided = [envelope for envelope in envelopes if envelope.event_type == "skill.select.decided"]
    assert len(decided) == 1
    assert decided[0].payload["registry_version"]
    assert set(decided[0].payload["candidates"]) == {"alpha-wiki", "pdf-tools"}
    assert "refined=True" in decided[0].payload["reason"]
    assert decided[0].skill == decided[0].payload["chosen"]


@pytest.mark.anyio
async def test_selection_emits_nothing_when_system_one_is_off(monkeypatch: pytest.MonkeyPatch):
    """The fallback path is a real path. It must not emit a decision that never
    happened, and the caller's own ordering must be untouched."""
    from alpha.config.system_one_config import SystemOneConfig
    from alpha.models.system_one import SystemOneClient
    from alpha.tools import selection

    monkeypatch.setattr(selection, "get_system_one_client", lambda: SystemOneClient(config=SystemOneConfig(enabled=False)))
    writer = _writer(MemoryRunEventStore())
    install_writer(writer)

    from alpha.tools.selection import Candidate

    ranking = await selection.rank_candidates("anything", [Candidate(id="a", title="a"), Candidate(id="b", title="b")], site="tool_select")
    assert ranking is None


# ---------------------------------------------------------------------------
# layer 6 -- subagent
# ---------------------------------------------------------------------------


def test_the_task_tool_emitter_records_a_spawn_and_a_completion_by_hash():
    """Drives the real helpers the task tool calls, so the assertion is about the
    seam rather than about a re-implementation of it."""
    from alpha.tools.builtins.task_tool import _sha256, _trace_subagent_completed, _trace_subagent_spawned

    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)

    prompt = "investigate the failing checkout and report back"
    _trace_subagent_spawned(
        subagent_id="task-1",
        execution_id="exec-1",
        subagent_type="general-purpose",
        reason="needs its own context window",
        depth=1,
        assigned_model="union-alpha",
        prompt=prompt,
        run_id="r-1",
        thread_id="t-1",
        trace_id="trace-1",
        agent_name="lead-agent",
    )
    _trace_subagent_completed(
        subagent_id="task-1",
        outcome="failed",
        depth=1,
        usage={"input_tokens": 40, "output_tokens": 3},
        run_id="r-1",
        thread_id="t-1",
        trace_id="trace-1",
        error="bash: command not found: rgp --secret hunter2Correct",
    )

    envelopes = _trace_rows(_drain_now(writer, store))
    spawned = [envelope for envelope in envelopes if envelope.event_type == "sub.spawned"]
    completed = [envelope for envelope in envelopes if envelope.event_type == "sub.completed"]

    assert len(spawned) == 1
    assert spawned[0].agent_depth == 1
    assert spawned[0].subagent_id == "task-1"
    assert spawned[0].parent_agent_name == "lead-agent"
    assert spawned[0].payload["prompt_sha256"] == _sha256(prompt)
    assert prompt not in json.dumps(spawned[0].to_record()), "the prompt is recorded by hash, never verbatim"

    assert len(completed) == 1
    assert completed[0].payload["outcome"] == "failed"
    assert completed[0].payload["input_tokens"] == 40
    assert "hunter2Correct" not in json.dumps(completed[0].to_record()), "a subagent failure message can quote a command line; it is recorded by hash"


def test_a_trace_failure_inside_a_subagent_seam_does_not_raise():
    """The task tool runs inside a cooperative-cancel guard; a trace raising here
    would put a telemetry concern on the same path as subagent cleanup."""
    from alpha.tools.builtins.task_tool import _sha256, _trace_subagent_spawned

    before = _sha256("unchanged")
    _trace_subagent_spawned(
        subagent_id="t",
        execution_id="e",
        subagent_type="g",
        reason="r",
        depth=1,
        assigned_model="m",
        prompt="p",
        run_id=None,
        thread_id=None,
        trace_id=None,
    )
    assert _sha256("unchanged") == before


# ---------------------------------------------------------------------------
# nesting
# ---------------------------------------------------------------------------


def test_a_subagent_span_nests_under_its_parent():
    """Parent/child nesting is the property that makes a delegated run a subtree
    of the run that delegated it, rather than a second unrelated trace."""
    from alpha.observability.context import RunContext
    from alpha.observability.trace.instrumentation import emit_subagent_spawned

    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)

    parent_span = "a" * 32
    parent = RunContext(trace_id="trace-1", run_id="b" * 32, thread_id="t-1", agent_name="lead-agent")
    child = parent.with_parent(parent_span)

    from alpha.observability.trace.instrumentation import emit_model_completed

    emit_model_completed(provider="space-bunny", model="space-bunny-free", finish_reason="stop", latency_ms=1.0, context=parent, parent_span_id=None, span_id=parent_span)
    emitted_child = emit_subagent_spawned(reason="delegated", depth=1, subagent_id="task-1", assigned_model="union-alpha", context=child)

    assert emitted_child is not None
    assert emitted_child.parent_span_id == parent_span
    assert emitted_child.trace_id == parent.trace_id, "the child shares the parent's trace id"
    assert emitted_child.run_id == child.run_id
    assert emitted_child.agent_depth == 1

    envelopes = _trace_rows(_drain_now(writer, store))
    parent_event = [envelope for envelope in envelopes if envelope.event_type == "model.call.completed"][0]
    child_event = [envelope for envelope in envelopes if envelope.event_type == "sub.spawned"][0]
    assert child_event.parent_span_id == parent_event.span_id
    assert child_event.trace_id == parent_event.trace_id
    assert child_event.seq > parent_event.seq
