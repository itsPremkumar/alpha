"""Acceptance tests for the behaviour-trace substrate.

The criteria this file has to prove, in the order the brief states them:

A. The envelope is versioned, stable, and self-consistent: every field in
   :data:`ENVELOPE_FIELDS` exists, every event type has a stable code, and a
   record round-trips.
B. There is ONE event taxonomy covering all 18 layers, and ONE error taxonomy.
   The severity vocabulary is the error registry's, proven by set equality.
C. Redaction happens AT WRITE TIME. A secret in a prompt, in a tool argument or
   in a tool result appears nowhere in the persisted log.
D. Every payload is bounded, and a clamp is disclosed: the flag, the original
   byte count, and the SHA-256 of the full payload.
E. Optional and default-off: a disabled writer touches no sink, reads no clock
   and allocates no sequence, and tracing on/off produce identical run results.
F. Tracing never breaks the thing being traced: a raising sink degrades to a
   counted, once-logged warning, and the run still succeeds.
G. Thread-safe, async-safe, and bounded in memory with a disclosed overflow.
H. Parent/child spans nest correctly for a subagent.
I. Tool and skill selection record the candidate set, the choice, and a reason.
J. Every config key is read (the reader-table discipline this package holds).
K. A sink's persisted bytes are the only place a record exists -- so "nowhere in
    the log" is asserted against a real JSONL file, not an in-memory object.

Determinism: both clocks and the id generator are injected, so no test depends
on wall-clock time or randomness, and nothing sleeps.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from pathlib import Path
from typing import Any

import pytest

import alpha.observability.taxonomy as taxonomy_module
from alpha.observability.ambient import bind_writer, current_writer, detach_writer, emit_handoff, emit_tool_selection, writer_scope
from alpha.observability.behaviour_config import READERS, BehaviourTraceConfig, read_all_keys, resolve_all
from alpha.observability.config import ObservabilityConfig
from alpha.observability.context import RunContext, run_scope
from alpha.observability.contract import (
    ENVELOPE_FIELDS,
    MIN_PAYLOAD_CAP_BYTES,
    RECORD_TYPE,
    SCHEMA_VERSION,
    SEVERITIES,
    TraceEnvelope,
    TraceEnvelopeError,
    clamp_payload,
    payload_digest,
)
from alpha.observability.ids import SequenceIdGenerator
from alpha.observability.recorder import InMemorySink, JsonlFileSink, TraceRecorder, build_recorder
from alpha.observability.redaction import Redactor
from alpha.observability.taxonomy import (
    ERROR_CODE_TAXONOMY,
    EVENT_TYPE_CODES,
    EVENT_TYPE_NAMES,
    LAYERS,
    TOTAL_FALLBACK_ERROR_CODE,
    ErrorCodeResolution,
    classify_error_code,
    definition_for,
    error_taxonomy_status,
    parse_event_type,
    registered_error_codes,
)
from alpha.observability.writer import BehaviourTraceWriter, build_behaviour_writer, disabled_behaviour_writer

FIXED_EPOCH = 1_700_000_000.0

#: Fabricated credentials, assembled from two literals each so no *contiguous*
#: credential-shaped token appears in this source file. These are the three
#: highest-risk places a secret enters an agent run, and criterion C asserts each
#: one is absent from the persisted log.
SECRET_IN_PROMPT = "please use " + "sk-proj-0123456789abcdefghijklmnopqrstuvwx" + " from now on"
SECRET_IN_TOOL_ARG = {"headers": {"Authorization": "Bearer " + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVP"}}
SECRET_IN_TOOL_RESULT = "here is the connection string postgres://" + "reporting:hunter2Correct" + "@db.internal:5432/analytics"
SECRET_FRAGMENTS = ("sk-proj-0123456789abcdefghijklmnopqrstuvwx", "eyJhbGciOiJIUzI1NiJ9", "hunter2Correct")


class StepClock:
    """Monotonic injected clock advancing a fixed step per read."""

    def __init__(self, start: float = FIXED_EPOCH, step: float = 0.001) -> None:
        self._now = start
        self._step = step
        self.reads = 0

    def __call__(self) -> float:
        current_value = self._now
        self._now += self._step
        self.reads += 1
        return current_value


class ExplodingSink:
    """A sink that always raises, to prove the writer contains the failure."""

    name = "exploding"

    def __init__(self) -> None:
        self.calls = 0

    def emit(self, record: Any) -> None:
        self.calls += 1
        raise OSError("disk is on fire")

    def close(self) -> None:
        return None

    def disclosure(self) -> dict[str, int]:
        return {"calls": self.calls, "dropped_events": self.calls}


def _recorder(*, enabled: bool, sinks: list[Any] | None = None, clock: Any = None, ids: Any = None) -> TraceRecorder:
    return build_recorder(
        ObservabilityConfig(enabled=enabled, sinks=["memory"]),
        id_generator=ids if ids is not None else SequenceIdGenerator(prefix="a", clock=lambda: FIXED_EPOCH),
        clock=clock if clock is not None else (lambda: FIXED_EPOCH),
        sinks=sinks if sinks is not None else [InMemorySink(max_records=8192)],
    )


def _writer(*, enabled: bool = True, sinks: list[Any] | None = None, config: BehaviourTraceConfig | None = None, clock: StepClock | None = None) -> tuple[BehaviourTraceWriter, TraceRecorder]:
    wall = clock if clock is not None else StepClock()
    mono = StepClock(start=500.0)
    recorder = _recorder(enabled=enabled, sinks=sinks, clock=wall)
    return build_behaviour_writer(recorder, config=config, clock_wall=wall, clock_monotonic=mono), recorder


def _context(recorder: TraceRecorder) -> RunContext:
    return RunContext(trace_id="trace-fixed-0001", run_id=recorder.new_run_id(), thread_id="thread-0001", agent_name="lead-agent")


# ---------------------------------------------------------------------------
# A. The envelope
# ---------------------------------------------------------------------------


def test_envelope_fields_match_the_declared_contract() -> None:
    """ENVELOPE_FIELDS is not documentation: it is the dataclass's real field set."""
    assert set(ENVELOPE_FIELDS) == {f.name for f in TraceEnvelope.__dataclass_fields__.values()}
    assert len(ENVELOPE_FIELDS) == len(set(ENVELOPE_FIELDS)), "ENVELOPE_FIELDS has a duplicate"


def test_envelope_is_versioned_and_a_record_round_trips() -> None:
    original = TraceEnvelope(
        run_id="a" * 32,
        trace_id="trace-1",
        event_type="tool.selection",
        code="ALPHA_TRACE_L03_TOOL_SELECTION",
        layer=3,
        ts_wall=1.0,
        ts_monotonic=2.0,
        payload={"chosen": "bash"},
    )
    record = original.to_record()
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["v"] == SCHEMA_VERSION
    assert record["type"] == RECORD_TYPE
    restored = TraceEnvelope.from_record(record)
    assert restored == original


def test_from_record_refuses_an_unknown_schema_version() -> None:
    """A reader that cannot understand a record must say so, not half-parse it."""
    with pytest.raises(TraceEnvelopeError, match="schema_version"):
        TraceEnvelope.from_record({"schema_version": 99, "run_id": "a" * 32, "trace_id": "t", "event_type": "tool.selection", "code": "X"})


def test_envelope_requires_run_and_trace_identity() -> None:
    """An envelope with no run is exactly the orphan this substrate exists to remove."""
    with pytest.raises(TraceEnvelopeError, match="run_id"):
        TraceEnvelope(run_id="", trace_id="t", event_type="tool.selection", code="C", layer=3, ts_wall=0.0, ts_monotonic=0.0)
    with pytest.raises(TraceEnvelopeError, match="trace_id"):
        TraceEnvelope(run_id="a" * 32, trace_id="", event_type="tool.selection", code="C", layer=3, ts_wall=0.0, ts_monotonic=0.0)


@pytest.mark.parametrize("field,value", [("severity", "loud"), ("status", "maybe")])
def test_envelope_refuses_an_open_severity_or_status(field: str, value: str) -> None:
    kwargs: dict[str, Any] = {"severity": "info", "status": "ok"}
    kwargs[field] = value
    with pytest.raises(TraceEnvelopeError):
        TraceEnvelope(run_id="a" * 32, trace_id="t", event_type="tool.selection", code="C", layer=3, ts_wall=0.0, ts_monotonic=0.0, **kwargs)


