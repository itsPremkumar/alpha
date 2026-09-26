"""Publishes trace-derived counters into the metrics registries that exist.

This module exists to *not* be a third metrics system. Alpha already has two,
and a trace bridge that invented a third would be three places to look during
an incident and two that nothing scrapes:

* :class:`alpha.ops.metrics.MetricsRegistry` -- the declarative, series-bounded
  registry behind ``get_metrics_registry()``, with Prometheus text exposition
  and an ``alpha_metrics_series_dropped_total`` counter whose *absence means
  zero drops*. Undeclared names and undeclared label keys are refused, so
  label cardinality stays an explicit decision.
* :class:`alpha.memory.health.metrics.MetricsRegistry` -- the injected
  in-process registry used by the memory health plane, with counters, gauges
  and a bounded histogram reservoir that discloses ``dropped_samples``.

Both are constructed by their host and passed in. This bridge holds a
reference, creates nothing global, and declares its metric names once,
idempotently, so re-declaring on a second Gateway reload is a no-op rather than
a conflict.

The cardinality rule, stated once
---------------------------------
**A trace id, run id, thread id, user id and span id are never a metric
label.** Not "usually" -- never. Two reasons, and the first is the one that
matters:

1. The ops registry keeps at most ``max_series`` (2048) label combinations and
   *refuses* new ones past the cap. Labelling by run id would consume the
   entire budget with the first few hundred runs and then silently stop
   recording everything else.
2. A run id in a scrape target is a cardinality bomb with a credential-shaped
   handle on it: it turns ``/metrics`` into an index of who ran what, when.

So metrics here are **aggregate counters over the taxonomy**, and identity
lives in the trace file where it belongs. The label sets are the closed sets
:data:`EVENT_NAMES`, :data:`EVENT_STATUSES` and :data:`REDACTION_REASONS`
plus a handful of small fixed strings -- bounded by construction, because the
source of every label value is a closed set.

The bridge also forwards the recorder's own honesty counters
(``sampled_out_runs``, sink failures, dropped events) so "the trace is
incomplete" is visible on the same dashboard as "the run failed", rather than
only to whoever has the file.
"""

from __future__ import annotations

from typing import Any

from .events import EVENT_NAMES, EVENT_STATUSES, TraceEvent
from .recorder import TraceRecorder
from .redaction import REDACTION_REASONS

__all__ = [
    "BRIDGE_METRICS",
    "METRIC_PREFIX",
    "TraceMetricsBridge",
]

METRIC_PREFIX = "observability_trace_"

#: ``name -> (kind, help, labels)`` for the ops registry. Every label set here
#: is drawn from a closed set, so the series count is bounded by the taxonomy
#: rather than by traffic.
BRIDGE_METRICS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        f"{METRIC_PREFIX}events_total",
        "counter",
        "Trace events recorded, by taxonomy name and terminal status.",
        ("event", "status"),
    ),
    (
        f"{METRIC_PREFIX}runs_total",
        "counter",
        "Trace runs recorded, by terminal status. Sampled-out runs are NOT counted here; see runs_sampled_out_total.",
        ("status",),
    ),
    (
        f"{METRIC_PREFIX}runs_sampled_out_total",
        "counter",
        "Runs refused by the per-run sampling decision. Absence of growth is the only proof a sample is complete.",
        (),
    ),
    (
        f"{METRIC_PREFIX}span_duration_ms",
        "histogram",
        "Span duration in milliseconds, by span name.",
        ("span",),
    ),
    (
        f"{METRIC_PREFIX}spans_total",
        "counter",
        "Spans recorded, by span name and terminal status.",
        ("span", "status"),
    ),
    (
        f"{METRIC_PREFIX}context_links_total",
        "counter",
        "Run contexts rebound across a boundary that does not copy contextvars, by boundary kind.",
        ("kind",),
    ),
    (
        f"{METRIC_PREFIX}redactions_total",
        "counter",
        "Values scrubbed before reaching a sink, by policy reason code.",
        ("reason",),
    ),
    (
        f"{METRIC_PREFIX}sink_dropped_total",
        "counter",
        "Records a sink refused. A non-zero value means the trace on disk is incomplete.",
        ("sink",),
    ),
    (
        f"{METRIC_PREFIX}sink_failures_total",
        "counter",
        "Sink writes that raised. Counted on every failure, logged once per sink.",
        ("sink",),
    ),
    (
        f"{METRIC_PREFIX}spans_depth_capped_total",
        "counter",
        "Spans created at the nesting cap rather than deepened, by span name.",
        ("span",),
    ),
)


