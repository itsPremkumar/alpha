"""The writer: the gate, the bounds, the containment, and the inertness.

The four properties this file exists to prove
---------------------------------------------
1. **Default-off and inert.** A disabled writer reads no clock, mints no id,
   builds no envelope and calls no sink -- asserted by counting sink calls, not
   by observing silence.
2. **Never breaks the run.** A sink that raises degrades to a counted,
   once-logged warning; the writer returns normally and the caller cannot tell
   anything went wrong. There is no retry storm: one failing emit produces one
   attempt.
3. **Bounded and disclosed.** Per-run cap, LRU seq table, ring overflow, durable
   buffer overflow -- every one has a counter in :meth:`TraceWriter.disclosure`.
4. **Tracing is not load-bearing.** The same work produces the same result with
   the writer off and on.

The reader table is the audit surface: it must cover exactly the config's keys,
and flipping each key must change something observable.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from alpha.observability.trace.config import READERS, TraceConfig, build_sinks, read_all_keys, resolve_all
from alpha.observability.trace.contract import TraceBounds
from alpha.observability.trace.instrumentation import (
    emit_error,
    emit_model_completed,
    emit_skill_selection,
    emit_tool_selection,
    record_event,
)
from alpha.observability.trace.sinks import RunEventStoreSink
from alpha.observability.trace.writer import (
    DISABLED_SEQ,
    TraceWriter,
    current_writer,
    disabled_writer,
    install_writer,
    reset_writer,
)


@pytest.fixture(autouse=True)
def _no_installed_writer():
    """Guarantee no writer leaks between tests.

    The writer is a process-level installation, and a leaked one would make every
    subsequent test's "nothing was recorded" assertion pass or fail for reasons
    that have nothing to do with the test.
    """
    reset_writer()
    yield
    reset_writer()


def _writer(**overrides) -> TraceWriter:
    return TraceWriter(TraceConfig(enabled=True, sinks=["memory"], **overrides))


def _model_payload(**extra: Any) -> dict[str, Any]:
    payload = {"provider": "space-bunny", "model": "space-bunny-free", "finish_reason": "stop", "latency_ms": 5.0}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_a_disabled_writer_touches_no_sink_and_reads_no_clock():
    calls: list[str] = []

    class _CountingSink:
        name = "counting"

        def emit_envelope(self, envelope: Any) -> None:
            calls.append(envelope.event_id)

        def close(self) -> None:
            return None

        def disclosure(self) -> dict[str, int]:
            return {"calls": len(calls)}

    def _explode() -> float:
        raise AssertionError("a disabled writer must not read a clock")

    writer = TraceWriter(TraceConfig(enabled=False), sinks=[_CountingSink()], wall_clock=_explode, monotonic_clock=_explode)

    assert writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload()) is None
    assert writer.next_seq("a" * 32) == DISABLED_SEQ
    assert calls == [], "a disabled writer must not reach a sink"
    assert writer.disclosure()["recorded_events"] == 0


def test_disabled_writer_is_constructed_with_no_sink_at_all():
    writer = TraceWriter(TraceConfig(enabled=False))
    assert writer.sinks == ()


def test_the_default_config_is_off():
    assert TraceConfig().enabled is False
    assert TraceConfig(enabled=True) is not None


def test_no_writer_installed_means_the_emitters_are_free():
    """The whole cost of the substrate in a default deployment."""
    assert current_writer() is None
    assert record_event("model.call.completed", run_id="a" * 32, payload=_model_payload()) is None
    assert emit_tool_selection(candidates=["a"], chosen="a", reason="r", run_id="a" * 32) is None


def test_disabled_writer_helper_is_inert_by_construction():
    writer = disabled_writer()
    assert writer.enabled is False
    assert writer.sinks == ()


def test_installing_a_writer_makes_the_emitters_reach_it():
    writer = _writer()
    assert install_writer(writer) is writer
    assert current_writer() is writer
    envelope = emit_model_completed(provider="space-bunny", model="space-bunny-free", finish_reason="stop", latency_ms=3.0, run_id="a" * 32)
    assert envelope is not None
    assert len(writer.ring()) == 1


def test_install_writer_refuses_a_non_writer():
    with pytest.raises(TypeError):
        install_writer(object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tracing is not load-bearing
# ---------------------------------------------------------------------------


def _simulate_a_run(writer: TraceWriter | None) -> tuple[list[str], list[str]]:
    """A miniature run. Returns ``(business_results, trace_event_types)``.

    The two lists are separated deliberately. The claim under test is that the
    *business* results are identical with tracing off and on -- if the emitter's
    return value leaked into the run's own outcome, tracing would have become
    load-bearing. The second list is the trace, and it is expected to be empty
    when tracing is off.
    """
    business: list[str] = []
    traced: list[str] = []
    selected = emit_tool_selection(candidates=["bash", "read_file"], chosen="bash", reason="literal name match", scores={"bash": 0.9}, run_id="run-1", thread_id="t-1", writer=writer)
    business.append(selected.payload["chosen"] if selected is not None else "bash")
    traced.append("none" if selected is None else selected.event_type)

    called = emit_model_completed(provider="space-bunny", model="space-bunny-free", finish_reason="stop", latency_ms=42.0, input_tokens=7, output_tokens=9, run_id="run-1", writer=writer)
    business.append("stop" if called is None else called.payload["finish_reason"])
    traced.append("none" if called is None else called.event_type)

    failed = emit_error(error_code="TIMEOUT", message="provider timed out", retried=True, run_id="run-1", writer=writer)
    business.append("timeout-handled")
    traced.append("none" if failed is None else failed.event_type)

    from alpha.observability.trace.instrumentation import emit_subagent_spawned

    spawned = emit_subagent_spawned(reason="delegated", depth=1, subagent_id="task-1", assigned_model="union-alpha", run_id="run-1", writer=writer)
    business.append("task-1")
    traced.append("none" if spawned is None else spawned.event_type)

    return business, traced


def test_a_run_produces_identical_results_with_tracing_off_and_on():
    """The load-bearing claim: tracing is not part of correctness."""
    off_business, off_traced = _simulate_a_run(None)
    on_business, on_traced = _simulate_a_run(_writer())

    assert off_business == on_business
    assert off_traced == ["none"] * 4, "with no writer installed nothing is recorded"
    assert on_traced == ["tool.select.decided", "model.call.completed", "err.raised", "sub.spawned"]


def test_an_installed_but_disabled_writer_is_also_transparent():
    """Disabled is not "quiet": an installed-but-off writer records nothing and
    builds no sink, so a deployment that installed one and left the gate false
    pays nothing and leaves no file."""
    off_business, _ = _simulate_a_run(None)
    writer = disabled_writer()
    install_writer(writer)
    on_business, on_traced = _simulate_a_run(current_writer())

    assert off_business == on_business
    assert on_traced == ["none"] * 4
    assert writer.sinks == ()
    assert writer.ring() == ()


# ---------------------------------------------------------------------------
# sequence
# ---------------------------------------------------------------------------


def test_seq_is_monotonic_per_run_and_starts_at_one():
    writer = _writer()
    assert [writer.next_seq("run-a") for _ in range(3)] == [1, 2, 3]
    assert writer.next_seq("run-b") == 1, "each run has its own sequence"


def test_seq_is_assigned_under_concurrency_without_gaps_or_repeats():
    writer = _writer()
    seen: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(50):
            value = writer.next_seq("run-concurrent")
            with lock:
                seen.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(seen) == list(range(1, 8 * 50 + 1))


def test_the_seq_table_is_lru_bounded_and_discloses_eviction():
    writer = _writer(max_tracked_runs=2)
    for index in range(5):
        writer.next_seq(f"run-{index}")
    disclosure = writer.disclosure()
    assert disclosure["evicted_run_counters"] > 0
    assert disclosure["tracked_runs"] <= 2


def test_a_closed_writer_records_nothing():
    writer = _writer()
    writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload())
    writer.close()
    assert writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload()) is None
    writer.close()  # idempotent


# ---------------------------------------------------------------------------
# bounds and disclosure
# ---------------------------------------------------------------------------


def test_the_per_run_event_cap_refuses_and_counts():
    writer = _writer(max_events_per_run=3)
    for _ in range(3):
        assert writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload()) is not None
    assert writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload()) is None
    disclosure = writer.disclosure()
    assert disclosure["refused_events"] == 1
    assert len(writer.ring()) == 3


def test_sampling_is_per_run_and_deterministic():
    writer = _writer(sample_rate=0.0)
    assert writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload()) is None
    assert writer.disclosure()["sampled_out_runs"] >= 1

    half = _writer(sample_rate=0.5)
    first = [half.sampled(f"run-{index}") for index in range(50)]
    second = [half.sampled(f"run-{index}") for index in range(50)]
    assert first == second, "the sampling decision must be reproducible from the run id alone"
    assert 0 < sum(first) < 50, "a 0.5 rate over 50 runs must admit some and refuse some"


def test_the_ring_is_bounded_and_reports_its_overflow():
    writer = TraceWriter(TraceConfig(enabled=True, sinks=["memory"], ring_capacity=3))
    for _ in range(6):
        writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload())
    assert len(writer.ring()) == 3
    assert writer.disclosure()["sinks"]["memory"]["dropped_events"] == 3


def test_a_record_with_no_identity_is_dropped_and_counted_not_fabricated():
    writer = _writer()
    assert writer.record("model.call.completed", payload=_model_payload()) is None
    assert writer.disclosure()["rejected_events"] == 1
    assert writer.ring() == ()


def test_a_contract_violation_is_counted_and_does_not_raise():
    writer = _writer()
    assert writer.record("model.call.completed", run_id="a" * 32, payload={"provider": "p"}) is None
    assert writer.record("not.a.real.code", run_id="a" * 32, payload={}) is None
    disclosure = writer.disclosure()
    assert disclosure["rejected_events"] == 2
    assert writer.ring() == ()


def test_bounds_come_from_the_config_in_one_place():
    config = TraceConfig(enabled=True, payload_max_bytes=512, payload_max_items=7, payload_max_value_chars=99, payload_max_depth=3)
    bounds = config.bounds()
    assert isinstance(bounds, TraceBounds)
    assert (bounds.max_payload_bytes, bounds.max_items, bounds.max_value_chars, bounds.max_depth) == (512, 7, 99, 3)
    assert bounds.redaction_policy == config.redaction_policy


# ---------------------------------------------------------------------------
# sink failure containment
# ---------------------------------------------------------------------------


class _ExplodingSink:
    """A sink that always raises, and counts how often it was asked."""

    name = "exploding"

    def __init__(self) -> None:
        self.attempts = 0

    def emit_envelope(self, envelope: Any) -> None:
        self.attempts += 1
        raise RuntimeError("sink is down")

    def close(self) -> None:
        return None

    def disclosure(self) -> dict[str, int]:
        return {"calls": self.attempts}


def test_a_failing_sink_is_contained_counted_and_the_run_still_succeeds(caplog):
    exploding = _ExplodingSink()
    writer = TraceWriter(TraceConfig(enabled=True, sinks=["memory"]), sinks=[exploding])
    install_writer(writer)

    business, traced = _simulate_a_run(writer)

    assert business == ["bash", "stop", "timeout-handled", "task-1"], "the caller must not be able to tell the sink failed"
    assert traced == ["tool.select.decided", "model.call.completed", "err.raised", "sub.spawned"], "the envelope is still built; only the hand-off failed"
    assert writer.disclosure()["sink_failures"] == {"exploding": 4}
    messages = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert sum("trace sink 'exploding' failed" in message for message in messages) == 1, "one sink, one warning -- not one per event"


def test_a_failing_sink_does_not_stop_the_remaining_sinks():
    class _GoodSink:
        name = "good"

        def __init__(self) -> None:
            self.seen: list[str] = []

        def emit_envelope(self, envelope: Any) -> None:
            self.seen.append(envelope.event_type)

        def close(self) -> None:
            return None

        def disclosure(self) -> dict[str, int]:
            return {"calls": len(self.seen)}

    good = _GoodSink()
    writer = TraceWriter(TraceConfig(enabled=True, sinks=["memory"]), sinks=[_ExplodingSink(), good])
    writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload())
    assert good.seen == ["model.call.completed"]


def test_there_is_no_retry_storm():
    """One emit, one attempt. A sink that retries internally is how an
    observability outage becomes a CPU outage."""
    exploding = _ExplodingSink()
    writer = TraceWriter(TraceConfig(enabled=True, sinks=["memory"]), sinks=[exploding])
    for _ in range(20):
        writer.record("model.call.completed", run_id="a" * 32, payload=_model_payload())
    assert exploding.attempts == 20, "one attempt per event, never more"
    assert writer.disclosure()["sink_failures"] == {"exploding": 20}


# ---------------------------------------------------------------------------
# the durable sink
# ---------------------------------------------------------------------------


class _MemoryStore:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    async def put_batch(self, rows: list[dict]) -> list[dict]:
        self.batches.append(rows)
        return rows


class _BrokenStore:
    async def put_batch(self, rows: list[dict]) -> list[dict]:
        raise RuntimeError("database is locked")


@pytest.mark.anyio
async def test_the_durable_sink_writes_the_same_rows_the_route_serves():
    store = _MemoryStore()
    writer = TraceWriter(TraceConfig(enabled=True, sinks=["run_events"]), store=store)
    writer.record("model.call.completed", run_id="a" * 32, thread_id="t-1", payload=_model_payload())
    written = await writer.drain()
    assert written == 1
    row = store.batches[0][0]
    assert row["event_type"] == "model.call.completed"
    assert row["category"] == "trace"
    assert row["metadata"]["schema_version"] == 1


@pytest.mark.anyio
async def test_a_broken_store_counts_a_flush_failure_and_retains_the_batch():
    sink = RunEventStoreSink(_BrokenStore())
    sink.put_nowait(_envelope_for_sink())
    assert await sink.drain() == 0
    disclosure = sink.disclosure()
    assert disclosure["flush_failures"] == 1
    assert disclosure["pending"] == 1, "the rows are retained, not lost"


@pytest.mark.anyio
async def test_the_durable_buffer_is_bounded_and_counts_its_overflow():
    sink = RunEventStoreSink(_MemoryStore(), max_pending=2)
    assert sink.put_nowait(_envelope_for_sink()) is True
    assert sink.put_nowait(_envelope_for_sink()) is True
    assert sink.put_nowait(_envelope_for_sink()) is False
    assert sink.disclosure()["overflowed"] == 1


def test_flush_if_possible_is_a_no_op_off_a_loop():
    """A synchronous callback on a worker thread must not block or fail; the rows
    simply wait for the next explicit drain."""
    sink = RunEventStoreSink(_MemoryStore())
    sink.put_nowait(_envelope_for_sink())
    assert sink.flush_if_possible() is False
    assert sink.pending == 1


def test_a_run_events_sink_with_no_store_is_refused_not_silently_dropped():
    with pytest.raises(ValueError, match="no store was supplied"):
        build_sinks(TraceConfig(enabled=True, sinks=["run_events"]))


def test_a_file_sink_with_no_path_is_refused_not_silently_dropped():
    with pytest.raises(ValueError, match="file_sink_path"):
        TraceWriter(TraceConfig(enabled=True, sinks=["file"]))


def test_the_memory_and_file_sinks_are_the_recorders_own():
    """Reuse, not a second bounding idea. If this ever stops being true, the
    package has two ring buffers and two overflow conventions."""
    from alpha.observability.recorder import InMemorySink, JsonlFileSink
    from alpha.observability.trace.sinks import TraceRecordSink

    memory = build_sinks(TraceConfig(enabled=True, sinks=["memory"], ring_capacity=11))
    assert isinstance(memory[0], TraceRecordSink)
    assert isinstance(memory[0]._inner, InMemorySink)
    assert memory[0]._inner.max_records == 11

    written = build_sinks(TraceConfig(enabled=True, sinks=["file"], file_sink_path="x.jsonl"))
    assert isinstance(written[0]._inner, JsonlFileSink)


def _envelope_for_sink():
    from alpha.observability.trace.contract import TraceEnvelope

    return TraceEnvelope.build(
        event_type="model.call.completed",
        run_id="a" * 32,
        trace_id="t",
        seq=1,
        thread_id="t-1",
        payload=_model_payload(),
    )


# ---------------------------------------------------------------------------
# reader table
# ---------------------------------------------------------------------------


def test_the_reader_table_covers_exactly_the_schema_keys():
    assert set(READERS) == set(read_all_keys())
    assert set(resolve_all()) == set(read_all_keys())


def test_every_reader_names_a_consumer():
    for key, reader in READERS.items():
        assert reader.consumer.strip(), f"reader for {key!r} names no consumer; that is the lie the table exists to prevent"


def test_flipping_each_key_changes_what_the_reader_table_reports():
    """The reader table is the audit surface, so the property is: the value a key
    is set to is the value its reader returns. A key whose reader ignored it
    would still satisfy the coverage test and still be a lie."""
    config = TraceConfig(
        enabled=True,
        sinks=["memory"],
        sample_rate=0.25,
        max_events_per_run=11,
        max_tracked_runs=3,
        ring_capacity=17,
        file_sink_path="p.jsonl",
        file_sink_max_events=7,
        file_sink_max_bytes=99,
        payload_max_bytes=1024,
        payload_max_items=5,
        payload_max_value_chars=64,
        payload_max_depth=2,
        redaction_policy="standard",
        async_queue_max_pending=8,
        auto_flush=False,
    )
    resolved = resolve_all(config)
    assert resolved["enabled"] is True
    assert resolved["sinks"] == ["memory"]
    assert resolved["sample_rate"] == 0.25
    assert resolved["max_events_per_run"] == 11
    assert resolved["max_tracked_runs"] == 3
    assert resolved["ring_capacity"] == 17
    assert resolved["file_sink_path"] == "p.jsonl"
    assert resolved["file_sink_max_events"] == 7
    assert resolved["file_sink_max_bytes"] == 99
    assert resolved["payload_max_bytes"] == 1024
    assert resolved["payload_max_items"] == 5
    assert resolved["payload_max_value_chars"] == 64
    assert resolved["payload_max_depth"] == 2
    assert resolved["redaction_policy"] == "standard"
    assert resolved["async_queue_max_pending"] == 8
    assert resolved["auto_flush"] is False


def test_the_payload_keys_actually_reach_the_bounds():
    config = TraceConfig(enabled=True, payload_max_bytes=1024, payload_max_items=5, payload_max_value_chars=64, payload_max_depth=2, redaction_policy="standard")
    bounds = config.bounds()
    assert (bounds.max_payload_bytes, bounds.max_items, bounds.max_value_chars, bounds.max_depth, bounds.redaction_policy) == (1024, 5, 64, 2, "standard")


def test_the_ring_capacity_actually_reaches_the_sink():
    sinks = build_sinks(TraceConfig(enabled=True, sinks=["memory"], ring_capacity=17))
    assert sinks[0]._inner.max_records == 17


def test_an_unknown_key_is_refused_rather_than_ignored():
    """A key nobody reads is a lie told in YAML; so is a key nobody is allowed to
    declare."""
    with pytest.raises(Exception):
        TraceConfig(not_a_real_key=1)


# ---------------------------------------------------------------------------
# skill selection carries the registry version
# ---------------------------------------------------------------------------


def test_skill_selection_records_the_registry_version_and_the_candidates():
    writer = _writer()
    envelope = emit_skill_selection(
        candidates=["alpha-wiki", "pdf-tools", "chart-maker"],
        chosen="pdf-tools",
        reason="only description mentioning a PDF",
        registry_version="catalog:7",
        scores={"pdf-tools": 0.81, "alpha-wiki": 0.12, "chart-maker": 0.07},
        run_id="a" * 32,
        writer=writer,
    )
    assert envelope is not None
    assert envelope.payload["candidates"] == ["alpha-wiki", "pdf-tools", "chart-maker"]
    assert envelope.payload["chosen"] == "pdf-tools"
    assert envelope.payload["reason"]
    assert envelope.payload["registry_version"] == "catalog:7"
    assert envelope.payload["scores"]["pdf-tools"] == 0.81
    assert envelope.skill == "pdf-tools"