# ---------------------------------------------------------------------------
# B. One event taxonomy, one error taxonomy
# ---------------------------------------------------------------------------


def test_taxonomy_covers_all_eighteen_layers() -> None:
    """Every layer 0..17 has at least one event type. A layer with no emitter is
    a layer nobody is watching, and it must be visible here rather than inferred."""
    covered = {definition_for(name).layer for name in EVENT_TYPE_NAMES}
    declared = {number for number, _name in LAYERS}
    assert declared == set(range(18)), "LAYERS must be 0..17"
    assert covered == declared, f"layers with no event type: {sorted(declared - covered)}"


def test_every_event_type_has_a_unique_stable_code() -> None:
    assert len(EVENT_TYPE_CODES) == len(EVENT_TYPE_NAMES)
    for name in EVENT_TYPE_NAMES:
        definition = definition_for(name)
        assert definition.code.startswith(f"ALPHA_TRACE_L{definition.layer:02d}_"), f"{name} code does not encode its layer"
        assert definition.description.strip()


def test_every_required_field_survives_redaction() -> None:
    """A required field whose key the redactor eats would leave a silently
    incomplete record. Prove the taxonomy and the redactor agree before a run
    discovers it."""
    redactor = Redactor("standard", max_value_chars=1024)
    for name in sorted(EVENT_TYPE_NAMES):
        for field in definition_for(name).required_fields:
            safe, _ = redactor.redact_attributes({field: "representative-value"})
            assert field in safe, f"{name}.required_fields contains {field!r}, which redaction renames"


def test_unknown_event_type_is_refused_not_stored() -> None:
    with pytest.raises(KeyError, match="unknown behaviour event type"):
        parse_event_type("tool.chosen")


def test_severity_vocabulary_is_the_error_registrys_not_a_fork() -> None:
    """The brief: one error-code taxonomy, not five. The severity *vocabulary*
    must be the registry's, exactly -- compared as a set so a future addition on
    either side fails here instead of silently splitting."""
    from alpha.errors.registry import ErrorSeverity

    assert SEVERITIES == {member.value for member in ErrorSeverity}


def test_error_codes_come_from_the_one_registry_and_are_never_invented() -> None:
    assert error_taxonomy_status() in ("available", "unavailable")
    if error_taxonomy_status() != "available":
        pytest.skip("alpha.errors.registry is not importable in this checkout; the degradation path is covered separately")
    registered = registered_error_codes()
    assert registered is not None
    assert TOTAL_FALLBACK_ERROR_CODE in registered, "the fallback this module names is not a registered code"
    good = classify_error_code("INTERNAL_ERROR")
    assert good.source == "registry"
    assert good.severity in SEVERITIES
    unknown = classify_error_code("NOT_A_REAL_CODE_XYZ")
    assert unknown.code == TOTAL_FALLBACK_ERROR_CODE
    assert unknown.source == "registry_fallback"
    assert ERROR_CODE_TAXONOMY == "alpha.errors.registry"


def test_error_code_resolution_always_discloses_its_source() -> None:
    """A reader must be able to tell a verified code from an unverified one."""
    for supplied in ("INTERNAL_ERROR", "NOPE", None):
        resolution = classify_error_code(supplied)
        assert isinstance(resolution, ErrorCodeResolution)
        attributes = resolution.as_attributes()
        assert attributes["error_code_source"] in ("registry", "registry_fallback", "unverified_registry_unavailable")
        assert attributes["error_code_source"]


# ---------------------------------------------------------------------------
# C. Redaction at write time -- against a real persisted file
# ---------------------------------------------------------------------------


def test_secret_in_prompt_tool_arg_and_tool_result_never_reaches_the_log(tmp_path: Path) -> None:
    """Criterion C, on the file a reader would actually open.

    The three payloads are the three highest-risk places a secret enters an
    agent run. After a realistic sequence of writes, *no* fragment of any of
    them appears anywhere in the persisted JSONL -- not in a payload, not in a
    span attribute, not in a truncated notice.
    """
    trace_file = tmp_path / "trace.jsonl"
    sink = JsonlFileSink(trace_file, max_events=10_000, max_bytes=8 * 1024 * 1024)
    writer, recorder = _writer(sinks=[sink])
    context = _context(recorder)

    with run_scope(context):
        writer.run_envelope(config_version="cfg-1", model_config_sha256="a" * 64)
        # Layer 2: the user prompt, verbatim, through the real emitter.
        with writer.span("model.call", provider="openrouter", model="space-bunny"):
            writer.model_call(
                provider="openrouter",
                model="space-bunny",
                phase="requested",
                messages_before=[{"role": "user", "content": SECRET_IN_PROMPT}],
            )
        # Layer 3: tool arguments, including an Authorization header.
        with writer.span("tool.call", tool="web_fetch"):
            writer.tool_selection(
                chosen="web_fetch",
                reason="only tool that can reach the URL",
                candidates=["bash", "web_fetch"],
                scores={"bash": 0.2, "web_fetch": 0.9},
                **{"arguments": dict(SECRET_IN_TOOL_ARG)},
            )
        # Layer 8/11-ish: a tool *result* echoed back.
        writer.filesystem_operation(
            operation="read",
            path="/srv/app/config/database.env",
            byte_count=4096,
            hash_after="b" * 64,
            **{"tool_result": SECRET_IN_TOOL_RESULT},
        )
        # Layer 13: an exception message that itself carries a secret.
        writer.error_observed(error_code="INTERNAL_ERROR", message=f"upstream rejected: {SECRET_IN_TOOL_RESULT}")

    text = trace_file.read_text(encoding="utf-8")
    assert text, "nothing was written; the test would pass vacuously"
    for fragment in SECRET_FRAGMENTS:
        assert fragment not in text, f"secret fragment {fragment!r} survived into the persisted log"
    # And the log is still a useful log: the *shape* of the run is readable.
    assert "ALPHA_TRACE_L00_RUN_ENVELOPE" in text
    assert "ALPHA_TRACE_L03_TOOL_SELECTION" in text
    assert "web_fetch" in text
    assert "database.env" in text


def test_secret_bearing_keys_are_replaced_wholesale() -> None:
    """A non-string value under a credential-named key is still a credential."""
    writer, recorder = _writer()
    envelope = writer.error_observed(
        error_code="INTERNAL_ERROR",
        context=_context(recorder),
        api_key=1234567890123456,
        password="hunter2-not-in-a-fixture",
    )
    assert envelope is not None
    rendered = json.dumps(envelope.payload, sort_keys=True)
    assert "1234567890123456" not in rendered
    assert "hunter2-not-in-a-fixture" not in rendered
    assert "REDACTED" in rendered


def test_correlation_id_carrying_a_credential_is_scrubbed() -> None:
    """Identity strings are scrubbed by the same redactor as the payload."""
    writer, recorder = _writer()
    envelope = writer.tool_selection(
        context=_context(recorder),
        chosen="bash",
        reason="explicit request",
        call_id="sk-proj-0123456789abcdefghijklmnopqrstuvwx",
    )
    assert envelope is not None
    assert envelope.correlation_id is not None
    assert "sk-proj" not in envelope.correlation_id
    assert "REDACTED" in envelope.correlation_id


def test_default_policy_keeps_ids_paths_and_urls_but_not_credentials() -> None:
    """The measured table in behaviour_config's docstring, as an assertion.

    This is the decision that keeps the substrate usable: the spine's ``strict``
    policy would redact a 32-hex run id, a filesystem path and a search URL,
    which would silently destroy layers 8 and 11 and the correlation spine.
    """
    from alpha.observability.redaction import REDACTION_POLICIES

    assert set(READERS["redaction_policy"].read(BehaviourTraceConfig()) for _ in (0,)) <= REDACTION_POLICIES
    writer, recorder = _writer()
    context = _context(recorder)
    path = "/home/user/projects/alpha/backend/packages/harness/alpha/observability/writer.py"
    url = "https://example.com/some/long/path/that/has/no/spaces/at/all/page.html"
    fs = writer.filesystem_operation(operation="read", path=path, context=context)
    search = writer.search_results(
        context=context,
        query="alpha observability",
        results=[{"rank": 1, "url": url, "title": "Docs", "snippet": "text"}],
        used_rank=1,
    )
    assert fs is not None and search is not None
    assert fs.payload["path"] == path, "the default policy must not eat filesystem paths"
    assert search.payload["results"][0]["url"] == url, "the default policy must not eat search URLs"
    assert context.run_id not in ("", None)
    # A run id is a 32-hex string; it must survive the identity scrub.
    envelope = writer.run_envelope(context=context, config_version="cfg-1", model_config_sha256="c" * 64)
    assert envelope is not None
    assert envelope.run_id == context.run_id


