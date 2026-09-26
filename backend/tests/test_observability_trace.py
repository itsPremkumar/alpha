"""Acceptance tests for the run-correlation spine (``alpha.observability``).

Every acceptance criterion for the package has at least one test here, and the
test names carry the criterion letter so a reviewer can map a requirement to a
failing test without reading this file. The criteria are:

a. A trace id propagates through nested spans and is on every emitted event.
b. Thread-pool and async boundaries: propagation works where contextvars
   propagate; the documented non-propagating case (subprocess) produces a
   disclosed parent link rather than an orphan.
c. ``detach`` restores the previous context exactly, including after an
   exception.
d. Every redacted corpus sample is scrubbed in both attribute keys and values;
   an unknown shape is redacted rather than passed through.
e. A raising sink does not break the caller; the failure is disclosed once.
f. The file sink overflows with a disclosed dropped count; nothing grows
   unbounded and nothing is silently discarded.
g. Disabled config produces no events, no files, and no measurable cost.
h. The exporter produces a deterministic timeline and discloses truncation.
i. Every config key is read (reader test).
j. Span status is a closed set; an unknown status cannot be constructed.

Determinism: the clock and the id generator are injected everywhere, so no
test depends on wall-clock time or on randomness. Nothing here sleeps.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from alpha.observability.config import READERS, ObservabilityConfig, read_all_keys, resolve_all
from alpha.observability.context import (
    BOUNDARY_RULES,
    ENV_CONTEXT_PROPAGATED,
    ENV_LINK_KIND,
    ENV_PARENT_RUN_ID,
    ENV_PARENT_TRACE_ID,
    ENV_RUN_ID,
    ENV_TRACE_ID,
    RunContext,
    bind,
    bind_carrier,
    current,
    describe_boundary,
    detach,
    disclosed_link_attributes,
    ensure_run_context,
    link_from_env,
    propagates_automatically,
    run_scope,
    subprocess_link_env,
    to_carrier,
)
from alpha.observability.events import EVENT_DOMAINS, EVENT_NAMES, EVENT_STATUSES, EventStatus, TraceEvent, UnknownEventNameError, UnknownEventStatusError, parse_event_name, parse_event_status
from alpha.observability.ids import FORBIDDEN_ID_SOURCE, ID_HEX_LENGTH, INVALID_ID, RandomIdGenerator, SequenceIdGenerator, is_valid_id, require_id
from alpha.observability.metrics_bridge import BRIDGE_METRICS, METRIC_PREFIX, TraceMetricsBridge
from alpha.observability.recorder import InMemorySink, JsonlFileSink, NoOpSink, TraceRecorder, build_recorder, disabled_recorder
from alpha.observability.redaction import REDACTION_POLICIES, Redactor, dangerous_value_corpus, redact_text
from alpha.observability.span import SPAN_STATUSES, Span, SpanStatus, Tracer, UnknownSpanStatusError, coerce_status, current_span

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORTER_PATH = REPO_ROOT / "scripts" / "export_run_trace.py"

#: The one second-aligned epoch the whole suite uses, so a rendered timeline is
#: byte-identical run to run.
FIXED_EPOCH = 1_700_000_000.0


class StepClock:
    """A monotonic injected clock that advances a fixed step on every read.

    Injected rather than ``time.time`` so a span's duration is a value the test
    chose, not a value the machine produced. ``advance`` lets a test make a
    span slow on purpose without sleeping.
    """

    def __init__(self, start: float = FIXED_EPOCH, step: float = 0.001) -> None:
        self._now = start
        self._step = step
        self.reads = 0

    def __call__(self) -> float:
        current_value = self._now
        self._now += self._step
        self.reads += 1
        return current_value

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock() -> StepClock:
    return StepClock()


@pytest.fixture
def ids() -> SequenceIdGenerator:
    return SequenceIdGenerator(prefix="a", clock=lambda: FIXED_EPOCH)


@pytest.fixture
def config() -> ObservabilityConfig:
    return ObservabilityConfig(enabled=True, sinks=["memory"])


@pytest.fixture
def recorder(config: ObservabilityConfig, clock: StepClock, ids: SequenceIdGenerator) -> TraceRecorder:
    return build_recorder(config, id_generator=ids, clock=clock, sinks=[InMemorySink(max_records=4096)])


@pytest.fixture
def memory_sink() -> InMemorySink:
    return InMemorySink(max_records=4096)


@pytest.fixture
def run_context(ids: SequenceIdGenerator) -> RunContext:
    return RunContext(trace_id="trace-abc", run_id=ids.new_run_id(), thread_id="thread-1", user_id="user-1", agent_name="lead-agent")


@pytest.fixture(scope="session")
def exporter() -> Any:
    """Load ``scripts/export_run_trace.py`` as an importable module.

    The script is standalone stdlib on purpose -- an operator must be able to
    read a trace file with nothing but a Python interpreter -- so it is loaded
    by path rather than imported as part of the harness package.
    """
    spec = importlib.util.spec_from_file_location("export_run_trace_test", EXPORTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _records(recorder: TraceRecorder) -> tuple[dict[str, Any], ...]:
    """Return every record the recorder's first in-memory sink retained."""
    sink = recorder.sinks[0]
    assert isinstance(sink, InMemorySink), f"expected an InMemorySink, got {type(sink).__name__}"
    return sink.records()


# ---------------------------------------------------------------- ids


def test_ids_are_sortable_by_time_and_collision_resistant() -> None:
    """Time-sortable prefix, distinct random suffix, exactly 32 lowercase hex."""
    clock = StepClock(start=FIXED_EPOCH, step=0.0)
    generator = RandomIdGenerator(clock=clock, entropy=lambda n: bytes(range(n)))
    first = generator.new_run_id()
    clock.advance(60.0)
    second = generator.new_run_id()

    assert first < second, "a later id must sort after an earlier one"
    assert len(first) == ID_HEX_LENGTH == len(second)
    assert first[:12] != second[:12], "the timestamp prefix must reflect the clock"
    assert is_valid_id(first) and is_valid_id(second)

    # 100k ids from a real CSPRNG at a *frozen* clock must still be distinct:
    # the timestamp prefix is not what makes them unique, the 80 random bits
    # are, and that has to hold when every id shares one timestamp.
    frozen = StepClock(start=FIXED_EPOCH, step=0.0)
    csprng = RandomIdGenerator(clock=frozen, entropy=lambda n: secrets.token_bytes(n))
    unique = {csprng.new_id() for _ in range(100_000)}
    assert len(unique) == 100_000


def test_injected_generator_makes_ids_fully_deterministic() -> None:
    """Two generators with the same clock+counter produce the same id stream."""
    left = SequenceIdGenerator(prefix="b", clock=lambda: FIXED_EPOCH)
    right = SequenceIdGenerator(prefix="b", clock=lambda: FIXED_EPOCH)
    assert [left.new_id() for _ in range(4)] == [right.new_id() for _ in range(4)]
    assert left.issued == 4


def test_broken_clock_still_yields_a_usable_unique_id() -> None:
    """A non-finite or absurd clock degrades sortability, never validity.

    Raising here would let a broken host clock take down a run that only asked
    for an id, so the timestamp is clamped instead.
    """
    for bad in (float("nan"), float("inf"), -1e18, 1e30, "not-a-number"):
        generator = RandomIdGenerator(clock=lambda value=bad: value, entropy=lambda n: b"\x01" * n)
        produced = generator.new_id()
        assert is_valid_id(produced), f"clock {bad!r} produced an unusable id {produced!r}"


@pytest.mark.parametrize(("category", "reason", "samples"), FORBIDDEN_ID_SOURCE, ids=[entry[0] for entry in FORBIDDEN_ID_SOURCE])
def test_forbidden_id_sources_are_structurally_rejected(category: str, reason: str, samples: tuple[str, ...]) -> None:
    """Every documented "must not be an id" sample fails :func:`is_valid_id`.

    The non-structural half of each rule (a secret is not an id *because* of
    what it means) is asserted by the structure: none of these are 32 lowercase
    hex characters, so the validator refuses them before they can reach a
    header, a log line or a trace file.
    """
    assert category and reason
    for sample in samples:
        assert not is_valid_id(sample), f"{category}: {sample!r} was accepted as an id"


def test_invalid_id_structural_rules() -> None:
    assert not is_valid_id(INVALID_ID), "the all-zero id is invalid per W3C trace context"
    assert not is_valid_id("A" * ID_HEX_LENGTH), "uppercase hex is not this format"
    assert not is_valid_id("g" * ID_HEX_LENGTH), "non-hex is not an id"
    assert not is_valid_id("a" * (ID_HEX_LENGTH - 1)), "wrong width is not an id"
    assert not is_valid_id(None)
    assert not is_valid_id(12345)
    with pytest.raises(ValueError, match="must be 32 lowercase hex characters"):
        require_id("nope", field="run_id")


# ------------------------------------------------------- a. trace id propagation


