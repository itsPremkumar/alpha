"""Swarm fault-injection: one node's failure must not damage its siblings.

The GROUPS runner already has ``test_sibling_survival.py`` and
``test_chaos_fault_injection.py``.  This is the swarm-DAG equivalent, and it
covers the failure classes that were actually reproducible against
``alpha/swarm``:

* a dead node must not cancel or re-execute healthy siblings;
* a failed node's error must reach the caller, not be laundered into a summary
  that reads as partial success;
* fan-out/fan-in must report partial success honestly;
* the hard budget must actually bound real spend, including the spend of a
  discarded attempt;
* every terminal task state must have released its dispatch right;
* reconciliation must be bounded and must not duplicate healthy work.

Every test drives the real ``AsyncSwarmRunner`` with a stubbed worker backend
(``runner._build_worker``), so no provider is ever contacted and every
assertion is about the engine's own bookkeeping.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter

import pytest

import alpha.swarm.coordinator as coord_mod
import alpha.swarm.incidents as inc_mod
from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.models import (
    SwarmBudget,
    SwarmMode,
    SwarmPlan,
    SwarmTaskNode,
    TaskNodeState,
)
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.watchdog import SwarmWatchdog

TERMINAL_STATES = {TaskNodeState.COMPLETED, TaskNodeState.FAILED, TaskNodeState.CANCELLED}


@pytest.fixture(autouse=True)
def _isolated_swarm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    inc_mod._GLOBAL_INCIDENT_MANAGER = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None
    inc_mod._GLOBAL_INCIDENT_MANAGER = None


class StubWorkerFactory:
    """Builds stub workers from a ``task_id -> behaviour`` table and records
    every real execution, so a test can assert on *how many times* an objective
    was actually attempted (not merely on the final state)."""

    def __init__(
        self,
        behaviour: dict[str, str],
        *,
        delay: float = 0.0,
        delays: dict[str, float] | None = None,
        tokens: int = 3,
        tool_calls: int = 1,
    ):
        self.behaviour = behaviour
        self.delay = delay
        self.delays = dict(delays or {})
        self.tokens = tokens
        self.tool_calls = tool_calls
        self.executions: list[str] = []
        self._lock = threading.Lock()

    def counts(self) -> Counter:
        with self._lock:
            return Counter(self.executions)

    def build(self, _task: SwarmTaskNode):
        factory = self

        class _Worker:
            def set_context(self, context):  # noqa: ANN001
                return None

            def execute_task(self, task, plan):  # noqa: ANN001
                with factory._lock:
                    factory.executions.append(task.task_id)
                mode = factory.behaviour.get(task.task_id, "ok")
                time.sleep(factory.delays.get(task.task_id, factory.delay))
                if mode == "fail":
                    raise RuntimeError(f"stub hard failure in {task.task_id}")
                if mode == "cancel":
                    raise asyncio.CancelledError()
                if mode == "garbage":
                    return "not-a-mapping"
                return {
                    "summary": f"deliverable for {task.task_id}: shard analysed and reported.",
                    "evidence": [{"source": "stub", "kind": "note", "reference": f"stub:{task.task_id}"}],
                    "artifacts": [],
                    "tool_calls": factory.tool_calls,
                    "usage": {"input_tokens": factory.tokens, "output_tokens": factory.tokens},
                }

        return _Worker()


def _plan(swarm_id: str, goal: str = "fault injection", *, max_concurrency: int = 4, **kw) -> SwarmPlan:
    return SwarmPlan(
        swarm_id=swarm_id,
        goal=goal,
        mode=SwarmMode.PARALLEL,
        status="running",
        max_concurrency=max_concurrency,
        **kw,
    )


def _runner(coordinator, factory, **kw) -> AsyncSwarmRunner:
    runner = AsyncSwarmRunner(coordinator, poll_interval=0.01, **kw)
    runner._build_worker = factory.build
    return runner


def _event_types(coordinator, swarm_id) -> list[str]:
    return [event.event_type for event in coordinator.get_events(swarm_id, limit=400)]


# ---------------------------------------------------------------------------
# 1. Sibling survival: a hard failure in one node must not touch its siblings
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_failed_node_does_not_cancel_healthy_siblings(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-sibling-survival", max_concurrency=4)
    for index in range(1, 5):
        plan.tasks[f"task-map-{index}"] = SwarmTaskNode(
            task_id=f"task-map-{index}",
            objective=f"process shard {index}",
            max_attempts=1,
        )
    plan.tasks["task-reduce"] = SwarmTaskNode(
        task_id="task-reduce",
        objective="join the healthy shards",
        dependencies=["task-map-1", "task-map-3", "task-map-4"],
        max_attempts=1,
    )
    # A second join that DOES depend on the dead shard. It must be refused
    # honestly rather than run over a missing input or silently dropped.
    plan.tasks["task-join-all"] = SwarmTaskNode(
        task_id="task-join-all",
        objective="join every shard including the dead one",
        dependencies=["task-map-1", "task-map-2", "task-map-3", "task-map-4"],
        max_attempts=1,
    )
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-map-2": "fail"})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    healthy = ["task-map-1", "task-map-3", "task-map-4"]
    for task_id in healthy:
        assert plan.tasks[task_id].state == TaskNodeState.COMPLETED, f"{task_id} must survive its sibling's failure"
    # The join over the healthy shards still ran: a failure upstream must not
    # silently skip an unrelated fan-in.
    assert plan.tasks["task-reduce"].state == TaskNodeState.COMPLETED
    # ...but the join that needed the dead shard is refused, with a reason.
    assert plan.tasks["task-join-all"].state == TaskNodeState.FAILED
    assert "unrunnable" in (plan.tasks["task-join-all"].error_message or "")
    # The plan is NOT reported as clean.
    assert plan.status == "partial_success"
    assert result["status"] == "partial_success"
    assert plan.tasks["task-map-2"].state == TaskNodeState.FAILED
    # The failure is visible in the caller's deliverable, with the ids.
    assert "Failed Tasks" in plan.final_result
    assert "task-map-2" in plan.final_result
    assert "task-join-all" in plan.final_result
    assert "task-map-1" in plan.final_result and "task-reduce" in plan.final_result


@pytest.mark.asyncio
async def test_a_failed_nodes_error_message_reaches_the_caller(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-error-surfaced")
    plan.tasks["task-ok"] = SwarmTaskNode(task_id="task-ok", objective="healthy work", max_attempts=1)
    plan.tasks["task-bad"] = SwarmTaskNode(task_id="task-bad", objective="doomed work", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-bad": "fail"})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    # The plan object the caller holds carries the real cause...
    assert "stub hard failure in task-bad" in plan.tasks["task-bad"].error_message
    # ...the event journal carries it...
    failures = [e for e in coordinator.get_events(plan.swarm_id, limit=200) if e.event_type == "TASK_FAILED"]
    assert failures and any("stub hard failure in task-bad" in str(e.details.get("error")) for e in failures)
    # ...and the run is not laundered into something that reads as success.
    assert result["status"] == "partial_success"
    assert result["aggregated"] is True
    assert result["result"]["failed_tasks"] == 1
    assert result["result"]["completed_tasks"] == 1


# ---------------------------------------------------------------------------
# 2. Terminal states must have released their dispatch right
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_every_terminal_task_state_has_released_its_lease(tmp_path):
    """A FAILED task that still owns an unexpired lease is an illegal state.

    ``runner``'s "orphaned execution disappeared" branch is the one terminal
    transition in the runner that used to skip the lease release, so a
    cancelled/failed node was persisted and projected as still owning work.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-lease-release")
    plan.tasks["task-ok"] = SwarmTaskNode(task_id="task-ok", objective="healthy work", max_attempts=1)
    plan.tasks["task-cancel"] = SwarmTaskNode(task_id="task-cancel", objective="cancelling work", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-cancel": "cancel"})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    assert result["status"] in {"partial_success", "failed"}
    for task in plan.tasks.values():
        if task.state in TERMINAL_STATES:
            assert task.lease_id is None, f"{task.task_id} is {task.state} but still holds lease {task.lease_id}"
            assert task.lease_owner is None, f"{task.task_id} is {task.state} but still owns {task.lease_owner}"
            assert task.lease_expires_at is None, f"{task.task_id} is {task.state} but still has a lease expiry"
            assert task.next_attempt_at is None, f"{task.task_id} is {task.state} but is still scheduled for a retry"


@pytest.mark.asyncio
async def test_a_node_found_running_with_no_execution_is_failed_and_releases_its_lease(tmp_path):
    """The runner's "orphaned execution" transition.

    A plan can arrive with a node still marked RUNNING and holding an
    *unexpired* lease while nothing is actually executing it (a cooperating
    process died between the two facts).  The runner must retire that node --
    and must do it like every other terminal transition, releasing the
    dispatch right.  This branch used to leave the node FAILED but still
    holding its lease, so the checkpoint and every API projection reported a
    dead task that still claimed to own an unexpired lease.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-orphaned-lease", max_concurrency=4)
    plan.tasks["task-ok"] = SwarmTaskNode(task_id="task-ok", objective="healthy work", max_attempts=1)
    plan.tasks["task-zombie"] = SwarmTaskNode(
        task_id="task-zombie",
        objective="claimed to be running, but no execution exists",
        state=TaskNodeState.RUNNING,
        lease_id="lease-from-a-dead-process",
        lease_owner="runner:someone-else",
        # Far in the future, so the watchdog's lease-expiry path does not fire
        # first and the orphaned branch is the one under test.
        lease_expires_at=time.time() + 3600.0,
        started_at=time.time(),
    )
    coordinator._swarms[plan.swarm_id] = plan

    result = await asyncio.wait_for(_runner(coordinator, StubWorkerFactory({})).run_swarm_async(plan.swarm_id), timeout=30)

    zombie = plan.tasks["task-zombie"]
    assert zombie.state == TaskNodeState.FAILED
    assert "orphaned" in (zombie.error_message or "")
    assert zombie.lease_id is None, "a FAILED node must not still own a dispatch right"
    assert zombie.lease_owner is None
    assert zombie.lease_expires_at is None
    assert zombie.completed_at is not None
    assert plan.tasks["task-ok"].state == TaskNodeState.COMPLETED
    assert result["status"] == "partial_success"


@pytest.mark.asyncio
async def test_worker_scoped_cancellation_is_a_bounded_failure_not_a_stranded_node(tmp_path):
    """A worker that cancels itself must not park its node in RUNNING.

    Re-raising ``CancelledError`` without touching the node left it RUNNING
    with a live lease, unreapable until the lease expired, and the eventual
    terminal error was the misleading "orphaned: execution disappeared while
    the swarm was idle" instead of the real cause.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-cancel-scope")
    plan.tasks["task-ok"] = SwarmTaskNode(task_id="task-ok", objective="healthy work", max_attempts=1)
    plan.tasks["task-cancel"] = SwarmTaskNode(
        task_id="task-cancel",
        objective="self-cancelling work",
        max_attempts=1,
    )
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-cancel": "cancel"})
    await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    cancelled = plan.tasks["task-cancel"]
    assert cancelled.state == TaskNodeState.FAILED
    assert "cancelled" in (cancelled.error_message or "").lower()
    assert "orphaned" not in (cancelled.error_message or "")
    assert cancelled.lease_id is None
    assert plan.tasks["task-ok"].state == TaskNodeState.COMPLETED
    # The cancellation must not be able to loop forever on the same node.
    assert factory.counts()["task-cancel"] == 1, "max_attempts=1 must bound a self-cancelling worker"


