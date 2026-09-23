"""Tests for the scheduled-task queue-health collector and its endpoint.

Evidence-first: every assertion below was pinned against the actual source
before this file was written.

* ``app/scheduler/queue_health.py`` — ``QUEUE_SAMPLE_LIMIT = 200``, the pure
  ``build_queue_health`` builder, and the async ``collect_queue_health``
  collector (repository/service facts in, redacted report + ``alpha_scheduler_*``
  gauges out).
* ``app/gateway/routers/scheduled_tasks.py`` — ``GET /api/scheduled-tasks/queue-health``
  is registered (~line 188) *before* ``GET /scheduled-tasks/{task_id}``
  (~line 275) so the literal segment is not swallowed by the wildcard, and it
  is guarded by ``@require_permission("threads", "read")`` plus an explicit
  401 raised inside the handler when ``get_optional_user_from_request``
  yields no user.

The contract under test: the report is payload-free — only counts, ages,
configuration numbers and status labels may appear in the serialized JSON or
in the recorded metric names/labels.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from alpha.ops.metrics import MetricsRegistry
from app.gateway.authz import AuthContext
from app.gateway.routers import scheduled_tasks
from app.scheduler.queue_health import (
    QUEUE_SAMPLE_LIMIT,
    build_queue_health,
    collect_queue_health,
)

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)
URL = "/api/scheduled-tasks/queue-health"
TASK_URL = "/api/scheduled-tasks/task-SENTINEL-ID"

# Marker values that must never reach a report, a metric name or a label.
_SENTINELS = (
    "SENTINEL",
    "TITLE-SENTINEL",
    "PROMPT-SENTINEL",
    "ERROR-SENTINEL",
    "task-SENTINEL-ID",
    "run-SENTINEL-ID",
    "thread-SENTINEL",
    "user-SENTINEL",
    "queue-health-user",
)

# Keys a payload-carrying row would have; none of them may survive redaction.
_FORBIDDEN_KEYS = {
    "id",
    "task_id",
    "run_id",
    "thread_id",
    "user_id",
    "title",
    "prompt",
    "error",
    "assistant_id",
    "schedule_spec",
    "next_run_at",
}

_REPORT_KEYS = {
    "status",
    "failing",
    "poller",
    "tasks",
    "occurrences",
    "limits",
    "generated_at",
}

_SCHEDULER_GAUGES = {
    "alpha_scheduler_active_runs",
    "alpha_scheduler_queued_sample_count",
    "alpha_scheduler_oldest_queued_age_seconds",
    "alpha_scheduler_ready",
}

_MISSING = object()


@pytest.fixture(autouse=True)
def _agent_workspace_home(tmp_path, monkeypatch):
    """Confine ``AGENT_WORKSPACE_HOME`` to a per-test temp directory."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


# -- stubs -------------------------------------------------------------


class _TaskRepo:
    """Caller-scoped task rows; carries payload on purpose (must not leak)."""

    def __init__(self, tasks):
        self._tasks = list(tasks)
        self.listed_for: str | None = None

    async def list_by_user(self, user_id):
        self.listed_for = user_id
        return list(self._tasks)

    async def get(self, task_id, *, user_id):
        # Keyed by id only: this stub exists to prove the {task_id} wildcard
        # resolves to its own handler, not to model owner scoping.
        for task in self._tasks:
            if task["id"] == task_id:
                return task
        return None


class _RunRepo:
    """Stub of the scheduler's bounded drain view (``list_queued_runs``)."""

    def __init__(self, rows, *, active_runs=0):
        self._rows = list(rows)
        self._active_runs = active_runs
        self.requested_limits: list[int] = []

    async def count_active_runs(self):
        return self._active_runs

    async def list_queued_runs(self, *, limit):
        # Mirrors the SQL view: never returns more rows than were requested.
        self.requested_limits.append(limit)
        return list(self._rows[:limit])


def _payload_task(status="enabled"):
    """A fully populated task row: every payload field must be dropped."""
    return {
        "id": "task-SENTINEL-ID",
        "user_id": "user-SENTINEL",
        "thread_id": "thread-SENTINEL",
        "title": "TITLE-SENTINEL",
        "prompt": "PROMPT-SENTINEL",
        "assistant_id": "lead_agent",
        "schedule_type": "cron",
        "schedule_spec": {"cron": "0 9 * * *"},
        "timezone": "UTC",
        "status": status,
        "next_run_at": None,
    }