def _health_metric_name(name: str) -> str:
    return f"alpha.{name.removeprefix(METRIC_PREFIX)}"


class TraceMetricsBridge:
    """Adapter from trace records to the existing metrics registries.

    Args:
        ops_registry: An :class:`alpha.ops.metrics.MetricsRegistry`, or
            ``None`` to skip that target. Injected rather than fetched from
            ``get_metrics_registry()`` so a test is isolated from the
            process-wide one and so the bridge never has to mutate a global to
            exist.
        health_registry: An :class:`alpha.memory.health.metrics.MetricsRegistry`
            instance, or ``None``. That registry is designed for injection --
            its own docstring says "no global metric object is created here" --
            so this is a one-to-one fit.
    """

    __slots__ = ("_health_registry", "_ops_registry", "_redaction_reasons", "_sink_names")

    def __init__(self, *, ops_registry: Any = None, health_registry: Any = None) -> None:
        self._ops_registry = ops_registry
        self._health_registry = health_registry
        # Pre-allocated label maps. Reusing dicts is not a micro-optimisation
        # here: it keeps the bridge from ever constructing a label value that
        # is not already in the closed set.
        self._sink_names = {"memory", "file", "null"}
        self._redaction_reasons = set(REDACTION_REASONS)

    @property
    def targets(self) -> tuple[str, ...]:
        """Which registries this bridge is wired to."""
        return tuple(name for name, value in (("ops", self._ops_registry), ("health", self._health_registry)) if value is not None)

    def declare(self) -> None:
        """Declare every bridge metric on the ops registry. Idempotent.

        The ops registry treats an identical redeclaration as a no-op and a
        *conflicting* one as an error, so calling this on every Gateway
        lifespan start is safe and a label-set change fails loudly at startup
        rather than at first scrape.
        """
        if self._ops_registry is None:
            return
        for name, kind, help_text, labels in BRIDGE_METRICS:
            if kind == "counter":
                self._ops_registry.counter(name, help=help_text, labels=labels)
            elif kind == "gauge":
                self._ops_registry.gauge(name, help=help_text, labels=labels)
            else:
                self._ops_registry.histogram(name, help=help_text, labels=labels)

    def observe(self, event: TraceEvent) -> None:
        """Publish one event's counters to every wired registry.

        Safe to call on any event: a name or status outside the closed sets
        cannot exist (the model rejects them), so there is no unknown-label
        path to defend against here.
        """
        if event.name not in EVENT_NAMES or event.status.value not in EVENT_STATUSES:
            return
        self._inc_ops(f"{METRIC_PREFIX}events_total", {"event": event.name, "status": event.status.value})
        self._inc_health(_health_metric_name(f"{METRIC_PREFIX}events_total"), {"event": event.name, "status": event.status.value}, 1)
        if event.name == "run.ended":
            self._inc_ops(f"{METRIC_PREFIX}runs_total", {"status": event.status.value})
            self._inc_health(_health_metric_name(f"{METRIC_PREFIX}runs_total"), {"status": event.status.value}, 1)
        if event.name == "run.context.linked" and event.link_kind in _LINK_KINDS:
            self._inc_ops(f"{METRIC_PREFIX}context_links_total", {"kind": event.link_kind})

    def observe_span(self, record: dict[str, Any]) -> None:
        """Publish one span record's counters.

        Takes the :meth:`Span.to_record` mapping rather than the ``Span``, so
        the bridge works for a span read back out of a trace file as well as for
        a live one.
        """
        name = str(record.get("name", ""))
        if not name:
            return
        status = record.get("status")
        self._inc_ops(f"{METRIC_PREFIX}spans_total", {"span": name, "status": str(status or "open")})
        duration = record.get("duration")
        if duration is not None and self._ops_registry is not None:
            self._ops_registry.histogram(f"{METRIC_PREFIX}span_duration_ms", help=BRIDGE_METRICS[3][2], labels=("span",)).observe(float(duration) * 1000.0, span=name)
        if record.get("depth_capped"):
            self._inc_ops(f"{METRIC_PREFIX}spans_depth_capped_total", {"span": name})

    def observe_redactions(self, reasons: dict[str, int]) -> None:
        """Publish redaction reason counts. Unknown reason codes are ignored."""
        for reason, count in reasons.items():
            if reason not in self._redaction_reasons:
                continue
            self._inc_ops(f"{METRIC_PREFIX}redactions_total", {"reason": reason}, count)
            self._inc_health(_health_metric_name(f"{METRIC_PREFIX}redactions_total"), {"reason": reason}, count)

    def publish_recorder_disclosure(self, recorder: TraceRecorder) -> None:
        """Forward the recorder's honesty counters into the registries.

        This is what makes "the trace on disk is incomplete" visible on the same
        surface as "runs are failing", instead of only to whoever holds the
        file.
        """
        disclosure = recorder.disclosure()
        self._inc_ops(f"{METRIC_PREFIX}runs_sampled_out_total", {}, float(disclosure.get("sampled_out_runs", 0)))
        for sink, sink_disclosure in dict(disclosure.get("sinks", {})).items():
            if sink not in self._sink_names:
                continue
            self._inc_ops(f"{METRIC_PREFIX}sink_dropped_total", {"sink": sink}, float(sink_disclosure.get("dropped_events", 0)))
        for sink, failures in dict(disclosure.get("sink_failures", {})).items():
            if sink not in self._sink_names:
                continue
            self._inc_ops(f"{METRIC_PREFIX}sink_failures_total", {"sink": sink}, float(failures))

    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic, JSON-safe view of what this bridge wired up.

        Used by the test to assert the target set and the declared names
        without depending on either registry's internals.
        """
        declared = [name for name, _kind, _help, _labels in BRIDGE_METRICS]
        return {
            "targets": list(self.targets),
            "declared": declared,
            "health_metrics": [_health_metric_name(name) for name in declared],
        }

    # -- internals ------------------------------------------------------------

    def _inc_ops(self, name: str, labels: dict[str, str], value: float = 1.0) -> None:
        if self._ops_registry is None:
            return
        handle = self._ops_registry.counter(name, help=_help_for(name), labels=_labels_for(name))
        handle.inc(value, **labels)

    def _inc_health(self, name: str, labels: dict[str, str], value: float) -> None:
        if self._health_registry is None:
            return
        self._health_registry.increment(name, value, labels=labels)


_LABEL_FOR: dict[str, tuple[str, ...]] = {name: labels for name, _kind, _help, labels in BRIDGE_METRICS}
_HELP_FOR: dict[str, str] = {name: help_text for name, _kind, help_text, _labels in BRIDGE_METRICS}

#: Boundary kinds the context module can disclose, mirrored so a label value is
#: always drawn from this closed set. Kept as data rather than imported to
#: avoid a package-internal cycle: ``context`` does not import ``events``.
_LINK_KINDS: frozenset[str] = frozenset(
    {
        "asyncio_task",
        "asyncio_to_thread",
        "run_in_executor",
        "thread_pool",
        "thread",
        "event_bus",
        "multiprocessing",
        "subprocess",
    }
)


def _help_for(name: str) -> str:
    return _HELP_FOR.get(name, "Trace-derived counter.")


def _labels_for(name: str) -> tuple[str, ...]:
    return _LABEL_FOR.get(name, ())
