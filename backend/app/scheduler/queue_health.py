"""Scheduled-task queue health: counts, ages, and poller liveness only.

Serves ``GET /api/scheduled-tasks/queue-health``. Deliberately payload-free —
no task ids, titles, prompts, thread ids, run errors, or user ids leave this
module; only counts, ages, configuration numbers, and status labels do. That
matches the redaction rule OpenClaw's ingress-pressure snapshot applies.

Scope notes (honest limits, mirrored in the response):

* caller task counts come from ``list_by_user`` (this caller only);
* the queue sample comes from the scheduler's own drain view,
  ``list_queued_runs(limit)``: at most one queued row per thread that has
  older active work, ordered by attempt count first and ``created_at``
  second. Therefore ``oldest_queued_age_seconds_in_sample`` is exact only
  within the bounded sample, and ``queued_sample_capped`` says the sample
  may be hiding more rows;
* ``active_runs`` is the exact global active-occurrence count
  (``count_active_runs``), a number with no identifiers attached.

Degraded verdicts: a configured poller that is not running
(``scheduler:poller_stopped``), or an in-sample queued occurrence at least
``queue_timeout_seconds`` old (``queue:oldest_queued_age_exceeds_timeout``)
— at that age the expiry sweep should already have failed the row, so its
presence means dispatch is stalled.

Collection also records bounded gauges into the shared metrics registry
(``alpha_scheduler_*``) so a Prometheus scrape of ``/api/ops/metrics``
reflects the same numbers this endpoint reports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

QUEUE_SAMPLE_LIMIT = 200


def _age_seconds(created_at: Any, now: datetime) -> float | None:
    """Age in seconds of an ISO timestamp; None when absent/unparseable."""
    if not created_at or not isinstance(created_at, str):
        return None
    try:
        stamp = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return max(0.0, (now - stamp).total_seconds())


def _poller_status(service: Any) -> str:
    if service is None:
        return "not_configured"
    task = getattr(service, "_task", None)
    if task is None or task.done():
        return "stopped"
    return "running"


def build_queue_health(
    *,
    task_statuses: list[str],
    active_runs: int,
    queued_sample: list[dict[str, Any]],
    max_concurrent_runs: int | None,
    queue_timeout_seconds: int | None,
    poller: str,
    sample_limit: int,
    now: datetime,
) -> dict[str, Any]:
    """Pure builder: rows in, redacted queue-health report out."""
    by_status: dict[str, int] = {}
    for status in task_statuses:
        by_status[status] = by_status.get(status, 0) + 1

    oldest_age: float | None = None
    for row in queued_sample:
        age = _age_seconds(row.get("created_at"), now)
        if age is not None and (oldest_age is None or age > oldest_age):
            oldest_age = age

    failing: list[str] = []
    if poller == "stopped":
        failing.append("scheduler:poller_stopped")
    if oldest_age is not None and queue_timeout_seconds is not None and oldest_age >= float(queue_timeout_seconds):
        failing.append("queue:oldest_queued_age_exceeds_timeout")

    return {
        "status": "degraded" if failing else "ready",
        "failing": failing,
        "poller": poller,
        "tasks": {"total": len(task_statuses), "by_status": by_status},
        "occurrences": {
            "active_runs": int(active_runs),
            "queued_sample_count": len(queued_sample),
            "queued_sample_limit": sample_limit,
            "queued_sample_capped": len(queued_sample) >= sample_limit,
            "oldest_queued_age_seconds_in_sample": None if oldest_age is None else round(oldest_age, 3),
        },
        "limits": {
            "max_concurrent_runs": max_concurrent_runs,
            "queue_timeout_seconds": queue_timeout_seconds,
        },
        "generated_at": now.isoformat(),
    }


async def collect_queue_health(
    *,
    task_repo: Any,
    run_repo: Any,
    service: Any,
    user_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Gather repository/service facts and return the redacted report."""
    from alpha.ops.metrics import get_metrics_registry

    moment = now or datetime.now(UTC)
    tasks = await task_repo.list_by_user(user_id)
    active_runs = await run_repo.count_active_runs()
    queued_sample = await run_repo.list_queued_runs(limit=QUEUE_SAMPLE_LIMIT)
    task_statuses = [str(task.get("status") or "unknown") for task in tasks]
    # Read-only introspection of the config ScheduledTaskService captured at
    # construction; the service exposes no public stats() today.
    max_concurrent = getattr(service, "_max_concurrent_runs", None) if service is not None else None
    queue_timeout = getattr(service, "_queue_timeout_seconds", None) if service is not None else None
    report = build_queue_health(
        task_statuses=task_statuses,
        active_runs=active_runs,
        queued_sample=queued_sample,
        max_concurrent_runs=None if max_concurrent is None else int(max_concurrent),
        queue_timeout_seconds=None if queue_timeout is None else int(queue_timeout),
        poller=_poller_status(service),
        sample_limit=QUEUE_SAMPLE_LIMIT,
        now=moment,
    )

    registry = get_metrics_registry()
    occurrences = report["occurrences"]
    registry.gauge(
        "alpha_scheduler_active_runs",
        help="Exact global count of active (queued/launching/running) scheduled-task occurrences.",
    ).set(float(occurrences["active_runs"]))
    registry.gauge(
        "alpha_scheduler_queued_sample_count",
        help=f"Scheduled-task queued rows visible in the scheduler's bounded drain view (limit {QUEUE_SAMPLE_LIMIT}).",
    ).set(float(occurrences["queued_sample_count"]))
    oldest = occurrences["oldest_queued_age_seconds_in_sample"]
    if oldest is not None:
        registry.gauge(
            "alpha_scheduler_oldest_queued_age_seconds",
            help="Age of the oldest queued scheduled-task occurrence inside the bounded sample (absent = no queued rows).",
        ).set(float(oldest))
    else:
        registry.gauge(
            "alpha_scheduler_oldest_queued_age_seconds",
            help="Age of the oldest queued scheduled-task occurrence inside the bounded sample (absent = no queued rows).",
        ).remove_all()
    registry.gauge(
        "alpha_scheduler_ready",
        help="1 when the scheduled-task queue health report is ready, 0 when degraded.",
    ).set(1.0 if report["status"] == "ready" else 0.0)
    return report
