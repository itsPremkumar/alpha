"""SLO evaluation with explicit data sufficiency and burn-rate guards."""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import SLO, Alert, MetricSeries, SLOEvaluation, SLOTarget
from .probes import Clock

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*$", re.IGNORECASE)
_DURATION_FACTORS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _now(clock: Clock | None) -> datetime | None:
    if clock is None:
        return None
    try:
        value = clock() if callable(clock) else clock
    except Exception:  # noqa: BLE001 - an invalid clock means no fabricated fire time
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
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


def _duration_seconds(window: object) -> float | None:
    if isinstance(window, timedelta):
        return window.total_seconds() if window.total_seconds() > 0 else None
    if isinstance(window, bool):
        return None
    if isinstance(window, (int, float)):
        return float(window) if float(window) > 0 else None
    if not isinstance(window, str):
        return None
    match = _DURATION_RE.match(window)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    return amount * _DURATION_FACTORS[unit]


def _ordered_samples(series: MetricSeries) -> tuple[Any, ...]:
    """Return samples in timestamp order while preserving insertion ties."""

    indexed = list(enumerate(series.samples))
    if all(sample.observed_at is not None for _, sample in indexed):
        indexed.sort(key=lambda pair: (pair[1].observed_at or datetime.min.replace(tzinfo=UTC), pair[0]))
    return tuple(sample for _, sample in indexed)


def _sample_passes(value: float, target: SLOTarget) -> bool:
    if target.comparator == "min":
        return value >= target.threshold
    return value <= target.threshold


def _evaluation_reason(
    target: SLOTarget,
    status: str,
    *,
    observed: float | None,
    window_count: int,
    required: int,
    consecutive: int,
) -> str:
    if status == "insufficient_data":
        return f"insufficient_data: {window_count} observation(s) available; {required} consecutive window(s) required"
    if status == "breach":
        return f"{target.metric_name} breached {target.comparator} {target.threshold} for {consecutive} consecutive window(s); latest={observed}"
    return f"{target.metric_name} satisfies {target.comparator} {target.threshold}; latest={observed} across {window_count} observation(s)"