# ---------------------------------------------------------------------------
# 3. No duplicate (double) execution of a healthy node
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_fast_sibling_does_not_trigger_a_duplicate_execution(tmp_path):
    """A single instant completion must not redefine the plan as "fast".

    The straggler threshold was the bare mean of measured durations, so one
    sub-millisecond completion put every concurrent sibling over the 2.5x bar
    and the speculative-backup path launched a SECOND real execution of a
    healthy node.  In a 120-node plan that produced 122 executions.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-no-duplicate", max_concurrency=4)
    plan.tasks["task-fast"] = SwarmTaskNode(task_id="task-fast", objective="fast shard", max_attempts=1)
    plan.tasks["task-slow"] = SwarmTaskNode(task_id="task-slow", objective="slower shard", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    def speed(task_id: str) -> float:
        return 0.0 if task_id == "task-fast" else 0.3

    class Factory(StubWorkerFactory):
        def build(self, _task):
            factory = self

            class _Worker:
                def set_context(self, context):  # noqa: ANN001
                    return None

                def execute_task(self, task, plan):  # noqa: ANN001
                    with factory._lock:
                        factory.executions.append(task.task_id)
                    time.sleep(speed(task.task_id))
                    return {
                        "summary": f"deliverable for {task.task_id}",
                        "evidence": [{"source": "stub"}],
                        "tool_calls": 1,
                        "usage": {"input_tokens": 3, "output_tokens": 3},
                    }

            return _Worker()

    factory = Factory({})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    counts = factory.counts()
    assert counts["task-fast"] == 1, "the fast node was executed twice"
    assert counts["task-slow"] == 1, f"the slow node was executed {counts['task-slow']}x by the speculative-backup path"
    assert "SPECULATIVE_BACKUP_LAUNCHED" not in _event_types(coordinator, plan.swarm_id)
    assert result["status"] == "completed"


def test_watchdog_straggler_threshold_uses_the_larger_of_measured_and_declared():
    plan = _plan("swm-watchdog-basis")
    # A degenerate measured sample: one sub-millisecond completion.
    plan.tasks["done-fast"] = SwarmTaskNode(
        task_id="done-fast",
        objective="fast",
        state=TaskNodeState.COMPLETED,
        duration_seconds=0.0001,
    )
    # A sibling 0.5s into a declared 15s objective.
    plan.tasks["live"] = SwarmTaskNode(
        task_id="live",
        objective="slower",
        state=TaskNodeState.RUNNING,
        started_at=1000.0,
        lease_id="lease-live",
        lease_expires_at=2000.0,
    )

    report = SwarmWatchdog.check_and_reconcile(plan, now=1000.5, lease_seconds=60.0)

    assert report["measured_completed_duration_seconds"] == pytest.approx(0.0001)
    assert report["declared_duration_seconds"] == pytest.approx(15.0)
    assert report["average_completed_duration_seconds"] == pytest.approx(15.0)
    assert report["straggler_threshold_seconds"] == pytest.approx(37.5)
    assert "live" not in report["stragglers"]
    assert "live" not in report["speculative_backups_spawned"]
    assert plan.tasks["live"].state == TaskNodeState.RUNNING
    assert plan.tasks["live"].backup_worker_launched is False


def test_watchdog_still_flags_a_genuine_straggler_and_stays_bounded():
    plan = _plan("swm-watchdog-still-works")
    for index in (1, 2):
        plan.tasks[f"done-{index}"] = SwarmTaskNode(
            task_id=f"done-{index}",
            objective="fast",
            state=TaskNodeState.COMPLETED,
            duration_seconds=5.0,
        )
    plan.tasks["live"] = SwarmTaskNode(
        task_id="live",
        objective="slow",
        state=TaskNodeState.RUNNING,
        started_at=100.0,
        lease_id="lease-live",
        lease_expires_at=9_000_000.0,
    )

    spawned: list[str] = []
    for tick in range(50):
        report = SwarmWatchdog.check_and_reconcile(plan, now=150.0 + tick, lease_seconds=60.0)
        spawned.extend(report["speculative_backups_spawned"])
        assert len(report["stragglers"]) <= 1

    # A node that really is 2.5x the measured mean is still detected...
    assert plan.tasks["live"].state == TaskNodeState.STRAGGLING
    assert plan.tasks["live"].backup_worker_launched is True
    # ...and reconciliation is bounded: at most one backup per attempt.
    assert spawned == ["live"], f"reconciliation spawned {len(spawned)} backups for one node"


# ---------------------------------------------------------------------------
# 4. Budget honesty: the hard limit must bound REAL spend
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_budget_accounts_for_a_discarded_duplicate_attempt(tmp_path):
    """Losing the lease race decides the RESULT, not the cost.

    The provider call already happened and already reported its usage.  The
    fenced-result path used to return before ``record_usage``, so
    ``used_tokens``/``used_tool_calls`` under-reported real spend and the hard
    budget stopped bounding the run it exists to bound.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-fenced-cost", max_concurrency=4, budget=SwarmBudget(max_tokens=None, max_tool_calls=None))
    plan.tasks["task-a"] = SwarmTaskNode(task_id="task-a", objective="shard a", max_attempts=1)
    plan.tasks["task-b"] = SwarmTaskNode(task_id="task-b", objective="shard b", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    # The plan declares shard a should take 0.05s, so its sibling still running
    # at 0.15s genuinely IS a straggler by the plan's own declaration.  This is
    # the honest way to reach the speculative-backup path now that the
    # measured-mean heuristic can no longer manufacture one.
    plan.tasks["task-a"].estimated_seconds = 0.05
    plan.tasks["task-b"].estimated_seconds = 0.05

    factory = StubWorkerFactory({}, delays={"task-a": 0.0, "task-b": 0.4}, tokens=7, tool_calls=1)
    await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    assert "SPECULATIVE_BACKUP_LAUNCHED" in _event_types(coordinator, plan.swarm_id)
    assert "TASK_RESULT_FENCED" in _event_types(coordinator, plan.swarm_id)
    counts = factory.counts()
    assert sum(counts.values()) == 3, f"expected 2 accepted + 1 discarded execution, got {dict(counts)}"
    # Every real execution is charged: 3 x (7 + 7) tokens and 3 tool calls.
    assert plan.budget.used_tokens == 42, plan.budget.to_dict()
    assert plan.budget.used_tool_calls == 3, plan.budget.to_dict()


@pytest.mark.asyncio
async def test_token_budget_stops_dispatch_and_is_observable(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-token-budget", max_concurrency=2, budget=SwarmBudget(max_tokens=10))
    for index in range(4):
        plan.tasks[f"t{index}"] = SwarmTaskNode(task_id=f"t{index}", objective=f"work {index}", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({}, delay=0.02, tokens=6, tool_calls=1)
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    assert result["status"] == "budget_exhausted"
    assert plan.status == "budget_exhausted"
    assert plan.terminal_reason == "token_budget_exhausted"
    assert "SWARM_BUDGET_EXHAUSTED" in _event_types(coordinator, plan.swarm_id)
    stopped = [t for t in plan.tasks.values() if t.state == TaskNodeState.CANCELLED]
    assert stopped, "the budget must stop dispatch, not merely annotate it"
    for task in stopped:
        assert "budget_exhausted" in (task.error_message or "")


@pytest.mark.asyncio
async def test_wall_clock_budget_bounds_a_hanging_node(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-wallclock", max_concurrency=4, budget=SwarmBudget(max_wall_seconds=0.4))
    plan.tasks["task-hang"] = SwarmTaskNode(task_id="task-hang", objective="hangs", max_attempts=1)
    plan.tasks["task-ok"] = SwarmTaskNode(task_id="task-ok", objective="healthy", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    release = threading.Event()
    timer = threading.Timer(1.0, release.set)
    timer.daemon = True
    timer.start()

    class Factory(StubWorkerFactory):
        def build(self, _task):
            def set_context(context):  # noqa: ANN001
                return None

            def execute_task(task, plan):  # noqa: ANN001
                if task.task_id == "task-hang":
                    release.wait(timeout=5)
                    return {"summary": "late", "tool_calls": 1}
                return {"summary": "deliverable", "tool_calls": 1, "usage": {"input_tokens": 1, "output_tokens": 1}}

            class _Worker:
                pass

            worker = _Worker()
            worker.set_context = set_context
            worker.execute_task = execute_task
            return worker

    result = await asyncio.wait_for(_runner(coordinator, Factory({})).run_swarm_async(plan.swarm_id), timeout=30)

    assert result["status"] == "budget_exhausted"
    assert plan.terminal_reason == "wall_clock_budget_exhausted"
    assert plan.tasks["task-hang"].state == TaskNodeState.CANCELLED


# ---------------------------------------------------------------------------
# 5. Aggregator honesty
# ---------------------------------------------------------------------------
def test_a_plan_with_no_work_cannot_claim_completion():
    """``is_swarm_finished()`` treats an empty plan as finished.

    The keyword quality gate passes on the boilerplate deliverable text, which
    let a swarm that executed zero nodes publish ``status="completed"`` and a
    SWARM_COMPLETED event.
    """

    plan = SwarmPlan(swarm_id="swm-empty", goal="nothing to do", mode=SwarmMode.PARALLEL, tasks={})
    result = SwarmAggregator.aggregate(plan)
    assert plan.status == "partial_success"
    assert result["completed_tasks"] == 0
    assert "No Work Executed" in result["deliverable"]


def test_a_plan_whose_nodes_all_failed_reports_failed_not_partial_success():
    plan = SwarmPlan(
        swarm_id="swm-all-failed",
        goal="assess the migration risk across the fleet",
        mode=SwarmMode.PARALLEL,
        tasks={
            "a": SwarmTaskNode(task_id="a", objective="a", state=TaskNodeState.FAILED, error_message="x"),
            "b": SwarmTaskNode(task_id="b", objective="b", state=TaskNodeState.FAILED, error_message="y"),
        },
    )
    result = SwarmAggregator.aggregate(plan)
    assert plan.status == "failed"
    assert result["failed_tasks"] == 2
    assert "Failed Tasks" in result["deliverable"]


# ---------------------------------------------------------------------------
# 6. Audit invariants: a failure must leave an incident record
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ephemeral_worker_failure_still_leaves_an_incident(tmp_path):
    """Ephemeral subagents are the majority of a decomposed plan.

    Recording the incident was gated on ``task.assigned_worker``, so a hard
    failure of a map shard produced an EMPTY incident ledger and
    ``GET /api/swarms/{id}/incidents`` answered "no failure incidents" about a
    swarm that had just failed a node.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-incident-audit")
    plan.tasks["task-ephemeral"] = SwarmTaskNode(
        task_id="task-ephemeral",
        objective="ephemeral shard",
        worker_type="ephemeral",
        max_attempts=2,
    )
    plan.tasks["task-healthy"] = SwarmTaskNode(
        task_id="task-healthy",
        objective="healthy shard",
        worker_type="ephemeral",
        max_attempts=1,
    )
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-ephemeral": "fail"})
    await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    assert plan.tasks["task-ephemeral"].state == TaskNodeState.FAILED
    incidents = inc_mod.get_swarm_incident_manager().get_incidents(plan.swarm_id)
    # BOTH failed attempts are audited: the retryable one AND the one that
    # exhausted ``max_attempts`` (which the requeue-only gate never recorded).
    assert [incident.attempt for incident in incidents] == [1, 2], [i.to_dict() for i in incidents]
    assert all(incident.task_id == "task-ephemeral" for incident in incidents)
    assert all("stub hard failure" in incident.error_message for incident in incidents)
    assert all(incident.failed_worker == "ephemeral-worker" for incident in incidents)
    assert all(incident.resolved is False for incident in incidents)
    # The reason must be truthful about WHY there is no successor.
    assert all("ephemeral" in incident.reason for incident in incidents)
    assert "SWARM_INCIDENT_UNRESOLVED" in _event_types(coordinator, plan.swarm_id)


# ---------------------------------------------------------------------------
# 7. Bounded durable-write volume (large plans must not be disk-bound)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_large_plan_durable_writes_stay_linear_in_nodes(tmp_path):
    """A node transition marks the plan dirty instead of rewriting the file.

    ``checkpoint()`` serialises the whole plan and fsyncs it, and the runner
    called it several times per node transition -- so a run's total durable
    write volume was O(n^2) in the node count, 81% of a 60-node plan's wall
    clock, done synchronously while holding the state lock.  Pin the
    invariant (writes proportional to rounds, not to nodes x transitions) plus
    durability: the terminal plan must still be on disk when the run returns.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    n = 16
    plan = _plan("swm-write-volume", max_concurrency=4)
    for index in range(n):
        plan.tasks[f"n{index:02d}"] = SwarmTaskNode(task_id=f"n{index:02d}", objective=f"work {index}", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    writes = {"n": 0}
    original = type(coordinator).checkpoint

    def counting_checkpoint(self, swarm_id):  # noqa: ANN001
        writes["n"] += 1
        original(self, swarm_id)

    type(coordinator).checkpoint = counting_checkpoint
    try:
        factory = StubWorkerFactory({})
        result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=60)
    finally:
        type(coordinator).checkpoint = original

    assert result["status"] == "completed"
    assert plan.progress()["completed"] == n
    # One start write + at most one write per round + a terminal write.
    assert writes["n"] <= plan.round + 3, f"{writes['n']} durable writes for {n} nodes over {plan.round} rounds"
    assert writes["n"] < n, f"{writes['n']} durable writes for {n} nodes is per-node, not per-round"
    # Coalescing must not leave mutated state unwritten when the run ends.
    assert not coordinator._dirty, f"a finished run must not leave a dirty plan behind: {coordinator._dirty}"
    # Durability is not traded away: the terminal snapshot is on disk.
    persisted = tmp_path / "swarms" / f"{plan.swarm_id}.json"
    assert persisted.exists()
    reloaded = SwarmCoordinator(storage_dir=tmp_path / "swarms").get_swarm(plan.swarm_id)
    assert reloaded is not None
    assert reloaded.status == "completed"
    assert reloaded.progress()["completed"] == n


@pytest.mark.asyncio
async def test_the_final_result_message_is_durable_even_though_the_publish_is_coalesced(tmp_path):
    """``durable=False`` is for the runner's internal message only.

    A caller that publishes through the coordinator API must still see its
    message survive a process restart immediately, so the default stays
    ``True`` and only the runner opts out.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-message-durability")
    plan.tasks["task-1"] = SwarmTaskNode(task_id="task-1", objective="work", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    coordinator.publish_message(plan.swarm_id, topic="notes", sender="alice", content="durable note", idempotency_key="n1")
    assert not coordinator._dirty, "an external publish must be durable immediately, not deferred"

    reloaded = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    restored = reloaded.get_swarm(plan.swarm_id)
    assert restored is not None
    assert [message.content for message in reloaded.get_messages(plan.swarm_id)] == ["durable note"]


# ---------------------------------------------------------------------------
# 8. A full fan-out/join DAG really schedules what was authored
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fan_out_join_dag_schedules_exactly_what_was_authored(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-fanout-join", max_concurrency=4)
    plan.tasks["task-map-a"] = SwarmTaskNode(task_id="task-map-a", objective="shard a", max_attempts=1)
    plan.tasks["task-map-b"] = SwarmTaskNode(task_id="task-map-b", objective="shard b", max_attempts=1)
    plan.tasks["task-reduce"] = SwarmTaskNode(
        task_id="task-reduce",
        objective="join",
        dependencies=["task-map-a", "task-map-b"],
        max_attempts=1,
    )
    plan.tasks["task-verify"] = SwarmTaskNode(
        task_id="task-verify",
        objective="verify",
        dependencies=["task-reduce"],
        max_attempts=1,
    )
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    counts = factory.counts()
    assert counts == Counter({"task-map-a": 1, "task-map-b": 1, "task-reduce": 1, "task-verify": 1}), dict(counts)
    order = factory.executions
    assert order.index("task-reduce") > max(order.index("task-map-a"), order.index("task-map-b"))
    assert order[-1] == "task-verify"
    assert result["status"] == "completed"
    assert plan.progress() == {"total": 4, "pending": 0, "running": 0, "completed": 4, "failed": 0, "cancelled": 0}
    # The fan-in is not allowed to run before its inputs are real.
    dispatched = [e.task_id for e in coordinator.get_events(plan.swarm_id, limit=200) if e.event_type == "TASK_DISPATCHED"]
    assert dispatched.index("task-reduce") > max(dispatched.index("task-map-a"), dispatched.index("task-map-b"))


# ---------------------------------------------------------------------------
# 9. Fan-in honesty: one bad shard must not make the join look clean
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fan_in_over_a_failed_shard_reports_partial_success(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-fanin-honesty", max_concurrency=4)
    plan.tasks["task-map-1"] = SwarmTaskNode(task_id="task-map-1", objective="good shard", max_attempts=1)
    plan.tasks["task-map-2"] = SwarmTaskNode(task_id="task-map-2", objective="bad shard", max_attempts=1)
    plan.tasks["task-reduce"] = SwarmTaskNode(
        task_id="task-reduce",
        objective="join",
        dependencies=["task-map-1", "task-map-2"],
        max_attempts=1,
    )
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-map-2": "fail"})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    # The join cannot run over a dead shard, and must say so.
    assert plan.tasks["task-reduce"].state == TaskNodeState.FAILED
    assert "unrunnable" in (plan.tasks["task-reduce"].error_message or "")
    assert result["status"] == "partial_success"
    aggregate = result["result"]
    assert aggregate["completed_tasks"] == 1
    assert aggregate["failed_tasks"] == 2
    assert aggregate["execution_status"] == "partial_success"
    assert "task-map-2" in aggregate["deliverable"] and "task-reduce" in aggregate["deliverable"]
    assert "task-map-2" in plan.final_result and "task-reduce" in plan.final_result


# ---------------------------------------------------------------------------
# 10. A worker that returns a non-mapping must not be recorded as success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_worker_returning_a_non_mapping_is_an_honest_failure(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = _plan("swm-garbage")
    plan.tasks["task-ok"] = SwarmTaskNode(task_id="task-ok", objective="healthy work", max_attempts=1)
    plan.tasks["task-garbage"] = SwarmTaskNode(task_id="task-garbage", objective="misbehaving work", max_attempts=1)
    coordinator._swarms[plan.swarm_id] = plan

    factory = StubWorkerFactory({"task-garbage": "garbage"})
    result = await asyncio.wait_for(_runner(coordinator, factory).run_swarm_async(plan.swarm_id), timeout=30)

    assert plan.tasks["task-garbage"].state == TaskNodeState.FAILED
    assert "expected a mapping" in (plan.tasks["task-garbage"].error_message or "")
    assert result["status"] == "partial_success"
    assert plan.tasks["task-ok"].state == TaskNodeState.COMPLETED
