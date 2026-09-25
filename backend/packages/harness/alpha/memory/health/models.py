"""Typed records for the memory health and observability plane.

The models in this module are deliberately small, immutable-at-the-boundary
records.  They carry the evidence needed to answer two different questions:
*what is the current state?* and *can a caller prove how that state was
reached?*  A missing observation is represented explicitly instead of being
coerced to a healthy value or a numeric zero.

The health plane does not import any other memory implementation.  The models
are therefore safe to use from configuration, tests, and adapters without
creating a dependency cycle.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

HealthState = Literal["healthy", "degraded", "unavailable", "unknown"]
AlertSeverity = Literal["info", "warn", "critical"]
Comparator = Literal["min", "max"]
SLOStatus = Literal["pass", "breach", "insufficient_data"]

HEALTH_STATES: frozenset[str] = frozenset({"healthy", "degraded", "unavailable", "unknown"})
ALERT_SEVERITIES: frozenset[str] = frozenset({"info", "warn", "critical"})
COMPARATORS: frozenset[str] = frozenset({"min", "max"})


class _HealthModel(BaseModel):
    """Base model with strict fields and JSON-friendly defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _nonempty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


class ComponentHealth(_HealthModel):
    """One component check and the evidence behind its state."""

    name: str = Field(min_length=1)
    state: HealthState
    reason: str = ""
    checked_at: datetime | None = Field(default=None, validation_alias=AliasChoices("checked_at", "timestamp"))
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _nonempty(value, "name")

    @property
    def status(self) -> HealthState:
        """Compatibility/readability alias for ``state``."""

        return self.state


class HealthReport(_HealthModel):
    """A deterministic snapshot of all registered component checks."""

    components: tuple[ComponentHealth, ...] = ()
    overall_state: HealthState = Field(
        default="unknown",
        validation_alias=AliasChoices("overall_state", "overall", "state"),
    )
    generated_at: datetime | None = None
    scope: str = "default"

    @field_validator("components")
    @classmethod
    def _copy_components(cls, value: tuple[ComponentHealth, ...]) -> tuple[ComponentHealth, ...]:
        return tuple(sorted((item.model_copy(deep=True) for item in value), key=lambda item: item.name))

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        return _nonempty(value, "scope")

    @property
    def state(self) -> HealthState:
        """Short alias for ``overall_state``."""

        return self.overall_state

    @property
    def overall(self) -> HealthState:
        """Alias retained for callers that use the shorter noun."""

        return self.overall_state

    @property
    def component_map(self) -> dict[str, ComponentHealth]:
        """Return defensive copies keyed by component name."""

        return {item.name: item.model_copy(deep=True) for item in self.components}

    @classmethod
    def from_components(
        cls,
        components: tuple[ComponentHealth, ...] | list[ComponentHealth],
        *,
        scope: str = "default",
        generated_at: datetime | None = None,
    ) -> HealthReport:
        """Build a report and derive its conservative overall state.

        The precedence is intentional: an unavailable component cannot be
        hidden by a healthy peer, an unknown component cannot be hidden by a
        healthy peer, and a degraded component is visible even when all other
        components are healthy.  No probes means ``unknown``.
        """

        ordered = tuple(sorted((item.model_copy(deep=True) for item in components), key=lambda item: item.name))
        if not ordered:
            overall: HealthState = "unknown"
        elif any(item.state == "unavailable" for item in ordered):
            overall = "unavailable"
        elif any(item.state == "unknown" for item in ordered):
            overall = "unknown"
        elif any(item.state == "degraded" for item in ordered):
            overall = "degraded"
        else:
            overall = "healthy"
        return cls(
            components=ordered,
            overall_state=overall,
            generated_at=generated_at,
            scope=scope,
        )


class MetricSample(_HealthModel):
    """A single observed metric value.

    ``observed_at`` is optional because an explicitly supplied sample may not
    have a trustworthy clock.  SLO evaluation treats that missing timestamp as
    missing evidence rather than inventing one.
    """

    name: str = Field(min_length=1)
    value: float
    unit: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime | None = Field(default=None, validation_alias=AliasChoices("observed_at", "timestamp"))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _nonempty(value, "name")

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value

    @field_validator("labels")
    @classmethod
    def _sort_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key): str(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}