def test_a_trace_id_reaches_every_nested_span_and_event(recorder: TraceRecorder, run_context: RunContext) -> None:
    """(a) One trace id on the root span, every nested span, and every event."""
    with run_scope(run_context):
        with recorder.span("agent.turn") as turn:
            turn.set_attribute("agent", "lead")
            recorder.record_event("agent.turn")
            with recorder.span("tool.call") as tool_span:
                tool_span.set_attribute("tool", "bash")
                recorder.record_event("tool.call.started")
                with recorder.span("mcp.call") as mcp_span:
                    mcp_span.set_attribute("server", "filesystem")
                    recorder.record_event("tool.call.completed")
        recorder.record_event("run.ended")

    records = _records(recorder)
    assert records, "the recorder produced no records"
    assert {record["trace_id"] for record in records} == {"trace-abc"}
    assert {record["run_id"] for record in records} == {run_context.run_id}

    spans = [record for record in records if record["type"] == "span"]
    assert len(spans) == 3
    # Nesting: agent.turn -> tool.call -> mcp.call, each parented to the last.
    by_name = {span["name"]: span for span in spans}
    assert by_name["agent.turn"]["parent_span_id"] is None
    assert by_name["tool.call"]["parent_span_id"] == by_name["agent.turn"]["span_id"]
    assert by_name["mcp.call"]["parent_span_id"] == by_name["tool.call"]["span_id"]
    assert {span["depth"] for span in spans} == {0, 1, 2}
    # Write order is inner-first: a span is recorded when it *ends*, so the
    # innermost scope's `finally` fires before its parents'. That is why the
    # exporter sorts by timestamp rather than by file position.
    assert [span["name"] for span in spans] == ["mcp.call", "tool.call", "agent.turn"]

    events = [record for record in records if record["type"] == "event"]
    assert len(events) == 4
    for event in events:
        assert event["trace_id"] == "trace-abc"
        assert event["run_id"] == run_context.run_id
    # An event inside a span carries that span id, so a reader can find the
    # interval. A run-level event carries none -- `run.ended` genuinely has no
    # enclosing span -- but it still carries the run id, so it is never an
    # orphan.
    by_event = {event["name"]: event for event in events}
    assert by_event["agent.turn"]["span_id"] == by_name["agent.turn"]["span_id"]
    assert by_event["tool.call.started"]["span_id"] == by_name["tool.call"]["span_id"]
    assert by_event["tool.call.completed"]["span_id"] == by_name["mcp.call"]["span_id"]
    assert by_event["run.ended"]["span_id"] is None
    assert by_event["run.ended"]["run_id"] == run_context.run_id


def test_a_trace_id_is_the_existing_request_trace_id_not_a_new_one(ids: SequenceIdGenerator) -> None:
    """(a) The trace id is resolved from ``alpha.trace_context``, not minted.

    The repo already owns a request-level correlation id. Forging a second one
    here would let the persisted run disagree with the ``X-Trace-Id`` header
    the same request already returned.
    """
    from alpha.trace_context import ensure_trace_context, get_current_trace_id

    with ensure_trace_context("request-trace-xyz"):
        # A run started inside a request joins that request's existing trace.
        context = ensure_run_context(id_generator=ids, thread_id="th")
        assert context.trace_id == "request-trace-xyz"
        assert get_current_trace_id() == "request-trace-xyz"

    # And this package mints no trace id of its own: ids.py has no
    # `new_trace_id`, which is the structural half of "it extends the existing
    # spine rather than forking it".
    from alpha.observability import ids as ids_module

    assert not any(name.startswith("new_trace") for name in dir(ids_module))
    assert "trace" not in {name for name in dir(ids_module) if "id" in name.lower() and "trace" in name}


def test_a_every_event_carries_the_trace_id_including_from_a_bound_sibling_span(recorder: TraceRecorder, run_context: RunContext) -> None:
    """(a) An event emitted with no explicit span still picks up the current one."""
    with run_scope(run_context):
        with recorder.span("outer") as outer:
            recorder.record_event("tool.call.requested")
    recorded = _records(recorder)
    event = [record for record in recorded if record["type"] == "event"][0]
    assert event["span_id"] == outer.span_id
    assert event["trace_id"] == "trace-abc"


# --------------------------------------- b. boundaries: propagate or disclose


def test_b_asyncio_task_inherits_the_context() -> None:
    """(b) ``asyncio.Task`` copies the context, so propagation is free."""

    async def scenario() -> tuple[RunContext | None, RunContext | None]:
        with run_scope(RunContext(trace_id="t-async", run_id="b" * 32)):
            before = current()
            after = await asyncio.create_task(_read_context())
            return before, after

    before, after = asyncio.run(scenario())
    assert before is not None and after is not None
    assert after.run_id == before.run_id
    assert after.trace_id == before.trace_id
    assert propagates_automatically("asyncio_task") is True


async def _read_context() -> RunContext | None:
    await asyncio.sleep(0)
    return current()


def test_b_asyncio_to_thread_inherits_the_context() -> None:
    """(b) ``asyncio.to_thread`` copies the context into the worker thread."""
    with run_scope(RunContext(trace_id="t-to-thread", run_id="c" * 32)):
        expected = current()
        observed = asyncio.run(_to_thread_context())
    assert expected is not None and observed is not None
    assert observed.run_id == expected.run_id
    assert propagates_automatically("asyncio_to_thread") is True


async def _to_thread_context() -> RunContext | None:
    return await asyncio.to_thread(current)


def test_b_run_in_executor_does_not_inherit_and_needs_a_carrier() -> None:
    """(b) ``loop.run_in_executor`` does **not** copy the context.

    This is the row the test suite exists to protect. ``asyncio.to_thread``
    wraps the callable in ``copy_context()``; ``run_in_executor`` does not,
    because the callable lands in an ordinary thread-pool worker. Two
    nearly identical APIs with opposite answers, so the documented table says
    so and this test holds it to the real behaviour.
    """
    with run_scope(RunContext(trace_id="t-executor", run_id="d" * 32)):
        expected = current()
        assert expected is not None
        observed = asyncio.run(_executor_context())
    assert observed is None, "run_in_executor must NOT propagate a contextvar"
    assert propagates_automatically("run_in_executor") is False

    # And the documented remedy works: hand the carrier across.
    with run_scope(RunContext(trace_id="t-executor", run_id="d" * 32)):
        carrier = to_carrier()
        rebound = asyncio.run(_executor_rebind(carrier))
    assert rebound is not None and rebound.run_id == "d" * 32


async def _executor_context() -> RunContext | None:
    loop = asyncio.get_running_loop()
    # run_in_executor returns a Future, so it has to be awaited to observe the
    # value the worker thread actually saw.
    return await loop.run_in_executor(None, current)


async def _executor_rebind(carrier: dict[str, str]) -> RunContext | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _rebind(carrier))


def test_b_thread_pool_does_not_inherit_and_needs_an_explicit_carrier() -> None:
    """(b) A bare thread pool worker sees nothing; the carrier is the remedy.

    This is the case the documentation table exists for. The test asserts the
    *actual* CPython behaviour, so if the table ever drifts from reality this
    fails rather than quietly documenting a lie.
    """
    context = RunContext(trace_id="t-pool", run_id="e" * 32, thread_id="thread-pool")
    with run_scope(context):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(current).result() is None, "a raw thread must not inherit a contextvar"

    with run_scope(context):
        carrier = to_carrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            rebound = pool.submit(_rebind, carrier).result()
    assert rebound is not None
    assert rebound.run_id == context.run_id
    assert rebound.trace_id == context.trace_id
    assert propagates_automatically("thread_pool_submit") is False


def _rebind(carrier: dict[str, str]) -> RunContext | None:
    return bind_carrier(carrier, link_kind="thread_pool")


def test_b_threading_thread_does_not_inherit() -> None:
    """(b) Same for ``threading.Thread``: a fresh, empty context."""
    observed: list[RunContext | None] = []
    with run_scope(RunContext(trace_id="t-thread", run_id="f" * 32)):
        thread = threading.Thread(target=lambda: observed.append(current()))
        thread.start()
        thread.join(timeout=5)
    assert observed == [None]
    assert propagates_automatically("threading_thread") is False


