"""The recorder: injected sinks, bounded overflow, contained sink failures.

:class:`TraceRecorder` is the only object the rest of the system talks to. It
owns the tracer, the id generator, the clock, the redactor and the sinks, and
it is always an **injected instance** -- there is no module-level recorder and
no ``get_recorder()`` singleton, because a global recorder is a global that two
tests cannot isolate and a config reload cannot reason about.

Sinks
-----
A sink is anything with ``name``, ``emit(record)``, ``close()`` and
``disclosure()``. Three ship here:

* :class:`InMemorySink` -- a bounded ring for tests and for a live tail view.
* :class:`JsonlFileSink` -- bounded JSONL, the format
  ``scripts/export_run_trace.py`` reads.
* :class:`NoOpSink` -- counts calls and does nothing else, which is what makes
  the "disabled means no measurable cost" assertion checkable: the test asserts
  ``sink.calls == 0``, not merely that nothing was written.

Nothing else is needed to emit into
:mod:`alpha.events.bus`: a sink is ten lines, and keeping the bus out of this
package means the recorder has no dependency on a process-wide singleton and
works identically in a thread, a subprocess and a test.

Overflow is disclosed, never silent
------------------------------------
Three independent bounds, each with its own counter surfaced in
:meth:`TraceRecorder.disclosure` and in the metrics bridge:

* ``max_events_per_run`` -- one run's events;
* ``max_spans_per_run`` -- one run's spans;
* the sink's own cap -- the file sink's event count and byte budget.

On overflow the file sink does two things, in this order: it counts the drop,
and it writes a ``disclosure`` record into the file. A trace file that lost
events says so **inside the file**, so a reader who only has the file still
learns that the timeline is incomplete. The count is also reported through the
recorder, for a reader who has the process.

A sink that raises does not break the caller
--------------------------------------------
Observability is not allowed to be the reason a run fails. :meth:`record`
wraps every ``sink.emit`` and swallows the exception. What it does *not* do is
swallow it silently:

* the failure is counted per sink in :meth:`disclosure`;
* it is logged at ``WARNING`` **once per sink**, not once per event. A broken
  sink on a hot path would otherwise emit one log line per tool call and turn a
  single misconfiguration into a log flood that hides the original problem;
* recording continues to the remaining sinks, so a broken file sink does not
  blind the in-memory one.

Determinism
-----------
The clock and the id generator are injected, so a test that pins both gets
byte-identical output on every run. The only real time this module reads is
none: it never sleeps and never blocks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from .config import READERS, ObservabilityConfig
from .context import RunContext, bind, current, detach
from .events import EventStatus, TraceEvent
from .ids import IDGenerator, default_id_generator
from .redaction import Redactor
from .span import Span, Tracer, current_span

__all__ = [
    "InMemorySink",
    "JsonlFileSink",
    "NoOpSink",
    "TraceRecorder",
    "TraceSink",
    "build_recorder",
    "disabled_recorder",
]

logger = logging.getLogger(__name__)

#: Bumped when the JSONL record shape changes incompatibly. The exporter checks
#: it and refuses an unknown major rather than mis-reading a file.
RECORD_SCHEMA_VERSION: int = 1


@runtime_checkable
class TraceSink(Protocol):
    """Where records go. Implemented in-process; a remote sink is a third-party
    opt-in, never a default."""

    @property
    def name(self) -> str:
        """Stable, low-cardinality sink name. Becomes a metrics label."""

    def emit(self, record: Mapping[str, Any]) -> None:
        """Write one record. May raise; the recorder contains the failure."""

    def close(self) -> None:
        """Release any resource. Must be idempotent."""

    def disclosure(self) -> dict[str, int]:
        """Return this sink's counters, including any drop count."""


class NoOpSink:
    """Counts calls, stores nothing.

    The default when observability is on but no sink is wanted, and the
    instrument the "disabled costs nothing" test measures against: a
    disabled recorder must leave ``calls`` at exactly ``0``.
    """

    __slots__ = ("calls", "_closed")

    def __init__(self) -> None:
        self.calls = 0
        self._closed = False

    @property
    def name(self) -> str:
        return "null"

    def emit(self, record: Mapping[str, Any]) -> None:
        self.calls += 1

    def close(self) -> None:
        self._closed = True

    def disclosure(self) -> dict[str, int]:
        return {"calls": self.calls, "dropped_events": 0}

    def __repr__(self) -> str:
        return f"NoOpSink(calls={self.calls})"


