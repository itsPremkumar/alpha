"""Settings for the behaviour-trace writer. Default-OFF, and a no-op when off.

Separate from :class:`alpha.observability.config.ObservabilityConfig` on
purpose. That config governs the *span/trace* recorder that ships inert; this one
governs the behaviour substrate whose consumer is
:mod:`alpha.runtime.sentinel`, and the two have different bounds, different sinks
and a different failure contract. Merging them would mean changing the defaults
of a package the observability tests already pin, to accommodate a package that
must not be able to wake it up.

``enabled`` is the gate, and it is :data:`DEFAULT_ENABLED` (false). Nothing in
the runtime turns it on. When it is false:

* :meth:`TraceWriter.record` returns ``None`` at its first statement -- it does
  not read a clock, mint an id, build an envelope, or touch a sink;
* :meth:`TraceWriter.next_seq` returns ``0``;
* no sink is constructed, so a disabled writer creates no ring, no file and no
  parent directory.

There is no partially-on state. A disabled writer is inert, not quiet.

Every key is read through :data:`READERS`, naming the function that consumes it.
That table is the audit surface: ``test_trace_writer.py`` asserts it covers
exactly :func:`read_all_keys` with no missing and no extra key, and that
flipping each key changes an observable behaviour. A key nobody reads is a lie
told in YAML, which is the failure mode this package's own neighbours
(:mod:`alpha.memory.health.config`) already solved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from .contract import DEFAULT_MAX_VALUE_CHARS, TraceBounds
from .sinks import RunEventStoreSink, TraceRecordSink

__all__ = [
    "DEFAULT_ENABLED",
    "TRACE_SINK_NAMES",
    "READERS",
    "TraceConfig",
    "TraceSinkName",
    "build_writer",
    "read_all_keys",
    "resolve_all",
]

DEFAULT_ENABLED: Final[bool] = False

#: The closed sink set. ``memory`` is the bounded in-memory ring (a live tail
#: view and what the tests read), ``file`` the bounded JSONL writer, ``run_events``
#: the durable :class:`alpha.runtime.events.store.base.RunEventStore`, ``null`` the
#: no-op. No remote sink, for the same reason
#: :class:`alpha.observability.config.ObservabilityConfig` has none: a trace held
#: only in a third-party store is a screenshot, and this one has to be an input to
#: automatic repair.
TraceSinkName = str
TRACE_SINK_NAMES: Final[frozenset[str]] = frozenset({"memory", "file", "run_events", "null"})


class TraceConfig(BaseModel):
    """Settings for the behaviour-trace writer."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(default=DEFAULT_ENABLED, description="Master gate; false makes every write method an inert no-op that touches no sink.")
    sinks: list[str] = Field(default_factory=lambda: ["memory"], description="Which sinks are constructed. Ignored entirely when enabled is false.")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Per-run sampling probability. The decision is a hash of run_id so it is reproducible from the log alone.")
    max_events_per_run: int = Field(default=20_000, ge=1, description="Events one run may record before the writer starts disclosing refusals.")
    max_tracked_runs: int = Field(default=256, ge=1, description="How many runs' seq counters are retained at once; the least recently used is evicted and disclosed.")
    ring_capacity: int = Field(default=1024, ge=1, description="Records the in-memory ring retains. Reaching it evicts the oldest and is counted by the ring's own disclosure.")
    file_sink_path: str | None = Field(default=None, description="JSONL trace file for the `file` sink. None leaves the sink unconstructed, which is an error rather than a silent no-op.")
    file_sink_max_events: int = Field(default=100_000, ge=1, description="Events the file sink writes before it discloses an overflow count and stops growing.")
    file_sink_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, description="Byte budget for the trace file.")
    payload_max_bytes: int = Field(default=32_768, ge=64, description="Byte cap on one event payload; the envelope discloses what it dropped.")
    payload_max_items: int = Field(default=64, ge=1, description="Payload keys kept before the remainder is replaced by a disclosed marker.")
    payload_max_value_chars: int = Field(default=DEFAULT_MAX_VALUE_CHARS, ge=16, description="Longest single payload value stored; a longer one is truncated and the truncation disclosed.")
    payload_max_depth: int = Field(default=8, ge=1, description="Nesting depth a payload may reach before it is replaced by a disclosed marker.")
    redaction_policy: str = Field(default="strict", description="Scrubber policy applied at write time. strict additionally hides credential-shaped key names and bare high-entropy blobs.")
    async_queue_max_pending: int = Field(default=2048, ge=1, description="Rows the durable sink may hold before it refuses and counts an overflow.")
    auto_flush: bool = Field(default=True, description="Whether a durable sink schedules its own drain when a write happens on a running loop.")

    @property
    def file_sink_enabled(self) -> bool:
        """Whether the file sink can be constructed.

        Derived, not a key: a config naming ``file`` with no path is a
        configuration error and the writer raises rather than quietly dropping the
        sink.
        """
        return "file" in self.sinks and bool(self.file_sink_path)

    def bounds(self) -> TraceBounds:
        """Return the :class:`TraceBounds` these keys describe.

        One object, so the byte cap, the item cap, the value cap and the redaction
        policy cannot come from four different places and disagree.
        """
        return TraceBounds(
            max_payload_bytes=READERS["payload_max_bytes"].read(self),
            max_items=READERS["payload_max_items"].read(self),
            max_value_chars=READERS["payload_max_value_chars"].read(self),
            max_depth=READERS["payload_max_depth"].read(self),
            redaction_policy=READERS["redaction_policy"].read(self),
        )

    def described_readers(self) -> dict[str, str]:
        return {key: reader.consumer for key, reader in READERS.items()}


