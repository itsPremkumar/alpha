"""Run-correlation spine: one identity threaded through a run, and a trace it exports.

The problem this package solves is narrow and specific. Alpha's incident work
kept hitting the same wall: when something went wrong across a long,
multi-agent, tool-heavy run, there was no reliable way to correlate the agent
turn, the tool call, the memory write, the subagent and the gateway request
that caused it. Telemetry existed (tool discovery counters, health reports, a
support bundle) but none of it shared an identity with any other.

What is here
------------
* :mod:`.context` -- :class:`RunContext` and its contextvar propagation, the
  spine itself. The ``trace_id`` is resolved from the existing
  :mod:`alpha.trace_context` request id rather than minted here, so this
  extends the repo's correlation story instead of forking it.
* :mod:`.ids` -- collision-resistant, time-sortable id generation with an
  injectable generator for deterministic tests.
* :mod:`.span` -- timed spans on an injected clock, a closed status set, and
  redaction enforced at the only write path.
* :mod:`.events` -- the closed event taxonomy; unknown names are rejected at
  construction.
* :mod:`.recorder` -- injected sinks, bounded with disclosed overflow, and
  contained sink failures.
* :mod:`.redaction` -- key-name and value-pattern scrubbing with an explicit
  unknown-shape policy, layered over the existing
  :mod:`alpha.security.memory_redaction` engine.
* :mod:`.metrics_bridge` -- publishes trace counters into the metrics
  registries the Gateway already scrapes, rather than a third one.
* :mod:`.config` -- :class:`ObservabilityConfig`, default-off, with a reader
  table naming the consumer of every key.

Invariants this package holds
-----------------------------
* **Default-off and inert.** ``enabled: false`` means no event, no file, and
  no sink call -- asserted by counting sink calls, not by observing silence.
* **No new dependencies, no network export.** ``remote_sink_enabled`` exists
  so the *absence* of a network sink is a declared, testable decision; it is
  ``false`` in the shipped default and is refused at recorder construction
  until a remote sink is actually written and reviewed.
* **No global mutable singletons.** The recorder is an injected instance.
  The only ambient state is the two ContextVars, and both are always
  restorable through their tokens.
* **Nothing reaches a sink unredacted.** Redaction runs in a validator inside
  the event model and inside the span's only attribute write path, so there is
  no code path that skips it.
* **Loss is disclosed, never silent.** Every cap, drop, sink failure and
  sampled-out run is counted and surfaced in
  :meth:`TraceRecorder.disclosure`.

Reading a trace
---------------
``python scripts/export_run_trace.py <trace.jsonl>`` renders a deterministic
timeline. The file format is a versioned JSONL contract: one record per line,
each tagged ``event``, ``span`` or ``disclosure``, so one file carries the whole
story and a reader never has to guess a record's shape.

Import cost
-----------
The public surface is installed lazily (:pep:`562`) through
``alpha.memory._lazy_exports.install_lazy_exports``, the same mechanism the
memory subsystem uses to keep a config schema from dragging in a facade. The
config module in particular is imported by shared configuration code, and
eagerly pulling the recorder, tracer and sinks in behind it would recreate the
import cycle that mechanism exists to prevent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "BOUNDARY_RULES": "context",
    "BoundaryRule": "context",
    "DEFAULT_ENABLED": "config",
    "DEFAULT_MAX_ATTRIBUTES": "span",
    "DEFAULT_MAX_SPAN_DEPTH": "span",
    "ENV_LINK_KIND": "context",
    "EVENT_DOMAINS": "events",
    "EVENT_NAMES": "events",
    "EVENT_STATUSES": "events",
    "EventStatus": "events",
    "FORBIDDEN_ID_SOURCE": "ids",
    "ID_HEX_LENGTH": "ids",
    "IDGenerator": "ids",
    "InMemorySink": "recorder",
    "JsonlFileSink": "recorder",
    "METRIC_PREFIX": "metrics_bridge",
    "NoOpSink": "recorder",
    "NullSinkName": "config",
    "ObservabilityConfig": "config",
    "READERS": "config",
    "REDACTION_POLICIES": "redaction",
    "REDACTION_REASONS": "redaction",
    "RandomIdGenerator": "ids",
    "RedactionOutcome": "redaction",
    "Redactor": "redaction",
    "RunContext": "context",
    "SPAN_STATUSES": "span",
    "SequenceIdGenerator": "ids",
    "Span": "span",
    "SpanStatus": "span",
    "TRACE_EXTRA_PATTERNS": "redaction",
    "TRACE_SINK_NAMES": "config",
    "TraceEvent": "events",
    "TraceEventName": "events",
    "TraceMetricsBridge": "metrics_bridge",
    "TraceRecorder": "recorder",
    "TraceSink": "recorder",
    "Tracer": "span",
    "UnknownEventNameError": "events",
    "UnknownEventStatusError": "events",
    "UnknownSpanStatusError": "span",
    "bind": "context",
    "bind_carrier": "context",
    "build_recorder": "recorder",
    "coerce_status": "span",
    "current": "context",
    "dangerous_value_corpus": "redaction",
    "default_id_generator": "ids",
    "describe_boundary": "context",
    "detach": "context",
    "disabled_recorder": "recorder",
    "ensure_run_context": "context",
    "is_valid_id": "ids",
    "link_from_env": "context",
    "parse_event_name": "events",
    "parse_event_status": "events",
    "propagates_automatically": "context",
    "read_all_keys": "config",
    "redact_text": "redaction",
    "require_id": "ids",
    "resolve_all": "config",
    "run_scope": "context",
    "subprocess_link_env": "context",
    "to_carrier": "context",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports are required for type checkers and IDEs;
    # runtime names resolve through the PEP 562 installer below.
    from alpha.observability.config import DEFAULT_ENABLED as DEFAULT_ENABLED
    from alpha.observability.config import READERS as READERS
    from alpha.observability.config import TRACE_SINK_NAMES as TRACE_SINK_NAMES
    from alpha.observability.config import NullSinkName as NullSinkName
    from alpha.observability.config import ObservabilityConfig as ObservabilityConfig
    from alpha.observability.config import read_all_keys as read_all_keys
    from alpha.observability.config import resolve_all as resolve_all
    from alpha.observability.context import BOUNDARY_RULES as BOUNDARY_RULES
    from alpha.observability.context import ENV_LINK_KIND as ENV_LINK_KIND
    from alpha.observability.context import BoundaryRule as BoundaryRule
    from alpha.observability.context import RunContext as RunContext
    from alpha.observability.context import bind as bind
    from alpha.observability.context import bind_carrier as bind_carrier
    from alpha.observability.context import current as current
    from alpha.observability.context import describe_boundary as describe_boundary
    from alpha.observability.context import detach as detach
    from alpha.observability.context import ensure_run_context as ensure_run_context
    from alpha.observability.context import link_from_env as link_from_env
    from alpha.observability.context import propagates_automatically as propagates_automatically
    from alpha.observability.context import run_scope as run_scope
    from alpha.observability.context import subprocess_link_env as subprocess_link_env
    from alpha.observability.context import to_carrier as to_carrier
    from alpha.observability.events import EVENT_DOMAINS as EVENT_DOMAINS
    from alpha.observability.events import EVENT_NAMES as EVENT_NAMES
    from alpha.observability.events import EVENT_STATUSES as EVENT_STATUSES
    from alpha.observability.events import EventStatus as EventStatus
    from alpha.observability.events import TraceEvent as TraceEvent
    from alpha.observability.events import TraceEventName as TraceEventName
    from alpha.observability.events import UnknownEventNameError as UnknownEventNameError
    from alpha.observability.events import UnknownEventStatusError as UnknownEventStatusError
    from alpha.observability.events import parse_event_name as parse_event_name
    from alpha.observability.events import parse_event_status as parse_event_status
    from alpha.observability.ids import FORBIDDEN_ID_SOURCE as FORBIDDEN_ID_SOURCE
    from alpha.observability.ids import ID_HEX_LENGTH as ID_HEX_LENGTH
    from alpha.observability.ids import IDGenerator as IDGenerator
    from alpha.observability.ids import RandomIdGenerator as RandomIdGenerator
    from alpha.observability.ids import SequenceIdGenerator as SequenceIdGenerator
    from alpha.observability.ids import default_id_generator as default_id_generator
    from alpha.observability.ids import is_valid_id as is_valid_id
    from alpha.observability.ids import require_id as require_id
    from alpha.observability.metrics_bridge import METRIC_PREFIX as METRIC_PREFIX
    from alpha.observability.metrics_bridge import TraceMetricsBridge as TraceMetricsBridge
    from alpha.observability.recorder import InMemorySink as InMemorySink
    from alpha.observability.recorder import JsonlFileSink as JsonlFileSink
    from alpha.observability.recorder import NoOpSink as NoOpSink
    from alpha.observability.recorder import TraceRecorder as TraceRecorder
    from alpha.observability.recorder import TraceSink as TraceSink
    from alpha.observability.recorder import build_recorder as build_recorder
    from alpha.observability.recorder import disabled_recorder as disabled_recorder
    from alpha.observability.redaction import REDACTION_POLICIES as REDACTION_POLICIES
    from alpha.observability.redaction import REDACTION_REASONS as REDACTION_REASONS
    from alpha.observability.redaction import TRACE_EXTRA_PATTERNS as TRACE_EXTRA_PATTERNS
    from alpha.observability.redaction import RedactionOutcome as RedactionOutcome
    from alpha.observability.redaction import Redactor as Redactor
    from alpha.observability.redaction import dangerous_value_corpus as dangerous_value_corpus
    from alpha.observability.redaction import redact_text as redact_text
    from alpha.observability.span import DEFAULT_MAX_ATTRIBUTES as DEFAULT_MAX_ATTRIBUTES
    from alpha.observability.span import DEFAULT_MAX_SPAN_DEPTH as DEFAULT_MAX_SPAN_DEPTH
    from alpha.observability.span import SPAN_STATUSES as SPAN_STATUSES
    from alpha.observability.span import Span as Span
    from alpha.observability.span import SpanStatus as SpanStatus
    from alpha.observability.span import Tracer as Tracer
    from alpha.observability.span import UnknownSpanStatusError as UnknownSpanStatusError
    from alpha.observability.span import coerce_status as coerce_status

install_lazy_exports(__name__, _EXPORTS)
