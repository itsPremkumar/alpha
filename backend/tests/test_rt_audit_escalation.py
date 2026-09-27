"""rt-auditor: `alpha/runtime/escalation.py` crash-resume ledger attribution.

`recover_swarm_work` requeues a task whose lease died with its worker.  The
durable ledger entry it appends is the only record of *which* worker died, so
the ``from_ref`` it writes must name that worker.  The escalation branch a few
lines above builds the identical ref *before* it clears the lease; the resume
branch built it *after*.
"""

from __future__ import annotations

import time

import pytest

import alpha.runtime.escalation as escalation_mod
import alpha.swarm.coordinator as swarm_coordinator
from alpha.runtime.escalation import (
    DOMAIN_SWARM,
    get_handoff_ledger,
    recover_swarm_work,
    reset_ledger_caches,
)

DEAD_WORKER = "worker-that-died"
ASSIGNED_WORKER = "planner-assigned"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # ``AGENT_WORKSPACE_HOME`` isolates the handoff ledger; ``ALPHA_HOME``
    # isolates the swarm coordinator, whose default storage dir is derived from
    # it and would otherwise carry plans (and their non-unique ``task-map-N``
    # ids) from other tests into this one.
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / "alpha"))
    monkeypatch.setattr(swarm_coordinator, "_GLOBAL_COORDINATOR", None)
    reset_ledger_caches()
    yield
    monkeypatch.setattr(swarm_coordinator, "_GLOBAL_COORDINATOR", None)
    reset_ledger_caches()


def _crashed_task_plan():
    """A plan whose root task is leased by a worker that is now gone."""
    coordinator = swarm_coordinator.get_swarm_coordinator()
    plan = coordinator.create_swarm("resume my swarm work", items=["alpha", "beta"])
    root = next(task for task in plan.tasks.values() if not task.dependencies)
    # A real worker claims the lease it is about to work on, then dies.
    claimed = coordinator.claim_task(plan.swarm_id, root.task_id, owner=DEAD_WORKER, lease_seconds=1)
    assert claimed is not None, "the fixture must actually lease the task"
    # The dispatcher that planned the task is a *different* reference, so the
    # assertions below can tell which one the ledger actually names.
    node = coordinator.get_swarm(plan.swarm_id).tasks[root.task_id]
    node.assigned_worker = ASSIGNED_WORKER
    node.lease_expires_at = time.time() - 60
    coordinator.checkpoint(plan.swarm_id)
    return coordinator, plan, root


def test_swarm_resume_ledger_names_the_worker_whose_lease_died():
    coordinator, plan, root = _crashed_task_plan()

    summary = recover_swarm_work(start_runner=False)
    assert summary["resumed_tasks"] == [root.task_id]

    entries = get_handoff_ledger().entries(domain=DOMAIN_SWARM, kind="resume")
    task_resumes = [entry for entry in entries if entry.task_id == root.task_id]
    assert task_resumes, "the requeued task must have a durable resume record"
    entry = task_resumes[0]
    assert entry.from_ref == f"swarm:{plan.swarm_id}:{DEAD_WORKER}", f"the crash-resume ledger must name the worker whose lease died, not the planner that assigned it (got {entry.from_ref!r})"
    # Sanity: the requeue really did clear the lease, so the ref can only have
    # come from a value captured before the clear.
    assert coordinator.get_swarm(plan.swarm_id).tasks[root.task_id].lease_owner is None


def test_swarm_resume_ledger_agrees_with_the_escalation_branch():
    """Both crash paths must name the dead lease owner the same way.

    The escalation branch reads ``task.lease_owner`` before clearing it, so the
    two branches are directly comparable: a task whose ceiling is spent and a
    task that is requeued must produce the same ``from_ref`` shape.
    """
    coordinator, plan, root = _crashed_task_plan()
    root.attempts = root.max_attempts
    coordinator.checkpoint(plan.swarm_id)

    summary = recover_swarm_work(start_runner=False)
    assert summary["escalated_tasks"] == [root.task_id]

    entries = get_handoff_ledger().entries(domain=DOMAIN_SWARM)
    escalated = [entry for entry in entries if entry.kind == "escalation"]
    assert escalated, "an exhausted task must be escalated durably"
    assert escalated[0].from_ref == f"swarm:{plan.swarm_id}:{DEAD_WORKER}", f"escalation branch lost the lease owner too (got {escalated[0].from_ref!r})"


def test_plan_resume_ledger_names_the_reason_the_plan_was_parked():
    """A plan-level resume must carry the reason it was parked for.

    ``recover_swarm_work`` clears ``plan.terminal_reason`` as part of unpausing
    and then builds the entry's ``from_ref`` from it, so the
    ``or 'previous_process'`` fallback could never fire and every plan-level
    entry blamed an anonymous previous process.
    """
    coordinator, plan, root = _crashed_task_plan()
    # Park the whole plan the way a process restart does.
    plan.status = "paused"
    plan.terminal_reason = "process_restart_requires_resume"
    coordinator.checkpoint(plan.swarm_id)

    summary = recover_swarm_work(start_runner=False)
    assert plan.swarm_id in summary["resumed_plans"]

    plan_resumes = [entry for entry in get_handoff_ledger().entries(domain=DOMAIN_SWARM, kind="resume") if entry.details.get("scope") == "plan" and entry.details.get("swarm_id") == plan.swarm_id]
    assert plan_resumes, "unpausing a plan must leave a durable plan-level record"
    assert plan_resumes[0].from_ref == f"swarm:{plan.swarm_id}:process_restart_requires_resume", f"plan-level resume lost the parked reason (got {plan_resumes[0].from_ref!r})"


def test_module_still_records_its_own_ledger_entry_kind():
    """Guard: the assertions above are only meaningful on a real ledger."""
    ledger = get_handoff_ledger()
    assert isinstance(ledger, escalation_mod.HandoffLedger)
