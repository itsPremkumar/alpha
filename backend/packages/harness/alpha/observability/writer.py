"""The behaviour-trace writer: the only way anything is recorded.

:class:`BehaviourTraceWriter` is the whole of Alpha's behaviour tracing surface.
Every one of the 18 instrumentation layers calls one of its typed emitters;
there is no second way to record a behaviour fact, and no layer builds an
envelope by hand. That is the point: a trace two subsystems can populate with
two different shapes is a trace whose reader has to know which subsystem it is
looking at before it can trust the record.

What the writer does, in order
-----------------------------
1. **Identity.** Resolves ``run_id``/``trace_id``/``thread_id``/``agent_name``/
   ``parent_span_id`` from the ambient
   :class:`~alpha.observability.context.RunContext` (or from an explicit one) and
   scrubs every identity string through the same redactor as the payload. An
   event with no run context is **dropped and counted**, never written under a
   fabricated id.
2. **Validation.** The event type must be in the closed taxonomy
   (:mod:`alpha.observability.taxonomy`) and the payload must carry the
   definition's ``required_fields``. A violation is a call-site bug: it is
   counted, logged **once**, and the event is not written.
3. **Redaction at write time.** The payload goes through
   :meth:`~alpha.observability.redaction.Redactor.redact_attributes` *here*,
   before the envelope is built. There is no call site that can skip it and no
   sink that has to defend against it, because by the time a record exists it is
   already scrubbed. This closes the gap the brief names: redaction used to
   apply to traces only, never to logs.
4. **Bounding and disclosure.** The scrubbed payload is clamped to
   ``max_payload_bytes`` and the envelope carries ``truncated``,
   ``truncated_count``, the full payload's byte count and its SHA-256. Silent
   truncation is impossible by construction: :func:`clamp_payload` also writes a
   notice *into* the payload.
5. **Fan-out.** The record goes to the same sinks the spine's events and spans
   go to, so one trace file carries ``event``, ``span``, ``disclosure`` and
   ``behaviour`` records side by side. The writer does its own fan-out over the
   recorder's public ``sinks`` tuple rather than reaching into a private helper,
   because the recorder module is not this package's to change; the *sinks* are
   the shared resource, and that is what unification has to mean.

Where the run context comes from
--------------------------------
Every typed emitter takes an explicit ``context=`` keyword. That is not
decorative: without it, ``writer.tool_selection(..., context=ctx)`` silently puts
``ctx`` *into the payload* instead of selecting the run, and the resulting trace
records a run that does not exist. The failure is invisible at the call site and
catastrophic in the log, so the parameter is declared on every emitter rather
than left to a ``**kwargs`` catch-all.

Never break the thing being traced
----------------------------------
:class:`BehaviourTraceWriter` is not allowed to be the reason a run fails, and
not allowed to be invisible when it fails either:

* a sink that raises is counted per sink name, **logged once per sink** (a broken
  sink on a hot path would otherwise emit one WARNING per event and bury the
  cause), and the remaining sinks still receive the record;
* ``flush_failures`` in :meth:`disclosure` is the counter the brief asks for, so a
  caller that could not write its trace can *see* that;
* a malformed event is a counted rejection, not an exception into the agent's
  own execution path;
* the fan-out contains ``Exception``, **not** ``BaseException``. That is
  deliberate: swallowing ``KeyboardInterrupt``/``SystemExit`` would make a wedged
  sink un-stoppable, so Ctrl-C during a run whose trace sink is broken would
  leave the process killable only by SIGKILL. An observability path that can take
  down a run is one problem; one that takes down the ability to *stop* a run is a
  worse one. Asserted both ways in ``test_behaviour_trace.py``;
* nothing here awaits, sleeps, spawns a task, or holds a lock across a
  suspension point, so it is safe from a coroutine, a task, a thread, or a
  thread-pool worker.

Thread-safe, async-safe, bounded
--------------------------------
One ``threading.RLock`` guards the per-run sequence counters, the rejection
counters and the fan-out. The ambient run context and the current span are
``contextvars``, so per-task and per-thread isolation is the spine's rather than
this module's. The in-memory tail is an existing
:class:`~alpha.observability.recorder.InMemorySink` -- reused rather than
reimplemented, so the ``deque(maxlen=...)`` bound and the disclosed
``dropped_events`` count are literally the spine's own mechanism. If the
recorder is already configured with a ``memory`` sink, that sink *is* the tail;
the writer never creates a second ring.

One switch
----------
:meth:`traced` reads the injected recorder's ``ObservabilityConfig.enabled``,
which defaults ``False``. There is no second gate. A disabled writer's
:meth:`emit` returns ``None`` on its first statement -- before the clock is read,
before the redactor runs, before a sequence number is allocated and before any
sink is touched -- so tracing off is genuinely free and tracing on changes
nothing about the run's result. Both halves of that are tests, not claims:
``test_behaviour_trace.py::test_disabled_writer_costs_nothing`` counts sink
calls at zero, and
``::test_tracing_on_and_off_produce_identical_run_results`` runs the same
instrumented operation both ways and compares the results.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Final

from .behaviour_config import READERS, BehaviourTraceConfig
from .context import RunContext, current
from .contract import RECORD_TYPE, TraceEnvelope, TraceEnvelopeError, clamp_payload
from .recorder import InMemorySink, TraceRecorder, TraceSink
from .redaction import Redactor
from .span import Span, current_span
from .taxonomy import EventTypeDefinition, classify_error_code, definition_for

__all__ = [
    "MAX_IDENTITY_CHARS",
    "BehaviorWriter",
    "BehaviourTraceWriter",
    "EventTypeDefinition",
    "RECORD_TYPE",
    "build_behaviour_writer",
    "definition_for",
    "disabled_behaviour_writer",
]

logger = logging.getLogger(__name__)

#: Longest identity string stored. Ids are 32 hex characters and node names are
#: short, so 256 is generous while still bounding a caller that smuggled a
#: document into ``correlation_id``.
MAX_IDENTITY_CHARS: Final[int] = 256

#: Marker appended when an identity string is cut. Disclosed rather than silent,
#: for the same reason a cut payload is disclosed.
_IDENTITY_TRUNCATED: Final[str] = "[TRUNCATED:identity]"

#: Maps a :meth:`BehaviourTraceWriter.model_call` phase onto its event type. A
#: call has three edges, and collapsing them into one record destroys the TTFB
#: boundary, which only exists between the first and the last chunk.
_MODEL_CALL_PHASES: Final[Mapping[str, str]] = {
    "requested": "model.call.requested",
    "completed": "model.call.completed",
    "failed": "model.call.failed",
}


class _Rejection(Exception):
    """Internal: an event that must not be written, carrying a disclosure key.

    Deliberately not a public exception type. The writer catches it, counts it
    under ``disclosure()['rejected'][key]`` and logs once, so a call-site bug
    shows up as a number on a dashboard rather than as a traceback in somebody
    else's run.
    """


class BehaviourTraceWriter:
    """Records versioned, bounded, redacted behaviour events for one process.

    Args:
        recorder: The spine recorder whose sinks, config, tracer and id generator
            are reused. Its ``config.enabled`` **is** this writer's gate.
        config: Behaviour-trace bounds. Defaults to
            :class:`~alpha.observability.behaviour_config.BehaviourTraceConfig`.
        redactor: Override the redactor derived from *config*. Tests use this to
            pin a policy; production takes the configured one.
        clock_wall: Unix-seconds source.
        clock_monotonic: Monotonic-seconds source, and the only ordering authority
            inside one process.

    The recorder is **required**, not optional. There is deliberately no
    ``BehaviourTraceWriter()`` that quietly builds its own: a writer that owns a
    second recorder is a writer with two gates and two sets of disclosure
    counters, which is how "tracing is on" and "tracing is off" come to disagree.
    """

    __slots__ = (
        "_clock_monotonic",
        "_clock_wall",
        "_closed",
        "_config",
        "_disabled_recorder",
        "_flush_failures",
        "_lock",
        "_logged",
        "_no_context",
        "_per_run_seq",
        "_rejected",
        "_recorder",
        "_redactor",
        "_recorded",
        "_tail",
    )

    def __init__(
        self,
        recorder: TraceRecorder,
        *,
        config: BehaviourTraceConfig | None = None,
        redactor: Redactor | None = None,
        clock_wall: Any = time.time,
        clock_monotonic: Any = time.monotonic,
    ) -> None:
        if not isinstance(recorder, TraceRecorder):
            raise TypeError(f"BehaviourTraceWriter requires a TraceRecorder, got {type(recorder).__name__}")
        for name, value in (("clock_wall", clock_wall), ("clock_monotonic", clock_monotonic)):
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        self._recorder = recorder
        self._config = config if config is not None else BehaviourTraceConfig()
        self._redactor = (
            redactor
            if redactor is not None
            else Redactor(
                READERS["redaction_policy"].read(self._config),
                max_value_chars=READERS["max_payload_bytes"].read(self._config),
                max_items=64,
                max_depth=8,
            )
        )
        self._clock_wall = clock_wall
        self._clock_monotonic = clock_monotonic
        self._lock = threading.RLock()
        self._per_run_seq: OrderedDict[str, int] = OrderedDict()
        self._rejected: dict[str, int] = {}
        self._flush_failures: dict[str, int] = {}
        self._logged: set[str] = set()
        self._recorded = 0
        self._no_context = 0
        self._closed = False
        self._disabled_recorder: TraceRecorder | None = None
        self._tail = self._resolve_tail()

    # -- construction ---------------------------------------------------------

    def _resolve_tail(self) -> InMemorySink:
        """Return the bounded in-memory ring this writer reads back from.

        Reuses the recorder's existing ``memory`` sink when there is one, so a
        process never ends up with two rings holding the same run twice. When the
        recorder has no memory sink (a file-only deployment, or a disabled
        recorder) the writer builds one with this config's ``tail_maxlen`` -- the
        same ``deque``-maxlen bound and the same disclosed ``dropped_events``
        count the spine already uses, not a second mechanism.
        """
        for sink in self._recorder.sinks:
            if isinstance(sink, InMemorySink):
                return sink
        return InMemorySink(max_records=READERS["tail_maxlen"].read(self._config))

    @property
    def recorder(self) -> TraceRecorder:
        return self._recorder

    @property
    def config(self) -> BehaviourTraceConfig:
        return self._config

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    @property
    def tail(self) -> InMemorySink:
        """The bounded ring. ``disclosure()['dropped_events']`` is its overflow count."""
        return self._tail

    @property
    def traced(self) -> bool:
        """Whether this writer records anything.

        The one gate: the injected recorder's ``enabled``. A call site writes
        ``if writer.traced:`` -- or more usually just calls :meth:`emit` and lets
        the writer decide -- and there is no second switch to forget.
        """
        return self._recorder.enabled and not self._closed

    def __repr__(self) -> str:
        return f"BehaviourTraceWriter(traced={self.traced}, recorded={self._recorded})"

    # -- identity -------------------------------------------------------------

    def _scrub_identity(self, value: object) -> str | None:
        """Bound and scrub one identity string, or return ``None``.

        Identity strings are scrubbed with the *same* redactor as the payload, so
        a caller who puts a credential in ``correlation_id`` gets it replaced
        exactly as if it had been a payload value. The default ``standard``
        policy is what makes this safe rather than destructive: it catches every
        credential pattern family while leaving a 32-hex run id, a filesystem
        path and a URL intact. See :mod:`alpha.observability.behaviour_config` for
        the measured table behind that choice.
        """
        if value is None:
            return None
        text = value if isinstance(value, str) else str(value)
        if not text:
            return None
        outcome = self._redactor.redact_value(text, key=None)
        scrubbed = outcome.value if isinstance(outcome.value, str) else text
        if len(scrubbed) > MAX_IDENTITY_CHARS:
            # Disclosed, not silent: a cut identity is a correlation the reader
            # cannot follow, so the fact that it was cut has to be visible.
            return scrubbed[:MAX_IDENTITY_CHARS] + _IDENTITY_TRUNCATED
        return scrubbed

    def _resolve_run(self, context: RunContext | None) -> RunContext:
        if context is not None:
            if not isinstance(context, RunContext):
                raise _Rejection("bad_context_type")
            return context
        ambient = current()
        if ambient is None:
            raise _Rejection("no_run_context")
        return ambient

    def _next_seq(self, run_id: str) -> int:
        """Allocate the next per-run sequence number. The caller holds the lock.

        Per **run**, not per process: the brief's cursor is the existing
        ``after_seq``, a forward cursor within one run, and a process serving ten
        concurrent runs must not interleave ten runs' sequence numbers into one
        counter. ``event_id`` is ``<run_id>:<seq>`` zero-padded to six digits, so
        it is globally unique *and* lexicographically monotonic per run.
        """
        self._per_run_seq[run_id] = self._per_run_seq.get(run_id, 0) + 1
        self._per_run_seq.move_to_end(run_id)
        self._trim_runs()
        return self._per_run_seq[run_id]

    def _trim_runs(self) -> None:
        """Bound the per-run counter table. The caller holds the lock."""
        cap = READERS["max_tracked_runs"].read(self._config)
        while len(self._per_run_seq) > cap:
            self._per_run_seq.popitem(last=False)

    # -- the single write path ------------------------------------------------

    def emit(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        context: RunContext | None = None,
        status: str = "ok",
        severity: str | None = None,
        correlation_id: str | None = None,
        agent_name: str | None = None,
        subagent_id: str | None = None,
        agent_depth: int | None = None,
        node: str | None = None,
        from_node: str | None = None,
        to_node: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        extra_payload: Mapping[str, Any] | None = None,
    ) -> TraceEnvelope | None:
        """Build, scrub, bound and record one behaviour event.

        Args:
            event_type: A name from the closed taxonomy. Anything else is a
                counted rejection, not a stored string.
            payload: The event's own data. Merged with *extra_payload*, with
                *extra_payload* winning, then scrubbed and clamped.
            status: One of the closed status set. A clamped or degraded result is
                ``"partial"``, which is how a reader tells "this happened" from
                "this happened and here is all of it".
            severity: Overrides the definition's default. For an error-layer event
                the taxonomy's registered severity wins regardless; see
                :meth:`error_observed`.

        Returns:
            The recorded envelope, or ``None`` when the writer is disabled,
            closed, has no run context, or rejected the event. Every one of those
            is counted in :meth:`disclosure`.
        """
        if not self.traced:
            return None
        try:
            return self._emit_locked(
                event_type,
                payload,
                context=context,
                status=status,
                severity=severity,
                correlation_id=correlation_id,
                agent_name=agent_name,
                subagent_id=subagent_id,
                agent_depth=agent_depth,
                node=node,
                from_node=from_node,
                to_node=to_node,
                span_id=span_id,
                parent_span_id=parent_span_id,
                extra_payload=extra_payload,
            )
        except _Rejection as rejection:
            reason = str(rejection)
            with self._lock:
                self._rejected[reason] = self._rejected.get(reason, 0) + 1
                if reason == "no_run_context":
                    self._no_context += 1
            self._log_once(
                f"rejected:{reason}",
                "behaviour event %r was rejected (%s) and not recorded; this is a call-site bug and the count is in disclosure()['rejected']",
                event_type,
                reason,
            )
            return None
        except KeyError as exc:
            # taxonomy.definition_for raises KeyError for an unknown event type.
            with self._lock:
                self._rejected["unknown_event_type"] = self._rejected.get("unknown_event_type", 0) + 1
            self._log_once("rejected:unknown_event_type", "behaviour event %r is not in the closed taxonomy and was not recorded: %s", event_type, exc)
            return None
        except (ValueError, TypeError, TraceEnvelopeError) as exc:
            # Redaction or envelope construction refusing. Also a call-site bug,
            # also contained: an observability path that can take down a run is
            # not an observability path.
            with self._lock:
                self._rejected["invalid"] = self._rejected.get("invalid", 0) + 1
            self._log_once("rejected:invalid", "behaviour event %r was rejected as invalid and not recorded: %s", event_type, exc)
            return None

    def _emit_locked(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None,
        *,
        context: RunContext | None,
        status: str,
        severity: str | None,
        correlation_id: str | None,
        agent_name: str | None,
        subagent_id: str | None,
        agent_depth: int | None,
        node: str | None,
        from_node: str | None,
        to_node: str | None,
        span_id: str | None,
        parent_span_id: str | None,
        extra_payload: Mapping[str, Any] | None,
    ) -> TraceEnvelope:
        definition = definition_for(event_type)

        merged: dict[str, Any] = {}
        if payload:
            merged.update(payload)
        if extra_payload:
            merged.update(extra_payload)
        if any(field not in merged for field in definition.required_fields):
            raise _Rejection(f"missing_required:{definition.event_type}")

        run_context = self._resolve_run(context)

        # Redaction at write time, before the envelope exists. Two passes, both
        # through the same redactor: the payload mapping (keys included) and the
        # identity strings.
        safe_payload, _outcomes = self._redactor.redact_attributes(merged)
        # A required field whose *key* the redactor renamed would leave the stored
        # event silently incomplete, so the contract is re-checked against what
        # will actually be written rather than against what the caller passed.
        if any(field not in safe_payload for field in definition.required_fields):
            raise _Rejection(f"required_field_redacted:{definition.event_type}")

        clamped = clamp_payload(safe_payload, READERS["max_payload_bytes"].read(self._config))

        span = current_span()
        with self._lock:
            seq = self._next_seq(run_context.run_id)
            self._recorded += 1

        # An event belongs to the span in scope, so it carries that span's id and
        # that span's parent. Falling back to the run context's parent is what
        # makes a subagent's own events hang under its spawn span when no span is
        # open.
        effective_parent = parent_span_id
        if effective_parent is None and span is not None:
            effective_parent = span.parent_span_id
        if effective_parent is None:
            effective_parent = run_context.parent_span_id
        effective_depth = agent_depth if agent_depth is not None else (span.depth if span is not None else 0)

        envelope = TraceEnvelope(
            run_id=run_context.run_id,
            trace_id=run_context.trace_id,
            event_type=definition.event_type,
            code=definition.code,
            layer=definition.layer,
            ts_wall=float(self._clock_wall()),
            ts_monotonic=float(self._clock_monotonic()),
            payload=clamped.value,
            event_id=f"{run_context.run_id}:{seq:06d}",
            seq=seq,
            span_id=self._scrub_identity(span_id if span_id is not None else (span.span_id if span is not None else None)),
            parent_span_id=self._scrub_identity(effective_parent),
            thread_id=self._scrub_identity(run_context.thread_id),
            correlation_id=self._scrub_identity(correlation_id),
            agent_name=self._scrub_identity(agent_name if agent_name is not None else run_context.agent_name),
            subagent_id=self._scrub_identity(subagent_id),
            agent_depth=int(effective_depth),
            severity=severity if severity is not None else definition.default_severity,
            status="partial" if (clamped.truncated and status == "ok") else status,
            node=self._scrub_identity(node),
            from_node=self._scrub_identity(from_node),
            to_node=self._scrub_identity(to_node),
            payload_bytes=clamped.bytes_total,
            payload_stored_bytes=clamped.bytes_stored,
            payload_sha256=clamped.sha256,
            truncated=clamped.truncated,
            truncated_count=clamped.truncated_count,
        )
        self._fan_out(envelope.to_record())
        return envelope

    async def aemit(self, event_type: str, payload: Mapping[str, Any] | None = None, **kwargs: Any) -> TraceEnvelope | None:
        """Async-facing alias for :meth:`emit`.

        The body is deliberately the same synchronous call, and that is the honest
        design rather than a placeholder: there is nothing to await in the writer
        (no I/O beyond the injected sinks, no lock held across a suspension
        point, no task spawned), so an ``async`` wrapper that pretended otherwise
        would be a lie about a cost it does not have. It exists so an async call
        site reads as async, and so that if a future sink *is* async, this is the
        single place that has to change.
        """
        return self.emit(event_type, payload, **kwargs)

    def _fan_out(self, record: Mapping[str, Any]) -> None:
        """Hand one record to the tail and then to every recorder sink.

        The tail goes first, so a query against the in-process ring sees the
        record even when a durable sink raises.
        """
        sinks: list[TraceSink] = [self._tail]
        sinks.extend(sink for sink in self._recorder.sinks if sink is not self._tail)
        for sink in sinks:
            try:
                sink.emit(record)
            except Exception as exc:  # noqa: BLE001 - a sink must never break the run
                name = getattr(sink, "name", type(sink).__name__)
                with self._lock:
                    self._flush_failures[name] = self._flush_failures.get(name, 0) + 1
                self._log_once(
                    f"flush:{name}",
                    "behaviour trace sink %r failed and its records are lost; further failures of this sink will not be logged individually: %s",
                    name,
                    exc,
                )

    def _log_once(self, key: str, message: str, *args: Any) -> None:
        with self._lock:
            if key in self._logged:
                return
            self._logged.add(key)
        logger.warning(message, *args)

    # -- spans ----------------------------------------------------------------

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        """Open a real spine span for a timed operation.

        Delegates to :meth:`TraceRecorder.span`, so nesting, the depth cap, the
        parent linkage, the closed status set and the existing ``span`` record
        format are all the spine's -- reused, not reimplemented. Every behaviour
        event emitted inside the block inherits that span's id as its
        ``span_id``, which is what lets a reader rebuild the tree from a flat
        event stream and what makes requirement (c) -- a subagent's spans nesting
        under its parent's -- true by construction rather than by convention.

        With ``emit_spans`` off, or the writer disabled, this yields the spine's
        **inert** span: no clock read, no id, no record, one boolean test. Call
        sites therefore need no ``if traced`` branch.

        The inert path deliberately goes through a *disabled* recorder rather
        than through a hand-rolled stub: delegating to the enabled recorder would
        have kept creating and recording real spans, which would make
        ``emit_spans=False`` a switch that reads as real and does nothing.
        """
        if self.traced and READERS["emit_spans"].read(self._config):
            with self._recorder.span(name, attributes=attributes) as opened:
                yield opened
            return
        with self._inert_recorder().span(name, attributes=attributes) as inert:
            yield inert

    def _inert_recorder(self) -> TraceRecorder:
        """Return a permanently disabled recorder, used only for inert spans.

        Built once and kept, because it is stateless in practice (an inert span
        mints nothing and records nothing) and because building one per ``with``
        block would allocate a tracer, a redactor and an id generator on a path
        whose entire purpose is to allocate nothing.
        """
        if self._disabled_recorder is None:
            from .config import ObservabilityConfig  # noqa: PLC0415 - avoids a module-level cycle through .config -> .redaction

            self._disabled_recorder = TraceRecorder(ObservabilityConfig(enabled=False), sinks=[], clock=self._clock_wall, redactor=self._redactor)
        return self._disabled_recorder

    @asynccontextmanager
    async def aspan(self, name: str, **attributes: Any) -> AsyncIterator[Span]:
        """Async-facing alias for :meth:`span`.

        The spine's span binding is a ``contextvars.ContextVar``, which asyncio
        copies per task, so a synchronous ``with`` is already correct inside a
        coroutine. This wrapper exists for symmetry with :meth:`aemit` and for
        call sites that read better with ``async with``.
        """
        with self.span(name, **attributes) as opened:
            yield opened

    @staticmethod
    def _candidate_ref(candidate: Any) -> Any:
        """Reduce one candidate to something storable and joinable.

        A candidate is usually a tool or skill *object*; a record must not depend
        on the object staying alive, and ``repr()`` of an arbitrary object is a
        memory address. A string is kept, a mapping is narrowed to its identity
        keys, and anything else is rendered by a stable attribute or by its class
        name. The score stays in the sibling ``scores`` mapping keyed by the same
        reference, so a candidate and its score are joinable by a reader.
        """
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, Mapping):
            return {key: candidate[key] for key in ("name", "id", "tool", "skill", "score", "rank") if key in candidate}
        for attribute in ("name", "id", "tool_name", "skill_name"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str) and value:
                return value
        return type(candidate).__name__

    # -- typed emitters, one per layer ---------------------------------------
    #
    # Each builds the payload keys its definition declares as required, so a call
    # site states *what happened* and never hand-assembles a record. Extra data
    # goes through ``**extra`` into the same payload, where it is scrubbed and
    # clamped identically.
    #
    # ``**extra`` is a documented extension point with two consequences worth
    # knowing: an undeclared keyword lands in the payload **under its own name**
    # (so ``latency_ms=12.5`` becomes ``payload["latency_ms"]``), and a
    # *required* field misspelled is a rejection rather than a mis-shaped record.
    # Both are asserted in ``test_behaviour_trace.py`` so they stay decisions
    # rather than accidents.

    def run_envelope(
        self,
        *,
        context: RunContext | None = None,
        config_version: str,
        model_config_sha256: str,
        git_sha: str | None = None,
        envelope_kind: str = "run.start",
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 0. Run identity plus the build facts needed to read later events."""
        return self.emit(
            "run.envelope",
            {
                "config_version": config_version,
                "model_config_sha256": model_config_sha256,
                "git_sha": git_sha,
                "envelope_kind": envelope_kind,
                **extra,
            },
            context=context,
        )

    def node_transition(
        self,
        *,
        context: RunContext | None = None,
        from_node: str,
        to_node: str,
        reason: str,
        state_delta: Mapping[str, Any] | None = None,
        state_before: Mapping[str, Any] | None = None,
        state_after: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 1. A graph transition with the routing reason, not just the edge.

        ``reason`` is required by the taxonomy. A reader that knows the edge but
        not why the router took it can describe the run; one that knows both can
        decide whether the router was wrong.
        """
        return self.emit(
            "node.transition",
            {
                "from_node": from_node,
                "to_node": to_node,
                "reason": reason,
                "state_delta": dict(state_delta or {}),
                "state_before": dict(state_before or {}),
                "state_after": dict(state_after or {}),
                **extra,
            },
            context=context,
            from_node=from_node,
            to_node=to_node,
            node=to_node,
            correlation_id=correlation_id,
        )

    def model_call(
        self,
        *,
        context: RunContext | None = None,
        provider: str,
        model: str,
        phase: str,
        temperature: float | None = None,
        messages_before: Sequence[Any] | None = None,
        messages_after: Sequence[Any] | None = None,
        reasoning: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cost_usd: float | None = None,
        finish_reason: str | None = None,
        ttfb_ms: float | None = None,
        total_latency_ms: float | None = None,
        retry_count: int = 0,
        circuit_breaker: str | None = None,
        error_code: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 2. A model call.

        ``reasoning`` is **carried, not promised**. The models configured today
        (``space-bunny``, ``union-alpha``) declare ``supports_thinking: false``,
        so in practice this is ``None`` and the payload records that alongside
        ``reasoning_recorded``. The field exists so the day a model does return
        reasoning it is already in the schema, and so a reader can distinguish
        "the model returned no reasoning" from "this build does not record it".
        """
        if phase not in _MODEL_CALL_PHASES:
            raise ValueError(f"model_call phase must be one of {sorted(_MODEL_CALL_PHASES)}, got {phase!r}")
        include_reasoning = READERS["include_reasoning"].read(self._config)
        payload: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "phase": phase,
            "temperature": temperature,
            # Named ``usage_breakdown``, not ``token_breakdown``, and that is not
            # a style choice. The redactor's key rule is segment-aligned over
            # ``token``, so a key whose *segments* are ``token`` + ``breakdown``
            # matches the whole key and the entire mapping is replaced by
            # ``[REDACTED:secret_named_key]`` -- measured, not assumed. The
            # per-bucket names inside are safe (``input_tokens`` and
            # ``output_tokens`` do not match, because ``tokens`` is not the
            # segment ``token``), so the breakdown survives intact under its
            # current name. ``test_every_required_field_survives_redaction``
            # and ``test_token_breakdown_survives_the_secret_named_key_rule``
            # both pin this.
            "usage_breakdown": {
                "input": input_tokens,
                "output": output_tokens,
                "cached": cached_tokens,
                "reasoning": reasoning_tokens,
            },
            "cost_usd": cost_usd,
            "finish_reason": finish_reason,
            "ttfb_ms": ttfb_ms,
            "total_latency_ms": total_latency_ms,
            "retry_count": retry_count,
            "circuit_breaker": circuit_breaker,
            "reasoning": reasoning if include_reasoning else None,
            "reasoning_recorded": include_reasoning,
            "messages_before": list(messages_before or ()),
            "messages_after": list(messages_after or ()),
            **extra,
        }
        if phase == "completed" and finish_reason is None:
            # The definition requires finish_reason. Saying "unknown" explicitly
            # is better than the writer inventing a plausible value.
            payload["finish_reason"] = "unknown"
        if error_code is not None:
            payload["error_code"] = error_code
        return self.emit(
            _MODEL_CALL_PHASES[phase],
            payload,
            context=context,
            severity="error" if phase == "failed" else None,
        )

    def tool_selection(
        self,
        *,
        context: RunContext | None = None,
        chosen: str,
        reason: str,
        candidates: Sequence[Any] | None = None,
        scores: Mapping[str, Any] | None = None,
        call_id: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 3. The candidate set, the choice, and why -- not just the call.

        ``candidates`` is the set that was *considered*, and it includes the
        chosen one. Recording only the winner is what makes a bad tool choice
        undiagnosable: the question is always "what else was on the table", and a
        log that cannot answer it cannot be repaired from.
        """
        listed = list(candidates or ())
        return self.emit(
            "tool.selection",
            {
                "chosen": chosen,
                "reason": reason,
                "candidates": [self._candidate_ref(candidate) for candidate in listed],
                "candidate_count": len(listed) or 1,
                "scores": dict(scores or {}),
                **extra,
            },
            context=context,
            correlation_id=call_id,
        )

    def skill_selection(
        self,
        *,
        context: RunContext | None = None,
        chosen: str,
        reason: str,
        registry_version: str,
        candidates: Sequence[Any] | None = None,
        scores: Mapping[str, Any] | None = None,
        activation_id: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 4. :meth:`tool_selection`'s shape, plus the registry version.

        The version is required, not optional: a skill decision is only
        reproducible against the registry it was made from, and "which skill set
        was live at 14:02" is unanswerable without it.
        """
        listed = list(candidates or ())
        return self.emit(
            "skill.selection",
            {
                "chosen": chosen,
                "reason": reason,
                "registry_version": registry_version,
                "candidates": [self._candidate_ref(candidate) for candidate in listed],
                "candidate_count": len(listed) or 1,
                "scores": dict(scores or {}),
                **extra,
            },
            context=context,
            correlation_id=activation_id,
        )

    def provider_selection(
        self,
        *,
        context: RunContext | None = None,
        provider: str,
        reason: str,
        fallback_chain: Sequence[str] | None = None,
        health: str | None = None,
        circuit_breaker: str | None = None,
        attempts: Sequence[Mapping[str, Any]] | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 5. Provider chosen, the chain traversed to reach it, health, breaker."""
        return self.emit(
            "provider.selection",
            {
                "provider": provider,
                "reason": reason,
                "fallback_chain": list(fallback_chain or ()),
                "health": health,
                "circuit_breaker": circuit_breaker,
                "attempts": [dict(attempt) for attempt in (attempts or ())],
                **extra,
            },
            context=context,
        )

    def subagent_spawn(
        self,
        *,
        context: RunContext | None = None,
        spawn_reason: str,
        parent_agent: str,
        depth: int,
        model: str | None = None,
        prompt: str | None = None,
        subagent_id: str | None = None,
        siblings: Sequence[str] | None = None,
        task_id: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 6. A subagent was created: why, by whom, doing what, at what depth.

        ``siblings`` is the set it competes with for the concurrency limit, which
        is the difference between "a subagent was refused" being diagnosable and
        being a mystery.
        """
        return self.emit(
            "subagent.spawn",
            {
                "spawn_reason": spawn_reason,
                "parent_agent": parent_agent,
                "depth": depth,
                "model": model,
                "prompt": prompt,
                "siblings": list(siblings or ()),
                **extra,
            },
            context=context,
            subagent_id=subagent_id,
            agent_depth=depth,
            correlation_id=task_id,
        )

    def subagent_lifecycle(
        self,
        *,
        context: RunContext | None = None,
        phase: str,
        subagent_id: str | None = None,
        parent_agent: str | None = None,
        status: str = "ok",
        detail: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 6. A subagent lifecycle edge (``started``/``completed``/``failed``)."""
        return self.emit(
            "subagent.lifecycle",
            {"phase": phase, "parent_agent": parent_agent, "detail": detail, **extra},
            context=context,
            subagent_id=subagent_id,
            status=status,
        )

    def subagent_usage(
        self,
        *,
        context: RunContext | None = None,
        input_tokens: int,
        output_tokens: int,
        subagent_id: str | None = None,
        cached_tokens: int | None = None,
        cost_usd: float | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 6. A subagent's cumulative tokens and cost at a lifecycle edge."""
        return self.emit(
            "subagent.usage",
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "cost_usd": cost_usd,
                **extra,
            },
            context=context,
            subagent_id=subagent_id,
        )

    def swarm_plan(
        self,
        *,
        context: RunContext | None = None,
        plan_id: str,
        node_count: int,
        nodes: Sequence[Any] | None = None,
        budget_snapshot: Mapping[str, Any] | None = None,
        replanned_from: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 7. A swarm plan was created or replanned."""
        return self.emit(
            "swarm.plan",
            {
                "plan_id": plan_id,
                "node_count": node_count,
                "nodes": [self._candidate_ref(node) for node in (nodes or ())],
                "budget_snapshot": dict(budget_snapshot or {}),
                "replanned_from": replanned_from,
                **extra,
            },
            context=context,
            correlation_id=plan_id,
        )

    def swarm_node_attempt(
        self,
        *,
        context: RunContext | None = None,
        plan_id: str,
        node_id: str,
        attempt: int,
        worker: str | None = None,
        incident: str | None = None,
        budget_snapshot: Mapping[str, Any] | None = None,
        outcome: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 7. One swarm node attempt, with the budget snapshot at the time."""
        return self.emit(
            "swarm.node",
            {
                "plan_id": plan_id,
                "node_id": node_id,
                "attempt": attempt,
                "worker": worker,
                "incident": incident,
                "outcome": outcome,
                "budget_snapshot": dict(budget_snapshot or {}),
                **extra,
            },
            context=context,
            node=node_id,
            correlation_id=plan_id,
        )

    def search_query(
        self,
        *,
        context: RunContext | None = None,
        query: str,
        provider: str,
        result_count: int | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 8. A web search was issued."""
        return self.emit(
            "search.query",
            {"query": query, "provider": provider, "result_count": result_count, **extra},
            context=context,
        )

    def search_results(
        self,
        *,
        context: RunContext | None = None,
        query: str,
        results: Sequence[Mapping[str, Any]],
        used_rank: int | None,
        used_url: str | None = None,
        fetch_outcome: str | None = None,
        extract_outcome: str | None = None,
        content_sha256: str | None = None,
        used_rank_known: bool = True,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 8. Every result, which one was used, and what the fetch did.

        ``used_rank`` of ``None`` with ``used_rank_known=False`` is a real and
        valuable state -- "the answer cited a source that is not in the result
        set" is the single most useful thing this event can tell a sentinel -- so
        the field is required and may legitimately be ``None`` rather than
        silently defaulting to 0.
        """
        return self.emit(
            "search.results",
            {
                "query": query,
                "results": [dict(result) for result in results],
                "result_count": len(results),
                "used_rank": used_rank,
                "used_rank_known": used_rank_known,
                "used_url": used_url,
                "fetch_outcome": fetch_outcome,
                "extract_outcome": extract_outcome,
                "content_sha256": content_sha256,
                **extra,
            },
            context=context,
        )

    def memory_recall(
        self,
        *,
        context: RunContext | None = None,
        query: str,
        hits: Sequence[Mapping[str, Any]] | None = None,
        hit_count: int | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 9. A memory recall: the query and every hit with its score."""
        listed = [dict(hit) for hit in (hits or ())]
        return self.emit(
            "memory.recall",
            {
                "query": query,
                "hits": listed,
                "hit_count": hit_count if hit_count is not None else len(listed),
                **extra,
            },
            context=context,
        )

    def memory_write(
        self,
        *,
        context: RunContext | None = None,
        memory_kind: str,
        key: str | None = None,
        scope: str | None = None,
        byte_size: int | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 9. A memory write. Size and key, not the stored content."""
        return self.emit(
            "memory.write",
            {"memory_kind": memory_kind, "key": key, "scope": scope, "byte_size": byte_size, **extra},
            context=context,
        )

    def memory_evict(
        self,
        *,
        context: RunContext | None = None,
        memory_kind: str,
        reason: str,
        key: str | None = None,
        byte_size: int | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 9. A memory entry was evicted, and why."""
        return self.emit(
            "memory.evict",
            {"memory_kind": memory_kind, "reason": reason, "key": key, "byte_size": byte_size, **extra},
            context=context,
        )

    def knowledge_query(
        self,
        *,
        context: RunContext | None = None,
        query: str,
        document_count: int,
        allowlist_decision: str,
        documents: Sequence[Mapping[str, Any]] | None = None,
        context_digest: str | None = None,
        scores: Sequence[float] | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 10. A retrieval: documents, scores, the assembled digest, allowlist."""
        return self.emit(
            "knowledge.query",
            {
                "query": query,
                "document_count": document_count,
                "allowlist_decision": allowlist_decision,
                "documents": [dict(document) for document in (documents or ())],
                "scores": list(scores or ()),
                "context_digest": context_digest,
                **extra,
            },
            context=context,
        )

    def filesystem_operation(
        self,
        *,
        context: RunContext | None = None,
        operation: str,
        path: str,
        byte_count: int | None = None,
        hash_before: str | None = None,
        hash_after: str | None = None,
        outcome: str = "ok",
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 11. A filesystem operation: path, operation, bytes, hashes.

        **Hash only, never content.** This is the layer with the largest blast
        radius for a leak -- a ``read_file`` result is arbitrary user data -- so
        the emitter has no parameter through which content could be passed. A
        caller that wants content in the trace has to put it in ``**extra``,
        where the redactor sees it: one deliberate step rather than an accident.
        """
        return self.emit(
            "fs.operation",
            {
                "operation": operation,
                "path": path,
                "byte_count": byte_count,
                "hash_before": hash_before,
                "hash_after": hash_after,
                "outcome": outcome,
                **extra,
            },
            context=context,
            node=operation,
        )

    def budget_check(
        self,
        *,
        context: RunContext | None = None,
        decision: str,
        budget_kind: str | None = None,
        running_totals: Mapping[str, Any] | None = None,
        limit: Any = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 12. A budget governor decision and the totals behind it."""
        return self.emit(
            "budget.check",
            {
                "decision": decision,
                "budget_kind": budget_kind,
                "running_totals": dict(running_totals or {}),
                "limit": limit,
                **extra,
            },
            context=context,
        )

    def budget_throttle(
        self,
        *,
        context: RunContext | None = None,
        budget_kind: str,
        action: str,
        magnitude: Any = None,
        running_totals: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 12. The governor throttled the run."""
        return self.emit(
            "budget.throttle",
            {
                "budget_kind": budget_kind,
                "action": action,
                "magnitude": magnitude,
                "running_totals": dict(running_totals or {}),
                **extra,
            },
            context=context,
        )

    def error_observed(
        self,
        *,
        context: RunContext | None = None,
        error_code: str,
        message: str | None = None,
        stack_sha256: str | None = None,
        retried: bool = False,
        swallowed: bool = False,
        escalated: bool = False,
        source_layer: str | None = None,
        node: str | None = None,
        tool: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 13. A failure, in the one taxonomy.

        ``error_code`` is **required and never invented here**: it is resolved
        against :data:`alpha.observability.taxonomy.ERROR_CODE_TAXONOMY` and the
        envelope's ``severity`` is *derived* from that definition, so the trace
        cannot disagree with the log about how serious a code is. When the
        registry cannot be imported the supplied code is carried verbatim and
        marked ``error_code_source="unverified_registry_unavailable"``; see
        :func:`alpha.observability.taxonomy.classify_error_code`.

        ``stack_sha256`` is a hash of the traceback rather than the traceback: a
        traceback embeds source lines, and a source line can embed a literal. The
        hash is what makes "the same failure, seen twice" a decidable question.
        """
        resolution = classify_error_code(error_code)
        payload: dict[str, Any] = {
            "error_code": resolution.code,
            "message": message,
            "stack_sha256": stack_sha256,
            "retried": retried,
            "swallowed": swallowed,
            "escalated": escalated,
            "source_layer": source_layer,
            "tool": tool,
            "provider": provider,
            "model": model,
            **resolution.as_attributes(),
            **extra,
        }
        return self.emit(
            "error.observed",
            payload,
            context=context,
            node=node,
            severity=resolution.severity,
        )

    def handoff(
        self,
        *,
        context: RunContext | None = None,
        from_agent: str,
        to_agent: str,
        reason: str,
        attempt: int,
        outcome: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 14. Control moved from one actor to another."""
        return self.emit(
            "handoff.delegated",
            {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "reason": reason,
                "attempt": attempt,
                "outcome": outcome,
                **extra,
            },
            context=context,
        )

    def human_interrupt(
        self,
        *,
        context: RunContext | None = None,
        phase: str,
        interrupt_id: str | None = None,
        reason: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 15. The run was interrupted for a human, or resumed."""
        return self.emit(
            "human.interrupt",
            {"phase": phase, "interrupt_id": interrupt_id, "reason": reason, **extra},
            context=context,
            correlation_id=interrupt_id,
        )

    def human_approval(
        self,
        *,
        context: RunContext | None = None,
        decision: str,
        approver: str | None = None,
        subject: str | None = None,
        rationale: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 15. A human approved or denied an action."""
        return self.emit(
            "human.approval",
            {
                "decision": decision,
                "approver": approver,
                "subject": subject,
                "rationale": rationale,
                **extra,
            },
            context=context,
        )

    def human_action(
        self,
        *,
        context: RunContext | None = None,
        action: str,
        actor: str | None = None,
        target: str | None = None,
        detail: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 15. A UI action or a manual edit performed by a human."""
        return self.emit(
            "human.action",
            {
                "action": action,
                "actor": actor,
                "target": target,
                "detail": dict(detail or {}),
                **extra,
            },
            context=context,
        )

    def guardrail_denied(
        self,
        *,
        context: RunContext | None = None,
        guardrail: str,
        reason: str,
        action: str | None = None,
        kind: str | None = None,
        severity: str = "warning",
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 16. A guardrail stopped the run.

        ``kind`` separates a sandbox denial from a safety stop from a budget stop,
        because the remedy differs: a sandbox denial is a permissions problem, a
        safety stop is a content problem, a budget stop is a money problem.
        Reporting all three as "denied" is what makes a denial chart unreadable.
        """
        return self.emit(
            "guardrail.denied",
            {
                "guardrail": guardrail,
                "reason": reason,
                "action": action,
                "kind": kind,
                **extra,
            },
            context=context,
            status="refused",
            severity=severity,
        )

    def evolution_observed(
        self,
        *,
        context: RunContext | None = None,
        observation: str,
        evidence_seq: Sequence[int] | None = None,
        rule: str | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 17. The sentinel noticed something, with the events that show it.

        ``evidence_seq`` points at sequence numbers in *this* trace, so an
        observation is a claim about specific evidence rather than a free-text
        feeling. That is what makes the layer-17 loop closable: a diagnosis can be
        checked against the events it cites.
        """
        return self.emit(
            "evolution.observed",
            {
                "observation": observation,
                "evidence_seq": list(evidence_seq or ()),
                "rule": rule,
                **extra,
            },
            context=context,
        )

    def evolution_diagnosed(
        self,
        *,
        context: RunContext | None = None,
        diagnosis: str,
        confidence: float | None = None,
        observations: Sequence[str] | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 17. The sentinel formed a cause from observations."""
        return self.emit(
            "evolution.diagnosed",
            {
                "diagnosis": diagnosis,
                "confidence": confidence,
                "observations": list(observations or ()),
                **extra,
            },
            context=context,
        )

    def evolution_fix(
        self,
        *,
        context: RunContext | None = None,
        fix: str,
        target: str | None = None,
        applied: bool = True,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 17. A repair was attempted."""
        return self.emit(
            "evolution.fix",
            {"fix": fix, "target": target, "applied": applied, **extra},
            context=context,
        )

    def evolution_verified(
        self,
        *,
        context: RunContext | None = None,
        outcome: str,
        evidence: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> TraceEnvelope | None:
        """Layer 17. The fix was verified. This is the event that closes the loop."""
        return self.emit(
            "evolution.verified",
            {"outcome": outcome, "evidence": dict(evidence or {})},
            context=context,
            severity="info" if outcome == "passed" else "warning",
        )

    # -- read-back ------------------------------------------------------------

    def records(self, *, run_id: str | None = None, after_seq: int | None = None, limit: int | None = None) -> tuple[dict[str, Any], ...]:
        """Return behaviour records from the tail, oldest first.

        The in-process read path the query layer uses. ``after_seq`` has the same
        forward-cursor semantics as the durable run-event store's, so one caller
        pages either source with one code path.
        """
        selected = [record for record in self._tail.records() if record.get("type") == RECORD_TYPE]
        if run_id is not None:
            selected = [record for record in selected if record.get("run_id") == run_id]
        if after_seq is not None:
            selected = [
                record
                for record in selected
                if isinstance(record.get("seq"), int) and not isinstance(record.get("seq"), bool) and record["seq"] > after_seq
            ]
        if limit is not None:
            selected = selected[:limit]
        return tuple(selected)

    # -- disclosure -----------------------------------------------------------

    def disclosure(self) -> dict[str, Any]:
        """Every number an operator needs to trust this trace.

        The "nothing is silently discarded" surface, extending the recorder's own
        with the writer's: per-reason rejection counts, the flush-failure count
        per sink, the events dropped for want of a run context, the tail's own
        overflow count, and the resolved config.
        """
        with self._lock:
            return {
                "traced": self.traced,
                "recorded_events": self._recorded,
                "rejected": dict(self._rejected),
                "flush_failures": dict(self._flush_failures),
                "events_without_run_context": self._no_context,
                "tracked_run_counters": len(self._per_run_seq),
                "tail": dict(self._tail.disclosure()),
                "config": {key: reader.read(self._config) for key, reader in READERS.items()},
            }

    def close(self) -> None:
        """Stop recording. Idempotent, never raises.

        Closing does **not** close the recorder's sinks: the writer shares them,
        and a writer going out of scope must not pull a file handle out from
        under a span the recorder is still ending.
        """
        self._closed = True

    def __enter__(self) -> BehaviourTraceWriter:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


#: US spelling alias. One implementation, two names, so a call site does not
#: have to care which convention the surrounding file uses.
BehaviorWriter = BehaviourTraceWriter


def build_behaviour_writer(
    recorder: TraceRecorder,
    *,
    config: BehaviourTraceConfig | None = None,
    redactor: Redactor | None = None,
    clock_wall: Any = time.time,
    clock_monotonic: Any = time.monotonic,
) -> BehaviourTraceWriter:
    """Build a writer for *recorder*. The single supported construction path.

    No cached singleton, deliberately, for the same reason
    :func:`alpha.observability.recorder.build_recorder` has none: a
    process-wide writer is global mutable state that two tests cannot isolate and
    a config reload cannot reason about.
    """
    return BehaviourTraceWriter(recorder, config=config, redactor=redactor, clock_wall=clock_wall, clock_monotonic=clock_monotonic)


def disabled_behaviour_writer(*, clock_wall: Any = time.time, clock_monotonic: Any = time.monotonic) -> BehaviourTraceWriter:
    """Return a writer that is inert by construction.

    For code that must always hold a writer and must never write, so a caller
    never has to write ``if writer is None``.
    """
    from .recorder import disabled_recorder  # noqa: PLC0415 - keeps a second module-level entry into recorder out of the import graph

    return BehaviourTraceWriter(disabled_recorder(clock=clock_wall), clock_wall=clock_wall, clock_monotonic=clock_monotonic)
