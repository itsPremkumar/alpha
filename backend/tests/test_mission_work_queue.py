import pytest

from agent_workspace.mission.state_machine import TaskState
from agent_workspace.mission.work_queue import (
    BudgetExceededError,
    CyclicDependencyError,
    DurableWorkQueue,
    ExecutionBudget,
    TaskPriority,
)


def test_work_queue_dag_execution_order():
    wq = DurableWorkQueue()

    # Create DAG:
    # A (Setup) -> B (Backend), C (Frontend) -> D (Integration Test)
    t_a = wq.add_task(title="Setup DB", priority=TaskPriority.HIGH)
    t_b = wq.add_task(title="Backend API", priority=TaskPriority.MEDIUM, dependencies=[t_a.id])
    t_c = wq.add_task(title="Frontend UI", priority=TaskPriority.LOW, dependencies=[t_a.id])
    t_d = wq.add_task(title="E2E Tests", priority=TaskPriority.CRITICAL, dependencies=[t_b.id, t_c.id])

    # Only t_a should be ready initially
    ready = wq.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == t_a.id

    # Dispatch and complete t_a
    dispatched_a = wq.dispatch_next()
    assert dispatched_a.id == t_a.id
    assert dispatched_a.state == TaskState.RUNNING
    wq.complete_task(t_a.id, result={"db": "migrated"})

    # Now t_b and t_c should both be ready, with t_b (MEDIUM) preceding t_c (LOW)
    ready = wq.get_ready_tasks()
    assert len(ready) == 2
    assert ready[0].id == t_b.id
    assert ready[1].id == t_c.id

    # Dispatch and complete both
    wq.dispatch_next()
    wq.complete_task(t_b.id)
    wq.dispatch_next()
    wq.complete_task(t_c.id)

    # Now t_d should be ready
    ready = wq.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == t_d.id

    # Complete t_d
    wq.dispatch_next()
    wq.complete_task(t_d.id)
    assert len(wq.get_ready_tasks()) == 0

    stats = wq.stats()
    assert stats["total_tasks"] == 4
    assert stats["by_state"].get("completed") == 4


def test_work_queue_cycle_detection():
    wq = DurableWorkQueue()
    t1 = wq.add_task(title="Task 1")
    t2 = wq.add_task(title="Task 2", dependencies=[t1.id])

    # Trying to add a task that closes a cycle should raise CyclicDependencyError
    with pytest.raises(CyclicDependencyError):
        # We simulate cycle by trying to add task with invalid loop
        t3 = wq.add_task(title="Task 3", dependencies=[t2.id])
        t1.dependencies.append(t3.id)
        wq.topological_sort()


def test_work_queue_budget_caps():
    budget = ExecutionBudget(max_tool_calls=5, max_retries=1, max_cost_usd=1.0)
    wq = DurableWorkQueue(budget=budget)

    # Check budget recording
    budget.check_and_record(tool_calls=3, cost_usd=0.5)
    assert budget.current_tool_calls == 3

    # Exceeding budget should raise BudgetExceededError
    with pytest.raises(BudgetExceededError) as exc_info:
        budget.check_and_record(tool_calls=3)  # 3 + 3 = 6 > 5
    assert "tool calls limit" in str(exc_info.value)