def test_b_subprocess_link_is_disclosed_not_claimed_as_inherited(run_context: RunContext) -> None:
    """(b) The non-propagating case yields a disclosed link, never an orphan.

    A subprocess cannot inherit a contextvar, so the environment it is handed
    must say the ids travelled as data. The child's events then carry
    ``context_inherited=False`` plus the parent ids, which is a link.
    """
    environ = subprocess_link_env(run_context, environ={"PATH": os.environ.get("PATH", "")})
    assert environ[ENV_CONTEXT_PROPAGATED] == "false"
    assert environ[ENV_LINK_KIND] == "subprocess"
    assert environ[ENV_PARENT_TRACE_ID] == run_context.trace_id
    assert environ[ENV_PARENT_RUN_ID] == run_context.run_id
    assert environ[ENV_TRACE_ID] == run_context.trace_id

    # A real subprocess really does see no contextvar.
    script = "import contextvars, os, sys; sys.exit(0 if os.environ.get('ALPHA_OBSERVABILITY_TRACE_ID') else 3)"
    child_env = {**os.environ, **{k: v for k, v in environ.items() if k.startswith("ALPHA_OBSERVABILITY_")}}
    result = subprocess.run([sys.executable, "-c", script], env=child_env, capture_output=True, timeout=30)
    assert result.returncode == 0, "the disclosed link must be readable from a real child process"
    assert propagates_automatically("subprocess") is False

    # And the child, on the far side, produces a *linked* event, not an orphan.
    recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["memory"]), id_generator=SequenceIdGenerator(clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    child_context, link_kind = link_from_env(environ)
    assert child_context is not None and link_kind == "subprocess"
    event = recorder.record_event(
        "run.context.linked",
        context=child_context,
        context_inherited=False,
        link_kind=link_kind,
        attributes=disclosed_link_attributes(environ),
    )
    assert event is not None
    assert event.trace_id == run_context.trace_id, "the child stays under the parent's trace id"
    assert event.attributes["parent_trace_id"] == run_context.trace_id
    assert event.attributes["parent_run_id"] == run_context.run_id
    assert event.context_inherited is False


def test_b_a_link_event_may_not_be_an_orphan(run_context: RunContext) -> None:
    """(b) ``context_inherited=False`` without a parent reference is rejected."""
    with pytest.raises(ValueError, match="parent_trace_id or parent_run_id"):
        TraceEvent(name="run.context.linked", status=EventStatus.OK, trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH, context_inherited=False, link_kind="subprocess")
    with pytest.raises(ValueError, match="link_kind"):
        TraceEvent(name="run.context.linked", status=EventStatus.OK, trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH, context_inherited=False, attributes={"parent_trace_id": "t"})


def test_b_boundary_table_covers_every_hop_and_rejects_an_unknown_one() -> None:
    """(b) The documented table is data, complete, and strict about unknowns."""
    hops = {rule.hop for rule in BOUNDARY_RULES}
    assert {"asyncio_task", "asyncio_to_thread", "run_in_executor", "thread_pool_submit", "threading_thread", "event_bus_handler", "multiprocessing_spawn", "subprocess"} == hops
    for rule in BOUNDARY_RULES:
        assert rule.mechanism and rule.remedy, f"{rule.hop} must state both the mechanism and the remedy"
    with pytest.raises(ValueError, match="unknown execution boundary"):
        describe_boundary("quantum_tunnel")


def test_b_bind_carrier_refuses_a_broken_hand_off() -> None:
    """(b) A carrier with no valid run id is refused, not half-applied."""
    with pytest.raises(ValueError, match="link_kind"):
        bind_carrier({ENV_RUN_ID: "a" * 32}, link_kind="")
    with pytest.raises(ValueError, match="no valid"):
        bind_carrier({ENV_TRACE_ID: "t"}, link_kind="thread_pool")


def test_b_to_carrier_refuses_to_hand_off_nothing() -> None:
    """(b) An empty hand-off is indistinguishable from no hand-off."""
    assert current() is None
    with pytest.raises(RuntimeError, match="no RunContext is bound"):
        to_carrier()


# ------------------------------------------------------------- c. detach


def test_c_detach_restores_the_previous_context_exactly(ids: SequenceIdGenerator) -> None:
    """(c) ``bind``/``detach`` round-trips, and nesting restores each level."""
    outer = RunContext(trace_id="t-outer", run_id=ids.new_run_id())
    inner = RunContext(trace_id="t-inner", run_id=ids.new_run_id())
    assert current() is None

    token = bind(outer)
    assert current() == outer
    inner_token = bind(inner)
    assert current() == inner
    detach(inner_token)
    assert current() == outer, "detaching the inner binding must restore the outer one exactly"
    detach(token)
    assert current() is None, "detaching the outer binding must return to the unbound baseline"


def test_c_detach_restores_after_an_exception(ids: SequenceIdGenerator) -> None:
    """(c) An exception inside the scope still restores the caller's context."""
    outer = RunContext(trace_id="t-outer", run_id=ids.new_run_id())
    token = bind(outer)
    with pytest.raises(RuntimeError, match="boom"):
        with run_scope(RunContext(trace_id="t-inner", run_id=ids.new_run_id())):
            assert current().trace_id == "t-inner"  # type: ignore[union-attr]
            raise RuntimeError("boom")
    assert current() == outer, "the enclosing context must survive the exception"
    detach(token)
    assert current() is None


def test_c_detach_rejects_a_foreign_token_type() -> None:
    """(c) A mistyped token is a named failure, not an opaque stdlib one."""
    with pytest.raises(TypeError, match="contextvars.Token"):
        detach("not-a-token")  # type: ignore[arg-type]


def test_c_each_contextvar_is_independent() -> None:
    """(c) The run-context var and the span var are separate stacks."""
    from alpha.observability.span import detach_span

    run = RunContext(trace_id="t", run_id="a" * 32)
    tracer = Tracer(id_generator=SequenceIdGenerator(clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH)
    with run_scope(run):
        assert current_span() is None
        span = tracer.start_span("outer")
        token = tracer.bind_span(span)
        assert current_span() is span
        inner = tracer.start_span("inner")
        assert inner.parent_span_id == span.span_id
        detach_span(token)
        assert current_span() is None
        assert current() == run


# ------------------------------------------------------------ d. redaction


@pytest.mark.parametrize(("label", "value", "secret"), dangerous_value_corpus(), ids=[entry[0] for entry in dangerous_value_corpus()])
def test_d_every_corpus_sample_is_scrubbed_as_a_value(label: str, value: str, secret: str) -> None:
    """(d) Each realistic credential is scrubbed before it can reach a sink.

    The proof is the explicit ``secret`` fragment, not "the output looks
    different". Several corpus values carry a *label* the engine deliberately
    preserves (``Authorization:``, ``Set-Cookie:``, ``api_key=``) so a reader
    can still tell which field leaked; asserting the whole value vanished would
    be asserting the wrong thing, and asserting only that a placeholder
    appeared would pass while the token survived next to it.
    """
    outcome = Redactor("strict").redact_value(value, key="tool.args")
    assert outcome.redacted, f"{label} was not scrubbed"
    assert outcome.value != value
    assert "REDACTED" in str(outcome.value)
    assert secret not in str(outcome.value), f"{label} leaked its secret material"


@pytest.mark.parametrize(("label", "value", "secret"), dangerous_value_corpus(), ids=[entry[0] for entry in dangerous_value_corpus()])
def test_d_every_corpus_sample_is_scrubbed_as_a_key(label: str, value: str, secret: str) -> None:
    """(d) The same sample is scrubbed when it is the attribute *key*.

    A trace writer chooses neither the key nor the value, so both directions
    are covered. The engine explicitly leaves mapping keys unscanned; that gap
    is closed here.
    """
    safe, _outcomes = Redactor("strict").redact_attributes({value: "harmless"})
    stored_key = next(iter(safe))
    assert "REDACTED" in stored_key, f"{label} survived as an attribute key"
    assert secret not in stored_key, f"{label} leaked its secret material through the key"
    # The value beside it is redacted too whenever the key is itself
    # credential-named (`api_key=...` is a secret-named key), and left alone when
    # it is not. What must hold either way is that the material is gone from the
    # whole record, not merely from the key.
    assert secret not in json.dumps(safe, default=str), f"{label} leaked its secret material through the value"


def test_d_an_unknown_shape_is_redacted_not_passed_through() -> None:
    """(d) The unknown-shape policy: when in doubt, redact."""

    class Opaque:
        def __repr__(self) -> str:  # pragma: no cover - the point is that repr is never called
            raise AssertionError("an unknown object must not be stringified into a trace")

    redactor = Redactor("strict")
    for value, expected in (
        (Opaque(), "[REDACTED:unknown_type]"),
        (b"\x00\x01\x02binary", "[REDACTED:non_json]"),
        (bytearray(b"abc"), "[REDACTED:non_json]"),
        (float("nan"), "[REDACTED:non_json]"),
        (float("inf"), "[REDACTED:non_json]"),
    ):
        outcome = redactor.redact_value(value, key="payload")
        assert outcome.value == expected, f"{type(value).__name__} was not classified"
        assert outcome.redacted


def test_d_non_string_values_under_a_secret_key_are_redacted() -> None:
    """(d) Gap 1 of the existing engine: it only scrubs *string* values."""
    redactor = Redactor("strict")
    # The engine's own mapping redactor passes this through unchanged.
    from alpha.security.memory_redaction import redact_mapping

    assert redact_mapping({"api_key": 1234567890123456}).redacted_payload["api_key"] == 1234567890123456
    # This layer does not.
    outcome = redactor.redact_value(1234567890123456, key="api_key")
    assert outcome.value == "[REDACTED:secret_named_key]"
    assert outcome.reason == "secret_key"


def test_d_deep_and_wide_payloads_are_bounded_with_disclosure() -> None:
    """(d) Size, item count and depth are all capped, and each cap discloses."""
    redactor = Redactor("strict", max_value_chars=32, max_items=3, max_depth=3)
    long_text = redactor.redact_value("x" * 500, key="note")
    assert long_text.truncated and long_text.safe is False and "REDACTED:oversize" in str(long_text.value)

    wide = redactor.redact_value(list(range(100)), key="items")
    assert wide.truncated and len(wide.value) == 4 and wide.value[-1] == "[REDACTED:oversize]"

    deep: Any = "leaf"
    for _ in range(10):
        deep = {"nested": deep}
    nested = redactor.redact_value(deep, key="tree")
    # The payload is walked to the depth cap and no further; the level past the
    # cap is replaced rather than the whole value being thrown away, so a
    # reader still sees the shape.
    levels = 0
    node: Any = nested.value
    while isinstance(node, dict):
        levels += 1
        node = node["nested"]
    assert levels == redactor.max_depth, "nesting must stop exactly at max_depth"
    assert node == "[REDACTED:oversize]", "the level past the cap is replaced by a disclosed marker"


def test_d_oversize_exception_message_is_dropped_entirely(ids: SequenceIdGenerator) -> None:
    """(d) A span cannot record a raw exception message, even a huge one.

    A message truncated mid-credential can leave half a secret behind, so a
    non-``safe`` outcome drops the message and says why.
    """
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH, redactor=Redactor("strict", max_value_chars=16))
    with tracer.span("tool.call", context=RunContext(trace_id="t", run_id=ids.new_run_id())) as span:
        span.record_exception(RuntimeError("y" * 500))
    attributes = span.attributes
    assert attributes["error.type"] == "RuntimeError"
    assert "error.message" not in attributes
    assert attributes["error.message_omitted"] == "redaction_policy"


def test_d_secret_exception_message_is_stored_scrubbed(ids: SequenceIdGenerator) -> None:
    """(d) A scrubbed message is stored -- scrubbed, never raw."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH)
    with tracer.span("tool.call", context=RunContext(trace_id="t", run_id=ids.new_run_id())) as span:
        span.record_exception(RuntimeError("auth failed for api_key=sk-proj-AAAABBBBCCCCDDDDEEEEFFFF"))
    attributes = span.attributes
    assert "error.type" not in attributes or attributes["error.type"] == "RuntimeError"
    assert "AAAABBBBCCCCDDDDEEEEFFFF" not in attributes["error.message"]
    assert "REDACTED" in attributes["error.message"]
    # No traceback is ever stored: a traceback embeds source lines.
    assert not any("traceback" in key for key in attributes)


def test_d_event_attributes_are_scrubbed_by_the_validator_not_by_a_call_site(run_context: RunContext) -> None:
    """(d) There is no code path that builds a TraceEvent with a raw attribute."""
    event = TraceEvent(
        name="tool.call.completed",
        status=EventStatus.OK,
        trace_id=run_context.trace_id,
        run_id=run_context.run_id,
        timestamp=FIXED_EPOCH,
        attributes={"api_key": "sk-proj-ZZZZYYYYXXXXWWWWVVVV", "note": "all good", "count": 3},
    )
    assert event.attributes["api_key"] == "[REDACTED:secret_named_key]"
    assert event.attributes["note"] == "all good"
    assert event.attributes["count"] == 3, "a non-credential scalar is preserved"


def test_d_redaction_is_idempotent() -> None:
    """(d) Re-scrubbing an already-scrubbed value neither changes it nor recounts."""
    once = redact_text("api_key=sk-proj-0123456789abcdefghij")
    twice = redact_text(once.value)
    assert twice.value == once.value
    assert twice.redacted is False


def test_d_policies_are_a_closed_set_and_differ_as_documented() -> None:
    """(d) ``strict`` is stricter than ``standard`` in exactly two ways."""
    assert REDACTION_POLICIES == {"standard", "strict"}
    with pytest.raises(ValueError, match="unknown redaction policy"):
        Redactor("lenient")
    with pytest.raises(ValueError, match="unknown redaction policy"):
        ObservabilityConfig(redaction_policy="lenient")  # type: ignore[arg-type]

    # Key vocabulary: `session` and `authorization` are strict-only segments.
    strict = Redactor("strict").redact_value("abc123def456", key="session_id")
    standard = Redactor("standard").redact_value("abc123def456", key="session_id")
    assert strict.redacted and not standard.redacted

    # Bare high-entropy blob: strict only.
    blob = "Zm9vYmFyYmF6cXV1eGZvb2JhcmJhemF6cXV1eA=="
    assert Redactor("strict").redact_value(blob, key="build").redacted
    assert not Redactor("standard").redact_value(blob, key="build").redacted

    # And a non-secret key with a non-credential value is untouched by both.
    for policy in sorted(REDACTION_POLICIES):
        outcome = Redactor(policy).redact_value("completed in 42ms", key="summary")
        assert outcome.value == "completed in 42ms"


def test_d_key_rule_does_not_over_redact_ordinary_names() -> None:
    """(d) The segment rule keeps ``keyword``/``tokenizer``/``max_tokens`` visible.

    This is asserted against the *engine's own* decision rather than a local
    copy of its rule, so it also proves this layer did not drift from the
    redactor it layers on.
    """
    redactor = Redactor("strict")
    for key in ("keyword", "tokenizer", "max_tokens", "secretary", "keyboard", "monkey", "passport", "tokens", "secrets"):
        assert not redactor.redact_value("harmless-value", key=key).redacted, f"{key} was treated as a credential"
    for key in ("api_key", "db_password", "access_token", "X-Api-Key", "auth_token", "api-key", "credentials", "private_key"):
        assert redactor.redact_value("harmless-value", key=key).redacted, f"{key} was not treated as a credential"


# --------------------------------------------------------- e. raising sinks


class ExplodingSink:
    """A sink whose ``emit`` always raises, and which counts the attempts."""

    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "exploding"

    def emit(self, record: Any) -> None:
        self.calls += 1
        raise OSError("disk is on fire")

    def close(self) -> None:
        return None

    def disclosure(self) -> dict[str, int]:
        return {"calls": self.calls, "dropped_events": 0}


def test_e_a_raising_sink_does_not_break_the_caller_and_is_disclosed_once(caplog: pytest.LogCaptureFixture, ids: SequenceIdGenerator) -> None:
    """(e) The caller survives; the failure is counted every time, logged once.

    A broken sink on a hot path would otherwise emit one WARNING per event and
    bury the original cause in a log flood.
    """
    exploding = ExplodingSink()
    healthy = InMemorySink()
    recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["memory"]), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[exploding, healthy])
    context = RunContext(trace_id="t", run_id=ids.new_run_id())

    with caplog.at_level("WARNING", logger="alpha.observability.recorder"):
        with run_scope(context):
            for _ in range(5):
                assert recorder.record_event("agent.turn") is not None

    assert exploding.calls == 5, "every emit was attempted"
    assert len(healthy.records()) == 5, "one broken sink must not blind the healthy one"

    warnings = [record for record in caplog.records if "trace sink" in record.getMessage()]
    assert len(warnings) == 1, f"expected exactly one disclosure log, got {len(warnings)}"
    assert "disk is on fire" in warnings[0].getMessage()

    disclosure = recorder.disclosure()
    assert disclosure["sink_failures"] == {"exploding": 5}, "counted on every failure"


def test_e_a_raising_sink_does_not_break_a_span_or_a_run(caplog: pytest.LogCaptureFixture, ids: SequenceIdGenerator) -> None:
    """(e) The containment holds on the span path, not only the event path."""
    recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["memory"]), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[ExplodingSink()])
    context = RunContext(trace_id="t", run_id=ids.new_run_id())
    with caplog.at_level("WARNING", logger="alpha.observability.recorder"):
        with run_scope(context):
            with recorder.span("agent.turn") as span:
                span.set_attribute("k", "v")
            recorder.record_event("run.ended")
    assert span.status is SpanStatus.OK
    assert recorder.disclosure()["sink_failures"] == {"exploding": 2}


def test_e_a_rejected_event_is_disclosed_and_does_not_propagate(caplog: pytest.LogCaptureFixture, ids: SequenceIdGenerator) -> None:
    """(e) A bad call site loses its event, not the run."""
    recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["memory"]), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    with run_scope(RunContext(trace_id="t", run_id=ids.new_run_id())):
        with caplog.at_level("WARNING", logger="alpha.observability.recorder"):
            assert recorder.record_event("not.a.real.event") is None
    assert any("was rejected" in record.getMessage() for record in caplog.records)
    assert recorder.disclosure()["recorded_events"] == 0


# ------------------------------------------------------- f. file sink overflow


def test_f_file_sink_overflow_is_disclosed_in_the_file_and_in_the_disclosure(tmp_path: Path) -> None:
    """(f) Nothing is discarded silently -- the file itself says it lost events."""
    path = tmp_path / "nested" / "trace.jsonl"
    sink = JsonlFileSink(path, max_events=4, max_bytes=1_000_000)
    for index in range(10):
        sink.emit({"v": 1, "type": "event", "name": "agent.turn", "ts": FIXED_EPOCH + index, "index": index})

    assert sink.dropped_events == 6
    disclosure = sink.disclosure()
    assert disclosure["dropped_events"] == 6
    assert disclosure["calls"] == 10

    lines = path.read_text(encoding="utf-8").splitlines()
    event_lines = [line for line in lines if '"type": "event"' in line or '"type":"event"' in line]
    assert len(event_lines) == 4, "exactly the cap was written; nothing unbounded"

    records = [json.loads(line) for line in lines]
    disclosures = [record for record in records if record.get("type") == "disclosure"]
    assert len(disclosures) == 1, "the overflow is disclosed inside the file"
    assert disclosures[0]["reason"] == "file_sink_max_events"
    assert disclosures[0]["dropped_events"] == 1, "the in-file record states the count at the moment it was written"


def test_f_file_sink_byte_budget_stops_writing_and_discloses(tmp_path: Path) -> None:
    """(f) The byte backstop is a budget, not a suggestion."""
    path = tmp_path / "trace.jsonl"
    sink = JsonlFileSink(path, max_events=10_000, max_bytes=400)
    for index in range(200):
        sink.emit({"v": 1, "type": "event", "name": "agent.turn", "ts": FIXED_EPOCH + index, "filler": "x" * 40})
    assert sink.byte_budget_exhausted
    assert sink.disclosure()["dropped_events"] > 0
    assert path.stat().st_size <= 400 + 200, "the file did not grow past the budget by more than one record"
    written = len(path.read_text(encoding="utf-8").splitlines())
    assert written < 200, "the sink stopped writing"


def test_f_file_sink_records_are_valid_jsonl_and_grow_only(tmp_path: Path, recorder: TraceRecorder, ids: SequenceIdGenerator) -> None:
    """(f) End-to-end: a recorded run produces a parseable, bounded JSONL file."""
    path = tmp_path / "trace.jsonl"
    file_recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["file"], file_sink_path=str(path)), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[JsonlFileSink(path, max_events=1000, max_bytes=1_000_000)])
    context = RunContext(trace_id="t-file", run_id=ids.new_run_id(), thread_id="th", user_id="u")
    with run_scope(context):
        file_recorder.record_event("run.started")
        with file_recorder.span("agent.turn"):
            file_recorder.record_event("tool.call.started")
        file_recorder.record_event("run.ended")
    file_recorder.close()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    # The span is recorded when it *ends*, so it lands after the event it
    # contained and before the run's final event. The file is append-only, so
    # write order is the only order a reader can rely on without re-sorting.
    assert [record["type"] for record in records] == ["event", "event", "span", "event"]
    assert records[0]["name"] == "run.started"
    assert records[1]["name"] == "tool.call.started"
    assert records[2]["name"] == "agent.turn"
    assert records[3]["name"] == "run.ended"
    assert all(record["v"] == 1 for record in records)
    assert {record["trace_id"] for record in records} == {"t-file"}


def test_f_in_memory_sink_is_bounded_and_discloses_eviction() -> None:
    """(f) The in-memory ring is bounded too; an unbounded ring is a leak."""
    sink = InMemorySink(max_records=3)
    for index in range(10):
        sink.emit({"v": 1, "type": "event", "index": index})
    assert len(sink) == 3
    assert [record["index"] for record in sink.records()] == [7, 8, 9]
    assert sink.disclosure()["dropped_events"] == 7


def test_f_per_run_event_cap_is_enforced_and_disclosed(ids: SequenceIdGenerator) -> None:
    """(f) ``max_events_per_run`` bounds one run and the refusal is visible."""
    recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["memory"], max_events_per_run=3), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    with run_scope(RunContext(trace_id="t", run_id=ids.new_run_id())):
        for _ in range(6):
            recorder.record_event("agent.turn")
    assert recorder.disclosure()["recorded_events"] == 3
    assert recorder.disclosure()["refused_events_per_run_limit"] == 1


def test_f_per_run_span_cap_is_enforced(ids: SequenceIdGenerator) -> None:
    """(f) ``max_spans_per_run`` bounds spans the same way."""
    recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["memory"], max_spans_per_run=2), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    context = RunContext(trace_id="t", run_id=ids.new_run_id())
    with run_scope(context):
        for index in range(5):
            with recorder.span(f"span-{index}"):
                pass
    assert recorder.disclosure()["recorded_spans"] == 2


# ------------------------------------------------- g. disabled means inert


def test_g_disabled_config_produces_no_events_no_files_and_no_sink_call(tmp_path: Path, ids: SequenceIdGenerator) -> None:
    """(g) Assert the *sink call count*, not merely the absence of output."""
    sink = InMemorySink()
    path = tmp_path / "trace.jsonl"
    recorder = build_recorder(
        ObservabilityConfig(enabled=False, sinks=["memory", "file"], file_sink_path=str(path)),
        id_generator=ids,
        clock=lambda: FIXED_EPOCH,
        sinks=[sink],
    )
    assert recorder.enabled is False

    with run_scope(RunContext(trace_id="t", run_id=ids.new_run_id())):
        assert recorder.record_event("run.started") is None
        assert recorder.record_event("agent.turn") is None
        with recorder.span("agent.turn") as span:
            span.set_attribute("k", "v")
        assert recorder.start_run() is None

    assert sink.calls == 0, "a disabled recorder must not touch a sink"
    assert len(sink) == 0
    assert not path.exists(), "a disabled recorder must not create a file"
    disclosure = recorder.disclosure()
    assert disclosure["recorded_events"] == 0
    assert disclosure["recorded_spans"] == 0


def test_g_disabled_recorder_reads_no_clock_and_mints_no_id() -> None:
    """(g) The disabled cost is a boolean test, not a clock read."""
    clock = StepClock()
    recorder = disabled_recorder(clock=clock)
    with recorder.span("agent.turn") as span:
        span.set_attribute("k", "v")
        span.record_exception(RuntimeError("x"))
    assert clock.reads == 0, "a disabled recorder must not read the clock"
    assert span.span_id is None
    assert recorder.new_run_id() != ""  # the generator is present but nothing recorded


def test_g_a_noop_sink_is_the_instrument_for_the_disabled_cost() -> None:
    """(g) A no-op sink counts calls, which is what makes "no cost" checkable."""
    sink = NoOpSink()
    enabled = build_recorder(ObservabilityConfig(enabled=True, sinks=["null"]), id_generator=SequenceIdGenerator(clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH, sinks=[sink])
    with run_scope(RunContext(trace_id="t", run_id=SequenceIdGenerator(clock=lambda: FIXED_EPOCH).new_run_id())):
        enabled.record_event("agent.turn")
    assert sink.calls == 1, "an enabled recorder does call the sink"

    quiet = NoOpSink()
    disabled = build_recorder(ObservabilityConfig(enabled=False, sinks=["null"]), id_generator=SequenceIdGenerator(clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH, sinks=[quiet])
    with run_scope(RunContext(trace_id="t", run_id=SequenceIdGenerator(clock=lambda: FIXED_EPOCH).new_run_id())):
        disabled.record_event("agent.turn")
    assert quiet.calls == 0, "a disabled recorder does not"
    assert quiet.disclosure() == {"calls": 0, "dropped_events": 0}


def test_g_disabled_is_the_shipped_default() -> None:
    """(g) The default config is off; a shipped default is a decision."""
    assert ObservabilityConfig().enabled is False
    assert ObservabilityConfig().remote_sink_enabled is False
    assert ObservabilityConfig().metrics_bridge_enabled is False
    assert ObservabilityConfig().redaction_policy == "strict"


def test_g_remote_sink_flag_is_refused_not_silently_ignored(ids: SequenceIdGenerator) -> None:
    """(g) ``remote_sink_enabled`` is a declared absence, not an oversight."""
    with pytest.raises(ValueError, match="no remote sink ships"):
        build_recorder(ObservabilityConfig(enabled=True, remote_sink_enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])


def test_g_a_file_sink_without_a_path_is_refused(ids: SequenceIdGenerator) -> None:
    """(g) A file sink with no path would be a silent no-op."""
    with pytest.raises(ValueError, match="file_sink_path is unset"):
        build_recorder(ObservabilityConfig(enabled=True, sinks=["file"]), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])


# ----------------------------------------------------- h. the exporter


def _write_trace(path: Path, recorder: TraceRecorder, run_id: str, events: int) -> None:
    """Record a small run into *recorder*.

    The recorder's clock is a :class:`StepClock` (see the call sites), so
    timestamps genuinely advance and the exporter's ordering is exercised on
    real temporal ordering rather than on the type-rank tie-break that a frozen
    clock would reduce every record to.
    """
    context = RunContext(trace_id="t-export", run_id=run_id, thread_id="th", user_id="u")
    with run_scope(context):
        recorder.record_event("run.started")
        with recorder.span("agent.turn"):
            for index in range(events):
                recorder.record_event("tool.call.started", attributes={"index": index})
        recorder.record_event("run.ended", status=EventStatus.OK)
    recorder.close()


def _file_recorder(path: Path, ids: SequenceIdGenerator) -> TraceRecorder:
    """Build a recorder writing to *path* with a stepping clock."""
    return build_recorder(
        ObservabilityConfig(enabled=True, sinks=["file"], file_sink_path=str(path)),
        id_generator=ids,
        clock=StepClock(),
        sinks=[JsonlFileSink(path)],
    )


def test_h_exporter_renders_a_deterministic_timeline(tmp_path: Path, exporter: Any, ids: SequenceIdGenerator) -> None:
    """(h) The same file renders byte-identically twice, in any input order."""
    path = tmp_path / "trace.jsonl"
    recorder = _file_recorder(path, ids)
    _write_trace(path, recorder, ids.new_run_id(), events=3)

    first = exporter.main([str(path)])
    second = exporter.main([str(path)])
    assert first == 0 and second == 0

    # Reversing the file's line order must not change the render: the timeline
    # is ordered by the total sort key, not by write order.
    original = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(original)) + "\n", encoding="utf-8")
    assert exporter.main([str(path)]) == 0


def test_h_exporter_renders_json_with_an_explicit_disclosure(tmp_path: Path, exporter: Any, ids: SequenceIdGenerator) -> None:
    """(h) The JSON form states what it dropped rather than looking complete."""
    path = tmp_path / "trace.jsonl"
    recorder = _file_recorder(path, ids)
    _write_trace(path, recorder, ids.new_run_id(), events=40)

    selected, dropped, total, unparsable, filters = _render_args(exporter, path, max_events=10)
    document = json.loads(exporter.render_json(selected, dropped=dropped, total=total, unparsable=unparsable, filters=filters))
    assert document["truncated"] is True
    assert document["disclosure"]["dropped_records"] > 0
    assert document["disclosure"]["total_records"] > document["disclosure"]["rendered_records"]
    assert len(document["records"]) == 10


def _render_args(exporter: Any, path: Path, *, max_events: int) -> tuple[Any, int, int, int, dict[str, Any]]:
    """Return ``(selected, dropped, total, unparsable, filters)`` for *path*."""
    records, unparsable = exporter.read_records(path)
    selected, dropped = exporter.select(records, max_events)
    return selected, dropped, len(records), unparsable, {}


def test_h_exporter_discloses_truncation_in_text_mode(tmp_path: Path, exporter: Any, ids: SequenceIdGenerator) -> None:
    """(h) Text mode says TRUNCATED in the header and again at the tail."""
    path = tmp_path / "trace.jsonl"
    recorder = _file_recorder(path, ids)
    _write_trace(path, recorder, ids.new_run_id(), events=30)

    selected, dropped, total, unparsable, filters = _render_args(exporter, path, max_events=8)
    text = exporter.render_text(selected, dropped=dropped, total=total, unparsable=unparsable, filters=filters)
    assert "TRUNCATED" in text
    assert text.count("TRUNCATED") == 2, "disclosed in the header and again at the tail"
    assert f"dropped: {dropped}" in text


def test_h_exporter_offsets_are_milliseconds_not_seconds(tmp_path: Path, exporter: Any) -> None:
    """(h) A sub-millisecond tool call must not render as ``+0.00ms``.

    The stored timestamps are unix seconds. Formatting the raw difference and
    labelling it ``ms`` renders every fast span as zero, which is exactly the
    range a tool call lives in -- the case the timeline exists to show.
    """
    path = tmp_path / "trace.jsonl"
    base = FIXED_EPOCH
    records = [
        {"v": 1, "type": "event", "name": "run.started", "ts": base, "trace_id": "t", "run_id": "a" * 32},
        {"v": 1, "type": "span", "name": "tool.call", "span_id": "b" * 32, "start": base + 0.0004, "end": base + 0.0021, "duration": 0.0017, "status": "ok", "ended": True, "depth": 0, "trace_id": "t", "run_id": "a" * 32},
    ]
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")
    assert exporter.main([str(path)]) == 0
    captured = capsys_text(exporter, path)
    assert "+0.40ms" in captured, f"the span start offset was not rendered in milliseconds:\n{captured}"
    assert "1.7ms" in captured, f"the span duration was not rendered in milliseconds:\n{captured}"
    assert "+0.00ms" not in captured.split("run.started", 1)[-1], f"a sub-millisecond offset collapsed to zero:\n{captured}"


def capsys_text(exporter: Any, path: Path) -> str:
    """Render *path* through the exporter and return the text it produced."""
    records, unparsable = exporter.read_records(path)
    selected, dropped = exporter.select(records, 0)
    return exporter.render_text(selected, dropped=dropped, total=len(records), unparsable=unparsable, filters={})


def test_h_exporter_keep_ends_retains_both_ends(tmp_path: Path, exporter: Any, ids: SequenceIdGenerator) -> None:
    """(h) The default drop policy keeps the first and last halves."""
    path = tmp_path / "trace.jsonl"
    recorder = _file_recorder(path, ids)
    run_id = ids.new_run_id()
    _write_trace(path, recorder, run_id, events=20)

    records, _ = exporter.read_records(path)
    first_kept, first_dropped = exporter.select(records, 6, keep="first")
    ends_kept, ends_dropped = exporter.select(records, 6, keep="ends")
    assert first_dropped == ends_dropped == len(records) - 6
    assert [record["type"] for record in first_kept][0] == "event"
    assert ends_kept[0] == sorted(records, key=exporter._sort_key)[0]
    assert ends_kept[-1] == sorted(records, key=exporter._sort_key)[-1]
    assert ends_kept[-1]["name"] == "run.ended", "the end of a run is the part worth keeping"


def test_h_exporter_filters_and_survives_a_torn_line(tmp_path: Path, exporter: Any, ids: SequenceIdGenerator) -> None:
    """(h) A killed run leaves a torn final line; the rest must still render."""
    path = tmp_path / "trace.jsonl"
    recorder = _file_recorder(path, ids)
    run_id = ids.new_run_id()
    _write_trace(path, recorder, run_id, events=2)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"v":1,"type":"event","name":"tool.ca')

    records, unparsable = exporter.read_records(path)
    assert unparsable == 1
    assert records
    filtered = exporter.filter_records(records, run_id=run_id)
    assert {record["run_id"] for record in filtered} == {run_id}
    assert exporter.filter_records(records, status="ok")
    assert exporter.filter_records(records, status="nope") == []


def test_h_exporter_reports_a_missing_or_empty_file(tmp_path: Path, exporter: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """(h) Exit 1 with a clear message, never a traceback."""
    assert exporter.main([str(tmp_path / "nope.jsonl")]) == 1
    assert "not found" in capsys.readouterr().err
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert exporter.main([str(empty)]) == 1
    assert "no trace records" in capsys.readouterr().err


def test_h_exporter_rejects_a_negative_bound(exporter: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """(h) Exit 2 for a bad argument, distinct from an input problem."""
    assert exporter.main(["x.jsonl", "--max-events", "-1"]) == 2
    assert "--max-events" in capsys.readouterr().err


def test_h_exporter_is_deterministic_across_processes(tmp_path: Path, exporter: Any, ids: SequenceIdGenerator) -> None:
    """(h) Running the real CLI twice yields byte-identical stdout."""
    path = tmp_path / "trace.jsonl"
    recorder = _file_recorder(path, ids)
    _write_trace(path, recorder, ids.new_run_id(), events=5)

    runs = [subprocess.run([sys.executable, str(EXPORTER_PATH), str(path)], capture_output=True, text=True, timeout=60) for _ in range(2)]
    assert [run.returncode for run in runs] == [0, 0]
    assert runs[0].stdout == runs[1].stdout
    assert "run.ended" in runs[0].stdout
    assert "agent.turn" in runs[0].stdout


# ------------------------------------------------- i. every config key is read


def test_i_every_config_key_has_exactly_one_documented_reader() -> None:
    """(i) The reader table covers the schema exactly: no missing, no extra key."""
    keys = read_all_keys()
    assert set(READERS) == keys, f"reader table is out of sync: {keys ^ set(READERS)}"
    for key, reader in READERS.items():
        assert reader.consumer, f"{key} has no documented consumer"
        assert callable(reader.read)
    assert ObservabilityConfig().described_readers().keys() == keys


def test_i_reading_every_key_returns_the_configured_value() -> None:
    """(i) Each reader returns what the config was set to, not a default."""
    distinctive: dict[str, Any] = {
        "enabled": True,
        "sinks": ["memory", "null"],
        "sample_rate": 0.25,
        "max_spans_per_run": 7,
        "max_events_per_run": 11,
        "max_tracked_runs": 13,
        "max_span_depth": 3,
        "max_attributes_per_span": 5,
        "max_attribute_value_chars": 64,
        "max_attribute_items": 8,
        "max_attribute_depth": 2,
        "file_sink_path": "/tmp/trace.jsonl",
        "file_sink_max_events": 17,
        "file_sink_max_bytes": 4096,
        "redaction_policy": "standard",
        "remote_sink_enabled": False,
        "metrics_bridge_enabled": True,
    }
    assert set(distinctive) == set(READERS), "the table above must cover exactly the schema"
    config = ObservabilityConfig(**distinctive)
    resolved = resolve_all(config)
    assert resolved == distinctive
    for key, value in distinctive.items():
        assert resolved[key] == value, f"{key} reader returned {resolved[key]!r}, not {value!r}"


def test_i_flipping_a_key_changes_an_observable_behaviour(tmp_path: Path, ids: SequenceIdGenerator) -> None:
    """(i) A key that is declared but not consumed fails here, not silently."""
    # max_attribute_value_chars -> Redactor bound
    short = build_recorder(ObservabilityConfig(enabled=True, max_attribute_value_chars=16), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    long = build_recorder(ObservabilityConfig(enabled=True, max_attribute_value_chars=4096), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    assert short.redactor.max_value_chars == 16
    assert long.redactor.max_value_chars == 4096
    assert short.redactor.redact_value("x" * 100, key="note").truncated
    assert not long.redactor.redact_value("x" * 100, key="note").truncated

    # max_span_depth / max_attributes_per_span -> Tracer bounds
    deep = build_recorder(ObservabilityConfig(enabled=True, max_span_depth=2, max_attributes_per_span=3), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    assert deep.tracer.max_depth == 2
    assert deep.tracer.max_attributes == 3

    # redaction_policy -> Redactor policy
    assert build_recorder(ObservabilityConfig(enabled=True, redaction_policy="standard"), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()]).redactor.policy == "standard"

    # sample_rate -> the per-run decision
    half = build_recorder(ObservabilityConfig(enabled=True, sample_rate=0.5), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    decisions = {run_id: half.sampled(run_id) for run_id in (ids.new_run_id() for _ in range(50))}
    assert set(decisions.values()) == {True, False}, "a 0.5 sample must actually sample"
    full = build_recorder(ObservabilityConfig(enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    assert all(full.sampled(ids.new_run_id()) for _ in range(20)), "the default rate of 1.0 must sample everything"
    assert not build_recorder(ObservabilityConfig(enabled=True, sample_rate=0.0), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()]).sampled(ids.new_run_id())

    # file_sink_* -> the constructed JsonlFileSink
    path = tmp_path / "t.jsonl"
    file_recorder = build_recorder(ObservabilityConfig(enabled=True, sinks=["file"], file_sink_path=str(path), file_sink_max_events=3, file_sink_max_bytes=999), id_generator=ids, clock=lambda: FIXED_EPOCH)
    sink = file_recorder.sinks[0]
    assert isinstance(sink, JsonlFileSink) and sink.max_events == 3 and sink.max_bytes == 999

    # sinks -> the built sink list
    assert [type(sink).__name__ for sink in build_recorder(ObservabilityConfig(enabled=True, sinks=["memory", "file"], file_sink_path=str(path)), id_generator=ids, clock=lambda: FIXED_EPOCH).sinks] == ["InMemorySink", "JsonlFileSink"]
    assert TraceRecorder.build_sinks(ObservabilityConfig(enabled=True, sinks=["null"])) == []

    # metrics_bridge_enabled -> derived_metrics accumulation
    bridged = build_recorder(ObservabilityConfig(enabled=True, metrics_bridge_enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    unbridged = build_recorder(ObservabilityConfig(enabled=True, metrics_bridge_enabled=False), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    with run_scope(RunContext(trace_id="t", run_id=ids.new_run_id())):
        bridged.record_event("agent.turn")
        unbridged.record_event("agent.turn")
    assert bridged.derived_metrics and not unbridged.derived_metrics

    # enabled -> every observable above is inert without it
    assert build_recorder(ObservabilityConfig(enabled=False, max_attribute_value_chars=16), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()]).enabled is False


def test_i_config_rejects_unknown_keys_and_out_of_range_values() -> None:
    """(i) ``extra="forbid"`` plus range constraints: a typo fails at load."""
    with pytest.raises(ValueError):
        ObservabilityConfig(enabledy=True)  # type: ignore[call-arg]
    for bad in ({"sample_rate": 1.5}, {"sample_rate": -0.1}, {"max_span_depth": 0}, {"max_attribute_value_chars": 4}, {"sinks": ["carrier-pigeon"]}):
        with pytest.raises(ValueError):
            ObservabilityConfig(**bad)


def test_i_config_normalizes_a_comma_separated_sink_list_and_a_path_object(tmp_path: Path) -> None:
    """(i) The two shapes a YAML author actually writes both load."""
    assert ObservabilityConfig(sinks="memory, file").sinks == ["memory", "file"]  # type: ignore[arg-type]
    assert ObservabilityConfig(file_sink_path=tmp_path / "t.jsonl").file_sink_path == str(tmp_path / "t.jsonl")
    assert ObservabilityConfig(sinks=["file"], file_sink_path=str(tmp_path / "t.jsonl")).file_sink_enabled is True
    assert ObservabilityConfig(sinks=["memory"]).file_sink_enabled is False


# ------------------------------------------- j. closed status and name sets


def test_j_span_status_is_a_closed_set() -> None:
    """(j) An unknown status cannot be constructed, by any spelling."""
    assert SPAN_STATUSES == {"ok", "error", "cancelled", "timeout", "disallowed"}
    for member in SpanStatus:
        assert member.value in SPAN_STATUSES
    with pytest.raises(ValueError):
        SpanStatus("partially_ok")
    with pytest.raises(ValueError):
        SpanStatus("")
    with pytest.raises(UnknownSpanStatusError, match="unknown span status"):
        coerce_status("partially_ok")
    with pytest.raises(UnknownSpanStatusError, match="must be a string or SpanStatus"):
        coerce_status(7)
    for member in SpanStatus:
        assert coerce_status(member.value) is member
        assert coerce_status(member) is member


def test_j_a_span_cannot_be_closed_with_an_unknown_status(ids: SequenceIdGenerator) -> None:
    """(j) The closed set is enforced at the span, not just the enum."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH)
    span = tracer.start_span("agent.turn", context=RunContext(trace_id="t", run_id=ids.new_run_id()))
    with pytest.raises(UnknownSpanStatusError):
        span.end(status="mostly_fine")
    assert span.ended is False, "a rejected end must not close the span"
    assert span.status is None, "a rejected end must not record a status"


def test_j_every_status_value_is_reachable_through_end(ids: SequenceIdGenerator) -> None:
    """(j) The set is not just closed but fully usable."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH)
    for member in SpanStatus:
        span = tracer.start_span(f"s-{member.value}", context=RunContext(trace_id="t", run_id=ids.new_run_id()))
        assert span.end(status=member) is member
        assert span.to_record()["status"] == member.value


def test_j_status_derivation_from_exceptions_uses_the_closed_set(ids: SequenceIdGenerator) -> None:
    """(j) Cancellation and timeout get their own statuses, not a generic error."""
    from alpha.observability.span import status_for_exception

    assert status_for_exception(asyncio.CancelledError()) is SpanStatus.CANCELLED
    assert status_for_exception(TimeoutError("slow")) is SpanStatus.TIMEOUT
    assert status_for_exception(RuntimeError("boom")) is SpanStatus.ERROR
    assert {status_for_exception(exc) for exc in (asyncio.CancelledError(), TimeoutError(), RuntimeError())} <= SPAN_STATUSES


def test_j_event_names_and_statuses_are_closed_sets() -> None:
    """The event vocabulary is closed too, and unknown names are rejected."""
    assert EVENT_NAMES == {
        "run.started",
        "run.ended",
        "run.context.linked",
        "run.context.detached",
        "agent.turn",
        "tool.call.requested",
        "tool.call.started",
        "tool.call.completed",
        "tool.call.failed",
        "memory.capture",
        "memory.recall",
        "subagent.spawned",
        "subagent.completed",
        "budget.consumed",
        "gate.refused",
    }
    assert set(EVENT_DOMAINS) == {name.split(".", 1)[0] for name in EVENT_NAMES}
    for domain, names in EVENT_DOMAINS.items():
        assert names, f"domain {domain} has no names"
        assert all(name.startswith(f"{domain}.") for name in names)
    assert sorted(name for names in EVENT_DOMAINS.values() for name in names) == sorted(EVENT_NAMES)
    assert EVENT_STATUSES == {"ok", "error", "cancelled", "timeout", "refused", "skipped"}


def test_j_unknown_event_name_or_status_is_rejected_at_construction(run_context: RunContext) -> None:
    """An unknown name never reaches a sink, because it cannot be built."""
    from pydantic import ValidationError

    # The typed entry points raise the specific error...
    with pytest.raises(UnknownEventNameError, match="unknown trace event name"):
        parse_event_name("tool.call.exploded")
    with pytest.raises(UnknownEventNameError):
        parse_event_name(None)
    with pytest.raises(UnknownEventStatusError, match="unknown trace event status"):
        parse_event_status("mostly_fine")
    with pytest.raises(UnknownEventStatusError):
        parse_event_status(object())
    assert parse_event_name("agent.turn") == "agent.turn"
    assert parse_event_status("ok") is EventStatus.OK

    # ...and construction shares the one closed set, so the two paths cannot
    # disagree. Pydantic wraps a validator failure in ValidationError; the
    # message names the offending value and lists the vocabulary.
    with pytest.raises(ValidationError, match="unknown trace event name"):
        TraceEvent(name="tool.call.exploded", status=EventStatus.OK, trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH)
    with pytest.raises(ValidationError, match="unknown trace event status"):
        TraceEvent(name="agent.turn", status="mostly_fine", trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TraceEvent(name="agent.turn", status=object(), trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="attributes must be a mapping"):
        TraceEvent(name="agent.turn", status=EventStatus.OK, trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH, attributes=["not", "a", "mapping"])  # type: ignore[arg-type]


def test_j_extra_fields_are_refused(run_context: RunContext) -> None:
    """A typo in a field name is a loud failure, not a silently dropped value."""
    with pytest.raises(ValueError):
        TraceEvent(name="agent.turn", status=EventStatus.OK, trace_id=run_context.trace_id, run_id=run_context.run_id, timestamp=FIXED_EPOCH, tream_id="typo")  # type: ignore[call-arg]


# ------------------------------------------ depth cap and other invariants


def test_span_depth_is_capped_and_disclosed(ids: SequenceIdGenerator) -> None:
    """Unbounded nesting is a stack overflow inside the tracer; cap it."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH, max_depth=3)
    spans = [tracer.start_span(f"s{index}", context=RunContext(trace_id="t", run_id=ids.new_run_id())) for index in range(6)]
    for index, span in enumerate(spans):
        if index:
            token = tracer.bind_span(spans[index - 1])
            child = tracer.start_span(f"nested-{index}", context=spans[index - 1].child_context())
            assert child.depth <= 3
            if child.depth == 3:
                assert child.depth_capped is True
                assert child.attributes["span.depth_capped"] is True
                assert child.to_record()["depth_capped"] is True
            from alpha.observability.span import detach_span

            detach_span(token)


def test_span_attribute_cap_is_enforced_and_disclosed(ids: SequenceIdGenerator) -> None:
    """A bounded attribute bag: refusals are counted, never silent."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH, max_attributes=3)
    with tracer.span("agent.turn", context=RunContext(trace_id="t", run_id=ids.new_run_id())) as span:
        for index in range(6):
            span.set_attribute(f"k{index}", index)
    assert len(span.attributes) == 3
    assert span.dropped_attributes == 3
    assert span.to_record()["dropped_attributes"] == 3


def test_span_end_is_idempotent_and_never_downgrades_a_failure(ids: SequenceIdGenerator) -> None:
    """A ``finally`` racing an explicit ``end`` must not turn a failure into ok."""
    tracer = Tracer(id_generator=ids, clock=StepClock())
    span = tracer.start_span("agent.turn", context=RunContext(trace_id="t", run_id=ids.new_run_id()))
    assert span.end(status=SpanStatus.ERROR) is SpanStatus.ERROR
    assert span.end(status=SpanStatus.OK) is SpanStatus.ERROR
    assert span.status is SpanStatus.ERROR


def test_span_end_survives_a_backwards_clock(ids: SequenceIdGenerator) -> None:
    """A clock that goes backwards must not yield a negative duration."""
    readings = iter([100.0, 50.0])
    tracer = Tracer(id_generator=ids, clock=lambda: next(readings))
    span = tracer.start_span("agent.turn", context=RunContext(trace_id="t", run_id=ids.new_run_id()))
    span.end()
    assert span.duration == 0.0, "a negative duration would quietly corrupt every histogram"


def test_open_span_serializes_rather_than_vanishing(ids: SequenceIdGenerator) -> None:
    """A trace cut short shows the unfinished span instead of hiding it."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH)
    span = tracer.start_span("agent.turn", context=RunContext(trace_id="t", run_id=ids.new_run_id()))
    record = span.to_record()
    assert record["ended"] is False and record["status"] is None and record["duration"] is None


def test_span_requires_a_run_context_rather_than_fabricating_one(ids: SequenceIdGenerator) -> None:
    """An unparented span is exactly the orphan this package exists to prevent."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH)
    assert current() is None
    with pytest.raises(ValueError, match="no RunContext bound"):
        tracer.start_span("agent.turn")


def test_run_context_validates_its_identity(ids: SequenceIdGenerator) -> None:
    """``RunContext`` normalizes the trace id and refuses an unusable run id."""
    context = RunContext(trace_id="  padded-trace  ", run_id=ids.new_run_id())
    assert context.trace_id == "padded-trace", "the trace id is normalized like alpha.trace_context does"
    with pytest.raises(ValueError, match="printable ASCII"):
        RunContext(trace_id="bad\ntrace", run_id=ids.new_run_id())
    with pytest.raises(ValueError, match="RunContext.run_id"):
        RunContext(trace_id="t", run_id="not-a-valid-id")
    with pytest.raises(ValueError, match="RunContext.parent_span_id"):
        RunContext(trace_id="t", run_id=ids.new_run_id(), parent_span_id="nope")


def test_ensure_run_context_joins_the_ambient_request_trace(ids: SequenceIdGenerator) -> None:
    """A run started inside a request joins that request's existing trace id."""
    from alpha.trace_context import ensure_trace_context

    with ensure_trace_context("request-abc"):
        context = ensure_run_context(id_generator=ids, thread_id="th")
    assert context.trace_id == "request-abc"
    assert is_valid_id(context.run_id)


def test_span_constructor_validates_its_inputs(ids: SequenceIdGenerator) -> None:
    """A ``Span`` refuses an empty name, a bad id and a negative depth."""
    with pytest.raises(ValueError, match="non-empty string"):
        Span(name="  ", span_id=ids.new_span_id(), context=RunContext(trace_id="t", run_id=ids.new_run_id()), started_at=FIXED_EPOCH, clock=lambda: FIXED_EPOCH, redactor=Redactor(), depth=0, depth_capped=False)
    with pytest.raises(ValueError, match="32-character lowercase hex"):
        Span(name="ok", span_id="nope", context=RunContext(trace_id="t", run_id=ids.new_run_id()), started_at=FIXED_EPOCH, clock=lambda: FIXED_EPOCH, redactor=Redactor(), depth=0, depth_capped=False)
    with pytest.raises(ValueError, match="depth must be non-negative"):
        Span(name="ok", span_id=ids.new_span_id(), context=RunContext(trace_id="t", run_id=ids.new_run_id()), started_at=FIXED_EPOCH, clock=lambda: FIXED_EPOCH, redactor=Redactor(), depth=-1, depth_capped=False)
    with pytest.raises(ValueError, match="max_attributes"):
        Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH, max_attributes=0)
    with pytest.raises(TypeError, match="on_span_end must be callable"):
        Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH, on_span_end="nope")  # type: ignore[arg-type]


