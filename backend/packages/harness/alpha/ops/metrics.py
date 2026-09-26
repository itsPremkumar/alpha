"""Bounded in-process metrics with Prometheus text exposition.

Port of the OpenClaw ``diagnostics-prometheus`` store contract to the
Gateway:

* every metric is declared once (name, type, help, label names). Undeclared
  names and undeclared label keys are refused, which keeps label
  cardinality an explicit decision instead of an accident;
* the registry retains at most ``max_series`` (default 2048, matching
  OpenClaw's cap) label combinations. New series past the cap are refused
  and bump ``alpha_metrics_series_dropped_total`` — itself exempt from the
  cap and rendered only once non-zero, so **absence of the counter means
  zero drops**, exactly like the upstream exporter;
* label values are sanitized to a bounded policy: values over 64 characters
  or containing control characters collapse to ``other`` so a raw
  identifier can never leak into scrape output. Quotes and backslashes are
  escaped at render time;
* :meth:`MetricsRegistry.render_prometheus` emits Prometheus text
  exposition format (``text/plain; version=0.0.4; charset=utf-8``).

Content that never appears here: prompt text, message payloads, task ids,
thread ids, file paths, or secrets — this registry only carries numbers its
own modules recorded under declared names.

Declarations are idempotent: asking for the same name/type/labels again
returns the existing handle (first declaration's help wins); a conflicting
redeclaration raises ``ValueError``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

MAX_SERIES = 2048
SERIES_DROPPED_NAME = "alpha_metrics_series_dropped_total"
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_LABEL_VALUE_MAX = 64
_OTHER = "other"
_METRIC_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

# Anchored when this module is first imported. Gateway routers import it
# eagerly during app creation, so this tracks process startup closely; the
# help text says exactly that rather than claiming to be a process start.
_PROCESS_START_TIME = time.time()


def _validate_metric_name(name: str) -> None:
    if not name or any(ch not in _METRIC_NAME_OK for ch in name) or not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"invalid metric name: {name!r}")


def _sanitize_label_value(value: Any) -> str:
    """Bounded label policy: long or control-character values become ``other``."""
    text = str(value)
    if len(text) > _LABEL_VALUE_MAX:
        return _OTHER
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return _OTHER
    return text


def _escape_label_value(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _escape_help(text: str) -> str:
    cleaned = "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in text)
    return cleaned.replace("\\", "\\\\")


def _format_float(value: float) -> str:
    return f"{value:.12g}"


class _Declaration:
    __slots__ = ("name", "metric_type", "help", "labels", "buckets")

    def __init__(
        self,
        name: str,
        metric_type: str,
        help_text: str,
        labels: tuple[str, ...],
        buckets: tuple[float, ...] | None,
    ) -> None:
        self.name = name
        self.metric_type = metric_type
        self.help = help_text
        self.labels = labels
        self.buckets = buckets


class MetricHandle:
    """Type-checked view over one declared metric name."""

    def __init__(self, registry: MetricsRegistry, name: str) -> None:
        self._registry = registry
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        self._registry._inc(self._name, value, labels)

    def set(self, value: float, **labels: Any) -> None:
        self._registry._set(self._name, value, labels)

    def observe(self, value: float, **labels: Any) -> None:
        self._registry._observe(self._name, value, labels)

    def remove(self, **labels: Any) -> None:
        self._registry._remove(self._name, labels)

    def remove_all(self) -> None:
        self._registry._remove(self._name, None)


class MetricsRegistry:
    """Thread-safe bounded metric store."""

    def __init__(self, *, max_series: int = MAX_SERIES) -> None:
        if max_series < 1:
            raise ValueError("max_series must be at least 1")
        self._max_series = max_series
        self._lock = threading.RLock()
        self._declared: dict[str, _Declaration] = {}
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[list[int], float, int]] = {}

    # -- declaration -------------------------------------------------

    def counter(self, name: str, *, help: str, labels: tuple[str, ...] = ()) -> MetricHandle:
        return self._declare(name, "counter", help, labels, None)

    def gauge(self, name: str, *, help: str, labels: tuple[str, ...] = ()) -> MetricHandle:
        return self._declare(name, "gauge", help, labels, None)

    def histogram(
        self,
        name: str,
        *,
        help: str,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> MetricHandle:
        if not buckets or any(b <= 0 for b in buckets) or list(buckets) != sorted(buckets):
            raise ValueError("histogram buckets must be positive and sorted")
        return self._declare(name, "histogram", help, labels, tuple(float(b) for b in buckets))

    def _declare(
        self,
        name: str,
        metric_type: str,
        help_text: str,
        labels: tuple[str, ...],
        buckets: tuple[float, ...] | None,
    ) -> MetricHandle:
        _validate_metric_name(name)
        for label in labels:
            _validate_metric_name(label)
        if len(set(labels)) != len(labels):
            raise ValueError(f"duplicate label names on {name!r}")
        with self._lock:
            existing = self._declared.get(name)
            if existing is not None:
                if existing.metric_type != metric_type or existing.labels != labels:
                    raise ValueError(f"metric {name!r} already declared with a different type or labels")
                return MetricHandle(self, name)
            self._declared[name] = _Declaration(name, metric_type, help_text, labels, buckets)
            return MetricHandle(self, name)

    # -- mutation ----------------------------------------------------

    def _series_count(self) -> int:
        return len(self._counters) + len(self._gauges) + len(self._histograms)

    def _key(self, name: str, labels: dict[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
        decl = self._declared[name]
        unknown = set(labels) - set(decl.labels)
        if unknown:
            raise ValueError(f"undeclared label(s) {sorted(unknown)} on metric {name!r}")
        ordered = tuple((label, _sanitize_label_value(labels.get(label, ""))) for label in decl.labels)
        return (name, ordered)

    def _admit(self, key: tuple[str, tuple[tuple[str, str], ...]]) -> bool:
        """Reserve a slot for a new series; False when the cap refuses it."""
        if key[0] == SERIES_DROPPED_NAME:
            return True
        if key in self._counters or key in self._gauges or key in self._histograms:
            return True
        if self._series_count() >= self._max_series:
            self._bump_dropped()
            return False
        return True

    def _bump_dropped(self) -> None:
        name = SERIES_DROPPED_NAME
        if name not in self._declared:
            self._declared[name] = _Declaration(name, "counter", "New metric series refused because the registry series cap was reached (absent = zero drops).", (), None)
        key = (name, ())
        self._counters[key] = self._counters.get(key, 0.0) + 1.0

    def _inc(self, name: str, value: float, labels: dict[str, Any]) -> None:
        with self._lock:
            decl = self._declared[name]
            if decl.metric_type not in ("counter", "gauge"):
                raise ValueError(f"metric {name!r} is a {decl.metric_type}; inc() requires counter or gauge")
            if decl.metric_type == "counter" and value < 0:
                raise ValueError("counters only grow; use a gauge for decreasing values")
            key = self._key(name, labels)
            if not self._admit(key):
                return
            store = self._counters if decl.metric_type == "counter" else self._gauges
            store[key] = store.get(key, 0.0) + float(value)

    def _set(self, name: str, value: float, labels: dict[str, Any]) -> None:
        with self._lock:
            decl = self._declared[name]
            if decl.metric_type != "gauge":
                raise ValueError(f"metric {name!r} is a {decl.metric_type}; set() requires a gauge")
            key = self._key(name, labels)
            if not self._admit(key):
                return
            self._gauges[key] = float(value)

    def _observe(self, name: str, value: float, labels: dict[str, Any]) -> None:
        with self._lock:
            decl = self._declared[name]
            if decl.metric_type != "histogram":
                raise ValueError(f"metric {name!r} is a {decl.metric_type}; observe() requires a histogram")
            key = self._key(name, labels)
            if key not in self._histograms and not self._admit(key):
                return
            counts, total, count = self._histograms.get(key, ([0] * len(decl.buckets or ()), 0.0, 0))
            buckets = decl.buckets or ()
            for index, bound in enumerate(buckets):
                if float(value) <= bound:
                    counts[index] += 1
            self._histograms[key] = (counts, total + float(value), count + 1)

    def _remove(self, name: str, labels: dict[str, Any] | None) -> None:
        with self._lock:
            if labels is None:
                for store in (self._counters, self._gauges, self._histograms):
                    for key in [k for k in store if k[0] == name]:
                        del store[key]
                return
            key = self._key(name, labels)
            self._counters.pop(key, None)
            self._gauges.pop(key, None)
            self._histograms.pop(key, None)

    # -- rendering ---------------------------------------------------

    def render_prometheus(self) -> str:
        """Render the registry as Prometheus text exposition format."""
        with self._lock:
            lines: list[str] = []
            for name, decl in self._declared.items():
                if not self._series_exists(name):
                    # Declared but currently has no series: absent from the
                    # exposition entirely (help/type lines with no samples
                    # would leak the name while carrying no value). This is
                    # what lets remove_all() express "no signal" honestly,
                    # and it subsumes the drop-counter "absent = zero drops"
                    # rule — that counter is rendered once it is bumped.
                    continue
                lines.append(f"# HELP {name} {_escape_help(decl.help)}")
                lines.append(f"# TYPE {name} {decl.metric_type}")
                if decl.metric_type in ("counter", "gauge"):
                    store = self._counters if decl.metric_type == "counter" else self._gauges
                    for key, value in sorted(store.items(), key=lambda item: item[0]):
                        if key[0] != name:
                            continue
                        lines.append(f"{name}{_render_labels(decl.labels, key[1])} {_format_float(value)}")
                else:
                    buckets = decl.buckets or ()
                    for key, (counts, total, count) in sorted(self._histograms.items(), key=lambda item: item[0]):
                        if key[0] != name:
                            continue
                        for index, bound in enumerate(buckets):
                            lines.append(f'{name}_bucket{_render_labels(decl.labels, key[1], le=_format_float(bound))} {counts[index]}')
                        lines.append(f'{name}_bucket{_render_labels(decl.labels, key[1], le="+Inf")} {count}')
                        lines.append(f"{name}_sum{_render_labels(decl.labels, key[1])} {_format_float(total)}")
                        lines.append(f"{name}_count{_render_labels(decl.labels, key[1])} {count}")
            return "\n".join(lines) + ("\n" if lines else "")

    def _series_exists(self, name: str) -> bool:
        return any(k[0] == name for k in (*self._counters, *self._gauges, *self._histograms))

    # -- introspection (tests/dashboards) ----------------------------

    def series_count(self) -> int:
        with self._lock:
            return self._series_count()


def _render_labels(label_names: tuple[str, ...], key_values: tuple[tuple[str, str], ...], **extra: str) -> str:
    parts = [f'{name}="{_escape_label_value(value)}"' for name, value in key_values]
    parts.extend(f'{name}="{_escape_label_value(value)}"' for name, value in extra.items())
    if not parts:
        return ""
    return "{" + ",".join(parts) + "}"


_REGISTRY = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    """Process-wide registry used by the ops surfaces."""
    return _REGISTRY


def publish_process_metrics(registry: MetricsRegistry | None = None) -> None:
    """Refresh process-level gauges (uptime) into the registry."""
    target = registry if registry is not None else _REGISTRY
    target.gauge(
        "alpha_process_uptime_seconds",
        help="Seconds since the ops metrics module was imported during Gateway app creation (approximates process start).",
    ).set(time.time() - _PROCESS_START_TIME)