class MetricSeries(_HealthModel):
    """A bounded or caller-supplied sequence of samples for one metric."""

    name: str = Field(min_length=1)
    samples: tuple[MetricSample, ...] = ()
    window: str | int | float | None = None
    dropped_samples: int = Field(default=0, ge=0)
    overflowed: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _nonempty(value, "name")

    @field_validator("samples")
    @classmethod
    def _copy_samples(cls, value: tuple[MetricSample, ...]) -> tuple[MetricSample, ...]:
        return tuple(sample.model_copy(deep=True) for sample in value)

    @property
    def values(self) -> list[float]:
        """Values in observation order."""

        return [sample.value for sample in self.samples]

    @property
    def count(self) -> int:
        """Number of retained samples, excluding dropped observations."""

        return len(self.samples)

    @property
    def dropped_count(self) -> int:
        """Explicit number of observations omitted by a bounded reservoir."""

        return self.dropped_samples

    @property
    def overflow_count(self) -> int:
        """Alias for :attr:`dropped_samples` used by persistence consumers."""

        return self.dropped_samples

    @property
    def dropped(self) -> int:
        """Short alias for the disclosed overflow count."""

        return self.dropped_samples


class Alert(_HealthModel):
    """An evidence-bearing alert emitted by an SLO breach."""

    severity: AlertSeverity
    component: str = Field(min_length=1)
    message: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)
    fired_at: datetime | None = Field(default=None, validation_alias=AliasChoices("fired_at", "timestamp"))

    @field_validator("component", "message")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _nonempty(value, "field")


class SLOTarget(_HealthModel):
    """A lower- or upper-bound target for one metric.

    ``min`` means the observed value must be at least ``threshold``; ``max``
    means it must be at most ``threshold``.  Equality passes.  The optional
    ``consecutive_windows`` field is a small burn-rate guard: an alert is only
    fired after that many trailing evaluation windows breach the target.
    """

    metric_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("metric_name", "metric"),
        description="Metric series evaluated by this target.",
    )
    comparator: Comparator
    threshold: float
    window: str | int | float | timedelta = 1
    description: str = ""
    consecutive_windows: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("consecutive_windows", "burn_rate_windows", "streak"),
        description="Number of trailing breached windows required before alerting.",
    )
    severity: AlertSeverity = "warn"

    @field_validator("metric_name")
    @classmethod
    def _validate_metric_name(cls, value: str) -> str:
        return _nonempty(value, "metric_name")

    @field_validator("threshold")
    @classmethod
    def _validate_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("SLO threshold must be finite")
        return value

    @field_validator("window")
    @classmethod
    def _validate_window(cls, value: str | int | float | timedelta) -> str | int | float | timedelta:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("window must not be empty")
            return cleaned
        if isinstance(value, timedelta):
            if value.total_seconds() <= 0:
                raise ValueError("window must be positive")
            return value
        if value <= 0:
            raise ValueError("window must be positive")
        return value

    @property
    def metric(self) -> str:
        """Alias for ``metric_name``."""

        return self.metric_name


class SLO(SLOTarget):
    """Named SLO spelling of :class:`SLOTarget`.

    Both names are public because configuration files and Python callers often
    use the terms interchangeably.  Keeping a subclass (rather than two
    unrelated schemas) makes validation and evaluation identical.
    """


class SLOEvaluation(_HealthModel):
    """Result of evaluating one target against one metric series."""

    target: SLOTarget
    status: SLOStatus
    observed_value: float | None = None
    window_count: int = Field(default=0, ge=0)
    required_windows: int = Field(default=1, ge=1)
    consecutive_breaches: int = Field(default=0, ge=0)
    reason: str = ""
    alert: Alert | None = None

    @property
    def state(self) -> SLOStatus:
        """Alias for ``status``."""

        return self.status

    @property
    def outcome(self) -> SLOStatus:
        """Alias for ``status`` used by pipeline adapters."""

        return self.status

    @property
    def passed(self) -> bool:
        """Only a real passing evaluation is true; insufficient data is false."""

        return self.status == "pass"

    @property
    def insufficient_data(self) -> bool:
        """Whether the evaluator lacked enough trustworthy observations."""

        return self.status == "insufficient_data"

    @property
    def breached(self) -> bool:
        """Whether the target is currently in a breach state."""

        return self.status == "breach"

    @property
    def alerts(self) -> list[Alert]:
        """Return the single emitted alert, or an empty list."""

        return [self.alert.model_copy(deep=True)] if self.alert is not None else []


# A concise alias for callers that prefer the result-oriented name.
SLOResult = SLOEvaluation


__all__ = [
    "ALERT_SEVERITIES",
    "COMPARATORS",
    "HEALTH_STATES",
    "Alert",
    "AlertSeverity",
    "Comparator",
    "ComponentHealth",
    "HealthReport",
    "HealthState",
    "MetricSample",
    "MetricSeries",
    "SLO",
    "SLOResult",
    "SLOEvaluation",
    "SLOStatus",
    "SLOTarget",
]
