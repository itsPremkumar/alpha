"""Aggregated readiness report with explicit ``failing`` reasons.

``GET /health/ready`` stays the unauthenticated orchestrator probe owned by
``app.gateway.app`` + :mod:`app.gateway.health` (liveness/readiness split,
probe gates, deadlines). This module is the *operator detail* surface behind
``GET /api/ops/readiness`` (it adds no endpoint-level auth of its own — it
matches this router's existing posture; the queue-health endpoint in
``scheduled_tasks.py`` does authenticate): it reuses the exact same
persistence verdict and adds two more checks, each contributing an entry to
``failing`` when degraded — the shape OpenClaw's ``/readyz`` detail uses
(``failing: [...]`` naming each subsystem).

Checks:

* ``persistence.database`` / ``persistence.checkpointer`` — the
  :func:`app.gateway.health.readiness_payload` verdict (``ok`` /
  ``not_configured`` / ``unreachable``); ``unreachable`` fails readiness.
* ``event_loop`` — from :mod:`alpha.ops.event_loop`. ``no_signal`` (no
  completed sampling window yet) is *not* a failure: absence of data never
  counts as evidence either way. A completed window whose maximum scheduling
  delay exceeds :data:`EVENT_LOOP_DEGRADED_MAX_DELAY_MS` fails readiness.
* ``scheduler`` — whether the durable scheduled-task poller configured on
  ``app.state.scheduled_task_service`` currently has a live poller task.
  ``not_configured`` (scheduler disabled) is not a failure; a configured
  service whose poller task is absent or done means scheduled work is
  stalled and fails readiness.

The endpoint always answers 200 with ``status``/``failing`` — orchestration
traffic must keep using public ``/health/ready`` (which owns the 503), so an
authenticated dashboard never loses the detail body to a status code.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from alpha.ops.event_loop import get_event_loop_sampler
from alpha.ops.metrics import get_metrics_registry
from app.gateway.health import READINESS_CHECKPOINTER_CONFIG_ATTR, readiness_payload

logger = logging.getLogger(__name__)

EVENT_LOOP_DEGRADED_MAX_DELAY_MS = 1000.0


def _event_loop_check(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reduce a sampler snapshot to a check result (omission = no signal)."""
    check: dict[str, Any] = {"status": "no_signal", "windows_completed": int(snapshot.get("windows_completed", 0))}
    if check["windows_completed"] <= 0:
        return check
    for field in ("delay_last_max_ms", "delay_max_ms", "delay_p99_ms", "cpu_core_ratio"):
        if field in snapshot:
            check[field] = snapshot[field]
    last_max = snapshot.get("delay_last_max_ms")
    if last_max is None:
        # Windows exist but the snapshot carries no delay figure: absence of
        # the figure is no signal, never an implicit "ok".
        return check
    if float(last_max) > EVENT_LOOP_DEGRADED_MAX_DELAY_MS:
        check["status"] = "degraded"
    else:
        check["status"] = "ok"
    return check


def _scheduler_check(state: Any) -> dict[str, Any]:
    """Report the scheduled-task poller's liveness from app state (read-only)."""
    service = getattr(state, "scheduled_task_service", None)
    if service is None:
        return {"status": "not_configured"}
    # Read-only introspection of ScheduledTaskService's poller task; the
    # service exposes no public stats() today. `_task` is set by start()
    # and cleared by stop().
    task = getattr(service, "_task", None)
    if task is None or task.done():
        return {"status": "stopped"}
    return {"status": "running"}


def compose_readiness_report(
    *,
    service: str,
    database: str,
    checkpointer: str,
    event_loop: dict[str, Any],
    scheduler: dict[str, Any],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Pure composition of check results into the readiness report shape."""
    failing: list[str] = []
    if database == "unreachable":
        failing.append("database:unreachable")
    if checkpointer == "unreachable":
        failing.append("checkpointer:unreachable")
    if event_loop.get("status") == "degraded":
        failing.append("event_loop:delay")
    if scheduler.get("status") == "stopped":
        failing.append("scheduler:poller_stopped")
    return {
        "service": service,
        "status": "degraded" if failing else "ready",
        "failing": failing,
        "checks": {
            "persistence": {"database": database, "checkpointer": checkpointer},
            "event_loop": event_loop,
            "scheduler": scheduler,
        },
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
    }


async def build_readiness_report(request: Request) -> dict[str, Any]:
    """Gather live checks and return the operator readiness report."""
    checkpointer_config = getattr(request.app.state, READINESS_CHECKPOINTER_CONFIG_ATTR, None)
    _status_code, payload = await readiness_payload(checkpointer_config)
    sampler = get_event_loop_sampler()
    sampler.ensure_running()
    event_loop = _event_loop_check(sampler.snapshot())
    scheduler = _scheduler_check(request.app.state)
    report = compose_readiness_report(
        service="agent-workspace-gateway",
        database=str(payload.get("database", "unreachable")),
        checkpointer=str(payload.get("checkpointer", "unreachable")),
        event_loop=event_loop,
        scheduler=scheduler,
    )
    registry = get_metrics_registry()
    registry.gauge(
        "alpha_readiness_ready",
        help="1 when the aggregated ops readiness report is ready, 0 when degraded.",
    ).set(1.0 if report["status"] == "ready" else 0.0)
    registry.gauge(
        "alpha_readiness_failing_count",
        help="Number of failing checks in the latest aggregated ops readiness report.",
    ).set(float(len(report["failing"])))
    return report