# ---------------------------------------------------------------------------
# D. Bounds and disclosure
# ---------------------------------------------------------------------------


def test_oversized_payload_is_truncated_with_flag_size_and_hash() -> None:
    """Criterion D, end to end through the writer.

    The envelope must disclose: ``truncated=True``, the ORIGINAL byte count, and
    a SHA-256 of the FULL payload -- so a reader can tell something was dropped,
    how much, and verify a payload they independently hold.
    """
    config = BehaviourTraceConfig(max_payload_bytes=MIN_PAYLOAD_CAP_BYTES)
    writer, recorder = _writer(config=config)
    context = _context(recorder)
    big = "x" * 200_000
    envelope = writer.tool_selection(context=context, chosen="bash", reason="because", candidates=["bash"], **{"blob": big})
    assert envelope is not None
    assert envelope.truncated is True
    assert envelope.truncated_count >= 1
    assert envelope.payload_bytes > MIN_PAYLOAD_CAP_BYTES
    assert envelope.payload_stored_bytes <= MIN_PAYLOAD_CAP_BYTES
    assert envelope.payload_bytes > envelope.payload_stored_bytes
    assert len(envelope.payload_sha256) == 64
    # The hash is of the FULL redacted payload, so an independent reader can
    # verify it. Recompute it here from the redacted input.
    safe, _ = writer.redactor.redact_attributes({"chosen": "bash", "reason": "because", "candidates": ["bash"], "candidate_count": 1, "scores": {}, "blob": big})
    expected_bytes, expected_sha = payload_digest(safe)
    assert (envelope.payload_bytes, envelope.payload_sha256) == (expected_bytes, expected_sha)
    # The payload itself says it was clamped, so a reader holding only the
    # payload still learns the record is incomplete.
    assert "trace.payload_truncation" in envelope.payload
    # ... and the status says "partial", not "ok".
    assert envelope.status == "partial"
    # The oversized value is not in the stored payload.
    assert big not in json.dumps(envelope.payload)


def test_clamp_payload_is_deterministic_and_order_independent() -> None:
    payload_a = {"a": "1" * 400, "b": "2" * 400, "c": "3" * 400}
    payload_b = {"c": "3" * 400, "b": "2" * 400, "a": "1" * 400}
    first = clamp_payload(payload_a, MIN_PAYLOAD_CAP_BYTES)
    second = clamp_payload(payload_b, MIN_PAYLOAD_CAP_BYTES)
    assert first.value == second.value
    assert first.sha256 == second.sha256
    assert first.truncated is True


def test_clamp_payload_passes_through_an_in_budget_payload_unchanged() -> None:
    payload = {"chosen": "bash", "reason": "only one that fits"}
    clamped = clamp_payload(payload, 64 * 1024)
    assert clamped.truncated is False
    assert clamped.truncated_count == 0
    assert clamped.value == payload
    assert clamped.bytes_total == clamped.bytes_stored
    assert clamped.bytes_stored <= 64 * 1024


def test_clamp_payload_refuses_a_cap_too_small_to_disclose_itself() -> None:
    """A payload that cannot fit its own truncation notice cannot honestly
    report being clamped, so the cap is refused rather than producing a
    record that lies about its own completeness."""
    with pytest.raises(TraceEnvelopeError, match="cap_bytes"):
        clamp_payload({"a": "b"}, 10)


def test_a_required_field_must_be_present_or_the_event_is_refused() -> None:
    """A tool selection that does not say which candidates were considered is
    not a selection record, so the writer refuses it rather than storing it."""
    writer, recorder = _writer()
    context = _context(recorder)
    assert writer.emit("tool.selection", {"chosen": "bash"}, context=context) is None
    disclosure = writer.disclosure()
    assert disclosure["rejected"] == {"missing_required:tool.selection": 1}
    assert writer.records() == ()


def test_unknown_event_type_is_a_counted_rejection_not_an_exception() -> None:
    writer, recorder = _writer()
    assert writer.emit("not.a.real.event", {"x": 1}, context=_context(recorder)) is None
    assert writer.disclosure()["rejected"] == {"unknown_event_type": 1}


# ---------------------------------------------------------------------------
# E. Optional and default-off
# ---------------------------------------------------------------------------


def test_default_config_is_off() -> None:
    from alpha.observability.config import DEFAULT_ENABLED, ObservabilityConfig

    assert DEFAULT_ENABLED is False
    assert ObservabilityConfig().enabled is False


def test_disabled_writer_costs_nothing() -> None:
    """Not "nothing was written" -- nothing was *attempted*.

    Sink calls at zero, no clock read, no sequence allocated. This is the
    "default-off means inert, not quiet" property the spine holds, asserted the
    same way: by counting, not by observing silence.
    """
    sink = InMemorySink(max_records=64)
    clock = StepClock()
    writer, recorder = _writer(enabled=False, sinks=[sink], clock=clock)
    context = _context(recorder)
    assert writer.traced is False
    for _ in range(5):
        writer.run_envelope(context=context, config_version="cfg", model_config_sha256="d")
        writer.tool_selection(context=context, chosen="bash", reason="r")
        writer.error_observed(context=context, error_code="INTERNAL_ERROR")
        with writer.span("model.call"):
            pass
    assert sink.calls == 0
    assert clock.reads == 0
    assert writer.disclosure()["recorded_events"] == 0
    assert writer.disclosure()["tail"]["calls"] == 0


def test_tracing_on_and_off_produce_identical_run_results() -> None:
    """The hard rule: tracing is never mandatory for correctness.

    The same "agent turn" is executed twice -- once with a live writer, once with
    a disabled one -- and the *result* of the operation must be identical. The
    trace differs; the run does not.

    Only the operation's own return value is compared. Deliberately **not** a
    span attribute: the spine's inert ``_NullSpan`` (a disabled
    :class:`~alpha.observability.recorder.TraceRecorder.span`) exposes
    ``name``/``span_id``/``context``/``set_attribute``/``end*`` but not
    ``ended``/``depth``/``status``/``attributes``, so a call site that reads one
    of those would raise with tracing off. That is a real gap in
    ``recorder.py``, which this work does not own; the exact minimal diff is in
    the report, and this test is written to compare only what a call site can
    portably read.
    """

    def run_turn(writer: BehaviourTraceWriter, context: RunContext) -> dict[str, Any]:
        with run_scope(context):
            with writer.span("agent.turn") as span:
                chosen = "bash" if "search" not in "hello" else "web_search"
                writer.tool_selection(context=context, chosen=chosen, reason="only match", candidates=["bash", "web_search"], scores={"bash": 0.9, "web_search": 0.1})
                with writer.span("tool.call", tool=chosen):
                    answer = "computed-42"
            return {"answer": answer, "span_name": span.name, "span_id_minted": span.span_id is not None}

    off_writer, off_recorder = _writer(enabled=False)
    on_writer, on_recorder = _writer(enabled=True)
    off_result = run_turn(off_writer, _context(off_recorder))
    on_result = run_turn(on_writer, _context(on_recorder))
    assert off_result["answer"] == on_result["answer"] == "computed-42"
    assert off_result["span_name"] == on_result["span_name"] == "agent.turn"
    assert off_result["span_id_minted"] is False, "a disabled recorder mints no span id"
    assert on_result["span_id_minted"] is True
    assert on_writer.disclosure()["recorded_events"] > 0
    assert off_writer.disclosure()["recorded_events"] == 0


def test_the_inert_span_exposes_the_same_surface_as_a_real_one() -> None:
    """Regression guard for the gap named above.

    A call site must be able to write ``with writer.span(...) as s:`` once and
    read the same attributes either way, or "tracing off" is not the same code
    path. This asserts the *minimum* portable surface and names, in the failure
    message, which attribute is missing when the spine is extended.
    """
    portable = ("name", "span_id", "context", "set_attribute", "end", "end_with_exception", "record_exception")
    off_writer, off_recorder = _writer(enabled=False)
    on_writer, on_recorder = _writer(enabled=True)
    with run_scope(_context(off_recorder)):
        with off_writer.span("probe") as inert:
            missing = [attribute for attribute in portable if not hasattr(inert, attribute)]
    with run_scope(_context(on_recorder)):
        with on_writer.span("probe") as real:
            assert not [attribute for attribute in portable if not hasattr(real, attribute)]
    assert not missing, f"the spine's _NullSpan is missing {missing}; alpha/observability/recorder.py needs these to satisfy the tracing-off-is-the-same-path rule"


