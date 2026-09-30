"""An idle runner must not leave a swarm non-terminal with undispatchable work.

``POST /api/swarms/{id}/run-async`` answers 200 with
``{"status": "started_async", "runner_status": "running"}`` BEFORE the runner has
done anything. That 2xx is an acceptance, not an outcome, so the only thing that
can make it honest is the runner reaching a truthful terminal state.

The defect this pins, reproduced live against the gateway on a 3-item batch
(create -> ``POST /step`` -> ``POST /run-async``)::

    step 1   -> dispatched: [task-map-1, task-map-2, task-map-3]
    run-async-> 200 {"status":"started_async","runner_status":"running"}
      t+ 5s  status=running rev=12  map-1=failed/1 map-2=failed/1 map-3=failed/1 reduce=pending/0
      t+15s  status=running rev=12  (unchanged)
      t+30s  status=running rev=12  (unchanged)
      t+45s  status=running rev=12  (unchanged)

The runner exited on its first idle tick. ``step`` had leased all three map
tasks, so the runner's ``active`` set was empty while those tasks were RUNNING;
it reconciled them as orphans and FAILED them, then ``break``-ed. The reduce task
depends on all three, and ``SwarmScheduler.fail_unrunnable_tasks`` only fails a
PENDING task once a dependency is FAILED - but it had already run, *before* the
orphan pass. So the reducer became unrunnable exactly when nothing was left
watching, the revision froze, and ``status`` reported ``running`` forever with a
task no endpoint could ever dispatch.

The fix reaps again after the orphan pass, so the plan reaches a real terminal
state and the ``break`` is earned.

**Strength of coverage, stated honestly.** The four behavioural tests below drive
``SwarmScheduler`` directly. They pin the mechanism the fix depends on - that a
dependent is reaped once, and only once, its parents are terminal - but they
PASS against the unfixed runner, because the scheduler was always correct and
the bug was the runner calling it in the wrong ORDER. The single test that
actually bites is ``test_runner_reaps_after_the_orphan_pass_not_only_before_it``,
a structural source-order pin; deleting the second reap fails it (1 failed / 4
passed), and that is the whole of the regression coverage.

A behavioural runner-level test needs the idle branch of the poll loop extracted
into a callable. That is a refactor of the runner's control flow rather than a
test, so it is not done here. Until then the behavioural cases are documentary
and the ordering pin is the real guard. Do not read "5 passed" as five
independent proofs of this fix.

Live confirmation (gateway restarted so the running process actually loaded this
code - it runs `uvicorn app.gateway.app:app` with no `--reload`, so an earlier
re-run of the reproduction silently measured the pre-fix process and proved
nothing). Same script, same endpoint, before vs after:

  before: status=running rev=12  map-1..3=failed/1  reduce=pending/0   (frozen 40s+)
  after:  status=failed rev=15  map-1..3=failed/1  reduce=failed/0    (stable from t+5s)

The plan now reaches a real terminal state, `task-reduce` is reaped rather than
stranded PENDING, and `status` stops reporting `running` for a swarm nobody is
working on. The tasks being FAILED is the honest outcome here: `step` leased them
without executing, so they genuinely never ran and their reducer genuinely could
not.
"""

from __future__ import annotations

import pytest

from alpha.swarm.models import SwarmMode, TaskNodeState
from alpha.swarm.scheduler import SwarmScheduler


def _plan_with_orphaned_parents_and_a_reducer():
    """3 map tasks + a reducer: the exact shape that stranded `task-reduce`."""

    from alpha.swarm.decomposer import SwarmTaskDecomposer

    return SwarmTaskDecomposer.decompose(
        "Reply with the capital city of each of these three countries, comma separated.",
        mode=SwarmMode.MAP_REDUCE,
        items=["France", "Japan", "Peru"],
    )


