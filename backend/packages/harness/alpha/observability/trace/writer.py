"""The writer: the only way anything is recorded, and the thing that must never break.

Guarantees, in the order they matter
------------------------------------
1. **Tracing is never mandatory for correctness.** :meth:`TraceWriter.record`
   returns ``None`` and touches nothing when disabled, and returns ``None``
   rather than raising on *any* internal failure. A run behaves identically with
   tracing on or off; ``test_trace_writer.py`` proves that by running the same
   fixture both ways and comparing results byte for byte.
2. **No silent swallow.** Every refusal and every failure is counted and appears
   in :meth:`TraceWriter.disclosure`: per-run cap refusals, evicted seq counters,
   envelopes rejected by the contract, and per-sink failures including flush
   failures. Each distinct failure kind is logged at ``WARNING`` **once**, not
   once per event -- a broken sink on a hot path would otherwise turn one
   misconfiguration into a log flood that hides the original problem.
3. **No retry storm.** Nothing here retries. A durable sink buffers and the
   caller decides when to drain it; an internal retry loop is how an
   observability outage becomes a CPU outage.
4. **Bounded.** Per-run event cap, LRU-bounded per-run seq table, bounded
   in-memory ring (the *existing* recorder's :class:`InMemorySink`), bounded file
   sink, bounded durable-sink buffer. Every bound has a counter.
5. **Thread-safe and async-safe.** One ``RLock`` guards the counters. Sinks are
   called outside the lock so a slow sink cannot serialize the callers, and a
   durable sink's drain is scheduled on the running loop rather than blocking a
   synchronous callback.

Sequencing
----------
``seq`` is a **per-run** counter, which is what the existing
``GET /runs/{id}/events?after_seq=`` cursor pages on. It is not a global counter:
a global one would be a lock and a contention point on every write in the
process, for a number only a per-run reader ever compares. ``event_id`` carries
the run id as a prefix so it is still globally unique.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Final

from ..context import RunContext, current
from .config import READERS, TraceConfig, build_sinks
from .contract import TraceBounds, TraceEnvelope

__all__ = [
    "TraceWriter",
    "build_writer",
    "current_writer",
    "disabled_writer",
    "install_writer",
    "reset_writer",
]

logger = logging.getLogger(__name__)

#: Returned by :meth:`TraceWriter.next_seq` for a disabled writer. Not a real
#: sequence number, and deliberately not 1: a caller that ignored the disabled
#: gate would then produce an envelope claiming to be the first event of a run.
DISABLED_SEQ: Final[int] = 0


class TraceWriter:
    """Records sealed envelopes into injected sinks. Never raises to its caller."""

    __slots__ = (
        "_auto_flush",
        "_bounds",
        "_closed",
        "_config",
        "_evicted_runs",
        "_failure_lock",
        "_logged",
        "_lock",
        "_monotonic",
        "_redactor",
        "_rejected_events",
        "_sampled_out_runs",
        "_seq_counters",
        "_sink_failures",
        "_sinks",
        "_wall",
        "recorded_events",
        "refused_events",
    )

    def __init__(
        self,
        config: TraceConfig | None = None,
        *,
        store: Any = None,
        sinks: list[Any] | None = None,
        thread_id: str | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config if config is not None else TraceConfig()
        if not callable(wall_clock) or not callable(monotonic_clock):
            raise TypeError("wall_clock and monotonic_clock must be callable")
        if "file" in self._config.sinks and not self._config.file_sink_path:
            raise ValueError("sinks includes 'file' but file_sink_path is unset; a file sink with no path would be a silent no-op")
        self._wall = wall_clock
        self._monotonic = monotonic_clock
        self._bounds: TraceBounds = self._config.bounds()
        self._redactor = self._bounds.redactor()
        # A disabled writer constructs *no* sink. Not "builds one and never
        # calls it": the file sink creates a parent directory at construction
        # time, so a disabled writer that built one would leave a directory
        # behind on a machine that never turned tracing on.
        if sinks is not None:
            self._sinks = list(sinks)
        elif self._config.enabled:
            self._sinks = build_sinks(self._config, store, thread_id=thread_id)
        else:
            self._sinks = []
        self._auto_flush = bool(READERS["auto_flush"].read(self._config))
        self._lock = threading.RLock()
        self._failure_lock = threading.Lock()
        self._logged: set[str] = set()
        self._seq_counters: OrderedDict[str, int] = OrderedDict()
        self._sink_failures: dict[str, int] = {}
        self._rejected_events = 0
        self._sampled_out_runs = 0
        self._evicted_runs = 0
        self.recorded_events = 0
        self.refused_events = 0
        self._closed = False

    # -- accessors ------------------------------------------------------------

    @property
    def config(self) -> TraceConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def bounds(self) -> TraceBounds:
        return self._bounds

    @property
    def sinks(self) -> tuple[Any, ...]:
        return tuple(self._sinks)

    def __repr__(self) -> str:
        return f"TraceWriter(enabled={self.enabled}, sinks={[getattr(s, 'name', '?') for s in self._sinks]})"

    # -- the gate -------------------------------------------------------------

    def sampled(self, run_id: str) -> bool:
        """Return whether *run_id* is in the sample.

        A pure function of ``(run_id, sample_rate)`` via SHA-256, so the decision
        is reproducible from the log alone and a test needs no RNG. Sampled per
        *run*, never per event: a trace holding the turn but not the tool call
        that produced it is not a trace.
        """
        rate = READERS["sample_rate"].read(self._config)
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        digest = hashlib.sha256(f"trace:{run_id}:{rate:.6f}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64) < rate

    def next_seq(self, run_id: str) -> int:
        """Return the next per-run sequence number, or :data:`DISABLED_SEQ` when off.

        The counter table is LRU-bounded by ``max_tracked_runs``; eviction is
        counted because an evicted counter means that run's cap accounting is
        gone, and that has to be visible rather than inferred from a missing
        number.
        """
        if not self._config.enabled or self._closed:
            return DISABLED_SEQ
        if not self.sampled(run_id):
            with self._lock:
                self._sampled_out_runs += 1
            return DISABLED_SEQ
        with self._lock:
            current_seq = self._seq_counters.get(run_id, 0) + 1
            self._seq_counters[run_id] = current_seq
            self._seq_counters.move_to_end(run_id)
            self._trim_runs_locked()
        return current_seq

    def _trim_runs_locked(self) -> None:
        cap = READERS["max_tracked_runs"].read(self._config)
        while len(self._seq_counters) > cap:
            self._seq_counters.popitem(last=False)
            self._evicted_runs += 1

    def _admit_locked(self, run_id: str) -> bool:
        """Charge one event against the run's cap. The caller holds the lock.

        ``>=`` not ``>``: the counter is the number of events already admitted,
        so a limit of 3 admits exactly 3. An off-by-one here would mean the
        documented cap and the enforced cap disagree, which is the kind of
        discrepancy an operator only discovers after trusting the smaller number.
        """
        if self._seq_counters.get(run_id, 0) >= READERS["max_events_per_run"].read(self._config):
            self.refused_events += 1
            return False
        return True

    # -- the single write path -------------------------------------------------

    def record(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        context: RunContext | None = None,
        payload: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> TraceEnvelope | None:
        """Record one event. Returns the sealed envelope, or ``None``.

        ``None`` is returned -- never raised -- when the writer is disabled, the
        run is sampled out, the per-run cap is reached, no run identity is
        available, or the envelope violates the registry. Every one of those
        cases is counted in :meth:`disclosure`.

        Identity is taken from *context* or the ambient
        :class:`~alpha.observability.context.RunContext` when not passed, so an
        instrumentation call site states only what it knows and inherits the rest.
        """
        if not self._config.enabled or self._closed:
            return None
        try:
            return self._record_locked(event_type, run_id=run_id, trace_id=trace_id, context=context, payload=payload, **fields)
        except Exception as exc:  # noqa: BLE001 - the traced run must not fail here
            self._log_once("envelope", "trace event %r was rejected and not recorded: %s", event_type, exc)
            with self._lock:
                self._rejected_events += 1
            return None

    def _record_locked(
        self,
        event_type: str,
        *,
        run_id: str | None,
        trace_id: str | None,
        context: RunContext | None,
        payload: Mapping[str, Any] | None,
        **fields: Any,
    ) -> TraceEnvelope | None:
        bound = context if context is not None else current()
        effective_run = run_id if run_id else (bound.run_id if bound is not None else None)
        if not effective_run:
            # No identity and none supplied. Returning None rather than minting a
            # placeholder keeps a mis-wired call site visible as a missing event,
            # which is a diagnosable gap, instead of an orphan under a fabricated
            # id, which is not.
            with self._lock:
                self._rejected_events += 1
            self._log_once("identity", "trace event %r was dropped: no run context is bound and no run_id was passed", event_type)
            return None
        if not self.sampled(effective_run):
            with self._lock:
                self._sampled_out_runs += 1
            return None
        with self._lock:
            if not self._admit_locked(effective_run):
                return None

        envelope = TraceEnvelope.build(
            event_type=event_type,
            run_id=effective_run,
            trace_id=trace_id if trace_id else (bound.trace_id if bound is not None else effective_run),
            thread_id=fields.pop("thread_id", None) or (bound.thread_id if bound is not None else None),
            correlation_id=fields.pop("correlation_id", None),
            parent_span_id=fields.pop("parent_span_id", None) or (bound.parent_span_id if bound is not None else None),
            agent_name=fields.pop("agent_name", None) or (bound.agent_name if bound is not None else None),
            agent_depth=fields.pop("agent_depth", 0),
            subagent_id=fields.pop("subagent_id", None),
            span_id=fields.pop("span_id", None),
            seq=self.next_seq(effective_run),
            payload=payload,
            bounds=self._bounds,
            redactor=self._redactor,
            ts_monotonic=self._monotonic(),
            ts_wall=self._wall(),
            **fields,
        )
        self._fan_out(envelope)
        with self._lock:
            self.recorded_events += 1
        return envelope

    def record_envelope(self, envelope: TraceEnvelope) -> bool:
        """Fan an already-built envelope out. Never raises."""
        if not self._config.enabled or self._closed or envelope is None:
            return False
        self._fan_out(envelope)
        with self._lock:
            self.recorded_events += 1
        return True

    # -- fan-out --------------------------------------------------------------

    def _fan_out(self, envelope: TraceEnvelope) -> None:
        """Hand one envelope to every sink, containing each failure.

        Sinks are called outside the lock: a slow sink must not serialize the
        callers, and holding the writer's lock across I/O is how a trace sink
        turns into a lock convoy in the traced system.
        """
        scheduled_drain = False
        for sink in self._sinks:
            name = getattr(sink, "name", type(sink).__name__)
            try:
                if hasattr(sink, "put_nowait"):
                    sink.put_nowait(envelope)
                    if self._auto_flush and not scheduled_drain and hasattr(sink, "flush_if_possible"):
                        scheduled_drain = bool(sink.flush_if_possible())
                elif hasattr(sink, "emit_envelope"):
                    sink.emit_envelope(envelope)
                else:
                    sink.emit(envelope.to_record())
            except Exception as exc:  # noqa: BLE001 - a sink must never break the run
                self._note_sink_failure(name, exc)

    def _note_sink_failure(self, name: str, exc: BaseException) -> None:
        with self._lock:
            self._sink_failures[name] = self._sink_failures.get(name, 0) + 1
        self._log_once(f"sink:{name}", "trace sink %r failed and its records are lost; further failures of this sink will not be logged individually: %s", name, exc)

    def _log_once(self, key: str, message: str, *args: Any) -> None:
        with self._failure_lock:
            if key in self._logged:
                return
            self._logged.add(key)
        logger.warning(message, *args)

    # -- flushing -------------------------------------------------------------

    async def drain(self) -> int:
        """Drain every async-capable sink. Returns rows written.

        The only place a durable write happens, so a caller controls when and a
        failure cannot become a synchronous stall on a callback thread.
        """
        written = 0
        for sink in self._sinks:
            drain = getattr(sink, "drain", None)
            if drain is None:
                continue
            try:
                written += int(await drain())
            except Exception as exc:  # noqa: BLE001 - a sink must never break the run
                self._note_sink_failure(getattr(sink, "name", type(sink).__name__), exc)
        return written

    def drain_if_possible(self) -> bool:
        """Schedule a drain on the running loop, if there is one."""
        if not self._config.enabled or self._closed or not self._auto_flush:
            return False
        for sink in self._sinks:
            flush = getattr(sink, "flush_if_possible", None)
            if flush is None:
                continue
            try:
                if flush():
                    return True
            except Exception as exc:  # noqa: BLE001
                self._note_sink_failure(getattr(sink, "name", type(sink).__name__), exc)
        return False

    def close(self) -> None:
        """Close every sink. Idempotent, and never raises."""
        if self._closed:
            return
        self._closed = True
        for sink in self._sinks:
            try:
                sink.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                self._note_sink_failure(getattr(sink, "name", type(sink).__name__), exc)

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- disclosure ------------------------------------------------------------

    def disclosure(self) -> dict[str, Any]:
        """Every counter an operator needs to trust this trace.

        This is the "nothing is silently discarded" surface. Sampled-out runs,
        per-run refusals, evicted counters, contract rejections and each sink's
        own drops and flush failures all appear here.
        """
        with self._lock:
            base: dict[str, Any] = {
                "enabled": self._config.enabled,
                "recorded_events": self.recorded_events,
                "refused_events": self.refused_events,
                "rejected_events": self._rejected_events,
                "sampled_out_runs": self._sampled_out_runs,
                "evicted_run_counters": self._evicted_runs,
                "tracked_runs": len(self._seq_counters),
                "sink_failures": dict(self._sink_failures),
            }
        sinks: dict[str, Any] = {}
        for sink in self._sinks:
            name = getattr(sink, "name", type(sink).__name__)
            try:
                disclosure = getattr(sink, "disclosure", None)
                sinks[name] = dict(disclosure()) if disclosure is not None else {}
            except Exception:  # noqa: BLE001 - disclosure must not raise
                sinks[name] = {"disclosure_error": 1}
        base["sinks"] = sinks
        return base

    def ring(self) -> tuple[dict[str, Any], ...]:
        """Return the retained in-memory records, oldest first.

        The live-tail view: what a UI would poll, and what the tests read. Empty
        when no memory sink is configured, and empty when the writer is disabled
        (no sink is constructed at all).
        """
        for sink in self._sinks:
            inner = getattr(sink, "_inner", None)
            if inner is not None and hasattr(inner, "records"):
                return inner.records()
        return ()


# ---------------------------------------------------------------------------
# Process-level installation
# ---------------------------------------------------------------------------
# A module-level *registry*, deliberately not a lazily-constructed singleton. The
# contrast with ``alpha.events.bus.get_event_bus()`` is the point: that function
# builds a bus on access, so every caller pays for it. ``current_writer()`` here
# returns ``None`` until something installs a writer, which is what makes "no
# writer installed" indistinguishable from "tracing off" -- and both cost one
# ``is None`` test at the call site.

_installed_lock = threading.Lock()
_installed_writer: TraceWriter | None = None


def install_writer(writer: TraceWriter | None) -> TraceWriter | None:
    """Install *writer* as the process writer and return it.

    Passing ``None`` clears it, which is how a test or a shutdown path returns to
    the inert state. Only one writer is installed at a time; installing a second
    replaces the first, because two writers would split one run's sequence and
    there would be no way to tell which half a reader is looking at.
    """
    global _installed_writer
    if writer is not None and not isinstance(writer, TraceWriter):
        raise TypeError(f"install_writer expects a TraceWriter, got {type(writer).__name__}")
    with _installed_lock:
        _installed_writer = writer
    return writer


def current_writer() -> TraceWriter | None:
    """Return the installed writer, or ``None`` when there is none.

    ``None`` is the answer a call site must handle, and it is the answer in every
    default deployment: :data:`~.config.DEFAULT_ENABLED` is false and nothing
    installs a writer, so the whole substrate costs one attribute read.
    """
    return _installed_writer


def reset_writer() -> None:
    """Close and uninstall the installed writer. Never raises."""
    global _installed_writer
    with _installed_lock:
        writer = _installed_writer
        _installed_writer = None
    if writer is not None:
        try:
            writer.close()
        except Exception:  # noqa: BLE001 - reset must not raise
            pass


def build_writer(config: TraceConfig | None = None, *, store: Any = None, **kwargs: Any) -> TraceWriter:
    """Build a writer from *config*. The single construction path outside tests."""
    from .config import build_writer as _build

    return _build(config, store=store, **kwargs)

def disabled_writer() -> TraceWriter:
    """Return a writer that is inert by construction.

    The explicit "tracing is off here" value, so a caller that must always hold a
    writer never has to write ``if writer is None``.
    """
    return TraceWriter(TraceConfig(enabled=False), sinks=[])