def evaluate_slo(
    target: SLOTarget | SLO,
    series: MetricSeries | None,
    *,
    consecutive_windows: int | None = None,
    enabled_severities: Collection[str] | None = None,
    clock: Clock | None = None,
) -> SLOEvaluation:
    """Evaluate one target without treating missing data as a pass.

    Each retained sample is one evaluation window.  A numeric ``window`` is a
    minimum observation count (so missing observations remain insufficient);
    when it is a duration string and timestamps are present, only observations
    in that trailing horizon are considered.  ``consecutive_windows`` (or the
    target field) requires a trailing breach streak before a breach is
    declared.  This is a deliberately small burn-rate-style rule, not a
    fabricated rate estimate.
    """

    if not isinstance(target, SLOTarget):
        raise TypeError("target must be an SLOTarget or SLO")
    if series is not None and not isinstance(series, MetricSeries):
        raise TypeError("series must be a MetricSeries or None")
    required = target.consecutive_windows if consecutive_windows is None else int(consecutive_windows)
    if required < 1:
        raise ValueError("consecutive_windows must be at least 1")
    if series is None or series.name != target.metric_name:
        return SLOEvaluation(
            target=target.model_copy(deep=True),
            status="insufficient_data",
            window_count=0,
            required_windows=required,
            reason=_evaluation_reason(
                target,
                "insufficient_data",
                observed=None,
                window_count=0,
                required=required,
                consecutive=0,
            ),
        )
    available_samples = _ordered_samples(series)
    samples = available_samples
    if isinstance(target.window, (int, float)) and not isinstance(target.window, bool):
        required_samples = max(1, int(math.ceil(float(target.window))))
        if len(samples) < required_samples:
            samples = ()
    duration = _duration_seconds(target.window)
    if duration is not None and isinstance(target.window, (str, timedelta)) and samples:
        timestamped = [sample for sample in samples if sample.observed_at is not None]
        if len(timestamped) != len(samples):
            # A duration cannot be applied to a partially untimestamped series
            # without guessing which samples belong to the horizon.
            samples = ()
        else:
            latest = timestamped[-1].observed_at
            if latest is not None:
                cutoff = latest.timestamp() - duration
                samples = tuple(sample for sample in timestamped if sample.observed_at is not None and sample.observed_at.timestamp() >= cutoff)
    if not samples:
        available_count = len(available_samples)
        available_value = available_samples[-1].value if available_samples else None
        return SLOEvaluation(
            target=target.model_copy(deep=True),
            status="insufficient_data",
            observed_value=available_value,
            window_count=available_count,
            required_windows=required,
            reason=_evaluation_reason(
                target,
                "insufficient_data",
                observed=available_value,
                window_count=available_count,
                required=required,
                consecutive=0,
            ),
        )
    values = [sample.value for sample in samples]
    streak = 0
    for value in reversed(values):
        if _sample_passes(value, target):
            break
        streak += 1
    latest = values[-1]
    if len(samples) < required:
        status = "insufficient_data"
    elif streak >= required:
        status = "breach"
    else:
        status = "pass"
    alert: Alert | None = None
    if status == "breach":
        severity_allowed = enabled_severities is None or target.severity in set(enabled_severities)
        if severity_allowed:
            fire_time = samples[-1].observed_at or _now(clock)
            evidence: dict[str, Any] = {
                "metric": target.metric_name,
                "comparator": target.comparator,
                "threshold": target.threshold,
                "observed": latest,
                "window_count": len(samples),
                "required_windows": required,
                "consecutive_breaches": streak,
                "description": target.description,
            }
            if series.dropped_samples:
                evidence["dropped_samples"] = series.dropped_samples
                evidence["reservoir_overflowed"] = series.overflowed
            alert = Alert(
                severity=target.severity,
                component=target.metric_name,
                message=(f"SLO breached: {target.metric_name} {target.comparator} {target.threshold} (latest {latest})"),
                evidence=evidence,
                fired_at=fire_time,
            )
    return SLOEvaluation(
        target=target.model_copy(deep=True),
        status=status,
        observed_value=latest,
        window_count=len(samples),
        required_windows=required,
        consecutive_breaches=streak,
        reason=_evaluation_reason(
            target,
            status,
            observed=latest,
            window_count=len(samples),
            required=required,
            consecutive=streak,
        ),
        alert=alert,
    )


class SLOEvaluator:
    """Evaluate one or more targets while keeping alert filtering explicit."""

    def __init__(
        self,
        *,
        enabled_severities: Collection[str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.enabled_severities = frozenset(enabled_severities) if enabled_severities is not None else None
        self.clock = clock

    def evaluate(
        self,
        target: SLOTarget | SLO,
        series: MetricSeries | None,
        *,
        consecutive_windows: int | None = None,
    ) -> SLOEvaluation:
        return evaluate_slo(
            target,
            series,
            consecutive_windows=consecutive_windows,
            enabled_severities=self.enabled_severities,
            clock=self.clock,
        )

    def evaluate_all(
        self,
        targets: Iterable[SLOTarget | SLO],
        series_by_name: Mapping[str, MetricSeries] | None = None,
    ) -> list[SLOEvaluation]:
        """Evaluate targets in input order, preserving deterministic results."""

        available = dict(series_by_name or {})
        return [self.evaluate(target, available.get(target.metric_name)) for target in targets]


def evaluate_slos(
    targets: Iterable[SLOTarget | SLO],
    series_by_name: Mapping[str, MetricSeries] | None = None,
    *,
    enabled_severities: Collection[str] | None = None,
    clock: Clock | None = None,
) -> list[SLOEvaluation]:
    """Functional convenience wrapper around :class:`SLOEvaluator`."""

    return SLOEvaluator(enabled_severities=enabled_severities, clock=clock).evaluate_all(targets, series_by_name)


__all__ = ["SLOEvaluator", "evaluate_slo", "evaluate_slos"]