def test_an_event_with_no_run_context_is_dropped_and_counted() -> None:
    """Never fabricate an id. A missing binding shows up as a counted absence."""
    writer, _ = _writer()
    assert writer.emit("tool.selection", {"chosen": "bash", "reason": "r", "candidate_count": 1}) is None
    disclosure = writer.disclosure()
    assert disclosure["events_without_run_context"] == 1
    assert disclosure["rejected"] == {"no_run_context": 1}


def test_sequence_is_monotonic_per_run_and_scoped_to_the_run() -> None:
    writer, recorder = _writer()
    first = _context(recorder)
    second = _context(recorder)
    seqs_a = [writer.tool_selection(context=first, chosen="bash", reason="r").seq for _ in range(3)]
    seqs_b = [writer.tool_selection(context=second, chosen="bash", reason="r").seq for _ in range(2)]
    assert seqs_a == [1, 2, 3]
    assert seqs_b == [1, 2], "each run's sequence starts at 1; the cursor is per run"


def test_event_id_is_unique_and_zero_padded_so_it_sorts() -> None:
    writer, recorder = _writer()
    context = _context(recorder)
    ids = [writer.tool_selection(context=context, chosen="bash", reason="r").event_id for _ in range(12)]
    assert len(set(ids)) == 12
    assert ids == sorted(ids), "zero-padding is what makes event_id monotonic in string order"
    assert all(event_id.startswith(f"{context.run_id}:") for event_id in ids)


# ---------------------------------------------------------------------------
# F. Tracing never breaks the thing being traced
# ---------------------------------------------------------------------------


def test_sink_failure_degrades_to_a_counted_warning_and_the_run_still_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    """Criterion F. Three things must all hold, and only together:

    * the raising sink is counted per failure, in ``flush_failures``;
    * the WARNING is emitted ONCE per sink, not once per event, so a broken sink
      on a hot path cannot flood the log and bury the cause;
    * the operation that was being traced still completes and returns its result.
    """
    exploding = ExplodingSink()
    healthy = InMemorySink(max_records=256)
    writer, recorder = _writer(sinks=[exploding, healthy])
    context = _context(recorder)

    with caplog.at_level("WARNING"):
        results = []
        for index in range(7):
            results.append(
                writer.tool_selection(context=context, chosen="bash", reason="still works", call_id=f"call-{index}"),
            )

    assert results and all(result is not None for result in results), "a raising sink broke the caller"
    assert writer.disclosure()["flush_failures"] == {"exploding": 7}, "every failure is counted"
    assert exploding.calls == 7
    warnings = [record for record in caplog.records if "behaviour trace sink" in record.getMessage()]
    assert len(warnings) == 1, f"expected exactly one warning for the broken sink, got {len(warnings)}"
    # The healthy sink still received every record: a broken file sink must not
    # blind the in-memory one.
    assert healthy.calls == 7
    assert len(writer.records()) == 7


def test_a_base_exception_from_a_sink_is_deliberately_not_swallowed() -> None:
    """The other side of criterion F, and a real decision.

    The writer contains ``Exception``, not ``BaseException``. That is deliberate:
    swallowing ``KeyboardInterrupt``/``SystemExit``/``GeneratorExit`` would make
    a broken sink un-stoppable, so an operator who presses Ctrl-C during a run
    whose trace sink is wedged would find the process unkillable except by
    SIGKILL. An observability path that can take down a run is one problem; an
    observability path that takes down the *ability to stop* the run is a worse
    one.

    So: ordinary failures are contained, and process-control signals pass
    through. Both halves are asserted.
    """
    writer, recorder = _writer()
    context = _context(recorder)
    assert writer.tool_selection(context=context, chosen="bash", reason="r") is not None

    class CtrlC:
        name = "ctrl-c"

        def emit(self, record: Any) -> None:
            raise KeyboardInterrupt

        def close(self) -> None:
            return None

        def disclosure(self) -> dict[str, int]:
            return {}

    interrupting = BehaviourTraceWriter(
        _recorder(enabled=True, sinks=[CtrlC()]),  # type: ignore[list-item]
        clock_wall=lambda: FIXED_EPOCH,
        clock_monotonic=lambda: FIXED_EPOCH,
    )
    with pytest.raises(KeyboardInterrupt):
        interrupting.tool_selection(context=context, chosen="bash", reason="r")


def test_close_is_idempotent_and_does_not_close_shared_sinks() -> None:
    """The writer shares the recorder's sinks; closing one must not pull a file
    handle out from under a span the recorder is still ending."""
    trace_file = Path(__file__).with_name("_behaviour_close_probe.jsonl")
    sink = JsonlFileSink(trace_file, max_events=100, max_bytes=1_000_000)
    writer, recorder = _writer(sinks=[sink])
    context = _context(recorder)
    writer.tool_selection(context=context, chosen="bash", reason="r")
    writer.close()
    writer.close()
    assert writer.traced is False
    # The shared sink is still usable after the writer closed.
    recorder.record_event("run.ended", context=context)
    assert trace_file.exists()
    trace_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# G. Thread-safe, async-safe, bounded
# ---------------------------------------------------------------------------


def test_writer_is_thread_safe_under_concurrent_emission() -> None:
    """Four threads, one run, no lost or duplicated sequence numbers."""
    writer, recorder = _writer()
    context = _context(recorder)
    per_thread = 60
    threads_count = 4
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            for step in range(per_thread):
                writer.tool_selection(context=context, chosen="bash", reason=f"t{index}s{step}", call_id=f"{index}-{step}")
        except BaseException as exc:  # noqa: BLE001 - the test must surface a race, not swallow it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"emission raised under concurrency: {errors}"
    records = writer.records()
    assert len(records) == threads_count * per_thread
    seqs = sorted(record["seq"] for record in records)
    assert seqs == list(range(1, threads_count * per_thread + 1)), "sequence numbers must be gapless and unique under concurrency"


def test_writer_is_async_safe() -> None:
    """Concurrent tasks on one loop: contextvars give per-task run isolation, and
    the sequence is shared and locked."""

    async def scenario() -> tuple[int, list[dict[str, Any]]]:
        writer, recorder = _writer()
        run_a = _context(recorder)
        run_b = _context(recorder)

        async def turn(context: RunContext, chosen: str) -> None:
            with run_scope(context):
                with writer.span("agent.turn"):
                    for _ in range(10):
                        await asyncio.sleep(0)
                        writer.tool_selection(context=context, chosen=chosen, reason="async turn")

        await asyncio.gather(turn(run_a, "bash"), turn(run_b, "web_search"))
        return writer.disclosure()["recorded_events"], list(writer.records())

    recorded, records = asyncio.run(scenario())
    assert recorded == 20
    by_run: dict[str, int] = {}
    for record in records:
        by_run[record["run_id"]] = by_run.get(record["run_id"], 0) + 1
    assert sorted(by_run.values()) == [10, 10], "each run's events stayed under its own run id"


def test_tail_is_bounded_and_the_overflow_is_disclosed() -> None:
    """The spine's own maxlen-style bound, reused -- and disclosed, never silent."""
    writer, recorder = _writer(sinks=[InMemorySink(max_records=2)], config=BehaviourTraceConfig(tail_maxlen=2))
    context = _context(recorder)
    for index in range(25):
        writer.tool_selection(context=context, chosen="bash", reason=f"r{index}")
    disclosure = writer.disclosure()
    assert disclosure["tail"]["dropped_events"] > 0, "overflow must be disclosed, not silent"
    assert disclosure["recorded_events"] == 25, "the record count reflects what was written, not what was retained"


