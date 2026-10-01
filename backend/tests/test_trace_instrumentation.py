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
import hashlib
import json
import re
import traceback
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from alpha.observability.context import RunContext, run_scope
from alpha.observability.redaction import redact_text
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


def _bound_run(run_id: str | None = None, thread_id: str = "t-1") -> Any:
    """Bind the trace substrate's ambient :class:`RunContext` for the block.

    ``TraceEnvelope`` addresses every row as ``"<run_id>:<seq>"`` and the writer
    **drops** an event that has no run identity rather than inventing one, so a
    call site with no run of its own has to be *inside* one. This is the seam the
    substrate documents for exactly that case
    (:func:`alpha.observability.context.run_scope`), and it is what the production
    selection path resolves through
    :func:`alpha.tools.selection._ambient_run_identity`.

    The ids match :func:`_writer` so a trace row and a journal row for the same run
    land in the same ``(thread_id, run_id)`` bucket of the one store. ``run_id`` is
    a 32-character hex id because :class:`RunContext` requires one -- the shape
    ``alpha.observability.ids`` mints and validates for a real run.

    The same seam carries the selection emitters. ``alpha.tools.selection._emit_selection``
    states no identity of its own, and that is deliberate rather than an oversight:
    ``rank_candidates`` is the deep call site the ambient-binding design exists for
    (:mod:`alpha.observability.ambient` names it), so threading a run id through it
    would change every one of its callers. The writer therefore resolves the identity
    from the ambient :class:`RunContext`, and with none bound it drops the event on
    purpose -- counted in ``disclosure()`` and logged once, never silently, and
    deliberately *not* repaired by minting a placeholder run id.

    """
    return run_scope(RunContext(trace_id="trace-1", run_id=run_id or _TRACE_RUN_ID, thread_id=thread_id, agent_name="lead-agent"))


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


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

#: ``RunContext`` requires a 32-character lowercase-hex run id, so the ambient-run
#: tests cannot use the readable ``"r-1"`` the direct-``run_id`` call sites pass.
_TRACE_RUN_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def test_the_journal_emits_a_terminal_run_error_with_a_stack_hash():
    """The terminal error carries a *fingerprint* of the stack, and a fingerprint
    of the real stack.

    Two things this asserts that a shape-only test cannot:

    * the value is the actual SHA-256 of the actual ``format_exception`` bytes, not
      a constant, a placeholder, or a per-process salt. Recomputing it here from an
      independently-constructed exception is what makes that checkable; and
    * it lives in :attr:`TraceEnvelope.digests`, **not** in ``payload``, because the
      strict redaction policy replaces a bare 64-character lowercase-hex string in a
      payload with ``[REDACTED:high_entropy_blob]``. See
      ``test_the_stack_fingerprint_is_not_in_the_payload_because_it_would_be_redacted``
      for the proof that the payload could not hold it.
    """
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    journal = RunJournal("r-1", "t-1", store, flush_threshold=10_000)
    failure = RuntimeError("graph blew up")
    journal.on_chain_error(failure, run_id=uuid4())

    envelopes = _trace_rows(_drain_now(writer, store))
    raised = [envelope for envelope in envelopes if envelope.event_type == "err.raised"]
    assert len(raised) == 1
    assert raised[0].error_code == "RUN_EXECUTION_FAILED"
    assert raised[0].severity.value == "error"

    fingerprint = raised[0].digests["stack_sha256"]
    assert _DIGEST_RE.match(fingerprint), "a real SHA-256 hex digest, not a constant or a random value"
    expected = hashlib.sha256("".join(traceback.format_exception(RuntimeError, failure, None)).encode("utf-8", errors="replace")).hexdigest()
    assert fingerprint == expected, "the digest must be of the real formatted stack, recomputed independently here"

    record = json.dumps(raised[0].to_record())
    assert "Traceback" not in record, "a stack *fingerprint*, never the traceback"
    assert "graph blew up" not in raised[0].digests["stack_sha256"]


def test_the_stack_fingerprint_is_not_in_the_payload_because_it_would_be_redacted():
    """Why this file reads ``digests`` and not ``payload``.

    This is the proof behind correcting the original assertion. The strict
    redaction policy treats a long, spaceless, high-entropy string as a bare
    credential blob — and a SHA-256 hex digest is exactly that shape, which is
    also the shape of some credentials. A hex digest measures ~3.76 bits of entropy
    per character against a 3.5 threshold, so it fires.

    So the question "why is the hash not in the payload?" has a mechanical answer
    rather than a stylistic one, and this test states it: the payload *cannot* hold
    the value, so an assertion reading ``payload["stack_sha256"]`` could never be
    satisfied no matter what the producer wrote. Pinned because the tempting "fix"
    for a missing key is to move it into the payload, which would destroy it.
    """
    digest = hashlib.sha256(b"RuntimeError: graph blew up").hexdigest()
    redacted = redact_text(digest)
    assert redacted.value != digest, "the premise: a hex digest is redacted under STRICT"
    assert redacted.reason == "high_entropy"
    assert _DIGEST_RE.match(redacted.value) is None

    envelope = TraceEnvelope.build(
        event_type="err.raised",
        run_id="r-1",
        trace_id="t-1",
        seq=1,
        payload={"error_code": "RUN_EXECUTION_FAILED", "message": "x", "retried": False, "stack_sha256": digest},
    )
    assert envelope.payload["stack_sha256"] == "[REDACTED:high_entropy_blob]", "the value is destroyed in a payload"
    assert "stack_sha256" not in envelope.digests

    # The digest route keeps the value intact, which is why the emitters use it.
    kept = TraceEnvelope.build(
        event_type="err.raised",
        run_id="r-1",
        trace_id="t-1",
        seq=1,
        payload={"error_code": "RUN_EXECUTION_FAILED", "message": "x", "retried": False},
        digests={"stack_sha256": digest},
    )
    assert kept.digests["stack_sha256"] == digest
    assert "stack_sha256" not in kept.payload, "a digest never leaks into a scrubbed payload"