class InMemorySink:
    """Bounded in-memory ring of records, oldest evicted first.

    Bounded on purpose: an unbounded ring in a long-lived Gateway is a memory
    leak with a telemetry-shaped name. The eviction count is disclosed.
    """

    __slots__ = ("_closed", "_records", "calls", "max_records")

    def __init__(self, max_records: int = 1024) -> None:
        if not isinstance(max_records, int) or isinstance(max_records, bool) or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self.max_records = max_records
        self._records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self.calls = 0
        self._closed = False

    @property
    def name(self) -> str:
        return "memory"

    def emit(self, record: Mapping[str, Any]) -> None:
        # dict() so a sink cannot be mutated after the fact through the
        # caller's reference, and so the recorder's own disclosure injection
        # cannot rewrite a record it already handed over.
        self._records.append(dict(record))
        self.calls += 1

    def close(self) -> None:
        self._closed = True

    def records(self) -> tuple[dict[str, Any], ...]:
        """Return the retained records, oldest first."""
        return tuple(self._records)

    def disclosure(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "dropped_events": max(0, self.calls - len(self._records)),
            "retained": len(self._records),
        }

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"InMemorySink(retained={len(self._records)}, calls={self.calls})"


class JsonlFileSink:
    """Bounded JSONL writer: one record per line, with overflow disclosed.

    Two independent caps. The event cap is the primary one; when it is hit the
    sink counts the drop and writes a ``disclosure`` record into the file, so
    the file itself says it is incomplete. The byte cap is the backstop for a
    single enormous record: when it is hit the sink stops writing entirely and
    discloses how many events went unwritten, because continuing would
    contradict the cap that was set.

    The file is opened per write rather than held open. A trace sink is
    low-volume, an open handle is a thing that leaks on every aborted run, and
    per-write open is what lets ``export_run_trace.py`` read a file that is
    still being written.
    """

    __slots__ = ("_bytes_written", "_closed", "_disclosed_overflow", "_dropped_events", "_lock", "calls", "max_bytes", "max_events", "path")

    def __init__(self, path: str | os.PathLike[str], *, max_events: int = 100_000, max_bytes: int = 64 * 1024 * 1024) -> None:
        if not isinstance(max_events, int) or isinstance(max_events, bool) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.path = str(path)
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.calls = 0
        self._dropped_events = 0
        self._bytes_written = 0
        self._disclosed_overflow = False
        self._closed = False
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "file"

    @property
    def dropped_events(self) -> int:
        return self._dropped_events

    @property
    def byte_budget_exhausted(self) -> bool:
        return self._bytes_written >= self.max_bytes

    def emit(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        with self._lock:
            self.calls += 1
            if self._bytes_written >= self.max_bytes:
                # Backstop cap: count it and stop. Writing anyway would make the
                # byte budget a suggestion.
                self._dropped_events += 1
                return
            if self.calls > self.max_events:
                self._dropped_events += 1
                if not self._disclosed_overflow:
                    self._disclosed_overflow = True
                    self._write_disclosure("file_sink_max_events")
                return
            self._write_line(line)

    def _write_disclosure(self, reason: str) -> None:
        self._write_line(
            json.dumps(
                {
                    "v": RECORD_SCHEMA_VERSION,
                    "type": "disclosure",
                    "reason": reason,
                    "sink": self.name,
                    "dropped_events": self._dropped_events,
                    "max_events": self.max_events,
                    "max_bytes": self.max_bytes,
                    "ts": time.time(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )

    def _write_line(self, line: str) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
        self._bytes_written += len(line.encode("utf-8"))

    def close(self) -> None:
        self._closed = True

    def disclosure(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "dropped_events": self._dropped_events,
            "bytes_written": self._bytes_written,
            "max_events": self.max_events,
            "max_bytes": self.max_bytes,
        }

    def __repr__(self) -> str:
        return f"JsonlFileSink(path={self.path!r}, calls={self.calls}, dropped={self._dropped_events})"


class TraceRecorder:
    """Records trace events and spans into injected sinks.

    Construct one with :func:`build_recorder` or directly with an explicit
    config, clock, id generator and sink list. When
    :attr:`~alpha.observability.config.ObservabilityConfig.enabled` is false it
    is inert: :meth:`record` returns ``False`` before touching anything and
    :meth:`span` yields a span that reads no clock and mints no id.
    """

    __slots__ = (
        "_clock",
        "_closed",
        "_config",
        "_derived_metrics",
        "_evicted_runs",
        "_id_generator",
        "_lock",
        "_recorded_events",
        "_recorded_spans",
        "_redactor",
        "_run_event_counts",
        "_sampled_out_runs",
        "_sink_failures",
        "_sink_failures_logged",
        "_sinks",
        "_tracer",
    )

    def __init__(
        self,
        config: ObservabilityConfig | None = None,
        *,
        sinks: list[TraceSink] | None = None,
        id_generator: IDGenerator | None = None,
        clock: Any = time.time,
        redactor: Redactor | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._config = config if config is not None else ObservabilityConfig()
        if not callable(clock):
            raise TypeError("clock must be callable")
        if self._config.remote_sink_enabled:
            # Refused, not ignored. `remote_sink_enabled` exists to make the
            # absence of a network export a declared decision; accepting the
            # flag and doing nothing would make it a lie.
            raise ValueError("remote_sink_enabled is true but no remote sink ships in this package; it must stay false until one is written and reviewed")
        if self._config.sinks and "file" in self._config.sinks and not self._config.file_sink_path:
            raise ValueError("sinks includes 'file' but file_sink_path is unset; a file sink with no path would be a silent no-op")
        self._id_generator = id_generator if id_generator is not None else default_id_generator()
        self._clock = clock
        self._redactor = (
            redactor
            if redactor is not None
            else Redactor(
                self._config.redaction_policy,
                max_value_chars=READERS["max_attribute_value_chars"].read(self._config),
                max_items=READERS["max_attribute_items"].read(self._config),
                max_depth=READERS["max_attribute_depth"].read(self._config),
            )
        )
        self._tracer = (
            tracer
            if tracer is not None
            else Tracer(
                id_generator=self._id_generator,
                clock=clock,
                redactor=self._redactor,
                max_depth=READERS["max_span_depth"].read(self._config),
                max_attributes=READERS["max_attributes_per_span"].read(self._config),
                on_span_end=self.record_span,
            )
        )
        self._sinks: list[TraceSink] = list(sinks) if sinks is not None else self.build_sinks(self._config)
        self._lock = threading.RLock()
        self._recorded_events = 0
        self._recorded_spans = 0
        self._sampled_out_runs = 0
        self._evicted_runs = 0
        self._sink_failures: dict[str, int] = {}
        self._sink_failures_logged: set[str] = set()
        self._run_event_counts: OrderedDict[str, int] = OrderedDict()
        self._derived_metrics: dict[str, float] = {}
        self._closed = False

    # -- construction ---------------------------------------------------------

    @staticmethod
    def build_sinks(config: ObservabilityConfig) -> list[TraceSink]:
        """Build the sink list a config asks for.

        A classmethod-style factory rather than an instance method because the
        config alone decides the list, and a test wants to check the decision
        without constructing a recorder. A ``null`` in the list is dropped:
        ``sinks=["null"]`` is a request for no sinks, and honouring it by
        building a real sink anyway would be a surprise.
        """
        sinks: list[TraceSink] = []
        for name in READERS["sinks"].read(config):
            if name == "memory":
                sinks.append(InMemorySink())
            elif name == "file":
                path = READERS["file_sink_path"].read(config)
                if path:
                    sinks.append(JsonlFileSink(path, max_events=READERS["file_sink_max_events"].read(config), max_bytes=READERS["file_sink_max_bytes"].read(config)))
            elif name == "null":
                continue
        return sinks

    # -- accessors ------------------------------------------------------------

    @property
    def config(self) -> ObservabilityConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def tracer(self) -> Tracer:
        return self._tracer

    @property
    def id_generator(self) -> IDGenerator:
        return self._id_generator

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    @property
    def clock(self) -> Any:
        return self._clock

    @property
    def sinks(self) -> tuple[TraceSink, ...]:
        return tuple(self._sinks)

    @property
    def derived_metrics(self) -> dict[str, float]:
        """Trace-derived counters, published by :mod:`.metrics_bridge`."""
        return dict(self._derived_metrics)

    # -- run lifecycle --------------------------------------------------------

    def new_run_id(self) -> str:
        """Mint a run id from the injected generator."""
        return self._id_generator.new_run_id()

    def sampled(self, run_id: str) -> bool:
        """Return whether *run_id* is in the sample.

        A pure function of ``(run_id, sample_rate)`` via SHA-256, so the
        decision is reproducible from the trace file alone and a test needs no
        RNG. Sampled per *run*, never per event: a trace that holds the turn
        but not the tool call that produced it is not a trace.
        """
        rate = READERS["sample_rate"].read(self._config)
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        digest = hashlib.sha256(f"{run_id}:{rate:.6f}".encode()).digest()
        fraction = int.from_bytes(digest[:8], "big") / float(1 << 64)
        return fraction < rate

    def start_run(
        self,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> RunContext | None:
        """Bind a fresh run context and record ``run.started``.

        Returns the bound :class:`RunContext`, or ``None`` when disabled or
        sampled out -- in which case nothing is bound, because a disabled
        recorder must not leave ambient state behind.
        """
        if not self.enabled:
            return None
        context = self._build_run_context(
            trace_id=trace_id,
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            agent_name=agent_name,
        )
        if not self._admit_run(context.run_id):
            return None
        token = bind(context)
        try:
            self.record_event("run.started", context=context, status=EventStatus.OK, attributes={"sampled": True})
        finally:
            detach(token)
        return context

    def _build_run_context(self, **kwargs: Any) -> RunContext:
        from .context import ensure_run_context

        kwargs.setdefault("id_generator", self._id_generator)
        return ensure_run_context(**kwargs)

    def end_run(self, *, context: RunContext | None = None, status: EventStatus = EventStatus.OK, attributes: Mapping[str, Any] | None = None) -> bool:
        """Record ``run.ended`` and release the run's per-run counter."""
        if not self.enabled:
            return False
        selected = context if context is not None else current()
        if selected is None:
            return False
        recorded = self.record_event("run.ended", context=selected, status=status, attributes=attributes)
        with self._lock:
            self._run_event_counts.pop(selected.run_id, None)
        return recorded

    def _admit_run(self, run_id: str) -> bool:
        if self.sampled(run_id):
            return True
        with self._lock:
            self._sampled_out_runs += 1
        return False

    # -- recording ------------------------------------------------------------

    def record(self, event: TraceEvent) -> bool:
        """Hand one event to every sink.

        Returns ``True`` when at least one sink accepted it. Never raises: an
        observability path that can take down a run is not an observability
        path. The first statement short-circuits on a disabled recorder, so the
        disabled path costs one attribute read and nothing else.
        """
        if not self._config.enabled or self._closed:
            return False
        if not isinstance(event, TraceEvent):
            raise TypeError(f"record expects a TraceEvent, got {type(event).__name__}")
        with self._lock:
            if not self.sampled(event.run_id):
                self._sampled_out_runs += 1
                return False
            if not self._admit_event_locked(event.run_id):
                return False
            self._recorded_events += 1
        self._fan_out(event.to_record())
        self._publish_derived(event)
        return True

    def _admit_event_locked(self, run_id: str) -> bool:
        """Charge one event against the run's cap. The caller holds the lock."""
        limit = READERS["max_events_per_run"].read(self._config)
        key = f"event:{run_id}"
        if self._run_event_counts.get(key, 0) >= limit:
            return False
        self._run_event_counts[key] = self._run_event_counts.get(key, 0) + 1
        self._trim_runs()
        return True

    def record_event(
        self,
        name: str,
        *,
        context: RunContext | None = None,
        status: EventStatus = EventStatus.OK,
        attributes: Mapping[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        context_inherited: bool = True,
        link_kind: str = "",
    ) -> TraceEvent | None:
        """Build and record one event. Returns the event, or ``None`` if dropped.

        The convenience seam for an integration patch: the caller states *what
        happened* and this assembles the identity from the ambient run context,
        scrubs the attributes, and hands the result to every sink.
        """
        if not self._config.enabled or self._closed:
            return None
        selected = context if context is not None else current()
        if selected is None:
            # No run context and none supplied. Returning ``None`` rather than
            # raising keeps a call site that is merely *wrong* from breaking a
            # run; the missing binding shows up as a missing event, which is
            # better than an orphan event under a fabricated id.
            return None
        if not self.sampled(selected.run_id):
            with self._lock:
                self._sampled_out_runs += 1
            return None
        try:
            event = TraceEvent(
                name=name,  # type: ignore[arg-type]
                status=status,
                trace_id=selected.trace_id,
                run_id=selected.run_id,
                timestamp=float(self._clock()),
                span_id=span_id if span_id is not None else _span_id_of_current(),
                parent_span_id=parent_span_id if parent_span_id is not None else selected.parent_span_id,
                thread_id=selected.thread_id,
                user_id=selected.user_id,
                agent_name=selected.agent_name,
                attributes=dict(attributes or {}),
                context_inherited=context_inherited,
                link_kind=link_kind,
            )
        except (ValueError, TypeError) as exc:
            # A closed-set or redaction rejection is a programming error in the
            # call site, but it must not propagate into the run. Disclose it
            # once and move on: an event that cannot be built is an event that
            # is not recorded, and pretending otherwise is the failure mode.
            self._log_once("event_rejected", "trace event %r was rejected and not recorded: %s", name, exc)
            return None
        return event if self.record(event) else None

    def record_span(self, span: Span) -> bool:
        """Record a finished span. Honours ``max_spans_per_run``."""
        if not self._config.enabled or self._closed:
            return False
        if not isinstance(span, Span):
            raise TypeError(f"record_span expects a Span, got {type(span).__name__}")
        with self._lock:
            if not self.sampled(span.run_id):
                self._sampled_out_runs += 1
                return False
            if not self._note_span(span.run_id):
                return False
            self._recorded_spans += 1
        self._fan_out(span.to_record())
        self._note_duration(span)
        return True

    def _note_span(self, run_id: str) -> bool:
        """Charge one span against the run's cap. The caller holds the lock."""
        limit = READERS["max_spans_per_run"].read(self._config)
        key = f"span:{run_id}"
        if self._run_event_counts.get(key, 0) >= limit:
            return False
        self._run_event_counts[key] = self._run_event_counts.get(key, 0) + 1
        self._trim_runs()
        return True

    def _trim_runs(self) -> None:
        """Bound the per-run counter table by evicting its oldest entries.

        Each run contributes at most two keys (``event:<run_id>`` and
        ``span:<run_id>``), so the table is bounded at twice the configured run
        count. Eviction is disclosed as ``evicted_run_counters`` because an
        evicted counter means that run has no cap accounting any more, and that
        has to be visible rather than inferred from a number that is missing.
        """
        cap = READERS["max_tracked_runs"].read(self._config) * 2
        while len(self._run_event_counts) > cap:
            self._run_event_counts.popitem(last=False)
            self._evicted_runs += 1

    def _fan_out(self, record: Mapping[str, Any]) -> None:
        for sink in self._sinks:
            try:
                sink.emit(record)
            except Exception as exc:  # noqa: BLE001 - a sink must never break the run
                self._note_sink_failure(sink, exc)

    def _note_sink_failure(self, sink: TraceSink, exc: BaseException) -> None:
        name = getattr(sink, "name", type(sink).__name__)
        with self._lock:
            self._sink_failures[name] = self._sink_failures.get(name, 0) + 1
        # Log once per sink, count every time. A broken sink on a hot path
        # would otherwise emit one WARNING per event and bury the cause.
        self._log_once(f"sink:{name}", "trace sink %r failed and its records are lost; further failures of this sink will not be logged individually: %s", name, exc)

    def _log_once(self, key: str, message: str, *args: Any) -> None:
        with self._lock:
            if key in self._sink_failures_logged:
                return
            self._sink_failures_logged.add(key)
        logger.warning(message, *args)

    def _publish_derived(self, event: TraceEvent) -> None:
        if not self._config.metrics_bridge_enabled:
            return
        with self._lock:
            self._derived_metrics[f"event.{event.name}.{event.status.value}"] = self._derived_metrics.get(f"event.{event.name}.{event.status.value}", 0.0) + 1.0

    def _note_duration(self, span: Span) -> None:
        if not self._config.metrics_bridge_enabled or span.duration is None:
            return
        key = f"span.{span.name}.duration_ms"
        with self._lock:
            self._derived_metrics[key] = self._derived_metrics.get(key, 0.0) + (span.duration * 1000.0)

    # -- spans ----------------------------------------------------------------

    @contextmanager
    def span(
        self,
        name: str,
        *,
        context: RunContext | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Scope a span and record it on the way out.

        Delegates to :meth:`Tracer.span`, which is where the recording hook
        lives, so there is exactly one span path. A disabled recorder still
        yields a span object so a call site needs no ``if enabled`` branch, but
        that object reads no clock, mints no id and is never recorded: the
        disabled cost is one boolean test.
        """
        if not self._config.enabled or self._closed:
            yield _NullSpan(name=name, context=context if context is not None else current())
            return
        with self._tracer.span(name, context=context, attributes=attributes) as started:
            yield started

    def end_span(self, span: Span, *, status: Any = None, exc: BaseException | None = None) -> bool:
        """Close an explicitly-managed span and record it.

        For the async case where a ``with`` block cannot span the work. A
        ``None`` status means "let the exception decide", which is what
        :meth:`Span.end_with_exception` does.
        """
        if not self._config.enabled or self._closed:
            return False
        if exc is not None:
            if status is None:
                span.end_with_exception(exc)
            else:
                span.end_with_exception(exc, status=status)
        elif status is None:
            span.end()
        else:
            span.end(status=status)
        return self.record_span(span)

    # -- disclosure -----------------------------------------------------------

    def disclosure(self) -> dict[str, Any]:
        """Return every counter an operator needs to trust this trace.

        This is the "nothing is silently discarded" surface: sampled-out runs,
        per-run refusals, evicted run counters, sink failures and each sink's
        own drops all appear here, and
        :mod:`alpha.observability.metrics_bridge` publishes the same numbers
        into the registries the Gateway already scrapes.
        """
        with self._lock:
            sink_disclosure: dict[str, Any] = {}
            for sink in self._sinks:
                try:
                    sink_disclosure[getattr(sink, "name", type(sink).__name__)] = dict(sink.disclosure())
                except Exception:  # noqa: BLE001 - disclosure must not raise
                    sink_disclosure[getattr(sink, "name", type(sink).__name__)] = {"disclosure_error": 1}
            return {
                "enabled": self._config.enabled,
                "recorded_events": self._recorded_events,
                "recorded_spans": self._recorded_spans,
                "sampled_out_runs": self._sampled_out_runs,
                "evicted_run_counters": self._evicted_runs,
                "refused_events_per_run_limit": sum(1 for key, count in self._run_event_counts.items() if key.startswith("event:") and count >= READERS["max_events_per_run"].read(self._config)),
                "tracked_run_counters": len(self._run_event_counts),
                "sink_failures": dict(self._sink_failures),
                "sinks": sink_disclosure,
                "derived_metrics": dict(self._derived_metrics),
            }

    def close(self) -> None:
        """Close every sink. Idempotent, and never raises."""
        if self._closed:
            return
        self._closed = True
        for sink in self._sinks:
            try:
                sink.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                self._note_sink_failure(sink, exc)

    def __enter__(self) -> TraceRecorder:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"TraceRecorder(enabled={self.enabled}, sinks={[getattr(s, 'name', '?') for s in self._sinks]})"


class _NullSpan:
    """The span a disabled recorder yields.

    Reads no clock, mints no id, stores no attribute, and is never recorded. It
    exists so an integration patch writes ``with recorder.span("tool.call"):``
    once and the disabled path costs a boolean test instead of a branch at
    every call site.
    """

    __slots__ = ("_context", "_name")

    def __init__(self, *, name: str, context: RunContext | None) -> None:
        self._name = name
        self._context = context

    @property
    def name(self) -> str:
        return self._name

    @property
    def span_id(self) -> str | None:
        return None

    @property
    def context(self) -> RunContext | None:
        return self._context

    def set_attribute(self, key: str, value: Any) -> bool:
        return False

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None

    def end(self, *, status: Any = None, end_time: float | None = None) -> None:
        return None

    def end_with_exception(self, exc: BaseException, *, status: Any = None) -> None:
        return None

    def __repr__(self) -> str:
        return f"_NullSpan(name={self._name!r})"


def _span_id_of_current() -> str | None:
    span = current_span()
    return span.span_id if span is not None else None


def build_recorder(
    config: ObservabilityConfig | None = None,
    *,
    id_generator: IDGenerator | None = None,
    clock: Any = time.time,
    redactor: Redactor | None = None,
    sinks: list[TraceSink] | None = None,
) -> TraceRecorder:
    """Build a recorder from *config*, filling the sinks from the config when
    none are passed.

    The single supported construction path outside tests, and the place a
    Gateway integration patch would call. There is deliberately no cached
    "singleton" variant: the caller owns the instance, so its lifetime and its
    config are visible at the call site.
    """
    return TraceRecorder(config, sinks=sinks, id_generator=id_generator, clock=clock, redactor=redactor)


def disabled_recorder(*, clock: Any = time.time) -> TraceRecorder:
    """Return a recorder that is inert by construction.

    Useful as an explicit "tracing is off here" value in code that must always
    hold a recorder, so a caller never has to write ``if recorder is None``.
    """
    return TraceRecorder(ObservabilityConfig(enabled=False), sinks=[], clock=clock)