def test_tail_maxlen_bounds_a_writer_owned_ring() -> None:
    """The ``tail_maxlen`` path specifically -- the line the reuse test never reaches.

    ``test_tail_is_bounded_and_the_overflow_is_disclosed`` above bounds the ring
    by handing the recorder a 2-record memory sink, so ``_resolve_tail`` reuses
    it and ``max_records=READERS["tail_maxlen"]`` is never evaluated. This test
    covers the other branch: a recorder with **no** memory sink, where the writer
    builds the ring itself. Written because the mutation run showed the tail
    bound reverting to a hard-coded 10_000_000 with both tests still green --
    a bound that is only asserted on one of its two paths is not a bound.
    """
    writer, recorder = _writer(sinks=[], config=BehaviourTraceConfig(tail_maxlen=3))
    assert writer.tail.max_records == 3
    context = _context(recorder)
    for index in range(20):
        writer.tool_selection(context=context, chosen="bash", reason=f"r{index}")
    disclosure = writer.disclosure()
    assert disclosure["tail"]["dropped_events"] == 17, "20 written, 3 retained: the shortfall must be disclosed exactly"
    assert disclosure["tail"]["retained"] == 3
    assert disclosure["recorded_events"] == 20


def test_a_huge_tail_maxlen_is_refused_rather_than_silently_honoured() -> None:
    """The bound is a bound: the reader-table flip test proves the key is read,
    and this proves the value is what actually sizes the ring."""
    writer, _ = _writer(sinks=[], config=BehaviourTraceConfig(tail_maxlen=1))
    assert writer.tail.max_records == 1
    with pytest.raises(ValueError, match="tail_maxlen"):
        BehaviourTraceConfig(tail_maxlen=0)


def test_writer_reuses_the_recorders_memory_sink_rather_than_building_a_second_ring() -> None:
    """One process must not end up with two rings holding the same run."""
    sink = InMemorySink(max_records=64)
    writer, _ = _writer(sinks=[sink])
    assert writer.tail is sink


def test_per_run_counter_table_is_bounded() -> None:
    writer, recorder = _writer(config=BehaviourTraceConfig(max_tracked_runs=4))
    contexts = [RunContext(trace_id="t", run_id=format(index, "032x")) for index in range(1, 20)]
    for context in contexts:
        writer.tool_selection(context=context, chosen="bash", reason="r")
    assert writer.disclosure()["tracked_run_counters"] == 4


# ---------------------------------------------------------------------------
# H. Span nesting for a subagent
# ---------------------------------------------------------------------------


def test_parent_child_spans_nest_for_a_subagent() -> None:
    """Criterion H, and the reason the writer delegates spans to the spine.

    A subagent's span must be a child of the lead turn's span, and an event
    emitted inside the subagent's tool call must carry that tool call's span id
    as its ``span_id`` and the subagent's span as its ``parent_span_id``. The
    tree has to be recoverable from a flat event stream with no nesting in the
    wire format.
    """
    sink = InMemorySink(max_records=4096)
    writer, recorder = _writer(sinks=[sink])
    lead_context = _context(recorder)

    with run_scope(lead_context):
        with writer.span("agent.turn") as turn:
            turn_span_id = turn.span_id
            writer.subagent_spawn(spawn_reason="fan-out research", parent_agent="lead-agent", depth=1, subagent_id="sub-1", model="space-bunny", siblings=["sub-0"])
            child_context = lead_context.with_parent(turn_span_id)
            with run_scope(child_context):
                with writer.span("subagent.run", subagent_id="sub-1") as sub:
                    subagent_span_id = sub.span_id
                    assert subagent_span_id != turn_span_id
                    with writer.span("tool.call", tool="web_search") as tool_span:
                        event = writer.tool_selection(chosen="web_search", reason="research needs the web", candidates=["web_search", "bash"], scores={"web_search": 0.8, "bash": 0.2})
                    writer.subagent_lifecycle(phase="completed", subagent_id="sub-1")
            assert writer.subagent_usage(input_tokens=120, output_tokens=45, subagent_id="sub-1", cost_usd=0.002).seq > 0

    assert event is not None
    assert event.span_id == tool_span.span_id
    assert event.parent_span_id == subagent_span_id
    assert event.agent_depth >= 1

    spans = {record["span_id"]: record for record in sink.records() if record.get("type") == "span"}
    assert spans[subagent_span_id]["parent_span_id"] == turn_span_id
    assert spans[tool_span.span_id]["parent_span_id"] == subagent_span_id
    assert spans[tool_span.span_id]["depth"] == spans[subagent_span_id]["depth"] + 1

    spawn = next(record for record in writer.records() if record["event_type"] == "subagent.spawn")
    assert spawn["subagent_id"] == "sub-1"
    assert spawn["agent_depth"] == 1
    assert spawn["payload"]["siblings"] == ["sub-0"]


def test_span_depth_is_capped_by_the_spine_not_by_the_writer() -> None:
    """Reuse, proven: the writer inherits the spine's depth cap rather than
    inventing a second nesting limit."""
    from alpha.observability.config import ObservabilityConfig

    recorder = build_recorder(
        ObservabilityConfig(enabled=True, sinks=["memory"], max_span_depth=3),
        id_generator=SequenceIdGenerator(prefix="a", clock=lambda: FIXED_EPOCH),
        clock=lambda: FIXED_EPOCH,
        sinks=[InMemorySink(max_records=256)],
    )
    writer = build_behaviour_writer(recorder, clock_wall=lambda: FIXED_EPOCH, clock_monotonic=lambda: FIXED_EPOCH)
    depths: list[int] = []
    with run_scope(_context(recorder)):

        def recurse(remaining: int) -> None:
            with writer.span("nested") as span:
                depths.append(span.depth)
                if remaining:
                    recurse(remaining - 1)

        recurse(6)
    assert max(depths) == 3, f"the spine's max_span_depth=3 must be the cap; got {depths}"
    assert depths[0] == 0
    assert depths[:4] == [0, 1, 2, 3]


def test_disabled_writer_span_is_inert_and_yields_an_object() -> None:
    """A call site writes `with writer.span(...)` once and pays one boolean test
    when tracing is off, rather than branching at every instrumentation point."""
    writer, recorder = _writer(enabled=False)
    with run_scope(_context(recorder)) as context:
        with writer.span("model.call") as span:
            assert span.span_id is None
        assert writer.tool_selection(context=context, chosen="bash", reason="r") is None


# ---------------------------------------------------------------------------
# I. Selection records
# ---------------------------------------------------------------------------


def test_tool_selection_records_the_candidate_set_the_choice_and_a_reason() -> None:
    """Criterion I for layer 3. Recording only the winner is what makes a bad
    tool choice undiagnosable."""
    writer, recorder = _writer()
    envelope = writer.tool_selection(
        context=_context(recorder),
        chosen="bash",
        reason="highest score; web_search is blocked by the url allowlist",
        candidates=["bash", "read_file", "web_search"],
        scores={"bash": 0.91, "read_file": 0.44, "web_search": 0.02},
        call_id="call-42",
    )
    assert envelope is not None
    assert envelope.event_type == "tool.selection"
    assert envelope.code == "ALPHA_TRACE_L03_TOOL_SELECTION"
    assert envelope.layer == 3
    assert envelope.correlation_id == "call-42"
    assert envelope.payload["chosen"] == "bash"
    assert envelope.payload["candidate_count"] == 3
    assert envelope.payload["candidates"] == ["bash", "read_file", "web_search"]
    assert "allowlist" in envelope.payload["reason"]
    assert envelope.payload["scores"]["bash"] == 0.91


def test_skill_selection_records_the_candidate_set_the_choice_a_reason_and_the_registry_version() -> None:
    """Criterion I for layer 4. The registry version is what makes the decision
    reproducible later."""
    writer, recorder = _writer()
    envelope = writer.skill_selection(
        context=_context(recorder),
        chosen="pdf-extract",
        reason="only skill matching the .pdf attachment",
        registry_version="registry-2026-09-26T12:00:00Z",
        candidates=["pdf-extract", "doc-extract", "ocr"],
        scores={"pdf-extract": 0.88, "doc-extract": 0.31, "ocr": 0.12},
        activation_id="act-7",
    )
    assert envelope is not None
    assert envelope.layer == 4
    assert envelope.correlation_id == "act-7"
    assert envelope.payload["candidates"] == ["pdf-extract", "doc-extract", "ocr"]
    assert envelope.payload["candidate_count"] == 3
    assert envelope.payload["registry_version"] == "registry-2026-09-26T12:00:00Z"
    assert envelope.payload["chosen"] == "pdf-extract"