def _queued_row(created_at, **payload):
    """A queued occurrence row: only ``created_at`` may be read from it."""
    row = {
        "id": "run-SENTINEL-ID",
        "task_id": "task-SENTINEL-ID",
        "thread_id": "thread-SENTINEL",
        "user_id": "user-SENTINEL",
        "status": "queued",
        "error": "ERROR-SENTINEL",
        "attempt_count": 0,
        "created_at": created_at.isoformat(),
    }
    row.update(payload)
    return row


def _service(*, running=True, max_concurrent_runs=4, queue_timeout_seconds=60):
    """Stand-in for ``ScheduledTaskService`` (read-only introspection)."""
    task = None if running is None else SimpleNamespace(done=lambda: not running)
    return SimpleNamespace(
        _task=task,
        _max_concurrent_runs=max_concurrent_runs,
        _queue_timeout_seconds=queue_timeout_seconds,
    )


def _fresh_registry(monkeypatch) -> MetricsRegistry:
    """Route collection at a fresh registry so assertions see only this run."""
    registry = MetricsRegistry()
    monkeypatch.setattr("alpha.ops.metrics.get_metrics_registry", lambda: registry)
    return registry


def _sample_lines(text):
    """Sample lines only — ``# HELP`` lines can echo ``<name> 1`` in prose."""
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def _keys(node, acc=None):
    """Recursively collect every mapping key present in the report."""
    acc = set() if acc is None else acc
    if isinstance(node, dict):
        for key, value in node.items():
            acc.add(key)
            _keys(value, acc)
    elif isinstance(node, list):
        for item in node:
            _keys(item, acc)
    return acc


async def _collect(monkeypatch, *, tasks=(), rows=(), active_runs=0, service=_MISSING, now=NOW):
    """Run the real collector against stub repos; return report + collaborators."""
    registry = _fresh_registry(monkeypatch)
    task_repo = _TaskRepo(tasks)
    run_repo = _RunRepo(rows, active_runs=active_runs)
    report = await collect_queue_health(
        task_repo=task_repo,
        run_repo=run_repo,
        service=None if service is _MISSING else service,
        user_id="user-SENTINEL",
        now=now,
    )
    return report, task_repo, run_repo, registry


def _client(monkeypatch, *, auth="ok", tasks=(), rows=(), active_runs=0, service=_MISSING):
    """Bare FastAPI app with the real router and a stub auth middleware."""
    app = FastAPI()
    app.include_router(scheduled_tasks.router)

    @app.middleware("http")
    async def authenticate(request, call_next):
        user = None if auth == "anonymous" else SimpleNamespace(id="queue-health-user")
        permissions = [] if auth == "denied" else ["threads:read"]
        request.state.auth = AuthContext(user=user, permissions=permissions)
        return await call_next(request)

    async def user_from_request(request):
        return request.state.auth.user

    monkeypatch.setattr(scheduled_tasks, "get_optional_user_from_request", user_from_request)
    task_repo = _TaskRepo(tasks)
    run_repo = _RunRepo(rows, active_runs=active_runs)
    monkeypatch.setattr(scheduled_tasks, "get_scheduled_task_repo", lambda _request: task_repo)
    monkeypatch.setattr(scheduled_tasks, "get_scheduled_task_run_repo", lambda _request: run_repo)
    if service is not _MISSING:
        app.state.scheduled_task_service = service
    _fresh_registry(monkeypatch)
    return TestClient(app)


# 1. payload-free body -------------------------------------------------


