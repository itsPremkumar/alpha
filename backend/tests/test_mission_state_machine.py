import pytest

from agent_workspace.mission.state_machine import (
    InvalidStateTransitionError,
    TaskState,
    TaskStateMachine,
)


def test_state_machine_happy_path():
    sm = TaskStateMachine(task_id="task_001")
    assert sm.current_state == TaskState.CREATED
    assert not sm.is_terminal

    # Flow: CREATED -> ANALYZING -> PLANNING -> READY -> RUNNING -> VERIFYING -> COMPLETED
    sm.transition_to(TaskState.ANALYZING, reason="Analyzing requirements")
    assert sm.current_state == TaskState.ANALYZING

    sm.transition_to(TaskState.PLANNING, reason="Synthesizing plan")
    assert sm.current_state == TaskState.PLANNING

    sm.transition_to(TaskState.READY, reason="Ready for execution")
    assert sm.current_state == TaskState.READY

    sm.transition_to(TaskState.RUNNING, reason="Worker dispatched")
    assert sm.current_state == TaskState.RUNNING

    sm.transition_to(TaskState.VERIFYING, reason="Running test suite")
    assert sm.current_state == TaskState.VERIFYING

    sm.transition_to(TaskState.COMPLETED, reason="All checks passed")
    assert sm.current_state == TaskState.COMPLETED
    assert sm.is_terminal
    assert len(sm.history) == 7


def test_state_machine_waiting_and_recovering():
    sm = TaskStateMachine(task_id="task_002", initial_state=TaskState.RUNNING)
    assert not sm.is_waiting

    sm.transition_to(TaskState.WAITING_FOR_TOOL, reason="Waiting for API response")
    assert sm.is_waiting

    sm.transition_to(TaskState.RUNNING, reason="Tool response received")
    assert not sm.is_waiting

    sm.transition_to(TaskState.RECOVERING, reason="Syntax error caught, fixing")
    assert sm.current_state == TaskState.RECOVERING

    sm.transition_to(TaskState.RUNNING, reason="Fix applied")
    assert sm.current_state == TaskState.RUNNING


def test_state_machine_illegal_transition_rejected():
    sm = TaskStateMachine(task_id="task_003", initial_state=TaskState.CREATED)

    # Illegal: cannot jump directly from CREATED to COMPLETED
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        sm.transition_to(TaskState.COMPLETED)
    assert "Illegal state transition" in str(exc_info.value)


def test_state_machine_recovery_reset():
    sm = TaskStateMachine(task_id="task_004", initial_state=TaskState.RUNNING)
    sm.transition_to(TaskState.FAILED, reason="Out of memory")
    assert sm.is_terminal

    # Explicit retry reset
    sm.reset_for_retry(reason="Retrying on larger instance")
    assert sm.current_state == TaskState.READY
    assert not sm.is_terminal
