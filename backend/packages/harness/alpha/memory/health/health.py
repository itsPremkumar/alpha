"""Facade for the opt-in memory health and observability plane."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from .config import HealthConfig
from .metrics import MetricsRegistry
from .models import (
    SLO,
    Alert,
    ComponentHealth,
    HealthReport,
    MetricSample,
    MetricSeries,
    SLOEvaluation,
    SLOTarget,
)
from .paths import write_metrics, write_report
from .probes import CallableProbe, Clock, FreshnessProbe, Probe
from .registry import HealthProbeRegistry
from .slo import evaluate_slo as evaluate_one_slo


def _clock_value(clock: Clock | None) -> datetime | None:
    if clock is None:
        return None
    try:
        value = clock() if callable(clock) else clock
    except Exception:  # noqa: BLE001 - a missing clock is disclosed, never invented
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


class MemoryHealth:
    """Coordinate probes, metrics, SLOs, and persistence behind one gate.

    The default configuration is disabled.  Every public operation checks
    that gate before invoking a collaborator, reading a clock, recording a
    metric, or touching the filesystem.  A caller therefore cannot accidentally
    turn health observability into production work by constructing the facade.
    """

    def __init__(
        self,
        config: HealthConfig | None = None,
        *,
        registry: HealthProbeRegistry | None = None,
        metrics: MetricsRegistry | None = None,
        clock: Clock | None = None,
        scope: str = "default",
    ) -> None:
        if config is not None and not isinstance(config, HealthConfig):
            raise TypeError("config must be a HealthConfig or None")
        self._config = (config or HealthConfig()).model_copy(deep=True)
        self._clock = clock
        self._scope = str(scope).strip() or "default"
        # Read every configuration key at the facade boundary.  These values
        # are then passed to the collaborators that enforce them.
        self._max_components = self._config.max_components
        self._freshness_warning_seconds = self._config.freshness_warning_seconds
        self._freshness_critical_seconds = self._config.freshness_critical_seconds
        self._slo = tuple(target.model_copy(deep=True) for target in self._config.slo)
        self._metrics_reservoir_size = self._config.metrics_reservoir_size
        self._alert_severities_enabled = frozenset(self._config.alert_severities_enabled)
        self._storage_path = self._config.storage_path
        self._registry = registry or HealthProbeRegistry(
            max_components=self._max_components,
            clock=self._clock,
        )
        self._metrics = metrics or MetricsRegistry(
            reservoir_size=self._metrics_reservoir_size,
            clock=self._clock,
        )
        self._last_report: HealthReport | None = None
        self._alerts: list[Alert] = []
        self._persistence_error: str | None = None

    @property
    def config(self) -> HealthConfig:
        return self._config.model_copy(deep=True)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def registry(self) -> HealthProbeRegistry:
        return self._registry

    @property
    def metrics(self) -> MetricsRegistry:
        return self._metrics

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def storage_path(self) -> str | None:
        return self._storage_path

    @property
    def last_report(self) -> HealthReport | None:
        with_report = self._last_report
        return with_report.model_copy(deep=True) if with_report is not None else None

    @property
    def persistence_error(self) -> str | None:
        return self._persistence_error

    def register(self, name: str | Probe, probe: Probe | None = None) -> bool:
        """Register a probe when enabled; return whether registration ran."""

        if not self.enabled:
            return False
        if probe is not None and callable(probe) and not any(callable(getattr(probe, method_name, None)) for method_name in ("check", "probe", "run")):
            if isinstance(name, str):
                probe = CallableProbe(name, probe)
            else:
                probe = CallableProbe(getattr(probe, "__name__", "callable"), probe)
        self._registry.register(name, probe)
        return True

    def unregister(self, name: str | Probe) -> Probe | None:
        """Unregister a probe when enabled."""

        if not self.enabled:
            return None
        return self._registry.unregister(name)

    def check_all(self) -> HealthReport | None:
        """Run all registered probes and return a deterministic report."""

        if not self.enabled:
            return None
        try:
            records = list(self._registry.check_all())
        except Exception as exc:  # noqa: BLE001 - an injected registry must not break a turn
            records = [
                ComponentHealth(
                    name="health_registry",
                    state="unavailable",
                    reason=f"health registry failed: {type(exc).__name__}: {exc}",
                    checked_at=_clock_value(self._clock),
                    evidence={"operation": "check_all"},
                )
            ]
        if self._persistence_error:
            records.append(
                ComponentHealth(
                    name="health_persistence",
                    state="unavailable",
                    reason=self._persistence_error,
                    checked_at=_clock_value(self._clock),
                    evidence={"operation": "previous_metric_or_report_write"},
                )
            )
        report = HealthReport.from_components(
            records,
            scope=self._scope,
            generated_at=_clock_value(self._clock),
        )
        try:
            write_report(report, storage_path=self._storage_path, scope=self._scope)
        except Exception as exc:  # noqa: BLE001 - persistence failure is report data
            self._persistence_error = f"could not persist health report: {type(exc).__name__}: {exc}"
            report = HealthReport.from_components(
                [
                    *records,
                    ComponentHealth(
                        name="health_persistence",
                        state="unavailable",
                        reason=self._persistence_error,
                        checked_at=_clock_value(self._clock),
                        evidence={"operation": "report_write"},
                    ),
                ],
                scope=self._scope,
                generated_at=report.generated_at,
            )
        else:
            self._persistence_error = None
        self._last_report = report
        return report.model_copy(deep=True)

    def report(self, *, refresh: bool = False) -> HealthReport | None:
        """Return the latest report, checking probes when no snapshot exists."""

        if not self.enabled:
            return None
        if refresh or self._last_report is None:
            return self.check_all()
        return self._last_report.model_copy(deep=True)

    def record(
        self,
        sample_or_name: MetricSample | str,
        value: float | int | None = None,
        *,
        unit: str = "",
        labels: Mapping[str, object] | None = None,
        observed_at: object | None = None,
        kind: str = "histogram",
    ) -> MetricSample | None:
        """Record a metric sample when enabled.

        A typed ``MetricSample`` is accepted directly; otherwise a name/value
        pair is required.  No timestamp is invented when neither the caller nor
        the injected clock supplies one.
        """

        if not self.enabled:
            return None
        if isinstance(sample_or_name, MetricSample):
            sample = self._metrics.record_sample(sample_or_name)
        elif isinstance(sample_or_name, str):
            if value is None:
                return None
            if kind == "counter":
                sample = self._metrics.increment(
                    sample_or_name,
                    value,
                    unit=unit,
                    labels=labels,
                    observed_at=observed_at,
                )
            elif kind == "gauge":
                sample = self._metrics.set_gauge(
                    sample_or_name,
                    value,
                    unit=unit,
                    labels=labels,
                    observed_at=observed_at,
                )
            elif kind == "histogram":
                sample = self._metrics.observe(
                    sample_or_name,
                    value,
                    unit=unit,
                    labels=labels,
                    observed_at=observed_at,
                )
            else:
                return None
        else:
            return None
        self._persist_metrics_best_effort()
        return sample.model_copy(deep=True)

    def series(
        self,
        name: str,
        *,
        labels: Mapping[str, object] | None = None,
        window: str | int | float | None = None,
    ) -> MetricSeries | None:
        """Return a defensive metric-series snapshot when enabled."""

        if not self.enabled:
            return None
        return self._metrics.series(name, labels=labels, window=window).model_copy(deep=True)

    def evaluate_slo(
        self,
        target: SLOTarget | SLO | Iterable[SLOTarget | SLO] | None = None,
        series: MetricSeries | Mapping[str, MetricSeries] | Iterable[MetricSeries] | None = None,
    ) -> SLOEvaluation | list[SLOEvaluation] | None:
        """Evaluate one target or the configured target list when enabled."""

        if not self.enabled:
            return None
        if target is None:
            targets: list[SLOTarget | SLO] = list(self._slo)
            evaluations = self._evaluate_many(targets, series)
            return evaluations
        if isinstance(target, (SLOTarget, SLO)):
            selected_series = self._select_series(target.metric_name, series)
            evaluation = evaluate_one_slo(
                target,
                selected_series,
                enabled_severities=self._alert_severities_enabled,
                clock=self._clock,
            )
            self._remember_evaluation(evaluation)
            return evaluation
        evaluations = self._evaluate_many(list(target), series)
        return evaluations

    def alerts(self) -> list[Alert]:
        """Return defensive copies of emitted alerts when enabled."""

        if not self.enabled:
            return []
        return [alert.model_copy(deep=True) for alert in sorted(self._alerts, key=self._alert_sort_key)]

    def render_text(self, report: HealthReport | None = None) -> str:
        """Render a stable human-readable report; disabled renders nothing."""

        if not self.enabled:
            return ""
        selected = report.model_copy(deep=True) if report is not None else self.report()
        if selected is None:
            return ""
        lines = [f"memory-health scope={selected.scope} overall={selected.overall_state} generated_at={selected.generated_at.isoformat() if selected.generated_at else 'unknown'}"]
        if not selected.components:
            lines.append("components: none (overall=unknown; no probes registered)")
        for component in selected.components:
            evidence = json.dumps(component.evidence, ensure_ascii=False, sort_keys=True, default=str)
            reason = component.reason or "unspecified"
            lines.append(f"- {component.name}: {component.state}; reason={reason}; checked_at={component.checked_at.isoformat() if component.checked_at else 'unknown'}; evidence={evidence}")
        return "\n".join(lines)

    def render_json(self, report: HealthReport | None = None) -> str:
        """Render stable sorted JSON; disabled renders nothing."""

        if not self.enabled:
            return ""
        selected = report.model_copy(deep=True) if report is not None else self.report()
        if selected is None:
            return ""
        return json.dumps(selected.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def freshness_probe(
        self,
        timestamp: object,
        *,
        name: str = "freshness",
    ) -> FreshnessProbe | None:
        """Build a probe using the configured freshness thresholds."""

        if not self.enabled:
            return None
        return FreshnessProbe(
            timestamp,
            self._clock,
            name=name,
            warn_after_seconds=self._freshness_warning_seconds,
            critical_after_seconds=self._freshness_critical_seconds,
        )

    def reset(self) -> None:
        """Reset in-memory collaborators when enabled (test/lifecycle seam)."""

        if not self.enabled:
            return
        self._registry.reset()
        self._metrics.reset()
        self._last_report = None
        self._alerts.clear()
        self._persistence_error = None

    def _persist_metrics_best_effort(self) -> None:
        try:
            write_metrics(self._metrics.snapshot(), storage_path=self._storage_path)
        except Exception as exc:  # noqa: BLE001 - persistence is observable, not turn-fatal
            self._persistence_error = f"could not persist metrics: {type(exc).__name__}: {exc}"
        else:
            self._persistence_error = None

    def _select_series(
        self,
        metric_name: str,
        series: MetricSeries | Mapping[str, MetricSeries] | Iterable[MetricSeries] | None,
    ) -> MetricSeries | None:
        if isinstance(series, MetricSeries):
            return series
        if isinstance(series, Mapping):
            selected = series.get(metric_name)
            return selected.model_copy(deep=True) if isinstance(selected, MetricSeries) else None
        if series is not None:
            for candidate in series:
                if isinstance(candidate, MetricSeries) and candidate.name == metric_name:
                    return candidate.model_copy(deep=True)
            return None
        return self._metrics.series(metric_name)

    def _evaluate_many(
        self,
        targets: list[SLOTarget | SLO],
        series: MetricSeries | Mapping[str, MetricSeries] | Iterable[MetricSeries] | None,
    ) -> list[SLOEvaluation]:
        evaluations: list[SLOEvaluation] = []
        for target in targets:
            selected = self._select_series(target.metric_name, series)
            evaluation = evaluate_one_slo(
                target,
                selected,
                enabled_severities=self._alert_severities_enabled,
                clock=self._clock,
            )
            self._remember_evaluation(evaluation)
            evaluations.append(evaluation)
        return evaluations

    def _remember_evaluation(self, evaluation: SLOEvaluation) -> None:
        if evaluation.alert is not None:
            self._alerts.append(evaluation.alert.model_copy(deep=True))

    @staticmethod
    def _alert_sort_key(alert: Alert) -> tuple[str, str, str]:
        return (alert.fired_at.isoformat() if alert.fired_at else "", alert.component, alert.message)


__all__ = ["MemoryHealth"]