@pytest.mark.asyncio
async def test_collected_report_drops_every_payload_field(monkeypatch):
    report, task_repo, _, _ = await _collect(
        monkeypatch,
        tasks=[_payload_task("enabled"), _payload_task("paused")],
        rows=[_queued_row(NOW - timedelta(seconds=5))],
        active_runs=2,
        service=_service(),
    )

    blob = json.dumps(report, sort_keys=True)
    # No payload key survives redaction...
    assert not (_keys(report) & _FORBIDDEN_KEYS), sorted(_keys(report) & _FORBIDDEN_KEYS)
    # ...and no payload *value* appears anywhere in the serialized body.
    for sentinel in _SENTINELS:
        assert sentinel not in blob, sentinel

    # What remains is counts / ages / config numbers / status labels only.
    assert set(report) == _REPORT_KEYS
    assert report["tasks"] == {"total": 2, "by_status": {"enabled": 1, "paused": 1}}
    assert report["occurrences"] == {
        "active_runs": 2,
        "queued_sample_count": 1,
        "queued_sample_limit": QUEUE_SAMPLE_LIMIT,
        "queued_sample_capped": False,
        "oldest_queued_age_seconds_in_sample": 5.0,
    }
    assert report["limits"] == {"max_concurrent_runs": 4, "queue_timeout_seconds": 60}
    assert report["poller"] == "running"
    # The caller id is used only to scope the query, never echoed back.
    assert task_repo.listed_for == "user-SENTINEL"
    assert "user-SENTINEL" not in blob