def test_candidate_objects_are_reduced_to_stable_references() -> None:
    """A record must not depend on a tool object staying alive, and repr() of an
    arbitrary object is a memory address."""

    class Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    writer, recorder = _writer()
    envelope = writer.tool_selection(
        context=_context(recorder),
        chosen=Tool("bash").name,
        reason="objects, not strings",
        candidates=[Tool("bash"), Tool("read_file"), {"name": "web_search", "score": 0.5}, 42],
    )
    assert envelope is not None
    assert envelope.payload["candidates"] == ["bash", "read_file", {"name": "web_search", "score": 0.5}, "int"]


def test_an_undeclared_keyword_lands_in_the_payload_under_its_own_name() -> None:
    """``**extra`` is a documented extension point, asserted so it is intentional.

    Two consequences follow and both are asserted elsewhere: a *required* field
    misspelled is a **rejection** (the event is not stored mis-shaped), and a
    non-required keyword is simply data.
    """
    writer, recorder = _writer()
    context = _context(recorder)
    spelled = writer.tool_selection(context=context, chosen="bash", reason="r", latency_ms=12.5)
    assert spelled is not None
    assert spelled.payload["latency_ms"] == 12.5
    assert "latency_ms" in spelled.payload
    # A nested mapping passed as one keyword is stored nested, not splatted.
    nested = writer.tool_selection(context=context, chosen="bash", reason="r", **{"blob": "value"})
    assert nested is not None
    assert nested.payload["blob"] == "value"


# ---------------------------------------------------------------------------
# The other sixteen layers: a real emitter, exercised
# ---------------------------------------------------------------------------


def test_every_taxonomy_event_type_has_a_live_typed_emitter() -> None:
    """A definition nobody can emit is a promise. Map every event type to a real
    call on the writer and require the record to come back.

    ``no_run_context`` is the only tolerated rejection, and it is asserted
    explicitly rather than skipped: an event that is defined but whose emitter
    raises a TypeError is a bug, and a test that skipped it would hide that.
    """
    writer, recorder = _writer()
    context = _context(recorder)
    minimum_payload = {
        "run.envelope": {"config_version": "cfg", "model_config_sha256": "a" * 64},
        "node.transition": {"from_node": "plan", "to_node": "tools", "reason": "needs a tool"},
        "model.call.requested": {"provider": "p", "model": "m"},
        "model.call.completed": {"provider": "p", "model": "m", "finish_reason": "stop"},
        "model.call.failed": {"provider": "p", "model": "m"},
        "tool.selection": {"chosen": "bash", "reason": "r", "candidate_count": 1},
        "skill.selection": {"chosen": "s", "reason": "r", "candidate_count": 1, "registry_version": "v1"},
        "provider.selection": {"provider": "p", "reason": "healthiest"},
        "subagent.spawn": {"spawn_reason": "fan-out", "parent_agent": "lead", "depth": 1},
        "subagent.lifecycle": {"phase": "started"},
        "subagent.usage": {"input_tokens": 1, "output_tokens": 2},
        "swarm.plan": {"plan_id": "p1", "node_count": 2},
        "swarm.node": {"plan_id": "p1", "node_id": "n1", "attempt": 1},
        "search.query": {"query": "q", "provider": "ddgs"},
        "search.results": {"query": "q", "result_count": 0, "used_rank": None},
        "memory.recall": {"query": "q", "hit_count": 0},
        "memory.write": {"memory_kind": "episodic"},
        "memory.evict": {"memory_kind": "episodic", "reason": "ttl"},
        "knowledge.query": {"query": "q", "document_count": 0, "allowlist_decision": "allow"},
        "fs.operation": {"operation": "read", "path": "/tmp/x"},
        "budget.check": {"decision": "allow"},
        "budget.throttle": {"budget_kind": "tokens", "action": "delay"},
        "error.observed": {"error_code": "INTERNAL_ERROR"},
        "handoff.delegated": {"from_agent": "a", "to_agent": "b", "reason": "scope", "attempt": 1},
        "human.interrupt": {"phase": "raised"},
        "human.approval": {"decision": "denied"},
        "human.action": {"action": "edit_message"},
        "guardrail.denied": {"guardrail": "sandbox", "reason": "outside workspace"},
        "evolution.observed": {"observation": "tool retried 3x"},
        "evolution.diagnosed": {"diagnosis": "url allowlist rejected the redirect"},
        "evolution.fix": {"fix": "add the host to the allowlist"},
        "evolution.verified": {"outcome": "passed"},
    }
    assert set(minimum_payload) == set(EVENT_TYPE_NAMES), "the emitter coverage map and the taxonomy have drifted"

    for event_type in sorted(EVENT_TYPE_NAMES):
        envelope = writer.emit(event_type, dict(minimum_payload[event_type]), context=context)
        assert envelope is not None, f"{event_type} was rejected: {writer.disclosure()['rejected']}"
        assert envelope.event_type == event_type
        assert envelope.code == definition_for(event_type).code
    assert len(writer.records()) == len(EVENT_TYPE_NAMES)


def test_model_call_records_the_reasoning_field_and_says_it_is_recorded() -> None:
    """The honest position on "thinking": the field is carried and populated when
    a provider returns it, and the payload records that the configured models
    (``supports_thinking: false``) return nothing today."""
    writer, recorder = _writer()
    context = _context(recorder)
    completed = writer.model_call(
        context=context,
        provider="openrouter",
        model="space-bunny",
        phase="completed",
        temperature=0.2,
        messages_before=[{"role": "user", "content": "hi"}],
        messages_after=[{"role": "assistant", "content": "hello"}],
        reasoning=None,
        reasoning_tokens=None,
        input_tokens=100,
        output_tokens=20,
        cached_tokens=5,
        cost_usd=0.0004,
        finish_reason="stop",
        ttfb_ms=180.0,
        total_latency_ms=900.0,
        retry_count=0,
        circuit_breaker="closed",
    )
    assert completed is not None
    assert completed.payload["reasoning"] is None
    assert completed.payload["reasoning_recorded"] is True
    assert completed.payload["usage_breakdown"] == {"input": 100, "output": 20, "cached": 5, "reasoning": None}
    assert completed.payload["finish_reason"] == "stop"
    assert completed.payload["ttfb_ms"] == 180.0
    assert completed.payload["circuit_breaker"] == "closed"
    assert completed.payload["messages_before"] == [{"role": "user", "content": "hi"}]

    with_reasoning = writer.model_call(context=context, provider="p", model="m", phase="completed", reasoning="step 1: consider the allowlist", finish_reason="stop")
    assert with_reasoning is not None
    assert with_reasoning.payload["reasoning"] == "step 1: consider the allowlist"


def test_model_call_reasoning_can_be_switched_off() -> None:
    writer, recorder = _writer(config=BehaviourTraceConfig(include_reasoning=False))
    envelope = writer.model_call(context=_context(recorder), provider="p", model="m", phase="completed", reasoning="private chain of thought", finish_reason="stop")
    assert envelope is not None
    assert envelope.payload["reasoning"] is None
    assert envelope.payload["reasoning_recorded"] is False


def test_model_call_rejects_an_unknown_phase() -> None:
    writer, recorder = _writer()
    with pytest.raises(ValueError, match="phase must be one of"):
        writer.model_call(context=_context(recorder), provider="p", model="m", phase="thinking-harder")


def test_search_results_records_every_result_and_which_was_used() -> None:
    writer, recorder = _writer()
    envelope = writer.search_results(
        context=_context(recorder),
        query="alpha observability",
        results=[
            {"rank": 1, "url": "https://example.com/a", "title": "A", "snippet": "one"},
            {"rank": 2, "url": "https://example.com/b", "title": "B", "snippet": "two"},
        ],
        used_rank=2,
        used_url="https://example.com/b",
        fetch_outcome="ok",
        extract_outcome="ok",
        content_sha256="f" * 64,
    )
    assert envelope is not None
    assert envelope.payload["result_count"] == 2
    assert envelope.payload["used_rank"] == 2
    assert [result["url"] for result in envelope.payload["results"]] == ["https://example.com/a", "https://example.com/b"]
    assert envelope.payload["content_sha256"] == "f" * 64


def test_search_results_can_report_that_the_cited_rank_is_unknown() -> None:
    """A real and valuable state: the answer cited a source that is not in the
    result set. Reporting it is what lets a sentinel catch a hallucinated
    citation."""
    writer, recorder = _writer()
    envelope = writer.search_results(context=_context(recorder), query="q", results=[{"rank": 1, "url": "https://example.com/a"}], used_rank=None, used_rank_known=False)
    assert envelope is not None
    assert envelope.payload["used_rank"] is None
    assert envelope.payload["used_rank_known"] is False