def test_fail_unrunnable_tasks_does_not_reap_a_dependent_of_a_running_task() -> None:
    """The precondition that makes the ordering bug possible, pinned directly.

    While the parents are RUNNING the reducer is genuinely runnable-looking, so
    the first `fail_unrunnable_tasks()` pass is *correct* to leave it alone. This
    is what the runner's first pass sees.
    """

    plan = _plan_with_orphaned_parents_and_a_reducer()
    scheduler = SwarmScheduler(plan)

    for task_id in ("task-map-1", "task-map-2", "task-map-3"):
        plan.tasks[task_id].state = TaskNodeState.RUNNING

    stranded = scheduler.fail_unrunnable_tasks()

    assert stranded == []
    assert plan.tasks["task-reduce"].state == TaskNodeState.PENDING


def test_fail_unrunnable_tasks_reaps_the_reducer_once_the_parents_fail() -> None:
    """The second pass, after the orphan pass, must reap it.

    Same plan, same scheduler: only the parent states differ. This is the pass the
    runner was missing, and it is what makes `is_swarm_finished()` truthful.
    """

    plan = _plan_with_orphaned_parents_and_a_reducer()
    scheduler = SwarmScheduler(plan)

    for task_id in ("task-map-1", "task-map-2", "task-map-3"):
        plan.tasks[task_id].state = TaskNodeState.FAILED

    stranded = scheduler.fail_unrunnable_tasks()

    assert [task.task_id for task in stranded] == ["task-reduce"]
    assert plan.tasks["task-reduce"].state == TaskNodeState.FAILED
    assert "dependency failed" in (plan.tasks["task-reduce"].error_message or "")
    # Every task terminal => the runner's `break` is earned and `status` can
    # leave `running` instead of lying forever.
    assert scheduler.is_swarm_finished() is True


def test_runner_reaps_after_the_orphan_pass_not_only_before_it() -> None:
    """Structural pin: the reaping must come AFTER the orphan-marking loop.

    Weaker than the two behavioural tests above - it proves the order in the
    source, not that the running swarm behaves. It is here because the defect was
    precisely an ordering, and a reader who moves the second call back above the
    orphan loop should be told why that breaks the plan.
    """

    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "swarm" / "runner.py").read_text(encoding="utf-8")

    orphan_pass = source.index("orphaned: execution disappeared while the swarm was idle")
    first_reap = source.index("stranded = scheduler.fail_unrunnable_tasks()")
    second_reap = source.index("newly_stranded = scheduler.fail_unrunnable_tasks()")

    assert first_reap < orphan_pass, "the first pre-existing reap stays where it was"
    assert orphan_pass < second_reap, "the second reap must come AFTER the orphan pass; before it, the reducer's dependencies are still RUNNING and nothing reaps the reducer"
    assert source.count("fail_unrunnable_tasks()") == 2, "exactly the two ordered reaping passes"


@pytest.mark.parametrize("orphan_state", [TaskNodeState.RUNNING, TaskNodeState.STRAGGLING])
def test_no_pending_task_survives_with_only_terminal_dependencies(orphan_state) -> None:
    """Whatever the parent state, the plan must not keep a PENDING task forever.

    The invariant the fix restores: after both passes, no PENDING/QUEUED task is
    left whose dependencies are all terminal. A plan that violates it is one the
    runner has abandoned while still reporting `running`.
    """

    plan = _plan_with_orphaned_parents_and_a_reducer()
    scheduler = SwarmScheduler(plan)
    parents = ("task-map-1", "task-map-2", "task-map-3")
    for task_id in parents:
        plan.tasks[task_id].state = orphan_state

    # Pass 1 (pre-orphan): correctly reaps nothing, orphans are still live.
    scheduler.fail_unrunnable_tasks()
    # The orphan pass, as the runner performs it.
    for task_id in parents:
        node = plan.tasks[task_id]
        if node.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
            node.state = TaskNodeState.FAILED
            node.lease_id = None
            node.lease_owner = None
            node.lease_expires_at = None
    # Pass 2 (post-orphan): the fix.
    scheduler.fail_unrunnable_tasks()

    terminal = (TaskNodeState.FAILED, TaskNodeState.CANCELLED, TaskNodeState.COMPLETED)
    stranded = [node.task_id for node in plan.tasks.values() if node.state == TaskNodeState.PENDING and any(d not in plan.tasks or plan.tasks[d].state in terminal for d in node.dependencies)]
    assert stranded == [], f"a PENDING task with only terminal dependencies can never run: {stranded}"
    assert scheduler.is_swarm_finished() is True