@pytest.mark.asyncio
async def test_endpoint_response_is_payload_free(monkeypatch):
    client = _client(
        monkeypatch,
        tasks=[_payload_task("enabled")],
        rows=[_queued_row(NOW - timedelta(seconds=5))],
        active_runs=1,
        service=_service(),
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text

    blob = response.text
    for sentinel in _SENTINELS:
        assert sentinel not in blob, sentinel
    payload = response.json()
    assert set(payload) == _REPORT_KEYS
    assert not (_keys(payload) & _FORBIDDEN_KEYS)


# 2. bounded sample honesty -------------------------------------------


def test_queue_sample_limit_is_two_hundred():
    assert QUEUE_SAMPLE_LIMIT == 200


@pytest.mark.asyncio
async def test_collector_requests_at_most_the_sample_limit(monkeypatch):
    # 201 rows exist; the bounded drain view is only allowed to return 200.
    rows = [_queued_row(NOW - timedelta(seconds=10)) for _ in range(QUEUE_SAMPLE_LIMIT)]
    rows.append(_queued_row(NOW - timedelta(seconds=9999)))
    report, _, run_repo, _ = await _collect(monkeypatch, rows=rows, service=_service())

    assert run_repo.requested_limits == [QUEUE_SAMPLE_LIMIT]
    occurrences = report["occurrences"]
    assert occurrences["queued_sample_limit"] == QUEUE_SAMPLE_LIMIT
    assert occurrences["queued_sample_count"] == QUEUE_SAMPLE_LIMIT
    # The cap flag is honest: it flips on exactly when rows hit the limit...
    assert occurrences["queued_sample_capped"] is True
    # ...because the age below is exact only within the bounded sample. The
    # 9999s row sits outside it, so it must not be reported as the oldest —
    # ``queued_sample_capped`` is what tells the reader the sample may be
    # hiding older rows rather than the age silently claiming global truth.
    assert occurrences["oldest_queued_age_seconds_in_sample"] == 10.0
    assert "9999" not in json.dumps(report)


def test_queued_sample_capped_flips_at_the_limit():
    def build(count):
        return build_queue_health(
            task_statuses=[],
            active_runs=0,
            queued_sample=[_queued_row(NOW - timedelta(seconds=1)) for _ in range(count)],
            max_concurrent_runs=None,
            queue_timeout_seconds=None,
            poller="not_configured",
            sample_limit=QUEUE_SAMPLE_LIMIT,
            now=NOW,
        )

    below = build(QUEUE_SAMPLE_LIMIT - 1)
    at_limit = build(QUEUE_SAMPLE_LIMIT)
    assert below["occurrences"]["queued_sample_count"] == 199
    assert below["occurrences"]["queued_sample_capped"] is False
    assert at_limit["occurrences"]["queued_sample_count"] == 200
    assert at_limit["occurrences"]["queued_sample_capped"] is True
    assert below["status"] == at_limit["status"] == "ready"


@pytest.mark.asyncio
async def test_oldest_queued_age_reports_none_without_queued_rows(monkeypatch):
    report, _, _, _ = await _collect(monkeypatch, rows=[], service=_service())
    assert report["occurrences"]["queued_sample_count"] == 0
    assert report["occurrences"]["oldest_queued_age_seconds_in_sample"] is None
    assert report["occurrences"]["queued_sample_capped"] is False


# 3. degraded verdicts -------------------------------------------------


@pytest.mark.asyncio
async def test_configured_but_stopped_poller_is_degraded(monkeypatch):
    report, _, _, registry = await _collect(monkeypatch, rows=[], service=_service(running=False))
    assert report["poller"] == "stopped"
    assert report["status"] == "degraded"
    assert report["failing"] == ["scheduler:poller_stopped"]
    assert "alpha_scheduler_ready 0" in _sample_lines(registry.render_prometheus())


@pytest.mark.asyncio
async def test_in_sample_oldest_queued_age_at_timeout_is_degraded(monkeypatch):
    # Age is exactly queue_timeout_seconds -> the `>=` boundary must trip.
    report, _, _, _ = await _collect(
        monkeypatch,
        rows=[_queued_row(NOW - timedelta(seconds=60))],
        service=_service(running=True, queue_timeout_seconds=60),
    )
    assert report["poller"] == "running"
    assert report["occurrences"]["oldest_queued_age_seconds_in_sample"] == 60.0
    assert report["status"] == "degraded"
    assert report["failing"] == ["queue:oldest_queued_age_exceeds_timeout"]


@pytest.mark.asyncio
async def test_fresh_queue_below_timeout_is_ready(monkeypatch):
    report, _, _, registry = await _collect(
        monkeypatch,
        rows=[_queued_row(NOW - timedelta(seconds=5))],
        service=_service(running=True, queue_timeout_seconds=60),
    )
    assert report["poller"] == "running"
    assert report["occurrences"]["oldest_queued_age_seconds_in_sample"] == 5.0
    assert report["status"] == "ready"
    assert report["failing"] == []
    assert "alpha_scheduler_ready 1" in _sample_lines(registry.render_prometheus())


@pytest.mark.asyncio
async def test_not_configured_scheduler_is_not_degraded(monkeypatch):
    # Disabled scheduler: service handle absent -> poller not_configured, no 503.
    report, _, _, _ = await _collect(monkeypatch, rows=[], service=None)
    assert report["poller"] == "not_configured"
    assert report["status"] == "ready"
    assert report["failing"] == []
    assert report["limits"] == {"max_concurrent_runs": None, "queue_timeout_seconds": None}


@pytest.mark.parametrize(
    ("poller", "expected_failing"),
    [
        ("not_configured", []),
        ("running", []),
        ("stopped", ["scheduler:poller_stopped"]),
    ],
)
def test_poller_verdict_labels(poller, expected_failing):
    report = build_queue_health(
        task_statuses=["enabled"],
        active_runs=0,
        queued_sample=[],
        max_concurrent_runs=2,
        queue_timeout_seconds=30,
        poller=poller,
        sample_limit=QUEUE_SAMPLE_LIMIT,
        now=NOW,
    )
    assert report["poller"] == poller
    assert report["failing"] == expected_failing
    assert report["status"] == ("degraded" if expected_failing else "ready")


def test_both_degraded_verdicts_are_reported_independently():
    report = build_queue_health(
        task_statuses=[],
        active_runs=0,
        queued_sample=[_queued_row(NOW - timedelta(seconds=300))],
        max_concurrent_runs=None,
        queue_timeout_seconds=60,
        poller="stopped",
        sample_limit=QUEUE_SAMPLE_LIMIT,
        now=NOW,
    )
    assert report["status"] == "degraded"
    assert report["failing"] == [
        "scheduler:poller_stopped",
        "queue:oldest_queued_age_exceeds_timeout",
    ]


# 4. endpoint registration order ---------------------------------------


def test_queue_health_route_is_registered_before_the_task_id_wildcard():
    app = FastAPI()
    app.include_router(scheduled_tasks.router)
    paths = [route.path for route in app.routes if hasattr(route, "path")]
    literal = "/api/scheduled-tasks/queue-health"
    wildcard = "/api/scheduled-tasks/{task_id}"
    assert literal in paths
    assert wildcard in paths
    # Pins the registration-order comment in scheduled_tasks.py.
    assert paths.index(literal) < paths.index(wildcard)


def test_queue_health_endpoint_returns_the_queue_health_shape(monkeypatch):
    client = _client(
        monkeypatch,
        tasks=[_payload_task("enabled")],
        rows=[_queued_row(NOW - timedelta(seconds=5))],
        active_runs=1,
        service=_service(),
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text

    payload = response.json()
    # A {task_id} response would be a task row; this is the queue-health shape.
    assert set(payload) == _REPORT_KEYS
    assert payload["status"] == "ready"
    assert payload["poller"] == "running"
    assert payload["occurrences"]["queued_sample_limit"] == QUEUE_SAMPLE_LIMIT
    assert "id" not in payload and "title" not in payload


def test_queue_health_is_not_swallowed_by_the_task_id_wildcard(monkeypatch):
    """Both routes resolve to their own handlers on the same app."""
    task = _payload_task("enabled")
    client = _client(monkeypatch, tasks=[task])

    detail = client.get(TASK_URL)
    assert detail.status_code == 200, detail.text
    # The wildcard still serves real task rows (payload and all)...
    assert detail.json()["id"] == "task-SENTINEL-ID"
    assert detail.json()["title"] == "TITLE-SENTINEL"

    health = client.get(URL)
    assert health.status_code == 200, health.text
    # ...while the literal segment returns the redacted health report instead.
    payload = health.json()
    assert set(payload) == _REPORT_KEYS
    assert "TITLE-SENTINEL" not in health.text


def test_endpoint_reports_not_configured_when_scheduler_disabled(monkeypatch):
    # Bare app: app.state carries no scheduled_task_service at all.
    client = _client(monkeypatch, tasks=[_payload_task("enabled")])
    response = client.get(URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["poller"] == "not_configured"
    assert payload["status"] == "ready"
    assert payload["failing"] == []


# 5. auth posture ------------------------------------------------------


@pytest.mark.parametrize(("auth", "status"), [("anonymous", 401), ("denied", 403)])
def test_queue_health_endpoint_enforces_authentication_and_read_permission(monkeypatch, auth, status):
    client = _client(monkeypatch, auth=auth, tasks=[_payload_task()])
    response = client.get(URL)
    assert response.status_code == status, response.text


@pytest.mark.asyncio
async def test_handler_raises_explicit_401_without_user(monkeypatch):
    """Below the decorator: the handler itself refuses an absent user."""
    monkeypatch.setattr(scheduled_tasks, "get_optional_user_from_request", AsyncMock(return_value=None))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(HTTPException) as exc_info:
        await call_unwrapped(scheduled_tasks.scheduled_task_queue_health, request=request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


# 6. gauges ------------------------------------------------------------


@pytest.mark.asyncio
async def test_collection_records_only_alpha_scheduler_gauges_without_payload(monkeypatch):
    report, _, _, registry = await _collect(
        monkeypatch,
        tasks=[_payload_task("enabled")],
        rows=[_queued_row(NOW - timedelta(seconds=5))],
        active_runs=3,
        service=_service(),
    )
    text = registry.render_prometheus()

    declared = {line.split()[2] for line in text.splitlines() if line.startswith("# HELP ")}
    assert declared == _SCHEDULER_GAUGES

    samples = _sample_lines(text)
    assert "alpha_scheduler_active_runs 3" in samples
    assert "alpha_scheduler_queued_sample_count 1" in samples
    assert "alpha_scheduler_oldest_queued_age_seconds 5" in samples
    assert "alpha_scheduler_ready 1" in samples
    assert "alpha_scheduler_ready 0" not in samples

    # Gauges are declared without labels, so no label value can carry an id.
    for line in text.splitlines():
        if line.startswith("alpha_scheduler_"):
            assert "{" not in line, line
    for sentinel in _SENTINELS:
        assert sentinel not in text, sentinel

    # The metric values mirror the report exactly.
    occurrences = report["occurrences"]
    assert occurrences["active_runs"] == 3
    assert occurrences["queued_sample_count"] == 1


@pytest.mark.asyncio
async def test_ready_gauge_is_zero_when_degraded(monkeypatch):
    _, _, _, registry = await _collect(monkeypatch, rows=[], service=_service(running=False))
    samples = _sample_lines(registry.render_prometheus())
    assert "alpha_scheduler_ready 0" in samples
    assert "alpha_scheduler_ready 1" not in samples


@pytest.mark.asyncio
async def test_oldest_queued_age_gauge_is_absent_without_queued_rows(monkeypatch):
    # Declared-but-empty must not render: absence is the honest "no signal".
    _, _, _, registry = await _collect(monkeypatch, rows=[], service=_service())
    text = registry.render_prometheus()
    assert "alpha_scheduler_oldest_queued_age_seconds" not in text
    assert "alpha_scheduler_ready 1" in _sample_lines(text)
