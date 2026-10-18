"""Run-correlated swarm recovery (bot-mode master plan, phase 3.1).

A Gateway restart cannot know which worker still owns a lease, so
:class:`~alpha.swarm.coordinator.SwarmCoordinator` parks every non-terminal
in-flight plan as ``paused`` with
``terminal_reason = RESTART_TERMINAL_REASON`` and requeues its
RUNNING/STRAGGLING tasks with their leases cleared
(``_load_persisted_swarms``). This module is the vocabulary *after* that
parking:

- :func:`scan_recoverable` — which parked plans are restart-recovered
  (owner-scoped, bounded; the bound reports truncation rather than hiding it).
- :func:`resume` — flip one recovered plan back to ``running``. Any task still
  holding a live-looking lease is re-parked to PENDING lease-free first, so the
  next scheduler tick claims **fresh** lease ids and nothing the dead process
  held can fence a new attempt.
- :func:`abandon` — cancel one paused plan with a named reason.
- :func:`auto_resume_on_start` — the one-shot startup pass, gated by
  ``config.yaml -> swarm.resume_on_start`` (``none`` | ``owned`` | ``all``,
  default ``none``: auto-resume spends model tokens, so it is opt-in — plan 11.3).

Owner-scoped reads answer ``not_found`` for another tenant's plan — never 403 —
matching the Gateway's swarm visibility rule. Tests:
``backend/tests/test_swarm_recovery.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from alpha.swarm.models import TaskNodeState

if TYPE_CHECKING:
    from alpha.swarm.coordinator import SwarmCoordinator
    from alpha.swarm.models import SwarmPlan

logger = logging.getLogger(__name__)

#: The plan-level ``terminal_reason`` written when a restart parks an
#: in-flight plan. Single authority: the coordinator writes it, this module
#: decides what counts as restart-recovered, and the frontend banner keys on
#: the same string (``lib/teamops.ts``).
RESTART_TERMINAL_REASON = "process_restart_requires_resume"

#: ``owned`` resumes only plans carrying this installation-local owner id —
#: the coordinator's own ``SwarmPlan.owner_id`` default. ``all`` reaches every
#: owner; neither ever resumes a plan another tenant explicitly paused.
INSTALLATION_OWNER_ID = "default"

#: The recovery scan is a bounded read, never an exhaustive enumeration claim.
#: Hitting the bound sets ``scan_truncated`` in the report.
DEFAULT_SCAN_LIMIT = 200

_VALID_MODES = frozenset({"none", "owned", "all"})


def _coordinator_or_default(coordinator: SwarmCoordinator | None) -> SwarmCoordinator:
    # Imported lazily: the coordinator imports this module's reason constant,
    # so a module-scope import here would cycle.
    if coordinator is not None:
        return coordinator

    from alpha.swarm.coordinator import get_swarm_coordinator

    return get_swarm_coordinator()


def is_restart_recovered(plan: SwarmPlan) -> bool:
    """Whether this plan was parked by a process restart, not by an operator."""
    return plan.status == "paused" and plan.terminal_reason == RESTART_TERMINAL_REASON


def scan_recoverable(
    coordinator: SwarmCoordinator | None = None,
    *,
    owner_id: str | None = None,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> list[SwarmPlan]:
    """The restart-recovered plans awaiting an explicit resume, newest first."""
    coordinator = _coordinator_or_default(coordinator)
    plans = coordinator.list_swarms(max(1, int(limit)), owner_id=owner_id, status="paused")
    return [plan for plan in plans if is_restart_recovered(plan)]


def resume(
    swarm_id: str,
    *,
    coordinator: SwarmCoordinator | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    """Resume one restart-recovered plan. Refuses anything else by name."""
    coordinator = _coordinator_or_default(coordinator)
    plan = coordinator.get_swarm(swarm_id, owner_id=owner_id)
    if plan is None:
        return {"swarm_id": swarm_id, "resumed": False, "status": "not_found", "reason": "not_found"}
    if not is_restart_recovered(plan):
        return {
            "swarm_id": swarm_id,
            "resumed": False,
            "status": plan.status,
            "reason": "not_paused" if plan.status != "paused" else "not_restart_recovered",
        }
    with coordinator.state_lock:
        # Fresh leases by construction: a task still marked RUNNING (e.g. the
        # checkpoint landed mid-commit) is requeued lease-free before the
        # resume, so no dead process's lease id can fence the next attempt.
        for task in plan.tasks.values():
            if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                task.state = TaskNodeState.PENDING
                task.lease_id = None
                task.lease_owner = None
                task.lease_expires_at = None
        coordinator.append_event(swarm_id, "SWARM_RECOVERY_RESUME", details={"reason": RESTART_TERMINAL_REASON})
        ok = coordinator.resume_swarm(swarm_id)
    return {
        "swarm_id": swarm_id,
        "resumed": ok,
        "status": "running" if ok else plan.status,
        "reason": "resumed_after_restart" if ok else "resume_refused",
    }


def abandon(
    swarm_id: str,
    reason: str = "",
    *,
    coordinator: SwarmCoordinator | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    """Cancel one paused plan with a named reason (the banner's Abandon)."""
    coordinator = _coordinator_or_default(coordinator)
    plan = coordinator.get_swarm(swarm_id, owner_id=owner_id)
    if plan is None:
        return {"swarm_id": swarm_id, "abandoned": False, "status": "not_found", "reason": "not_found"}
    if plan.status != "paused":
        return {"swarm_id": swarm_id, "abandoned": False, "status": plan.status, "reason": "not_paused"}
    text = str(reason or "").strip() or "abandoned_after_restart"
    ok = coordinator.cancel_swarm(swarm_id, text)
    return {"swarm_id": swarm_id, "abandoned": ok, "status": "cancelled" if ok else plan.status, "reason": text}


def auto_resume_on_start(
    mode: str = "none",
    *,
    coordinator: SwarmCoordinator | None = None,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> dict[str, Any]:
    """The one-shot startup pass applying ``swarm.resume_on_start``.

    ``none`` is the default and touches nothing — no coordinator is even
    constructed, so a policy-less deployment pays nothing for this feature.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"swarm.resume_on_start must be one of {sorted(_VALID_MODES)}, got {mode!r}")
    if mode == "none":
        return {
            "mode": "none",
            "scanned": 0,
            "resumed": [],
            "skipped": [],
            "scan_truncated": False,
            "note": "auto-resume is disabled; restart-recovered swarms wait for an explicit resume",
        }
    coordinator = _coordinator_or_default(coordinator)
    plans = scan_recoverable(coordinator, limit=limit)
    resumed: list[str] = []
    skipped: list[str] = []
    for plan in plans:
        if mode == "owned" and plan.owner_id != INSTALLATION_OWNER_ID:
            skipped.append(plan.swarm_id)
            continue
        outcome = resume(plan.swarm_id, coordinator=coordinator)
        (resumed if outcome["resumed"] else skipped).append(plan.swarm_id)
    report: dict[str, Any] = {
        "mode": mode,
        "scanned": len(plans),
        "resumed": resumed,
        "skipped": skipped,
        "scan_truncated": len(plans) >= max(1, int(limit)),
        "note": "owned: only recovered plans owned by the installation default owner were resumed" if mode == "owned" else "",
    }
    logger.info(
        "swarm.resume_on_start=%s: %d recovered plan(s) scanned, %d resumed, %d skipped",
        mode,
        len(plans),
        len(resumed),
        len(skipped),
    )
    return report
