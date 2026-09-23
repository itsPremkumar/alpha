"""Unit tests for the aggregated ops readiness report (app.gateway.ops_readiness)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.gateway.ops_readiness as ops_readiness
from app.gateway.ops_readiness import (
    EVENT_LOOP_DEGRADED_MAX_DELAY_MS,
    _event_loop_check,
    _scheduler_check,
    compose_readiness_report,
)
from app.gateway.routers import ops_integration


def _fixed_now() -> datetime:
    return datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


# -- compose_readiness_report (pure) ----------------------------------


def test_compose_ready_when_all_checks_pass() -> None:
    report = compose_readiness_report(
        service="agent-workspace-gateway",
        database="ok",
        checkpointer="not_configured",
        event_loop={"status": "ok"},
        scheduler={"status": "running"},
        generated_at=_fixed_now(),
    )
    assert report["status"] == "ready"
    assert report["failing"] == []
    assert report["checks"]["persistence"] == {"database": "ok", "checkpointer": "not_configured"}
    assert report["generated_at"] == "2026-09-23T12:00:00+00:00"


def test_compose_collects_every_failing_reason() -> None:
    report = compose_readiness_report(
        service="agent-workspace-gateway",
        database="unreachable",
        checkpointer="unreachable",
        event_loop={"status": "degraded"},
        scheduler={"status": "stopped"},
        generated_at=_fixed_now(),
    )
    assert report["status"] == "degraded"
    assert report["failing"] == [
        "database:unreachable",
        "checkpointer:unreachable",
        "event_loop:delay",
        "scheduler:poller_stopped",
    ]


def test_compose_not_configured_and_no_signal_are_not_failures() -> None:
    report = compose_readiness_report(
        service="agent-workspace-gateway",
        database="not_configured",
        checkpointer="not_configured",
        event_loop={"status": "no_signal"},
        scheduler={"status": "not_configured"},
        generated_at=_fixed_now(),
    )
    assert report["status"] == "ready"
    assert report["failing"] == []


def test_compose_defaults_generated_at_to_now() -> None:
    report = compose_readiness_report(
        service="s",
        database="ok",
        checkpointer="ok",
        event_loop={},
        scheduler={},
    )
    assert datetime.fromisoformat(report["generated_at"]).tzinfo is not None


# -- _event_loop_check ------------------------------------------------


def test_event_loop_check_is_no_signal_without_windows() -> None:
    check = _event_loop_check({"windows_completed": 0, "interval_seconds": 0.05})
    assert check["status"] == "no_signal"
    assert check["windows_completed"] == 0
    assert "delay_last_max_ms" not in check


def test_event_loop_check_ok_with_healthy_delay() -> None:
    check = _event_loop_check(
        {
            "windows_completed": 3,
            "delay_last_max_ms": 4.5,
            "delay_max_ms": 12.0,
            "delay_p99_ms": 12.0,
            "cpu_core_ratio": 0.12,
        }
    )
    assert check["status"] == "ok"
    assert check["delay_last_max_ms"] == 4.5
    assert check["cpu_core_ratio"] == 0.12


def test_event_loop_check_degraded_when_last_window_exceeds_threshold() -> None:
    threshold = EVENT_LOOP_DEGRADED_MAX_DELAY_MS
    check = _event_loop_check(
        {
            "windows_completed": 1,
            "delay_last_max_ms": threshold + 1.0,
            "delay_max_ms": threshold + 1.0,
        }
    )
    assert check["status"] == "degraded"


def test_event_loop_check_threshold_is_one_second() -> None:
    assert EVENT_LOOP_DEGRADED_MAX_DELAY_MS == 1000.0


def test_event_loop_check_boundary_delay_is_ok() -> None:
    check = _event_loop_check(
        {
            "windows_completed": 1,
            "delay_last_max_ms": EVENT_LOOP_DEGRADED_MAX_DELAY_MS,
        }
    )
    assert check["status"] == "ok"


# -- _scheduler_check -------------------------------------------------


def test_scheduler_check_not_configured_without_service() -> None:
    assert _scheduler_check(SimpleNamespace()) == {"status": "not_configured"}


def test_scheduler_check_stopped_when_poller_task_absent_or_done() -> None:
    assert _scheduler_check(SimpleNamespace(scheduled_task_service=SimpleNamespace(_task=None))) == {"status": "stopped"}
    done_task = SimpleNamespace(done=lambda: True)
    assert _scheduler_check(SimpleNamespace(scheduled_task_service=SimpleNamespace(_task=done_task))) == {"status": "stopped"}


def test_scheduler_check_running_with_live_poller_task() -> None:
    live_task = SimpleNamespace(done=lambda: False)
    state = SimpleNamespace(scheduled_task_service=SimpleNamespace(_task=live_task))
    assert _scheduler_check(state) == {"status": "running"}


# -- endpoints (bare FastAPI, monkeypatched deps) --------------------


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ops_integration.router)
    return TestClient(app)


class _StubSampler:
    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        self.ensure_running_calls = 0

    def ensure_running(self) -> None:
        self.ensure_running_calls += 1

    def snapshot(self) -> dict:
        return dict(self._snapshot)


def test_event_loop_endpoint_returns_snapshot(monkeypatch) -> None:
    stub = _StubSampler({"windows_completed": 0, "interval_seconds": 0.05})
    monkeypatch.setattr(ops_integration, "get_event_loop_sampler", lambda: stub)
    with _client() as client:
        response = client.get("/api/ops/event-loop")
    assert response.status_code == 200
    payload = response.json()
    # No completed window -> delay fields are omitted, never zero.
    assert payload["windows_completed"] == 0
    assert "delay_last_max_ms" not in payload
    assert "cpu_core_ratio" not in payload
    assert stub.ensure_running_calls == 1


def test_event_loop_endpoint_exposes_delay_fields_once_signal_exists(monkeypatch) -> None:
    stub = _StubSampler(
        {
            "windows_completed": 2,
            "delay_last_max_ms": 3.0,
            "delay_max_ms": 9.0,
            "delay_p99_ms": 9.0,
            "cpu_core_ratio": 0.25,
        }
    )
    monkeypatch.setattr(ops_integration, "get_event_loop_sampler", lambda: stub)
    with _client() as client:
        payload = client.get("/api/ops/event-loop").json()
    assert payload["delay_last_max_ms"] == 3.0
    assert payload["cpu_core_ratio"] == 0.25


async def _fake_ready_unreachable(_config=None):
    return (503, {"status": "degraded", "database": "unreachable", "checkpointer": "ok"})


async def _fake_ready_ok(_config=None):
    return (200, {"status": "ready", "database": "ok", "checkpointer": "ok"})


def test_readiness_endpoint_always_200_with_failing_detail(monkeypatch) -> None:
    monkeypatch.setattr(ops_readiness, "readiness_payload", _fake_ready_unreachable)
    monkeypatch.setattr(
        ops_readiness,
        "get_event_loop_sampler",
        lambda: _StubSampler({"windows_completed": 0}),
    )
    with _client() as client:
        response = client.get("/api/ops/readiness")
    # Authenticated detail surface never steals the 503 orchestration owns.
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert "database:unreachable" in payload["failing"]
    assert payload["checks"]["event_loop"]["status"] == "no_signal"
    assert payload["checks"]["scheduler"]["status"] == "not_configured"


def test_readiness_endpoint_reports_ready_when_persistence_ok(monkeypatch) -> None:
    monkeypatch.setattr(ops_readiness, "readiness_payload", _fake_ready_ok)
    monkeypatch.setattr(
        ops_readiness,
        "get_event_loop_sampler",
        lambda: _StubSampler({"windows_completed": 0}),
    )
    with _client() as client:
        payload = client.get("/api/ops/readiness").json()
    assert payload["status"] == "ready"
    assert payload["failing"] == []


def test_readiness_endpoint_degrades_when_scheduler_poller_stopped(monkeypatch) -> None:
    monkeypatch.setattr(ops_readiness, "readiness_payload", _fake_ready_ok)
    monkeypatch.setattr(
        ops_readiness,
        "get_event_loop_sampler",
        lambda: _StubSampler({"windows_completed": 0}),
    )
    with _client() as client:
        client.app.state.scheduled_task_service = SimpleNamespace(_task=None)
        payload = client.get("/api/ops/readiness").json()
    assert payload["status"] == "degraded"
    assert "scheduler:poller_stopped" in payload["failing"]


def test_metrics_endpoint_renders_prometheus_text(monkeypatch) -> None:
    from alpha.ops.metrics import MetricsRegistry

    fresh = MetricsRegistry()
    monkeypatch.setattr(ops_integration, "get_metrics_registry", lambda: fresh)
    stub = _StubSampler({"windows_completed": 1, "delay_last_max_ms": 2.0})
    monkeypatch.setattr(ops_integration, "get_event_loop_sampler", lambda: stub)

    with _client() as client:
        response = client.get("/api/ops/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = response.text
    assert "# TYPE alpha_event_loop_windows_completed gauge" in body
    assert "alpha_event_loop_windows_completed 1" in body
    assert "alpha_event_loop_delay_last_max_ms 2" in body
    assert "alpha_process_uptime_seconds " in body
    # No drops occurred -> the drop counter must be absent, not zero.
    assert "alpha_metrics_series_dropped_total" not in body


def test_metrics_endpoint_omits_delay_series_when_no_signal(monkeypatch) -> None:
    from alpha.ops.metrics import MetricsRegistry

    fresh = MetricsRegistry()
    monkeypatch.setattr(ops_integration, "get_metrics_registry", lambda: fresh)
    monkeypatch.setattr(
        ops_integration,
        "get_event_loop_sampler",
        lambda: _StubSampler({"windows_completed": 0}),
    )
    with _client() as client:
        body = client.get("/api/ops/metrics").text
    assert "alpha_event_loop_windows_completed 0" in body
    assert "alpha_event_loop_delay_last_max_ms" not in body