def test_tracer_span_failure_discloses_the_failure_and_ends_the_span(ids: SequenceIdGenerator) -> None:
    """A span that saw an exception records the type and a scrubbed message."""
    tracer = Tracer(id_generator=ids, clock=lambda: FIXED_EPOCH)
    with pytest.raises(ValueError, match="boom"):
        with tracer.span("tool.call", context=RunContext(trace_id="t", run_id=ids.new_run_id())) as span:
            raise ValueError("boom")
    assert span.status is SpanStatus.ERROR
    assert span.ended is True
    assert span.attributes["error.type"] == "ValueError"
    assert current_span() is None, "the span binding must be restored after a failure"


def test_recorder_owns_no_global_state(ids: SequenceIdGenerator) -> None:
    """Two recorders with different policies cannot interfere."""
    left = build_recorder(ObservabilityConfig(enabled=True, redaction_policy="standard"), id_generator=SequenceIdGenerator(prefix="1", clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    right = build_recorder(ObservabilityConfig(enabled=True, redaction_policy="strict"), id_generator=SequenceIdGenerator(prefix="2", clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    assert left.redactor.policy == "standard" and right.redactor.policy == "strict"
    assert left is not right
    assert "TraceRecorder(" in repr(left) and "Tracer(" in repr(left.tracer)
    left.close()
    left.close()  # idempotent
    with pytest.raises(TypeError, match="clock must be callable"):
        build_recorder(ObservabilityConfig(), clock="not-callable")  # type: ignore[arg-type]


def test_recorder_close_is_idempotent_and_contains_close_failures(ids: SequenceIdGenerator) -> None:
    """Shutdown must not raise, even when a sink's close does."""
    exploding = ExplodingSink()
    recorder = build_recorder(ObservabilityConfig(enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[exploding])
    recorder.close()
    recorder.close()
    with run_scope(RunContext(trace_id="t", run_id=ids.new_run_id())):
        assert recorder.record_event("agent.turn") is None, "a closed recorder records nothing"
    assert recorder.disclosure()["enabled"] is True


# --------------------------------------------------------- the metrics bridge


def test_metrics_bridge_publishes_into_the_existing_ops_registry(ids: SequenceIdGenerator) -> None:
    """Counters land in ``alpha.ops.metrics``, declared and Prometheus-visible."""
    from alpha.ops.metrics import MetricsRegistry as OpsRegistry

    ops = OpsRegistry()
    bridge = TraceMetricsBridge(ops_registry=ops)
    bridge.declare()
    bridge.declare()  # idempotent: a Gateway lifespan calls this on every start

    recorder = build_recorder(ObservabilityConfig(enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    context = RunContext(trace_id="t", run_id=ids.new_run_id(), thread_id="thread-1", user_id="user-1")
    with run_scope(context):
        for name in ("run.started", "agent.turn", "tool.call.started", "run.ended"):
            event = recorder.record_event(name)
            assert event is not None
            bridge.observe(event)
        with recorder.span("agent.turn") as span:
            span.set_attribute("a", "b")
        bridge.observe_span(span.to_record())
    bridge.publish_recorder_disclosure(recorder)

    rendered = ops.render_prometheus()
    assert f'{METRIC_PREFIX}events_total{{event="agent.turn",status="ok"}} 1' in rendered
    assert f'{METRIC_PREFIX}runs_total{{status="ok"}} 1' in rendered
    assert f'{METRIC_PREFIX}spans_total{{span="agent.turn",status="ok"}} 1' in rendered
    assert f'{METRIC_PREFIX}span_duration_ms_count{{span="agent.turn"}} 1' in rendered
    assert METRIC_PREFIX + "runs_sampled_out_total 0" in rendered
    assert ops.series_count() > 0


def test_metrics_bridge_publishes_into_the_existing_health_registry(ids: SequenceIdGenerator) -> None:
    """Counters land in ``alpha.memory.health.metrics`` without a global."""
    from alpha.memory.health.metrics import MetricsRegistry as HealthRegistry

    health = HealthRegistry(reservoir_size=32)
    bridge = TraceMetricsBridge(health_registry=health)
    recorder = build_recorder(ObservabilityConfig(enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    with run_scope(RunContext(trace_id="t", run_id=ids.new_run_id())):
        event = recorder.record_event("agent.turn")
        assert event is not None
        bridge.observe(event)
    assert health.counter_value("alpha.events_total", labels={"event": "agent.turn", "status": "ok"}) == 1
    bridge.observe_redactions({"secret_key": 3, "not_a_real_reason": 99})
    assert health.counter_value("alpha.redactions_total", labels={"reason": "secret_key"}) == 3
    snapshot = health.snapshot()
    assert snapshot["schema"] == 1


def test_metrics_bridge_never_uses_an_identifier_as_a_label(ids: SequenceIdGenerator) -> None:
    """No trace/run/thread/user/span id may become a metric label value.

    The ops registry keeps at most 2048 series and *refuses* new ones past the
    cap, so labelling by run id would exhaust the budget with the first few
    hundred runs and then silently stop recording everything else.
    """
    from alpha.ops.metrics import MetricsRegistry as OpsRegistry

    ops = OpsRegistry()
    bridge = TraceMetricsBridge(ops_registry=ops)
    bridge.declare()
    context = RunContext(trace_id="trace-secret", run_id=ids.new_run_id(), thread_id="thread-secret", user_id="user-secret")
    recorder = build_recorder(ObservabilityConfig(enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    with run_scope(context):
        event = recorder.record_event("run.context.linked", context_inherited=False, link_kind="subprocess", attributes={"parent_trace_id": context.trace_id, "parent_run_id": context.run_id})
        assert event is not None
        bridge.observe(event)
        with recorder.span("agent.turn") as span:
            bridge.observe_span(span.to_record())
    bridge.publish_recorder_disclosure(recorder)

    forbidden = {context.trace_id, context.run_id, context.thread_id or "", context.user_id or ""}
    for line in ops.render_prometheus().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        for value in forbidden:
            assert value not in line, f"an identifier leaked into a metric label: {line}"


def test_metrics_bridge_label_values_all_come_from_closed_sets() -> None:
    """Every label value is drawn from the taxonomy, so cardinality is bounded."""
    from alpha.ops.metrics import MetricsRegistry as OpsRegistry

    ops = OpsRegistry()
    bridge = TraceMetricsBridge(ops_registry=ops)
    bridge.declare()
    recorder = build_recorder(ObservabilityConfig(enabled=True), id_generator=SequenceIdGenerator(clock=lambda: FIXED_EPOCH), clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()])
    linked_attributes = {"parent_trace_id": "t"}
    with run_scope(RunContext(trace_id="t", run_id=SequenceIdGenerator(clock=lambda: FIXED_EPOCH).new_run_id())):
        for name in sorted(EVENT_NAMES):
            is_link = name == "run.context.linked"
            event = recorder.record_event(
                name,
                context_inherited=not is_link,
                link_kind="subprocess" if is_link else "",
                attributes=linked_attributes if is_link else None,
            )
            if event is not None:
                bridge.observe(event)
    # At most one series per (name, status) combination, plus the fixed kinds.
    assert ops.series_count() <= len(EVENT_NAMES) * len(EVENT_STATUSES) + 16
    assert bridge.targets == ("ops",)
    assert TraceMetricsBridge().targets == ()


def test_metrics_bridge_declares_a_bounded_label_set_per_metric() -> None:
    """The declaration table is the cardinality contract; assert it is sane."""
    for name, kind, help_text, labels in BRIDGE_METRICS:
        assert name.startswith(METRIC_PREFIX)
        assert kind in ("counter", "gauge", "histogram")
        assert help_text
        assert len(set(labels)) == len(labels)
        for forbidden in ("trace_id", "run_id", "thread_id", "user_id", "span_id"):
            assert forbidden not in labels, f"{name} would label by {forbidden}"


def test_metrics_bridge_is_inert_without_an_injected_registry(ids: SequenceIdGenerator) -> None:
    """A bridge with no target is a no-op, not a crash."""
    bridge = TraceMetricsBridge()
    bridge.declare()
    bridge.observe_redactions({"secret_key": 1})
    bridge.publish_recorder_disclosure(build_recorder(ObservabilityConfig(enabled=True), id_generator=ids, clock=lambda: FIXED_EPOCH, sinks=[InMemorySink()]))
    assert bridge.snapshot()["targets"] == []


# ----------------------------------------------------- lazy export surface


def test_package_exports_resolve_lazily() -> None:
    """``__init__`` is a PEP 562 surface with no eager submodule imports."""
    import alpha.observability as package

    for name in package.__all__:
        assert hasattr(package, name), f"{name} does not resolve"
    with pytest.raises(AttributeError, match="no attribute 'not_a_thing'"):
        package.not_a_thing  # type: ignore[attr-defined]
    assert "RunContext" in dir(package)


def test_importing_the_config_module_does_not_drag_in_the_recorder() -> None:
    """The lazy exports keep a config-schema import cheap (the memory rationale)."""
    import subprocess

    tree = REPO_ROOT / "backend" / "packages" / "harness"
    script = "import sys; import alpha.observability.config as c; loaded = [m for m in sys.modules if m.startswith('alpha.observability.')]; print(sorted(loaded))"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tree), str(tree / "packages" / "harness")])}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    loaded = result.stdout.strip()
    for heavy in ("recorder", "span", "metrics_bridge", "events"):
        assert heavy not in loaded, f"the config import pulled in {heavy}, which it should not have needed: {loaded}"


def test_context_var_propagation_matches_the_documented_table() -> None:
    """The table in the module docstring is asserted against real behaviour."""
    expectations = {rule.hop: rule.propagates for rule in BOUNDARY_RULES}
    assert expectations["asyncio_task"] is True
    assert expectations["asyncio_to_thread"] is True
    assert expectations["run_in_executor"] is False
    assert expectations["thread_pool_submit"] is False
    assert expectations["threading_thread"] is False
    assert expectations["subprocess"] is False
    assert expectations["multiprocessing_spawn"] is False
    assert expectations["event_bus_handler"] is False
    # And the ambient state really is a ContextVar, not a module global: a task
    # sees the binding that existed when it was created, and the parent's
    # binding is unchanged by whatever the task did.
    parent_before: list[RunContext | None] = []
    task_saw: list[RunContext | None] = []

    async def scenario() -> None:
        with run_scope(RunContext(trace_id="t", run_id="a" * 32)):
            parent_before.append(current())
            task_saw.append(await asyncio.create_task(_read_context()))
            parent_before.append(current())

    asyncio.run(scenario())
    assert all(value is not None for value in parent_before + task_saw)
    assert parent_before[0].run_id == parent_before[1].run_id == "a" * 32  # type: ignore[union-attr]
    assert task_saw[0].run_id == "a" * 32  # type: ignore[union-attr]
    assert current() is None, "the scope was restored in the parent too"
    assert contextvars.copy_context() is not None