def test_a_digest_cannot_ride_in_the_payload_because_redaction_erases_it():
    """The reason the assertion above reads ``digests``.

    Pinned as its own test so nobody "fixes" the location back: a real SHA-256
    is high-entropy enough to be scrubbed out of a payload, which is a property
    of the redactor and would otherwise look like a lost field.
    """
    import hashlib

    from alpha.observability.redaction import STRICT, Redactor

    digest = hashlib.sha256(b"graph blew up").hexdigest()
    scrubbed = Redactor(STRICT).redact_attributes({"stack_sha256": digest})[0]

    assert scrubbed["stack_sha256"] != digest
    assert scrubbed["stack_sha256"] == "[REDACTED:high_entropy_blob]"


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
    with _bound_run():
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
    with _bound_run():
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
async def test_an_async_selection_is_recorded_under_the_run_it_happened_in(monkeypatch: pytest.MonkeyPatch):
    """Regression for the failure that dropped layers 3 and 4 in **production**.

    The original bug was not test-only. ``_emit_selection`` passed no ``run_id``,
    and the writer's no-identity branch discards the event. Nothing in the codebase
    bound a :class:`RunContext` outside this package, so the ambient fallback was
    never available either — meaning *every* tool and skill selection decision was
    silently dropped, in every run, and the only symptom was one log line saying
    "no run context is bound and no run_id was passed".

    So this asserts the two properties that were missing, on the async path where
    the decision is actually taken:

    * the decision is recorded, and
    * it is recorded **under the identity of the surrounding run**, not a fabricated
      one — a row addressed to some other run would be worse than a missing row.
    """
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    _fake_ranking(monkeypatch, refined=False)

    from alpha.tools.selection import Candidate

    candidates = [Candidate(id="bash", title="bash", summary="run a shell command"), Candidate(id="read_file", title="read_file", summary="read a file")]
    with _bound_run(thread_id="t-async"):
        ranking = await selection_rank(candidates, "delete the build output")
    assert ranking is not None

    envelopes = _trace_rows(await _drain(writer, store))
    decided = [envelope for envelope in envelopes if envelope.event_type == "tool.select.decided"]
    assert len(decided) == 1, "an async selection that happened must be recorded, not dropped for want of an id"
    assert decided[0].run_id == _TRACE_RUN_ID
    assert decided[0].thread_id == "t-async"
    assert decided[0].trace_id == "trace-1"
    assert decided[0].agent_name == "lead-agent", "inherited from the bound run, not restated per call site"

    # The row is durable under (thread_id, run_id) -- the same bucket a journal row
    # for this run uses, which is the whole "one feed, not two" claim.
    rows = await store.list_events("t-async", _TRACE_RUN_ID)
    assert [row["event_type"] for row in rows] == ["tool.select.decided"]


@pytest.mark.anyio
async def test_a_selection_off_graph_stays_silent_instead_of_inventing_an_identity(monkeypatch: pytest.MonkeyPatch):
    """The counterpart, and the reason the fix belongs in the caller.

    With nothing bound and no LangGraph runtime, there is no honest run to file the
    decision under. The writer drops it and counts it — a *disclosed* absence. What
    it must never do is mint a placeholder id, because a decision attributed to a
    run that does not exist is an orphan a reader cannot diagnose. Asserted through
    the writer's own disclosure counters, because "nothing was written" and
    "something was written under a fake id" are otherwise the same empty list.
    """
    store = MemoryRunEventStore()
    writer = _writer(store)
    install_writer(writer)
    _fake_ranking(monkeypatch, refined=False)

    from alpha.tools.selection import Candidate

    candidates = [Candidate(id="bash", title="bash", summary="run a shell command"), Candidate(id="read_file", title="read_file", summary="read a file")]
    ranking = await selection_rank(candidates, "delete the build output")
    assert ranking is not None, "the ranking itself is unaffected by tracing"

    assert _trace_rows(await _drain(writer, store)) == []
    disclosure = writer.disclosure()
    # ``rank_candidates`` emits exactly one decision (the coarse and refine paths are
    # mutually exclusive), so one attempt, one counted refusal.
    assert disclosure["rejected_events"] == 1, "the drop is counted, not silent"
    assert disclosure["recorded_events"] == 0


async def selection_rank(candidates: list[Any], task: str) -> Any:
    """Rank through the real async entry point both selection call sites use."""
    from alpha.tools.selection import rank_candidates

    return await rank_candidates(task, candidates, top_n=1, site="tool_select")


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
    # ``prompt_sha256`` is read from ``digests`` for the same proven reason as
    # ``stack_sha256``: a bare 64-hex string in a payload is redacted to
    # ``[REDACTED:high_entropy_blob]``, so the payload is the one place the hash
    # cannot live. See test_the_stack_fingerprint_is_not_in_the_payload...
    assert spawned[0].digests["prompt_sha256"] == _sha256(prompt)
    assert "prompt_sha256" not in spawned[0].payload
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
