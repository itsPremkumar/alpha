"""Plan-time advisory disclosure of the process admission schedule.

## Why this exists

A swarm plan can declare up to ``limits.MAX_SWARM_PLAN_CONCURRENCY`` (1000)
concurrent workers, and the runner fans out exactly that wide.  But every swarm
worker executes through ``SubagentExecutor``, which inherits the process-wide
admission controller (``subagent_runtime`` — default 3 running, 64 queued,
``queue`` policy).  A plan that declares 200-way concurrency therefore realizes
3-way parallelism on a default deployment, and a burst wider than
running+queued **in flight at once** is refused admission rather than executed
faster — it comes back as a capacity rejection, not as speed.

Nothing here *changes* that: admission stays operator-owned and the refusal
answer stays governed only by the declared wall budget.  What this module does
is write the admission reality into the plan at ``create_swarm`` time, so the
tool response a model reads and the plan detail an operator reads cannot
oversell capacity the deployment will not realize.

## The rules

- **Advisory, never a gate.** The subsystem rule — projections are unmeasured,
  so they cannot be used for admission — applies to this disclosure too: a
  wide plan is still accepted, and ``wide_plan_risk`` is a labelled warning,
  not a refusal predicate.
- **Unknown is never zero.** If the armed schedule cannot be read, every
  capacity field is ``None`` and the note says so.  ``0`` would claim "the
  process runs no subagents", which is a different fact.
- **Measured on the startup schedule, at plan time.** The controller is
  startup-frozen; the live busy/queued count answers this instant's load and a
  hot-reloaded config answers a restart that has not happened.  The stamped
  figure is the schedule each worker will actually inherit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: The ``plan.metrics`` key the advisory is written under.
ADVISORY_METRIC_KEY = "admission_advisory"

_KNOWN_POLICIES = frozenset({"queue", "reject"})


def _nonneg_int(value: Any) -> int | None:
    """A finite non-negative int, or ``None``. ``0`` is a real value (queue size)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def resolve_admission_schedule() -> Any | None:
    """The armed ``subagent_runtime`` schedule, or ``None`` when unavailable.

    Deliberately lazy: ``subagents`` is the import graph of the executor, and a
    swarm module import must not pull it at module scope.  Any failure to
    resolve is an **unmeasured** disclosure, not an error: a swarm must remain
    creatable in processes where the admission plane was never armed.
    """
    try:
        from alpha.subagents.capacity import configured_subagent_admission_schedule as fetch
    except Exception:
        return None
    try:
        return fetch()
    except Exception:
        return None


def build_admission_advisory(plan_concurrency: int, schedule: Any | None) -> dict[str, Any]:
    """Derive the advisory block from the declared concurrency and the schedule."""
    declared = max(1, int(plan_concurrency))
    if schedule is None:
        return {
            "max_running": None,
            "max_queued": None,
            "admission_policy": None,
            "queue_timeout_seconds": None,
            "expected_realized_concurrency": None,
            "inflight_capacity": None,
            "wide_plan_risk": None,
            "source": None,
            "note": (f"advisory only (never enforced): the process admission schedule was not resolvable for this {declared}-way plan; realized parallelism is unknown, not zero"),
        }

    max_running = _nonneg_int(getattr(schedule, "max_running", None))
    max_queued = _nonneg_int(getattr(schedule, "max_queued", None))
    timeout = _nonneg_int(getattr(schedule, "queue_timeout_seconds", None))
    policy_raw = getattr(schedule, "admission_policy", None)
    policy = policy_raw if policy_raw in _KNOWN_POLICIES else None

    expected_realized = min(declared, max_running) if max_running is not None else None
    if max_running is not None and policy == "reject":
        inflight = max_running
    elif max_running is not None and policy == "queue" and max_queued is not None:
        inflight = max_running + max_queued
    else:
        inflight = None
    wide = declared > inflight if inflight is not None else None

    note = f"advisory only (never enforced): the plan declares {declared}-way task concurrency, the process admission pool runs at most {max_running} subagents concurrently"
    if policy == "queue":
        if max_queued is not None and max_queued > 0:
            note += f" and queues up to {max_queued} more while waiting (queue timeout {timeout}s)"
        else:
            note += " with no queue: concurrent work above the pool is refused immediately"
    elif policy == "reject":
        note += " and refuses admission rather than queueing"
    if wide is True:
        note += f"; in-flight work beyond {inflight} concurrent tasks may be refused admission and retried"

    return {
        "max_running": max_running,
        "max_queued": max_queued,
        "admission_policy": policy,
        "queue_timeout_seconds": timeout,
        "expected_realized_concurrency": expected_realized,
        "inflight_capacity": inflight,
        "wide_plan_risk": wide,
        "source": "startup subagent_runtime admission schedule",
        "note": note,
    }


def admission_advisory_metrics(
    plan_concurrency: int,
    *,
    schedule_provider: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """The advisory block for a plan declared ``plan_concurrency`` workers wide."""
    provider = schedule_provider or resolve_admission_schedule
    try:
        schedule = provider()
    except Exception:
        schedule = None
    return build_admission_advisory(plan_concurrency, schedule)
