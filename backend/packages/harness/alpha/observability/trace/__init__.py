"""The behaviour-trace substrate: Alpha's own end-to-end action log.

Why this exists rather than a vendor
------------------------------------
The consumer is :mod:`alpha.runtime.sentinel`. A trace held in a third-party
store is a *screenshot* -- something a human can look at after the fact. A trace
in our own append-only log is an *input to automatic repair*: the sentinel can
query "every model call in the last hour that finished for the wrong reason,
with the messages before and after" and act on the answer. Those are different
products, and only one of them can be built on someone else's retention policy.

What is deliberately NOT here
-----------------------------
Evaluation, dataset curation and prompt management. Those are what a vendor is
for; they are someone else's product surface and they are out of scope. What is
here is the substrate they would need anyway: one versioned contract, one writer,
one query surface.

The pieces
----------
* :mod:`.codes` -- the closed event-type registry for all eighteen instrumented
  layers, and the table that reconciles the repo's four legacy error taxonomies
  onto :mod:`alpha.errors.registry`.
* :mod:`.contract` -- :class:`~.contract.TraceEnvelope`, the one versioned
  envelope. Redaction and payload bounds happen inside its construction, so there
  is no path that writes an unredacted or a silently-truncated record.
* :mod:`.config` -- :class:`~.config.TraceConfig`, default-**off**, with a reader
  table naming the consumer of every key.
* :mod:`.writer` -- :class:`~.writer.TraceWriter`, the only write path.
  Thread-safe, async-safe, bounded, and incapable of raising into the run it is
  tracing.
* :mod:`.sinks` -- the durable bridge to the existing
  :class:`alpha.runtime.events.store.base.RunEventStore`, so the behaviour trace
  and the run feed are the *same rows* rather than two datasets to join.
* :mod:`.instrumentation` -- one typed emitter per layer.
* :mod:`.coverage` -- the machine-readable layer map, and the thing a reader
  should read before assuming a layer is instrumented.
* :mod:`.query` -- filters, the existing ``after_seq`` cursor, and aggregates.

Invariants
----------
* **Default-off and inert.** ``enabled: false`` means no clock read, no id, no
  envelope, no sink call.
* **Redaction at write time.** Prompts, tool arguments and tool results are the
  highest-risk payloads and are scrubbed before they can reach any sink.
* **Truncation is always disclosed.** ``truncated``, ``dropped_items``,
  ``payload_bytes`` and ``payload_sha256`` describe the *full* payload, so a
  reader can always tell that something was dropped and prove two events are the
  same event.
* **Loss is disclosed, never silent.** Every cap, drop, rejection and sink
  failure is counted in ``TraceWriter.disclosure()``.
* **Tracing is never load-bearing.** A run behaves identically with tracing on or
  off, and a test proves it.

Import cost
-----------
Nothing here is imported by the runtime at startup. The public surface is
installed lazily (:pep:`562`) through
``alpha.memory._lazy_exports.install_lazy_exports``, the same mechanism the rest
of ``alpha.observability`` uses, and every instrumentation call site resolves the
writer through :func:`~.writer.current_writer`, which returns ``None`` until
something installs one. The whole substrate therefore costs a default deployment
exactly one attribute read and no imports at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "DEFAULT_ENABLED": "config",
    "DISABLED_SEQ": "writer",
    "DURABLE_CATEGORY": "codes",
    "EVENT_CODES": "codes",
    "EVENT_TYPES": "codes",
    "ENVELOPE_SCHEMA_VERSION": "contract",
    "LAYER_EVENT_CODES": "codes",
    "LEGACY_ERROR_CODE_ALIASES": "codes",
    "LEGACY_TAXONOMIES": "codes",
    "MAX_EVENT_CODE_LENGTH": "codes",
    "PAYLOAD_DROP_NOTICE_KEY": "contract",
    "READERS": "config",
    "RunEventStoreSink": "sinks",
    "STATUS_SEVERITY_ALIASES": "codes",
    "TraceAggregate": "query",
    "TraceBounds": "contract",
    "TraceConfig": "config",
    "TraceEnvelope": "contract",
    "TraceFilter": "query",
    "TraceLayer": "codes",
    "TracePage": "query",
    "TraceRecordSink": "sinks",
    "TraceSeverity": "codes",
    "TraceWriter": "writer",
    "UnknownEnvelopeFieldError": "contract",
    "UnknownTraceErrorCode": "codes",
    "UnknownTraceEventTypeError": "codes",
    "aggregate": "query",
    "build_writer": "writer",
    "current_writer": "writer",
    "disabled_writer": "writer",
    "event_type": "codes",
    "install_writer": "writer",
    "layer_of": "codes",
    "matches": "query",
    "query": "query",
    "read_all_keys": "config",
    "require_event_type": "codes",
    "resolve_all": "config",
    "resolve_error_code": "codes",
    "reset_writer": "writer",
    "unresolved_error_codes": "codes",
    "unresolved_statuses": "codes",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.observability.trace.codes import DURABLE_CATEGORY as DURABLE_CATEGORY
    from alpha.observability.trace.codes import EVENT_CODES as EVENT_CODES
    from alpha.observability.trace.codes import EVENT_TYPES as EVENT_TYPES
    from alpha.observability.trace.codes import LAYER_EVENT_CODES as LAYER_EVENT_CODES
    from alpha.observability.trace.codes import LEGACY_ERROR_CODE_ALIASES as LEGACY_ERROR_CODE_ALIASES
    from alpha.observability.trace.codes import LEGACY_TAXONOMIES as LEGACY_TAXONOMIES
    from alpha.observability.trace.codes import MAX_EVENT_CODE_LENGTH as MAX_EVENT_CODE_LENGTH
    from alpha.observability.trace.codes import STATUS_SEVERITY_ALIASES as STATUS_SEVERITY_ALIASES
    from alpha.observability.trace.codes import EventTypeDef as EventTypeDef
    from alpha.observability.trace.codes import LegacyTaxonomy as LegacyTaxonomy
    from alpha.observability.trace.codes import TraceLayer as TraceLayer
    from alpha.observability.trace.codes import TraceSeverity as TraceSeverity
    from alpha.observability.trace.codes import UnknownTraceErrorCode as UnknownTraceErrorCode
    from alpha.observability.trace.codes import UnknownTraceEventTypeError as UnknownTraceEventTypeError
    from alpha.observability.trace.codes import event_type as event_type
    from alpha.observability.trace.codes import layer_of as layer_of
    from alpha.observability.trace.codes import require_event_type as require_event_type
    from alpha.observability.trace.codes import resolve_error_code as resolve_error_code
    from alpha.observability.trace.codes import unresolved_error_codes as unresolved_error_codes
    from alpha.observability.trace.codes import unresolved_statuses as unresolved_statuses
    from alpha.observability.trace.config import DEFAULT_ENABLED as DEFAULT_ENABLED
    from alpha.observability.trace.config import READERS as READERS
    from alpha.observability.trace.config import TRACE_SINK_NAMES as TRACE_SINK_NAMES
    from alpha.observability.trace.config import TraceConfig as TraceConfig
    from alpha.observability.trace.config import read_all_keys as read_all_keys
    from alpha.observability.trace.config import resolve_all as resolve_all
    from alpha.observability.trace.contract import ENVELOPE_SCHEMA_VERSION as ENVELOPE_SCHEMA_VERSION
    from alpha.observability.trace.contract import PAYLOAD_DROP_NOTICE_KEY as PAYLOAD_DROP_NOTICE_KEY
    from alpha.observability.trace.contract import TraceBounds as TraceBounds
    from alpha.observability.trace.contract import TraceEnvelope as TraceEnvelope
    from alpha.observability.trace.contract import UnknownEnvelopeFieldError as UnknownEnvelopeFieldError
    from alpha.observability.trace.coverage import COVERAGE as COVERAGE
    from alpha.observability.trace.coverage import LayerCoverage as LayerCoverage
    from alpha.observability.trace.coverage import coverage_report as coverage_report
    from alpha.observability.trace.instrumentation import emit_error as emit_error
    from alpha.observability.trace.instrumentation import emit_error_swallowed as emit_error_swallowed
    from alpha.observability.trace.instrumentation import emit_run_closed as emit_run_closed
    from alpha.observability.trace.instrumentation import emit_run_opened as emit_run_opened
    from alpha.observability.trace.instrumentation import record_event as record_event
    from alpha.observability.trace.query import DEFAULT_LIMIT as DEFAULT_LIMIT
    from alpha.observability.trace.query import MAX_LIMIT as MAX_LIMIT
    from alpha.observability.trace.query import TraceAggregate as TraceAggregate
    from alpha.observability.trace.query import TraceFilter as TraceFilter
    from alpha.observability.trace.query import TracePage as TracePage
    from alpha.observability.trace.query import aggregate as aggregate
    from alpha.observability.trace.query import coerce_ints as coerce_ints
    from alpha.observability.trace.query import coerce_set as coerce_set
    from alpha.observability.trace.query import envelopes_from_run_events as envelopes_from_run_events
    from alpha.observability.trace.query import matches as matches
    from alpha.observability.trace.query import query as query
    from alpha.observability.trace.sinks import RunEventStoreSink as RunEventStoreSink
    from alpha.observability.trace.sinks import TraceRecordSink as TraceRecordSink
    from alpha.observability.trace.writer import DISABLED_SEQ as DISABLED_SEQ
    from alpha.observability.trace.writer import TraceWriter as TraceWriter
    from alpha.observability.trace.writer import build_writer as build_writer
    from alpha.observability.trace.writer import current_writer as current_writer
    from alpha.observability.trace.writer import disabled_writer as disabled_writer
    from alpha.observability.trace.writer import install_writer as install_writer
    from alpha.observability.trace.writer import reset_writer as reset_writer

install_lazy_exports(__name__, _EXPORTS)
