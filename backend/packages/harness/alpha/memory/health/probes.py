"""Honest, dependency-injected health probes.

A probe is deliberately a very small seam.  It knows how to turn one
injected observation into a :class:`ComponentHealth`; it does not discover or
construct a memory backend.  This keeps the health plane independent of every
memory implementation and makes missing collaborators visible in the report.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from .models import ComponentHealth

Clock = Callable[[], datetime | float | int | str]
CountCallable = Callable[[], int | float]
SampleSupplier = Callable[[], Iterable[float | int]]


@runtime_checkable
class Probe(Protocol):
    """Protocol implemented by all health probes."""

    name: str

    def check(self) -> ComponentHealth:
        """Return one health record without raising for dependency failures."""


def _timestamp(value: object) -> datetime | None:
    """Convert an injected timestamp-like value without consulting a clock."""

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
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _checked_at(clock: Clock | None) -> datetime | None:
    if clock is None:
        return None
    try:
        raw_clock = clock() if callable(clock) else clock
        return _timestamp(raw_clock)
    except Exception:  # noqa: BLE001 - a missing/bad clock is an unavailable probe
        return None


def _error_reason(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip()
    suffix = f": {detail}" if detail else ""
    return f"{prefix}: {type(exc).__name__}{suffix}"


def _unavailable(
    name: str,
    reason: str,
    *,
    clock: Clock | None = None,
    evidence: dict[str, Any] | None = None,
) -> ComponentHealth:
    return ComponentHealth(
        name=name,
        state="unavailable",
        reason=reason,
        checked_at=_checked_at(clock),
        evidence=evidence or {},
    )


def _unknown(
    name: str,
    reason: str,
    *,
    clock: Clock | None = None,
    evidence: dict[str, Any] | None = None,
) -> ComponentHealth:
    return ComponentHealth(
        name=name,
        state="unknown",
        reason=reason,
        checked_at=_checked_at(clock),
        evidence=evidence or {},
    )


def _coerce_health(name: str, value: object, *, clock: Clock | None) -> ComponentHealth:
    if not isinstance(value, ComponentHealth):
        return _unavailable(
            name,
            f"probe returned {type(value).__name__}; expected ComponentHealth",
            clock=clock,
            evidence={"returned_type": type(value).__name__},
        )
    checked_at = value.checked_at if value.checked_at is not None else _checked_at(clock)
    return value.model_copy(deep=True, update={"name": name, "checked_at": checked_at})


class _BaseProbe:
    """Common naming and error behavior for injected probes."""

    def __init__(self, name: str, *, clock: Clock | None = None) -> None:
        cleaned = str(name).strip()
        if not cleaned:
            raise ValueError("probe name must not be empty")
        self.name = cleaned
        self._clock = clock

    def _now(self) -> datetime | None:
        return _checked_at(self._clock)

    def probe(self) -> ComponentHealth:
        """Alias for :meth:`check` for small probe adapters."""

        return self.check()

    def run(self) -> ComponentHealth:
        """Alias for :meth:`check` used by a few host probe adapters."""

        return self.check()

    def check(self) -> ComponentHealth:  # pragma: no cover - protocol implementation base
        raise TypeError(f"{type(self).__name__} must implement check()")


class CallableProbe(_BaseProbe):
    """Wrap a user callback that returns a health record or raises.

    The callback is intentionally not imported or discovered.  A callback
    failure becomes an ``unavailable`` record with the exception type and
    message, so a caller's turn cannot be taken down by observability itself.
    """

    def __init__(
        self,
        name: str | Callable[[], ComponentHealth],
        callback: Callable[[], ComponentHealth] | None = None,
        *,
        func: Callable[[], ComponentHealth] | None = None,
        clock: Clock | None = None,
    ) -> None:
        if callable(name) and callback is None and func is None:
            callback = name
            name = getattr(callback, "__name__", "callable")
        if callback is not None and func is not None:
            raise TypeError("provide callback or func, not both")
        selected = callback if callback is not None else func
        if selected is None:
            raise TypeError("CallableProbe requires a callback")
        if not callable(selected):
            raise TypeError("CallableProbe callback must be callable")
        super().__init__(str(name), clock=clock)
        self._callback = selected

    def check(self) -> ComponentHealth:
        try:
            result = self._callback()
        except Exception as exc:  # noqa: BLE001 - dependency errors are data here
            return _unavailable(
                self.name,
                _error_reason("callable probe failed", exc),
                clock=self._clock,
                evidence={"exception_type": type(exc).__name__},
            )
        return _coerce_health(self.name, result, clock=self._clock)


class StoreCountProbe(_BaseProbe):
    """Check an injected ``count()`` callable.

    A missing or raising dependency is unavailable and carries no numeric
    value in its evidence.  A legitimate count of zero remains a healthy
    observation; it is not confused with dependency absence.
    """

    def __init__(
        self,
        name: str = "store_count",
        count: CountCallable | Any | None = None,
        *,
        count_fn: CountCallable | None = None,
        clock: Clock | None = None,
    ) -> None:
        if count is not None and count_fn is not None:
            raise TypeError("provide count or count_fn, not both")
        selected = count_fn if count_fn is not None else count
        if selected is not None and not callable(selected) and callable(getattr(selected, "count", None)):
            selected = selected.count
        super().__init__(str(name), clock=clock)
        self._count = selected

    def check(self) -> ComponentHealth:
        if self._count is None or not callable(self._count):
            return _unavailable(
                self.name,
                "store count dependency is missing",
                clock=self._clock,
                evidence={"dependency": "count"},
            )
        try:
            value = self._count()
        except Exception as exc:  # noqa: BLE001 - missing store is not a green probe
            return _unavailable(
                self.name,
                _error_reason("store count dependency failed", exc),
                clock=self._clock,
                evidence={"dependency": "count", "exception_type": type(exc).__name__},
            )
        if isinstance(value, bool):
            return _unavailable(
                self.name,
                "store count returned a boolean instead of a number",
                clock=self._clock,
                evidence={"returned_type": type(value).__name__},
            )
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return _unavailable(
                self.name,
                "store count returned a non-numeric value",
                clock=self._clock,
                evidence={"returned_type": type(value).__name__},
            )
        if not math.isfinite(numeric) or numeric < 0:
            return _unavailable(
                self.name,
                "store count returned an invalid count",
                clock=self._clock,
                evidence={"returned_value": repr(value)},
            )
        return ComponentHealth(
            name=self.name,
            state="healthy",
            reason="count obtained",
            checked_at=self._now(),
            evidence={"count": numeric},
        )


class LatencyProbe(_BaseProbe):
    """Summarize injected latency samples without fabricating measurements."""

    def __init__(
        self,
        name: str = "latency",
        samples: Sequence[float | int] | SampleSupplier | None = None,
        *,
        threshold: float | None = None,
        max_latency_ms: float | None = None,
        max_allowed_ms: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        supplied_thresholds = [item for item in (threshold, max_latency_ms, max_allowed_ms) if item is not None]
        if len(supplied_thresholds) > 1:
            raise TypeError("provide only one latency threshold")
        selected_threshold = supplied_thresholds[0] if supplied_thresholds else None
        if selected_threshold is not None:
            selected_threshold = float(selected_threshold)
            if not math.isfinite(selected_threshold) or selected_threshold < 0:
                raise ValueError("latency threshold must be a finite non-negative number")
        super().__init__(str(name), clock=clock)
        self._samples = samples
        self._threshold = selected_threshold

    def _resolve_samples(self) -> Iterable[float | int]:
        if callable(self._samples):
            return self._samples()
        if self._samples is None:
            return ()
        return self._samples

    def check(self) -> ComponentHealth:
        if self._samples is None:
            return _unavailable(
                self.name,
                "latency sample dependency is missing",
                clock=self._clock,
                evidence={"dependency": "samples"},
            )
        try:
            raw_samples = list(self._resolve_samples())
        except Exception as exc:  # noqa: BLE001 - sampler failure is unavailable
            return _unavailable(
                self.name,
                _error_reason("latency sample dependency failed", exc),
                clock=self._clock,
                evidence={"dependency": "samples", "exception_type": type(exc).__name__},
            )
        if not raw_samples:
            return _unknown(
                self.name,
                "no latency samples were supplied",
                clock=self._clock,
                evidence={"sample_count": 0},
            )
        values: list[float] = []
        for sample in raw_samples:
            if isinstance(sample, bool):
                return _unavailable(
                    self.name,
                    "latency samples contain a boolean",
                    clock=self._clock,
                    evidence={"returned_type": type(sample).__name__},
                )
            try:
                numeric = float(sample)
            except (TypeError, ValueError):
                return _unavailable(
                    self.name,
                    "latency samples contain a non-numeric value",
                    clock=self._clock,
                    evidence={"returned_type": type(sample).__name__},
                )
            if not math.isfinite(numeric) or numeric < 0:
                return _unavailable(
                    self.name,
                    "latency samples contain an invalid value",
                    clock=self._clock,
                    evidence={"returned_value": repr(sample)},
                )
            values.append(numeric)
        maximum = max(values)
        ordered = sorted(values)
        # Linear interpolation on the empirical CDF; documented in metrics.py
        # and repeated here so the probe's evidence has a stated method.
        position = (len(ordered) - 1) * 0.95
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        p95 = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
        evidence: dict[str, Any] = {
            "sample_count": len(values),
            "max_ms": maximum,
            "p95_ms": p95,
        }
        if self._threshold is not None:
            evidence["threshold_ms"] = self._threshold
        state = "healthy" if self._threshold is None or maximum <= self._threshold else "degraded"
        reason = "latency within threshold" if state == "healthy" else "latency exceeds threshold"
        return ComponentHealth(
            name=self.name,
            state=state,
            reason=reason,
            checked_at=self._now(),
            evidence=evidence,
        )


class FreshnessProbe(_BaseProbe):
    """Compare an injected event timestamp with an explicitly injected clock.

    There is intentionally no ambient wall-clock fallback.  If either
    collaborator is absent or invalid, the result is unavailable with that
    fact disclosed.
    """

    def __init__(
        self,
        timestamp: datetime | float | int | str | Callable[[], datetime | float | int | str] | None = None,
        clock: Clock | None = None,
        *third_positional: Clock | None,
        name: str = "freshness",
        warn_after_seconds: float = 300.0,
        critical_after_seconds: float = 3600.0,
        warning_after_seconds: float | None = None,
        critical_after: float | None = None,
        max_age_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        threshold_seconds: float | None = None,
    ) -> None:
        if third_positional:
            if len(third_positional) != 1:
                raise TypeError("FreshnessProbe accepts at most three positional dependencies")
            name, timestamp, clock = str(timestamp), clock, third_positional[0]
        if warning_after_seconds is not None:
            warn_after_seconds = warning_after_seconds
        if critical_after is not None:
            critical_after_seconds = critical_after
        if max_age_seconds is not None:
            warn_after_seconds = max_age_seconds
        if stale_after_seconds is not None:
            critical_after_seconds = stale_after_seconds
        if threshold_seconds is not None:
            warn_after_seconds = threshold_seconds
        warn_after_seconds = float(warn_after_seconds)
        critical_after_seconds = float(critical_after_seconds)
        if not math.isfinite(warn_after_seconds) or warn_after_seconds < 0:
            raise ValueError("warn_after_seconds must be a finite non-negative number")
        if not math.isfinite(critical_after_seconds) or critical_after_seconds < warn_after_seconds:
            raise ValueError("critical_after_seconds must be finite and at least the warning threshold")
        super().__init__(str(name), clock=clock)
        self._timestamp = timestamp
        self._warn_after_seconds = warn_after_seconds
        self._critical_after_seconds = critical_after_seconds

    def _resolve_timestamp(self) -> object:
        if callable(self._timestamp):
            return self._timestamp()
        return self._timestamp

    def check(self) -> ComponentHealth:
        if self._timestamp is None:
            return _unavailable(
                self.name,
                "freshness timestamp dependency is missing",
                clock=self._clock,
                evidence={"dependency": "timestamp"},
            )
        if self._clock is None:
            return _unavailable(
                self.name,
                "freshness clock dependency is missing; no implicit clock is used",
                evidence={"dependency": "clock"},
            )
        try:
            raw_timestamp = self._resolve_timestamp()
        except Exception as exc:  # noqa: BLE001 - timestamp provider failure is data
            return _unavailable(
                self.name,
                _error_reason("freshness timestamp dependency failed", exc),
                clock=self._clock,
                evidence={"dependency": "timestamp", "exception_type": type(exc).__name__},
            )
        observed = _timestamp(raw_timestamp)
        current = _checked_at(self._clock)
        if observed is None or current is None:
            missing = "timestamp" if observed is None else "clock"
            return _unavailable(
                self.name,
                f"freshness {missing} is missing or invalid",
                clock=self._clock,
                evidence={"dependency": missing},
            )
        age = (current - observed).total_seconds()
        if age < 0:
            return ComponentHealth(
                name=self.name,
                state="degraded",
                reason="freshness timestamp is in the future",
                checked_at=current,
                evidence={
                    "age_seconds": age,
                    "observed_at": observed.isoformat(),
                    "warning_after_seconds": self._warn_after_seconds,
                    "critical_after_seconds": self._critical_after_seconds,
                },
            )
        if age >= self._critical_after_seconds:
            state = "unavailable"
            reason = "timestamp is critically stale"
        elif age >= self._warn_after_seconds:
            state = "degraded"
            reason = "timestamp is stale"
        else:
            state = "healthy"
            reason = "timestamp is fresh"
        return ComponentHealth(
            name=self.name,
            state=state,
            reason=reason,
            checked_at=current,
            evidence={
                "age_seconds": age,
                "observed_at": observed.isoformat(),
                "warning_after_seconds": self._warn_after_seconds,
                "critical_after_seconds": self._critical_after_seconds,
                "critical": state == "unavailable",
            },
        )


class QuotaProbe(_BaseProbe):
    """Check injected quota usage against a disclosed degradation ratio."""

    def __init__(
        self,
        name: str = "quota",
        used: float | int | Callable[[], float | int] | None = None,
        limit: float | int | Callable[[], float | int] | None = None,
        *,
        degraded_at: float = 0.8,
        threshold: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        selected_threshold = threshold if threshold is not None else degraded_at
        selected_threshold = float(selected_threshold)
        if not math.isfinite(selected_threshold) or not 0 < selected_threshold <= 1:
            raise ValueError("quota degradation threshold must be in (0, 1]")
        super().__init__(str(name), clock=clock)
        self._used = used
        self._limit = limit
        self._degraded_at = selected_threshold

    def _resolve(self, value: object) -> object:
        if callable(value):
            return value()
        return value

    def check(self) -> ComponentHealth:
        if self._used is None or self._limit is None:
            missing = "used" if self._used is None else "limit"
            return _unavailable(
                self.name,
                f"quota {missing} dependency is missing",
                clock=self._clock,
                evidence={"dependency": missing},
            )
        try:
            raw_used = self._resolve(self._used)
            raw_limit = self._resolve(self._limit)
        except Exception as exc:  # noqa: BLE001 - quota provider failure is data
            return _unavailable(
                self.name,
                _error_reason("quota dependency failed", exc),
                clock=self._clock,
                evidence={"exception_type": type(exc).__name__},
            )
        if isinstance(raw_used, bool) or isinstance(raw_limit, bool):
            return _unavailable(
                self.name,
                "quota values must be numeric",
                clock=self._clock,
                evidence={"returned_type": type(raw_used if isinstance(raw_used, bool) else raw_limit).__name__},
            )
        try:
            numeric_used = float(raw_used)
            numeric_limit = float(raw_limit)
        except (TypeError, ValueError):
            return _unavailable(
                self.name,
                "quota values must be numeric",
                clock=self._clock,
                evidence={"used_type": type(raw_used).__name__, "limit_type": type(raw_limit).__name__},
            )
        if not math.isfinite(numeric_used) or not math.isfinite(numeric_limit) or numeric_used < 0 or numeric_limit <= 0:
            return _unavailable(
                self.name,
                "quota values are invalid",
                clock=self._clock,
                evidence={"used": repr(raw_used), "limit": repr(raw_limit)},
            )
        ratio = numeric_used / numeric_limit
        degraded = ratio >= self._degraded_at
        return ComponentHealth(
            name=self.name,
            state="degraded" if degraded else "healthy",
            reason="quota threshold reached" if degraded else "quota within threshold",
            checked_at=self._now(),
            evidence={
                "used": numeric_used,
                "limit": numeric_limit,
                "ratio": ratio,
                "degraded_at": self._degraded_at,
            },
        )


__all__ = [
    "CallableProbe",
    "Clock",
    "FreshnessProbe",
    "LatencyProbe",
    "Probe",
    "QuotaProbe",
    "StoreCountProbe",
]
