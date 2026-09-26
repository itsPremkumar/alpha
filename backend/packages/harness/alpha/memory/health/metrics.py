"""Small explicit in-process metrics registry.

No global metric object is created here.  A host constructs one
``MetricsRegistry`` and passes that instance to the code it wants to observe.
Histograms use a bounded FIFO reservoir: once full, each new observation is
not retained and ``dropped_samples``/``overflowed`` disclose that loss.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from .models import MetricSample, MetricSeries
from .probes import Clock

MetricKind = str
_LABELS = dict[str, str]
_MISSING = object()


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def percentile(values: Iterable[float], quantile: float) -> float:
    """Return a linearly interpolated percentile of ``values``.

    The empirical CDF is represented by sorting the finite observations and
    interpolating between the two neighbouring ranks at
    ``(n - 1) * quantile``.  This is the same method used by
    :class:`LatencyProbe`; it is intentionally explicit rather than relying on
    a third-party percentile convention.  Empty input and quantiles outside
    ``[0, 1]`` are programming errors and raise ``ValueError``.
    """

    if not math.isfinite(float(quantile)) or not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be within [0, 1]")
    ordered: list[float] = []
    for value in values:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("percentile values must be finite")
        ordered.append(numeric)
    if not ordered:
        raise ValueError("percentile of an empty sequence")
    ordered.sort()
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


class _MetricState:
    __slots__ = ("kind", "counter", "gauge", "samples", "dropped_samples")

    def __init__(self, kind: MetricKind, reservoir_size: int) -> None:
        self.kind = kind
        self.counter = 0.0
        self.gauge: float | None = None
        self.samples: deque[MetricSample] = deque(maxlen=reservoir_size)
        self.dropped_samples = 0


class MetricsRegistry:
    """Thread-safe counter, gauge, and bounded histogram collection."""

    def __init__(
        self,
        reservoir_size: int = 256,
        *,
        clock: Clock | None = None,
        window: str | int | float | None = None,
    ) -> None:
        if not isinstance(reservoir_size, int) or isinstance(reservoir_size, bool) or reservoir_size < 1:
            raise ValueError("reservoir_size must be a positive integer")
        self._reservoir_size = reservoir_size
        self._clock = clock
        self._window = window
        self._states: dict[tuple[str, tuple[tuple[str, str], ...]], _MetricState] = {}
        self._lock = threading.RLock()

    @property
    def reservoir_size(self) -> int:
        return self._reservoir_size

    @property
    def window(self) -> str | int | float | None:
        return self._window

    @staticmethod
    def _normalize_labels(labels: Mapping[str, object] | None) -> _LABELS:
        if labels is None:
            return {}
        return {str(key): str(value) for key, value in sorted(labels.items(), key=lambda pair: str(pair[0]))}

    @staticmethod
    def _validate_name(name: str) -> str:
        cleaned = str(name).strip()
        if not cleaned:
            raise ValueError("metric name must not be empty")
        return cleaned

    def _observed_at(self, observed_at: object | None) -> datetime | None:
        if observed_at is not None:
            return _timestamp(observed_at)
        if self._clock is None:
            return None
        try:
            raw_clock = self._clock() if callable(self._clock) else self._clock
            return _timestamp(raw_clock)
        except Exception:  # noqa: BLE001 - a bad injected clock discloses as missing time
            return None

    def _state(self, name: str, labels: Mapping[str, object] | None, kind: MetricKind) -> tuple[tuple[str, tuple[tuple[str, str], ...]], _MetricState]:
        clean_name = self._validate_name(name)
        normalized = self._normalize_labels(labels)
        key = (clean_name, tuple(normalized.items()))
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _MetricState(kind, self._reservoir_size)
                self._states[key] = state
            elif state.kind != kind:
                raise ValueError(f"metric {clean_name!r} is already registered as {state.kind}, not {kind}")
            return key, state

    def _sample(
        self,
        name: str,
        value: float,
        unit: str,
        labels: Mapping[str, object] | None,
        observed_at: object | None,
    ) -> MetricSample:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("metric value must be finite")
        return MetricSample(
            name=self._validate_name(name),
            value=numeric,
            unit=str(unit),
            labels=self._normalize_labels(labels),
            observed_at=self._observed_at(observed_at),
        )

    @staticmethod
    def _append_sample(state: _MetricState, sample: MetricSample) -> None:
        if len(state.samples) >= state.samples.maxlen:
            state.dropped_samples += 1
        state.samples.append(sample)

    def increment(
        self,
        name: str,
        value: float | int = 1,
        *,
        unit: str = "",
        labels: Mapping[str, object] | None = None,
        observed_at: object | None = None,
    ) -> MetricSample:
        """Increment a counter and return the observed sample."""

        if isinstance(value, bool):
            raise ValueError("counter increment must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("counter increment must be finite")
        sample = self._sample(name, numeric, unit, labels, observed_at)
        _, state = self._state(name, labels, "counter")
        with self._lock:
            state.counter += numeric
            self._append_sample(state, sample)
        return sample

    def inc(self, name: str, value: float | int = 1, **kwargs: Any) -> MetricSample:
        """Short alias for :meth:`increment`."""

        return self.increment(name, value, **kwargs)

    def counter(
        self,
        name: str,
        value: float | int | object = _MISSING,
        *,
        unit: str = "",
        labels: Mapping[str, object] | None = None,
        observed_at: object | None = None,
    ) -> float | None | MetricSample:
        """Read a counter, or increment it when ``value`` is supplied.

        The dual shape keeps the tiny registry ergonomic for both common
        styles: ``counter("requests")`` reads and ``counter("requests", 1)``
        records.  A missing read is ``None`` rather than a fabricated zero.
        """

        if value is _MISSING:
            key = (self._validate_name(name), tuple(self._normalize_labels(labels).items()))
            with self._lock:
                state = self._states.get(key)
            return state.counter if state is not None and state.kind == "counter" else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("counter increment must be numeric")
        return self.increment(name, value, unit=unit, labels=labels, observed_at=observed_at)

    def counter_value(self, name: str, *, labels: Mapping[str, object] | None = None) -> float | None:
        """Explicit getter spelling for callers that do not use dual semantics."""

        return self.counter(name, labels=labels)

    def set_gauge(
        self,
        name: str,
        value: float | int,
        *,
        unit: str = "",
        labels: Mapping[str, object] | None = None,
        observed_at: object | None = None,
    ) -> MetricSample:
        """Set a gauge and return the observed sample."""

        if isinstance(value, bool):
            raise ValueError("gauge value must be numeric")
        sample = self._sample(name, value, unit, labels, observed_at)
        _, state = self._state(name, labels, "gauge")
        with self._lock:
            state.gauge = sample.value
            self._append_sample(state, sample)
        return sample

    def gauge(
        self,
        name: str,
        value: float | int | object = _MISSING,
        **kwargs: Any,
    ) -> MetricSample | float | None:
        """Set a gauge when ``value`` is supplied, otherwise read it."""

        if value is _MISSING:
            return self.gauge_value(name, labels=kwargs.get("labels"))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("gauge value must be numeric")
        return self.set_gauge(name, value, **kwargs)

    def gauge_value(self, name: str, *, labels: Mapping[str, object] | None = None) -> float | None:
        """Read a gauge, returning ``None`` when no observation exists."""

        key = (self._validate_name(name), tuple(self._normalize_labels(labels).items()))
        with self._lock:
            state = self._states.get(key)
            if state is None or state.kind != "gauge":
                return None
            return state.gauge

    def observe(
        self,
        name: str,
        value: float | int,
        *,
        unit: str = "",
        labels: Mapping[str, object] | None = None,
        observed_at: object | None = None,
    ) -> MetricSample:
        """Observe a histogram value, disclosing every overflow drop."""

        if isinstance(value, bool):
            raise ValueError("histogram value must be numeric")
        sample = self._sample(name, value, unit, labels, observed_at)
        _, state = self._state(name, labels, "histogram")
        with self._lock:
            self._append_sample(state, sample)
        return sample

    def histogram(self, name: str, value: float | int, **kwargs: Any) -> MetricSample:
        """Alias for :meth:`observe`."""

        return self.observe(name, value, **kwargs)

    def record_sample(self, sample: MetricSample) -> MetricSample:
        """Record an already typed sample in the appropriate metric kind.

        This is useful when an adapter has a trusted observation timestamp and
        does not want the registry to consult its clock.
        """

        if not isinstance(sample, MetricSample):
            raise TypeError("record_sample expects MetricSample")
        if sample.name not in self._metric_names_for_kind("counter") and sample.name not in self._metric_names_for_kind("gauge"):
            return self.observe(
                sample.name,
                sample.value,
                unit=sample.unit,
                labels=sample.labels,
                observed_at=sample.observed_at,
            )
        labels = sample.labels
        key = (sample.name, tuple(self._normalize_labels(labels).items()))
        with self._lock:
            state = self._states.get(key)
        if state is None:
            return self.observe(
                sample.name,
                sample.value,
                unit=sample.unit,
                labels=labels,
                observed_at=sample.observed_at,
            )
        if state.kind == "counter":
            return self.increment(
                sample.name,
                sample.value,
                unit=sample.unit,
                labels=labels,
                observed_at=sample.observed_at,
            )
        return self.set_gauge(
            sample.name,
            sample.value,
            unit=sample.unit,
            labels=labels,
            observed_at=sample.observed_at,
        )

    def series(
        self,
        name: str,
        *,
        labels: Mapping[str, object] | None = None,
        window: str | int | float | None = None,
    ) -> MetricSeries:
        """Return a defensive series snapshot, empty when no data exists."""

        clean_name = self._validate_name(name)
        normalized = self._normalize_labels(labels)
        with self._lock:
            if normalized:
                keys = [(clean_name, tuple(normalized.items()))]
            else:
                keys = [key for key in self._states if key[0] == clean_name]
            states = [self._states[key] for key in sorted(keys) if key in self._states]
            if not states:
                return MetricSeries(name=clean_name, samples=(), window=self._window if window is None else window)
            samples: list[MetricSample] = []
            dropped = 0
            for state in states:
                samples.extend(sample.model_copy(deep=True) for sample in state.samples)
                dropped += state.dropped_samples
        samples.sort(key=lambda sample: (sample.observed_at is None, sample.observed_at or datetime.min.replace(tzinfo=UTC)))
        return MetricSeries(
            name=clean_name,
            samples=tuple(samples),
            window=self._window if window is None else window,
            dropped_samples=dropped,
            overflowed=dropped > 0,
        )

    def percentile(self, name: str, quantile: float, *, labels: Mapping[str, object] | None = None) -> float | None:
        """Compute a percentile from retained histogram samples, or ``None``."""

        series = self.series(name, labels=labels)
        if not series.samples:
            return None
        return percentile(series.values, quantile)

    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible metrics snapshot."""

        with self._lock:
            names = sorted({key[0] for key in self._states})
            result: dict[str, Any] = {}
            for name in names:
                keys = sorted(key for key in self._states if key[0] == name)
                states: list[dict[str, Any]] = []
                for key in keys:
                    state = self._states[key]
                    states.append(
                        {
                            "kind": state.kind,
                            "labels": dict(key[1]),
                            "counter": state.counter if state.kind == "counter" else None,
                            "gauge": state.gauge if state.kind == "gauge" else None,
                            "samples": [sample.model_dump(mode="json") for sample in state.samples],
                            "dropped_samples": state.dropped_samples,
                            "overflowed": state.dropped_samples > 0,
                        }
                    )
                result[name] = states
        return {
            "schema": 1,
            "reservoir_size": self._reservoir_size,
            "window": self._window,
            "metrics": result,
        }

    def export(self) -> dict[str, Any]:
        """Alias for :meth:`snapshot` used by persistence adapters."""

        return self.snapshot()

    def reset(self) -> None:
        """Drop all metric state; the explicit registry remains reusable."""

        with self._lock:
            self._states.clear()

    def _metric_names_for_kind(self, kind: str) -> set[str]:
        with self._lock:
            return {key[0] for key, state in self._states.items() if state.kind == kind}

    def __len__(self) -> int:
        with self._lock:
            return len({key[0] for key in self._states})


__all__ = ["MetricsRegistry", "MetricKind", "percentile"]