class _Reader:
    __slots__ = ("consumer", "read")

    def __init__(self, read: Callable[[TraceConfig], Any], consumer: str) -> None:
        self.read = read
        self.consumer = consumer


#: The reader table. The audit surface: ``test_trace_writer.py`` asserts it covers
#: exactly :func:`read_all_keys`, and that flipping a key changes something
#: observable.
READERS: Final[Mapping[str, _Reader]] = {
    "enabled": _Reader(lambda c: c.enabled, "TraceWriter.record / next_seq early return; TraceWriter construction"),
    "sinks": _Reader(lambda c: list(c.sinks), "TraceWriter.build_sinks sink construction"),
    "sample_rate": _Reader(lambda c: c.sample_rate, "TraceWriter._sampled per-run decision"),
    "max_events_per_run": _Reader(lambda c: c.max_events_per_run, "TraceWriter._admit per-run event cap"),
    "max_tracked_runs": _Reader(lambda c: c.max_tracked_runs, "TraceWriter._seq_counters LRU bound"),
    "ring_capacity": _Reader(lambda c: c.ring_capacity, "InMemorySink ring size"),
    "file_sink_path": _Reader(lambda c: c.file_sink_path, "TraceWriter.build_sinks file sink target"),
    "file_sink_max_events": _Reader(lambda c: c.file_sink_max_events, "JsonlFileSink event cap"),
    "file_sink_max_bytes": _Reader(lambda c: c.file_sink_max_bytes, "JsonlFileSink byte budget"),
    "payload_max_bytes": _Reader(lambda c: c.payload_max_bytes, "TraceBounds.max_payload_bytes"),
    "payload_max_items": _Reader(lambda c: c.payload_max_items, "TraceBounds.max_items"),
    "payload_max_value_chars": _Reader(lambda c: c.payload_max_value_chars, "TraceBounds.max_value_chars"),
    "payload_max_depth": _Reader(lambda c: c.payload_max_depth, "TraceBounds.max_depth"),
    "redaction_policy": _Reader(lambda c: c.redaction_policy, "TraceBounds.redaction_policy -> Redactor policy"),
    "async_queue_max_pending": _Reader(lambda c: c.async_queue_max_pending, "RunEventStoreSink.max_pending"),
    "auto_flush": _Reader(lambda c: c.auto_flush, "TraceWriter._fan_out auto-drain scheduling"),
}


def read_all_keys(config: TraceConfig | None = None) -> frozenset[str]:
    """Return the canonical schema keys (or *config*'s own keys)."""
    if config is None:
        return frozenset(TraceConfig.model_fields)
    return frozenset(type(config).model_fields)


def resolve_all(config: TraceConfig | None = None) -> dict[str, Any]:
    """Read every key through :data:`READERS` and return the resolved map."""
    target = config if config is not None else TraceConfig()
    return {key: reader.read(target) for key, reader in READERS.items()}


def build_writer(config: TraceConfig | None = None, *, store: Any = None, thread_id: str | None = None, **kwargs: Any) -> Any:
    """Build a :class:`~.writer.TraceWriter` from *config*.

    The single supported construction path outside tests. ``store`` is the
    durable :class:`alpha.runtime.events.store.base.RunEventStore`; it is only
    required when ``run_events`` is in ``sinks``, and passing one builds a
    :class:`~.sinks.RunEventStoreSink` around it. ``thread_id`` is that sink's
    fallback thread, for the call sites that cannot supply one per event.
    """
    from .writer import TraceWriter  # local import keeps this module import-cycle free

    target = config if config is not None else TraceConfig()
    return TraceWriter(target, store=store, thread_id=thread_id, **kwargs)


def build_sinks(config: TraceConfig, store: Any = None, *, thread_id: str | None = None) -> list[Any]:
    """Build the sink list a config asks for, reusing the recorder's own sinks.

    The memory and file sinks are the *existing*
    :class:`alpha.observability.recorder.InMemorySink` and
    :class:`alpha.observability.recorder.JsonlFileSink`, not copies, so the
    bounding and disclosure behaviour is one implementation in this package and a
    ring overflow is reported the same way a span overflow is.

    ``thread_id`` is the durable sink's fallback thread. A
    :class:`alpha.runtime.events.store.base.RunEventStore` addresses every row by
    ``(thread_id, run_id)``, so a sink that is not told which thread it writes
    for can only use the thread each envelope carries. That is correct for a
    single-threaded worker and wrong for a Gateway serving many, which is why the
    parameter exists rather than being inferred.
    """
    from alpha.observability.recorder import InMemorySink, JsonlFileSink

    sinks: list[Any] = []
    for name in READERS["sinks"].read(config):
        if name == "memory":
            sinks.append(TraceRecordSink(InMemorySink(max_records=READERS["ring_capacity"].read(config))))
        elif name == "file":
            path = READERS["file_sink_path"].read(config)
            if path:
                sinks.append(
                    TraceRecordSink(
                        JsonlFileSink(
                            path,
                            max_events=READERS["file_sink_max_events"].read(config),
                            max_bytes=READERS["file_sink_max_bytes"].read(config),
                        )
                    )
                )
        elif name == "run_events":
            if store is None:
                raise ValueError("sinks includes 'run_events' but no store was supplied; a durable sink with no store would be a silent no-op")
            sinks.append(RunEventStoreSink(store, thread_id=thread_id, max_pending=READERS["async_queue_max_pending"].read(config)))
        elif name == "null":
            continue
    return sinks