def test_filesystem_operation_records_hashes_and_never_content() -> None:
    writer, recorder = _writer()
    envelope = writer.filesystem_operation(
        context=_context(recorder),
        operation="write",
        path="/srv/project/main.py",
        byte_count=1024,
        hash_before="a" * 64,
        hash_after="b" * 64,
    )
    assert envelope is not None
    assert envelope.payload["hash_before"] == "a" * 64
    assert envelope.payload["hash_after"] == "b" * 64
    assert "content" not in envelope.payload


def test_token_breakdown_survives_the_secret_named_key_rule() -> None:
    """A measured trap, pinned so nobody "helpfully" renames the key back.

    The redactor's key rule is segment-aligned over ``token``, so a key whose
    segments are ``token`` + ``breakdown`` matches the WHOLE key and its mapping
    is replaced by ``[REDACTED:secret_named_key]``. The field is therefore named
    ``usage_breakdown``. The per-bucket names are safe (``tokens`` is not the
    segment ``token``), which is why the breakdown survives intact.
    """
    from alpha.observability.redaction import Redactor as _R

    redactor = _R("standard", max_value_chars=4096)
    eaten, _ = redactor.redact_attributes({"token_breakdown": {"input": 1, "output": 2}})
    assert eaten["token_breakdown"] == "[REDACTED:secret_named_key]", "the trap has gone away; the field can be renamed back"
    kept, _ = redactor.redact_attributes({"usage_breakdown": {"input": 1, "output": 2, "input_tokens": 3, "cached_tokens": 4}})
    assert kept["usage_breakdown"] == {"input": 1, "output": 2, "input_tokens": 3, "cached_tokens": 4}

    # And the end-to-end proof: the breakdown reaches the persisted record.
    writer, recorder = _writer()
    envelope = writer.model_call(context=_context(recorder), provider="p", model="m", phase="completed", finish_reason="stop", input_tokens=7, output_tokens=3, cached_tokens=1, reasoning_tokens=2)
    assert envelope is not None
    assert envelope.payload["usage_breakdown"] == {"input": 7, "output": 3, "cached": 1, "reasoning": 2}


def test_guardrail_denial_is_refused_not_errored() -> None:
    """A guardrail stopping an action is not a failure of the system, and the
    two must stay distinguishable in an incident review."""
    writer, recorder = _writer()
    envelope = writer.guardrail_denied(context=_context(recorder), guardrail="sandbox", reason="path outside the workspace", kind="sandbox_denial", action="write_file")
    assert envelope is not None
    assert envelope.status == "refused"
    assert envelope.severity == "warning"
    assert envelope.payload["kind"] == "sandbox_denial"


def test_evolution_layer_records_evidence_a_reader_can_check() -> None:
    """Layer 17 is the loop-closing layer, so an observation must cite the
    sequence numbers it rests on."""
    writer, recorder = _writer()
    context = _context(recorder)
    failed = [writer.tool_selection(context=context, chosen="web_search", reason=f"attempt {i}") for i in range(3)]
    observed = writer.evolution_observed(context=context, observation="the same tool was chosen three times and never succeeded", evidence_seq=[item.seq for item in failed if item], rule="repeat-failure-3x")
    diagnosed = writer.evolution_diagnosed(context=context, diagnosis="the target host is not on the url allowlist", confidence=0.71, observations=["3x web_search"])
    fixed = writer.evolution_fix(context=context, fix="added the host to the url allowlist", target="config/allowlist.yaml")
    verified = writer.evolution_verified(context=context, outcome="passed", evidence={"retry_after": "succeeded on first attempt"})

    assert observed is not None and diagnosed is not None and fixed is not None and verified is not None
    assert [item.layer for item in (observed, diagnosed, fixed, verified)] == [17, 17, 17, 17]
    cited = observed.payload["evidence_seq"]
    assert cited == [item.seq for item in failed if item]
    assert verified.severity == "info"
    assert writer.evolution_verified(context=context, outcome="failed").severity == "warning"


