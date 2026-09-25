"""Hermetic regression coverage for the memory health plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from alpha.memory.health.config import HealthConfig, read_all_keys
from alpha.memory.health.health import MemoryHealth
from alpha.memory.health.metrics import MetricsRegistry, percentile
from alpha.memory.health.models import (
    SLO,
    ComponentHealth,
    HealthReport,
    MetricSample,
    MetricSeries,
    SLOTarget,
)
from alpha.memory.health.paths import (
    atomic_write_text,
    health_root,
    metric_path,
    metrics_path,
    read_json_with_status,
    read_metrics,
    read_report,
    report_path,
    safe_segment,
    write_metrics,
    write_report,
)
from alpha.memory.health.probes import (
    CallableProbe,
    FreshnessProbe,
    LatencyProbe,
    Probe,
    QuotaProbe,
    StoreCountProbe,
)
from alpha.memory.health.registry import (
    DuplicateProbeError,
    HealthProbeRegistry,
    RegistryCapacityError,
)
from alpha.memory.health.slo import evaluate_slo, evaluate_slos

FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def clock():
    return lambda: FIXED_NOW


@pytest.fixture
def storage_path(tmp_path: Path) -> Path:
    return tmp_path / "health-state"


def health(name: str, state: str = "healthy", *, reason: str = "ok", evidence: dict | None = None) -> ComponentHealth:
    return ComponentHealth(
        name=name,
        state=state,
        reason=reason,
        checked_at=FIXED_NOW,
        evidence=evidence or {},
    )


def series(name: str, values: list[float], *, observed_at: list[datetime] | None = None) -> MetricSeries:
    timestamps = observed_at or [FIXED_NOW + timedelta(seconds=index) for index in range(len(values))]
    return MetricSeries(
        name=name,
        samples=tuple(MetricSample(name=name, value=value, observed_at=timestamps[index]) for index, value in enumerate(values)),
    )


def test_models_preserve_evidence_and_rank_overall_states() -> None:
    records = [
        health("z", "degraded", reason="slow", evidence={"latency": 9}),
        health("a", "unavailable", reason="missing dependency", evidence={"dependency": "store"}),
        health("m", "unknown", reason="not checked"),
    ]
    report = HealthReport.from_components(records, scope="scope/a", generated_at=FIXED_NOW)
    assert report.overall_state == "unavailable"
    assert [item.name for item in report.components] == ["a", "m", "z"]
    copied = report.component_map["a"]
    copied.evidence["dependency"] = "mutated"
    assert report.component_map["a"].evidence == {"dependency": "store"}
    assert HealthReport.from_components([]).overall_state == "unknown"
    assert series("x", [1, 2]).values == [1.0, 2.0]


def test_callable_probe_supports_all_explicit_states_and_errors() -> None:
    def callback() -> ComponentHealth:
        return health("callable")

    assert isinstance(CallableProbe("callable", callback), Probe)
    callable_probe = CallableProbe("callable", callback)
    assert callable_probe.check().state == "healthy"
    assert callable_probe.probe().state == "healthy"
    assert callable_probe.run().state == "healthy"

    unknown = CallableProbe("unknown", lambda: health("unknown", "unknown", reason="not sampled"))
    assert unknown.check().state == "unknown"

    def raises() -> ComponentHealth:
        raise RuntimeError("backend offline")

    failed = CallableProbe("failed", raises, clock=lambda: FIXED_NOW).check()
    assert failed.state == "unavailable"
    assert "RuntimeError" in failed.reason
    assert failed.checked_at == FIXED_NOW

    invalid = CallableProbe("invalid", lambda: None).check()
    assert invalid.state == "unavailable"
    assert "expected ComponentHealth" in invalid.reason


def test_store_count_probe_never_turns_missing_dependency_into_zero() -> None:
    missing = StoreCountProbe("l1", None).check()
    assert missing.state == "unavailable"
    assert "count" not in missing.evidence

    def broken() -> int:
        raise OSError("store unavailable")

    failed = StoreCountProbe("l1", broken).check()
    assert failed.state == "unavailable"
    assert "OSError" in failed.reason
    assert "count" not in failed.evidence

    legitimate_zero = StoreCountProbe("l1", lambda: 0).check()
    assert legitimate_zero.state == "healthy"
    assert legitimate_zero.evidence == {"count": 0.0}


def test_latency_probe_reports_missing_empty_healthy_and_degraded() -> None:
    assert LatencyProbe("latency").check().state == "unavailable"
    assert LatencyProbe("latency", []).check().state == "unknown"
    assert LatencyProbe("latency", [1.0, 2.0], threshold=2.0).check().state == "healthy"
    assert LatencyProbe("latency", [1.0, 3.0], threshold=2.0).check().state == "degraded"

    def broken_samples():
        raise ValueError("sampler failed")

    failed = LatencyProbe("latency", broken_samples).check()
    assert failed.state == "unavailable"
    assert "ValueError" in failed.reason


def test_freshness_probe_requires_injected_clock_and_reports_age() -> None:
    assert FreshnessProbe(FIXED_NOW - timedelta(seconds=1)).check().state == "unavailable"
    stale = FreshnessProbe(
        FIXED_NOW - timedelta(seconds=20),
        lambda: FIXED_NOW,
        name="memory",
        warn_after_seconds=10,
        critical_after_seconds=60,
    ).check()
    assert stale.state == "degraded"
    assert stale.evidence["age_seconds"] == 20
    critical = FreshnessProbe(
        FIXED_NOW - timedelta(seconds=60),
        lambda: FIXED_NOW,
        warn_after_seconds=10,
        critical_after_seconds=60,
    ).check()
    assert critical.state == "unavailable"
    assert critical.evidence["critical"] is True
    future = FreshnessProbe(FIXED_NOW + timedelta(seconds=1), lambda: FIXED_NOW).check()
    assert future.state == "degraded"
    healthy = FreshnessProbe(FIXED_NOW - timedelta(seconds=1), lambda: FIXED_NOW).check()
    assert healthy.state == "healthy"
    assert FreshnessProbe(FIXED_NOW - timedelta(seconds=1), FIXED_NOW).check().state == "healthy"


def test_quota_probe_discloses_threshold_and_missing_values() -> None:
    assert QuotaProbe(used=1).check().state == "unavailable"
    assert QuotaProbe(used=1, limit=10, degraded_at=0.8).check().state == "healthy"
    degraded = QuotaProbe(used=8, limit=10, degraded_at=0.8).check()
    assert degraded.state == "degraded"
    assert degraded.evidence["ratio"] == pytest.approx(0.8)
    assert "threshold" in degraded.reason


def test_registry_rejects_duplicates_bounds_orders_and_copies_results() -> None:
    registry = HealthProbeRegistry(max_components=2, clock=lambda: FIXED_NOW)
    first = CallableProbe("z", lambda: health("z", evidence={"nested": {"value": 1}}))
    second = CallableProbe("a", lambda: health("a", "degraded", reason="slow"))
    registry.register("z", first)
    with pytest.raises(DuplicateProbeError):
        registry.register(first)
    with pytest.raises(DuplicateProbeError):
        registry.register("z", first)
    registry.register("a", second)
    assert registry.names() == ("a", "z")
    assert [item.name for item in registry.check_all()] == ["a", "z"]

    result = registry.check("z")
    result.evidence["nested"]["value"] = 99
    assert registry.last_results()[1].evidence["nested"]["value"] == 1
    assert registry.unregister("z") is first
    assert registry.names() == ("a",)
    registry.register("b", CallableProbe("b", lambda: health("b")))
    with pytest.raises(RegistryCapacityError, match="capacity"):
        registry.register("c", CallableProbe("c", lambda: health("c")))
    registry.reset()
    assert len(registry) == 0


def test_registry_discloses_bad_probe_objects_and_raising_probes(clock) -> None:
    class Broken:
        name = "broken"

        def check(self):
            raise LookupError("missing")

    registry = HealthProbeRegistry(clock=clock)
    registry.register("broken", Broken())
    result = registry.check("broken")
    assert result.state == "unavailable"
    assert result.checked_at == FIXED_NOW
    assert "LookupError" in result.reason

    class Runnable:
        name = "runnable"

        def run(self):
            return health("runnable")

    registry.register(Runnable())
    assert registry.check("runnable").state == "healthy"
    assert registry.check("not-there").state == "unavailable"


def test_metrics_counter_gauge_histogram_overflow_and_percentile() -> None:
    metrics = MetricsRegistry(reservoir_size=2, clock=lambda: FIXED_NOW)
    assert metrics.counter("not-recorded") is None
    metrics.counter("requests", 2, labels={"route": "/memory"})
    metrics.counter("requests", 3, labels={"route": "/memory"})
    assert metrics.counter("requests", labels={"route": "/memory"}) == 5.0
    assert metrics.counter_value("requests", labels={"route": "/memory"}) == 5.0
    assert metrics.series("requests", labels={"route": "/memory"}).values == [2.0, 3.0]
    metrics.gauge("queue", 4)
    assert metrics.gauge("queue") == 4.0
    metrics.observe("latency", 1, unit="ms")
    metrics.observe("latency", 3, unit="ms")
    metrics.observe("latency", 5, unit="ms")
    latency = metrics.series("latency")
    assert latency.values == [3.0, 5.0]
    assert latency.dropped_samples == 1
    assert latency.overflowed is True
    assert latency.dropped_count == 1
    assert latency.dropped == 1
    assert latency.overflow_count == 1
    assert metrics.percentile("latency", 0.5) == 4.0
    assert percentile([0, 10], 0.5) == 5.0
    metrics.inc("single", 1)
    metrics.histogram("histogram-alias", 7)
    assert metrics.counter("single") == 1.0
    assert metrics.series("histogram-alias").values == [7.0]
    typed_sample = MetricSample(name="typed", value=3, observed_at=FIXED_NOW)
    assert metrics.record_sample(typed_sample).value == 3.0
    snapshot = metrics.snapshot()
    assert snapshot["metrics"]["latency"][0]["dropped_samples"] == 1
    assert metrics.export() == snapshot
    assert list(snapshot["metrics"]) == sorted(snapshot["metrics"])


def test_slo_min_max_alert_and_insufficient_data_are_distinct() -> None:
    min_target = SLOTarget(metric_name="availability", comparator="min", threshold=0.99, severity="critical")
    max_target = SLOTarget(metric_name="latency", comparator="max", threshold=100, severity="warn")
    passing_min = evaluate_slo(min_target, series("availability", [0.995]))
    assert passing_min.status == "pass"
    assert passing_min.outcome == "pass"
    assert passing_min.passed is True
    assert passing_min.alert is None
    breached_max = evaluate_slo(max_target, series("latency", [101]))
    assert breached_max.status == "breach"
    assert breached_max.breached is True
    assert breached_max.alert is not None
    assert breached_max.alerts[0].severity == "warn"
    assert breached_max.alert.severity == "warn"
    insufficient = evaluate_slo(min_target, series("availability", []))
    assert insufficient.status == "insufficient_data"
    assert insufficient.insufficient_data is True
    assert insufficient.passed is False
    assert insufficient.alert is None


def test_slo_consecutive_window_boundary_is_exact() -> None:
    target = SLOTarget(
        metric_name="errors",
        comparator="max",
        threshold=2,
        consecutive_windows=2,
        severity="critical",
    )
    recovered = evaluate_slo(target, series("errors", [3, 1]))
    assert recovered.status == "pass"
    assert recovered.consecutive_breaches == 0
    boundary = evaluate_slo(target, series("errors", [3, 3]))
    assert boundary.status == "breach"
    assert boundary.consecutive_breaches == 2
    assert boundary.window_count == 2
    not_enough = evaluate_slo(target, series("errors", [3]))
    assert not_enough.status == "insufficient_data"


def test_slo_duration_window_and_severity_filter_are_explicit() -> None:
    target = SLOTarget(
        metric_name="latency",
        comparator="max",
        threshold=10,
        window="2s",
        severity="critical",
    )
    timed = series(
        "latency",
        [1, 20, 30],
        observed_at=[
            FIXED_NOW - timedelta(seconds=5),
            FIXED_NOW - timedelta(seconds=1),
            FIXED_NOW,
        ],
    )
    result = evaluate_slo(target, timed)
    assert result.status == "breach"
    assert result.window_count == 2
    suppressed = evaluate_slo(target, timed, enabled_severities=["warn"])
    assert suppressed.status == "breach"
    assert suppressed.alert is None


def test_slo_evaluator_evaluates_all_targets_in_order() -> None:
    targets = [
        SLO(metric="a", comparator="min", threshold=1),
        SLOTarget(metric_name="b", comparator="max", threshold=2),
    ]
    results = evaluate_slos(targets, {"a": series("a", [1]), "b": series("b", [3])})
    assert [result.status for result in results] == ["pass", "breach"]


def test_memory_health_is_default_off_and_does_not_touch_storage(tmp_path: Path) -> None:
    state = tmp_path / "disabled-state"
    health = MemoryHealth(HealthConfig(storage_path=str(state)), clock=lambda: FIXED_NOW)
    assert health.enabled is False
    assert health.register("x", CallableProbe("x", lambda: health("x"))) is False
    assert health.check_all() is None
    assert health.report() is None
    assert health.record("x", 1) is None
    assert health.series("x") is None
    assert health.evaluate_slo() is None
    assert health.alerts() == []
    assert health.render_text() == ""
    assert health.render_json() == ""
    assert not state.exists()


def test_memory_health_enabled_reports_unknown_and_stable_evidence_rendering(storage_path: Path) -> None:
    health = MemoryHealth(
        HealthConfig(enabled=True, storage_path=str(storage_path)),
        clock=lambda: FIXED_NOW,
    )
    empty = health.check_all()
    assert empty is not None
    assert empty.overall_state == "unknown"
    assert empty.components == ()
    assert "no probes registered" in health.render_text(empty)
    assert json.loads(health.render_json(empty))["overall_state"] == "unknown"

    health.register(
        "store",
        StoreCountProbe("store", None),
    )
    report = health.check_all()
    assert report is not None
    assert report.overall_state == "unavailable"
    text = health.render_text(report)
    rendered_json = health.render_json(report)
    assert "store" in text
    assert "missing" in text
    assert "dependency" in text
    assert json.loads(rendered_json)["components"][0]["evidence"]["dependency"] == "count"
    assert health.render_text(report) == text
    assert health.render_json(report) == rendered_json


def test_memory_health_discloses_persistence_failures(monkeypatch, storage_path: Path) -> None:
    import alpha.memory.health.health as health_module

    def broken_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(health_module, "write_metrics", broken_write)
    facade = MemoryHealth(HealthConfig(enabled=True, storage_path=str(storage_path)), clock=lambda: FIXED_NOW)
    sample = facade.record("latency", 1)
    assert sample is not None
    report = facade.check_all()
    assert report is not None
    persistence = next(item for item in report.components if item.name == "health_persistence")
    assert persistence.state == "unavailable"
    assert "disk full" in persistence.reason


def test_memory_health_records_metrics_and_evaluates_configured_slos(storage_path: Path) -> None:
    target = SLOTarget(metric_name="latency", comparator="max", threshold=10, severity="critical")
    config = HealthConfig(enabled=True, storage_path=str(storage_path), slo=[target])
    health = MemoryHealth(config, clock=lambda: FIXED_NOW)
    assert health.record("latency", 4, unit="ms") is not None
    assert health.record("latency", 20, unit="ms") is not None
    stored = health.series("latency")
    assert stored is not None
    assert stored.values == [4.0, 20.0]
    evaluation = health.evaluate_slo()
    assert isinstance(evaluation, list)
    assert evaluation[0].status == "breach"
    assert health.alerts()[0].severity == "critical"
    assert read_metrics(storage_path=str(storage_path)) is not None


def test_memory_health_freshness_factory_uses_config_thresholds(storage_path: Path) -> None:
    config = HealthConfig(
        enabled=True,
        storage_path=str(storage_path),
        freshness_warning_seconds=5,
        freshness_critical_seconds=10,
    )
    health = MemoryHealth(config, clock=lambda: FIXED_NOW)
    probe = health.freshness_probe(FIXED_NOW - timedelta(seconds=6), name="age")
    assert probe is not None
    result = probe.check()
    assert result.state == "degraded"
    assert result.evidence["warning_after_seconds"] == 5
    assert result.evidence["critical_after_seconds"] == 10


def test_health_config_defaults_aliases_and_validation() -> None:
    default = HealthConfig()
    assert default.enabled is False
    assert default.max_components == 64
    assert default.alert_severities == ["info", "warn", "critical"]
    aliased = HealthConfig(
        freshness_warn_seconds=2,
        freshness_stale_seconds=4,
        reservoir_size=3,
        alert_severities=["warn"],
    )
    assert aliased.freshness_warning_seconds == 2
    assert aliased.freshness_critical_seconds == 4
    assert aliased.metrics_reservoir_size == 3
    assert aliased.alert_severities_enabled == ["warn"]
    nested = HealthConfig(freshness_thresholds={"warning": 3, "critical": 6})
    assert nested.freshness_warning_seconds == 3
    assert nested.freshness_critical_seconds == 6
    assert HealthConfig(alert_severities_enabled=True).alert_severities_enabled == ["info", "warn", "critical"]
    assert HealthConfig(storage_path=Path("state")).storage_path == "state"
    with pytest.raises(ValidationError):
        HealthConfig(freshness_warning_seconds=10, freshness_critical_seconds=2)
    with pytest.raises(ValidationError):
        HealthConfig(alert_severities=["warn", "warn"])


def test_every_health_config_key_has_a_real_package_reader() -> None:
    import alpha.memory.health as health_package

    source_root = Path(health_package.__file__).parent
    sources = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))
    readers = {
        "enabled": "self._config.enabled",
        "max_components": "self._config.max_components",
        "freshness_warning_seconds": "self._config.freshness_warning_seconds",
        "freshness_critical_seconds": "self._config.freshness_critical_seconds",
        "slo": "self._config.slo",
        "metrics_reservoir_size": "self._config.metrics_reservoir_size",
        "alert_severities_enabled": "self._config.alert_severities_enabled",
        "storage_path": "self._config.storage_path",
    }
    assert set(read_all_keys()) == set(HealthConfig.model_fields)
    assert set(readers) == set(HealthConfig.model_fields)
    assert all(reader in sources for reader in readers.values())


def test_paths_atomic_round_trip_safe_segments_and_runtime_home(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(runtime))
    assert safe_segment("../user name") == "user_name"
    assert health_root() == (runtime / "memory" / "health").resolve()
    assert metrics_path().name == "metrics.json"
    assert metric_path("metric/unsafe").name == "metric_unsafe.json"
    target = report_path(scope="user/unsafe")
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert not list(target.parent.glob("*.tmp"))
    status_value, status_error, status_preserved = read_json_with_status(target.parent / "status.json")
    assert status_value is None
    assert status_error is None
    assert status_preserved is None

    report = HealthReport.from_components([], scope="user/unsafe", generated_at=FIXED_NOW)
    write_report(report, storage_path=runtime)
    assert read_report(storage_path=runtime, scope="user/unsafe") == report
    snapshot = {"schema": 1, "metrics": {}, "reservoir_size": 2, "window": None}
    write_metrics(snapshot, storage_path=runtime)
    assert read_metrics(storage_path=runtime) == snapshot


def test_paths_preserve_corrupt_report_and_metric_files(tmp_path: Path) -> None:
    report = report_path(storage_path=tmp_path, scope="scope")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("not json", encoding="utf-8")
    assert read_report(storage_path=tmp_path, scope="scope", corruption_token="test") is None
    corrupt_reports = list(report.parent.glob("scope.json.corrupt-*"))
    assert len(corrupt_reports) == 1
    assert corrupt_reports[0].read_text(encoding="utf-8") == "not json"

    metrics = metrics_path(storage_path=tmp_path)
    metrics.write_text("[]", encoding="utf-8")
    assert read_metrics(storage_path=tmp_path, corruption_token="metric") is None
    assert len(list(tmp_path.glob("metrics.json.corrupt-*"))) == 1


def test_health_root_and_public_exports_are_lazy_and_self_contained() -> None:
    import alpha.memory.health as package

    assert package.MemoryHealth.__module__ == "alpha.memory.health.health"
    assert package.HealthConfig.__module__ == "alpha.memory.health.config"
    assert package.SLOEvaluator.__module__ == "alpha.memory.health.slo"
    assert "MemoryHealth" in package.__all__
    assert "SLOEvaluator" in package.__all__
    assert "alpha.memory.agents" not in "\n".join(path.read_text(encoding="utf-8") for path in Path(package.__file__).parent.glob("*.py"))