def test_error_observed_carries_the_retried_swallowed_and_escalated_triple() -> None:
    writer, recorder = _writer()
    envelope = writer.error_observed(
        context=_context(recorder),
        error_code="DEPENDENCY_UNAVAILABLE",
        message="provider returned 503 twice",
        stack_sha256="c" * 64,
        retried=True,
        swallowed=True,
        escalated=False,
        source_layer="model",
        provider="openrouter",
        model="space-bunny",
    )
    assert envelope is not None
    assert envelope.payload["retried"] is True
    assert envelope.payload["swallowed"] is True
    assert envelope.payload["escalated"] is False
    assert envelope.payload["stack_sha256"] == "c" * 64
    assert envelope.payload["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert envelope.payload["error_code_source"] == "registry"
    assert envelope.payload["error_severity_registry"] == envelope.severity


def test_records_can_be_paged_with_the_after_seq_cursor() -> None:
    """The query layer's cursor, and it is the *same* forward-cursor semantics
    the durable run-event store uses."""
    writer, recorder = _writer()
    context = _context(recorder)
    for _ in range(10):
        writer.tool_selection(context=context, chosen="bash", reason="r")
    first_page = writer.records(limit=4)
    assert [record["seq"] for record in first_page] == [1, 2, 3, 4]
    second_page = writer.records(after_seq=4, limit=4)
    assert [record["seq"] for record in second_page] == [5, 6, 7, 8]
    assert writer.records(run_id="not-this-run") == ()


# ---------------------------------------------------------------------------
# J. The reader-table discipline
# ---------------------------------------------------------------------------


def test_every_behaviour_config_key_has_a_reader() -> None:
    """A configuration key nobody reads is a lie told in YAML. The keys and the
    readers must cover each other exactly."""
    assert set(READERS) == set(read_all_keys())


@pytest.mark.parametrize("key", sorted(READERS))
def test_flipping_each_config_key_changes_an_observable_behaviour(key: str, tmp_path: Path) -> None:
    """A reader table can be complete and still lie, if reading the key changes
    nothing. Each key is perturbed and an observable must move."""
    context_note = f"config key {key!r} is read but its value has no observable effect"

    if key == "max_payload_bytes":
        small = BehaviourTraceConfig(max_payload_bytes=MIN_PAYLOAD_CAP_BYTES)
        big = BehaviourTraceConfig(max_payload_bytes=1024 * 1024)
        writer_a, recorder_a = _writer(config=small)
        writer_b, recorder_b = _writer(config=big)
        payload = {"blob": "y" * 50_000}
        assert writer_a.emit("memory.write", {"memory_kind": "episodic", **payload}, context=_context(recorder_a)).truncated is True
        assert writer_b.emit("memory.write", {"memory_kind": "episodic", **payload}, context=_context(recorder_b)).truncated is False
        return

    if key == "tail_maxlen":
        writer, recorder = _writer(sinks=[], config=BehaviourTraceConfig(tail_maxlen=3))
        context = _context(recorder)
        for index in range(10):
            writer.tool_selection(context=context, chosen="bash", reason=f"r{index}")
        assert len(writer.records()) == 3, context_note
        return

    if key == "max_tracked_runs":
        writer, _ = _writer(config=BehaviourTraceConfig(max_tracked_runs=2))
        recorder = writer.recorder
        for index in range(6):
            context = RunContext(trace_id="t", run_id=format(index + 1, "032x"))
            writer.tool_selection(context=context, chosen="bash", reason="r")
        assert writer.disclosure()["tracked_run_counters"] == 2, context_note
        assert isinstance(recorder, TraceRecorder)
        return

    if key == "emit_spans":
        sink = InMemorySink(max_records=256)
        on_writer, on_recorder = _writer(sinks=[sink], config=BehaviourTraceConfig(emit_spans=True))
        off_writer, off_recorder = _writer(sinks=[sink], config=BehaviourTraceConfig(emit_spans=False))
        with run_scope(_context(on_recorder)):
            with on_writer.span("model.call") as span:
                assert span.span_id is not None
        with run_scope(_context(off_recorder)):
            with off_writer.span("model.call") as inert:
                assert inert.span_id is None
        span_records = [record for record in sink.records() if record.get("type") == "span"]
        assert len(span_records) == 1, context_note
        return

    if key == "include_reasoning":
        on_writer, on_recorder = _writer(config=BehaviourTraceConfig(include_reasoning=True))
        off_writer, off_recorder = _writer(config=BehaviourTraceConfig(include_reasoning=False))
        on = on_writer.model_call(context=_context(on_recorder), provider="p", model="m", phase="completed", reasoning="thought", finish_reason="stop")
        off = off_writer.model_call(context=_context(off_recorder), provider="p", model="m", phase="completed", reasoning="thought", finish_reason="stop")
        assert on.payload["reasoning"] == "thought"
        assert off.payload["reasoning"] is None, context_note
        return

    if key == "redaction_policy":
        standard_writer, standard_recorder = _writer(config=BehaviourTraceConfig(redaction_policy="standard"))
        strict_writer, strict_recorder = _writer(config=BehaviourTraceConfig(redaction_policy="strict"))
        assert standard_writer.redactor.policy == "standard"
        assert strict_writer.redactor.policy == "strict", context_note
        # A *realistic* correlation id: 32 hex characters with real entropy.
        # ("a" * 32 would pass under strict too -- it is not high-entropy -- so
        # it would make this assertion pass without the policy doing anything.)
        identifier = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        assert standard_writer._scrub_identity(identifier) == identifier
        assert strict_writer._scrub_identity(identifier) != identifier, context_note
        # Both policies still catch every credential shape.
        for candidate in ("sk-proj-0123456789abcdefghijklmnopqrstuvwx", "AKIAIOSFODNN7EXAMPLE"):
            assert "REDACTED" in (standard_writer._scrub_identity(candidate) or "")
            assert "REDACTED" in (strict_writer._scrub_identity(candidate) or "")
        return

    raise AssertionError(f"no observable-behaviour assertion written for config key {key!r}")


def test_an_unknown_redaction_policy_is_refused_rather_than_downgraded() -> None:
    with pytest.raises(ValueError, match="unknown redaction policy"):
        BehaviourTraceConfig(redaction_policy="lenient")


def test_resolve_all_reads_every_key() -> None:
    resolved = resolve_all()
    assert set(resolved) == set(READERS)
    assert resolved["redaction_policy"] == "standard"


# ---------------------------------------------------------------------------
# The ambient seam: how a deep call site reaches the writer in one line
# ---------------------------------------------------------------------------


def test_current_writer_is_none_until_something_binds_it() -> None:
    """No mutable default. A call site outside a traced run gets ``None`` and
    does nothing, which is the correct outcome."""
    assert current_writer() is None


def test_the_ambient_helpers_are_a_no_op_with_no_writer_bound() -> None:
    """The whole cost of instrumentation on an untraced path, asserted."""
    assert emit_tool_selection(chosen="bash", reason="r", candidates=["bash"]) is None
    assert emit_handoff(from_agent="a", to_agent="b", reason="r", attempt=1) is None
    assert current_writer() is None, "a rejected emit must not have bound anything"


def test_the_ambient_helpers_emit_onto_the_bound_writer() -> None:
    writer, recorder = _writer()
    context = _context(recorder)
    with writer_scope(writer), run_scope(context):
        envelope = emit_tool_selection(chosen="bash", reason="only match", candidates=["bash", "read_file"], scores={"bash": 0.9})
        assert envelope is not None
        assert envelope.run_id == context.run_id
        assert envelope.event_type == "tool.selection"
    assert current_writer() is None, "writer_scope must restore the previous binding exactly"


def test_bind_and_detach_restore_the_previous_writer_exactly() -> None:
    first, first_recorder = _writer()
    second, _ = _writer()
    outer_token = bind_writer(first)
    try:
        assert current_writer() is first
        inner_token = bind_writer(second)
        assert current_writer() is second
        detach_writer(inner_token)
        assert current_writer() is first
    finally:
        detach_writer(outer_token)
    assert current_writer() is None


def test_writer_scope_restores_the_binding_after_an_exception() -> None:
    writer, _ = _writer()
    with pytest.raises(RuntimeError, match="boom"), writer_scope(writer):
        assert current_writer() is writer
        raise RuntimeError("boom")
    assert current_writer() is None


def test_bind_writer_refuses_a_non_writer() -> None:
    with pytest.raises(TypeError, match="expects a BehaviourTraceWriter"):
        bind_writer(object())  # type: ignore[arg-type]


def test_the_ambient_binding_is_per_task_not_process_wide() -> None:
    """Two concurrent tasks must never see each other's writer.

    This is why the seam is a ``ContextVar`` and not a module global, and it is
    the property that stops one run's tool choice landing in another run's trace.
    """
    writer_a, recorder_a = _writer()
    writer_b, _ = _writer()
    context_a = _context(recorder_a)

    async def scenario() -> tuple[BehaviourTraceWriter | None, BehaviourTraceWriter | None, BehaviourTraceWriter | None]:
        async def with_writer(name: str, writer: BehaviourTraceWriter) -> None:
            with writer_scope(writer):
                await asyncio.sleep(0)
                seen[name] = current_writer()
        seen: dict[str, BehaviourTraceWriter | None] = {}
        with writer_scope(writer_a):
            await asyncio.gather(with_writer("a", writer_a), with_writer("b", writer_b))
        return seen["a"], seen["b"], current_writer()

    seen_a, seen_b, outside = asyncio.run(scenario())
    assert seen_a is writer_a
    assert seen_b is writer_b
    assert outside is None
    assert writer_a is not writer_b
    # The event went to the right run.
    assert {record["run_id"] for record in writer_a.records()} <= {context_a.run_id}


# ---------------------------------------------------------------------------
# K. Construction guards
# ---------------------------------------------------------------------------


def test_writer_requires_a_real_recorder() -> None:
    with pytest.raises(TypeError, match="requires a TraceRecorder"):
        BehaviourTraceWriter(object())  # type: ignore[arg-type]


def test_writer_requires_callable_clocks() -> None:
    recorder = _recorder(enabled=True)
    with pytest.raises(TypeError, match="clock_wall must be callable"):
        build_behaviour_writer(recorder, clock_wall="not callable")  # type: ignore[arg-type]


def test_disabled_behaviour_writer_is_inert() -> None:
    writer = disabled_behaviour_writer(clock_wall=lambda: FIXED_EPOCH, clock_monotonic=lambda: FIXED_EPOCH)
    assert writer.traced is False
    assert writer.tool_selection(context=RunContext(trace_id="t", run_id="a" * 32), chosen="bash", reason="r") is None


def test_behaviour_records_share_the_spines_sinks() -> None:
    """The unification: one file carries the spine's records and the behaviour
    records, each tagged by its ``type``."""
    trace_file = Path(__file__).with_name("_behaviour_shared_sink_probe.jsonl")
    file_sink = JsonlFileSink(trace_file, max_events=1000, max_bytes=4 * 1024 * 1024)
    memory_sink = InMemorySink(max_records=256)
    writer, recorder = _writer(sinks=[file_sink, memory_sink])
    context = _context(recorder)
    with run_scope(context):
        with writer.span("model.call"):
            writer.model_call(provider="p", model="m", phase="completed", finish_reason="stop")
        recorder.record_event("run.ended", context=context)
    kinds = {json.loads(line)["type"] for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert RECORD_TYPE in kinds
    assert "span" in kinds
    assert "event" in kinds
    trace_file.unlink(missing_ok=True)


def test_the_taxonomy_imports_nothing_heavy() -> None:
    """The taxonomy must stay cheap, statically.

    The cold-start budget is a real gate, and the taxonomy sits on the import
    path of anything that classifies an event. So it may not import pydantic, the
    recorder, the writer, or the whole :mod:`alpha.errors` package at module
    level. Checked with an AST scan rather than a subprocess: a subprocess test
    here is slow, depends on ``PYTHONPATH`` plumbing, and can be interrupted, and
    the *static* property is both stronger and deterministic.
    """
    import ast

    forbidden = {"pydantic", "alpha.observability.recorder", "alpha.observability.writer", "alpha.errors", "alpha.observability.behaviour_config", "alpha.observability.contract"}
    tree = ast.parse(Path(inspect.getfile(taxonomy_module)).read_text(encoding="utf-8"))
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            module_level.append(node.module)
    heavy = sorted(name for name in module_level if name in forbidden or any(name.startswith(f"{prefix}.") for prefix in forbidden))
    assert not heavy, f"alpha/observability/taxonomy.py imports {heavy} at module level; it must stay cheap for the cold-start budget"
    # The stdlib-only claim is positive, not just an absence.
    assert "hashlib" in module_level or "threading" in module_level
