"""Session lifecycle state machine invariants.

These tests are the executable form of the contract in
``packages/harness/alpha/runtime/sessions/AGENTS.md``. The two properties that
matter most:

* a parked task (``WAITING_*``) is never terminal and is always resumable, which
  is what makes "an internet outage must not become a task failure" true;
* a terminal state is final and a self-transition is always legal, which is
  what keeps a repeated heartbeat from raising and a recovery pass from
  resurrecting finished work.
"""

from __future__ import annotations

import pytest

from alpha.runtime.runs.schemas import RunStatus
from alpha.runtime.sessions.states import (
    ACTIVE_SESSION_STATES,
    SESSION_STATE_CLASSES,
    SESSION_STATE_TRANSITIONS,
    TERMINAL_SESSION_STATES,
    WAITING_SESSION_STATES,
    IllegalSessionTransition,
    SessionState,
    SessionStateClass,
    SessionStateSignal,
    allowed_transitions,
    can_transition,
    derive_session_state,
    is_active,
    is_resumable,
    is_terminal,
    session_state_class,
    transition_table,
    validate_transition,
)


class TestClassification:
    def test_every_state_is_classified_exactly_once(self) -> None:
        assert set(SESSION_STATE_CLASSES) == set(SessionState)

    def test_classes_partition_the_vocabulary_without_overlap(self) -> None:
        assert not ACTIVE_SESSION_STATES & WAITING_SESSION_STATES
        assert not ACTIVE_SESSION_STATES & TERMINAL_SESSION_STATES
        assert not WAITING_SESSION_STATES & TERMINAL_SESSION_STATES
        assert ACTIVE_SESSION_STATES | WAITING_SESSION_STATES | TERMINAL_SESSION_STATES == set(SessionState)

    def test_all_three_classes_are_reachable(self) -> None:
        assert ACTIVE_SESSION_STATES and WAITING_SESSION_STATES and TERMINAL_SESSION_STATES

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (SessionState.RUNNING, SessionStateClass.ACTIVE),
            (SessionState.WAITING_NETWORK, SessionStateClass.WAITING),
            (SessionState.PAUSED, SessionStateClass.WAITING),
            (SessionState.FAILED, SessionStateClass.TERMINAL),
        ],
    )
    def test_state_class_lookup(self, state: SessionState, expected: SessionStateClass) -> None:
        assert session_state_class(state) is expected
        assert is_active(state) is (expected is SessionStateClass.ACTIVE)
        assert is_terminal(state) is (expected is SessionStateClass.TERMINAL)


class TestTheCoreGuarantee:
    """A waiting task is alive. That is the whole point of the class split."""

    @pytest.mark.parametrize("state", sorted(WAITING_SESSION_STATES, key=lambda s: s.value))
    def test_waiting_states_are_never_terminal_and_are_resumable(self, state: SessionState) -> None:
        assert not is_terminal(state), f"{state.value} must not be terminal"
        assert is_resumable(state), f"{state.value} must be resumable"

    def test_a_network_outage_does_not_terminate_the_task(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, network_unavailable=True))
        assert report.state is SessionState.WAITING_NETWORK
        assert report.is_waiting
        assert not report.is_terminal
        assert report.is_resumable
        assert report.primary_reason == "network_unavailable"

    def test_a_provider_outage_is_distinct_from_a_network_outage(self) -> None:
        provider = derive_session_state(SessionStateSignal(run_status=RunStatus.running, provider_unavailable=True))
        assert provider.state is SessionState.WAITING_PROVIDER
        both = derive_session_state(SessionStateSignal(run_status=RunStatus.running, network_unavailable=True, provider_unavailable=True))
        assert both.state is SessionState.WAITING_NETWORK, "network outranks provider: without a link, provider health is unprovable"

    def test_a_completed_task_is_terminal_and_carries_no_resume_claim(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.success))
        assert report.state is SessionState.COMPLETED
        assert report.is_terminal
        assert not report.is_resumable


class TestTerminalRunMapping:
    @pytest.mark.parametrize(
        ("run_status", "expected"),
        [
            (RunStatus.success, SessionState.COMPLETED),
            (RunStatus.error, SessionState.FAILED),
            (RunStatus.timeout, SessionState.FAILED),
            (RunStatus.interrupted, SessionState.CANCELLED),
        ],
    )
    def test_terminal_run_status_maps_to_terminal_state(self, run_status: RunStatus, expected: SessionState) -> None:
        assert derive_session_state(SessionStateSignal(run_status=run_status)).state is expected

    def test_a_terminal_run_outranks_every_live_condition(self) -> None:
        """A network flap during teardown must not resurrect finished work."""
        report = derive_session_state(
            SessionStateSignal(
                run_status=RunStatus.success,
                network_unavailable=True,
                provider_unavailable=True,
                recovering=True,
                compacting=True,
                retrying=True,
                planning=True,
            )
        )
        assert report.state is SessionState.COMPLETED
        assert report.primary_reason == "run_success"

    def test_no_run_and_no_condition_is_created_not_queued(self) -> None:
        report = derive_session_state(SessionStateSignal())
        assert report.state is SessionState.CREATED
        assert report.primary_reason == "no_run"

    def test_no_run_with_a_live_condition_is_queued(self) -> None:
        report = derive_session_state(SessionStateSignal(resource_pending=True))
        assert report.state is SessionState.WAITING_RESOURCE
        assert report.primary_reason == "resource_pending"


class TestPrecedence:
    def test_pause_outranks_parking_conditions(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, paused=True, network_unavailable=True))
        assert report.state is SessionState.PAUSED
        assert report.primary_reason == "paused_by_user"

    def test_permanent_block_outranks_everything_except_a_terminal_run(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, permanently_blocked=True, network_unavailable=True))
        assert report.state is SessionState.BLOCKED
        assert report.primary_reason == "permanently_blocked"

    def test_parking_outranks_in_flight_phases(self) -> None:
        """Reporting ``RECOVERING`` for a session that cannot reach the provider would be a lie about progress."""
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, recovering=True, network_unavailable=True))
        assert report.state is SessionState.WAITING_NETWORK

    def test_permission_is_asked_about_before_a_resource_wait(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, permission_pending=True, resource_pending=True))
        assert report.state is SessionState.WAITING_PERMISSION

    def test_in_flight_phase_order(self) -> None:
        assert derive_session_state(SessionStateSignal(run_status=RunStatus.running)).state is SessionState.RUNNING
        assert derive_session_state(SessionStateSignal(run_status=RunStatus.running, recovering=True)).state is SessionState.RECOVERING
        assert derive_session_state(SessionStateSignal(run_status=RunStatus.running, compacting=True)).state is SessionState.COMPACTING
        assert derive_session_state(SessionStateSignal(run_status=RunStatus.running, retrying=True)).state is SessionState.RETRYING
        assert derive_session_state(SessionStateSignal(run_status=RunStatus.running, planning=True)).state is SessionState.PLANNING

    def test_pending_run_with_no_condition_reports_running(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.pending))
        assert report.state is SessionState.RUNNING
        assert report.metadata["run_status"] == "pending"


class TestTransitions:
    def test_table_covers_every_state(self) -> None:
        assert set(transition_table()) == set(SessionState)
        assert set(SESSION_STATE_TRANSITIONS) == set(SessionState)

    @pytest.mark.parametrize("state", sorted(ACTIVE_SESSION_STATES | WAITING_SESSION_STATES, key=lambda s: s.value))
    def test_self_transition_is_always_legal_for_a_live_state(self, state: SessionState) -> None:
        """A repeated observation is not an error; real change is compared by the caller."""
        assert can_transition(state, state)
        assert validate_transition(state, state) is state
        assert state in allowed_transitions(state)

    @pytest.mark.parametrize("state", sorted(TERMINAL_SESSION_STATES, key=lambda s: s.value))
    def test_a_terminal_state_is_final_even_against_itself(self, state: SessionState) -> None:
        """Re-reporting a finished state is not a transition, so it is refused too."""
        assert not can_transition(state, state)
        with pytest.raises(IllegalSessionTransition):
            validate_transition(state, state)

    @pytest.mark.parametrize("state", sorted(TERMINAL_SESSION_STATES, key=lambda s: s.value))
    def test_terminal_states_are_final(self, state: SessionState) -> None:
        assert allowed_transitions(state) == frozenset()
        for target in SessionState:
            assert not can_transition(state, target), f"{state.value} -> {target.value} must be refused"

    @pytest.mark.parametrize("state", sorted(WAITING_SESSION_STATES, key=lambda s: s.value))
    def test_every_waiting_state_can_return_to_work_and_can_be_stopped(self, state: SessionState) -> None:
        successors = allowed_transitions(state)
        assert successors & {SessionState.RUNNING, SessionState.RECOVERING, SessionState.QUEUED}, f"{state.value} must be able to resume"
        assert SessionState.CANCELLED in successors, f"{state.value} must be cancellable"
        assert SessionState.FAILED in successors, f"{state.value} must be able to fail"

    def test_a_waiting_state_cannot_jump_straight_to_completed(self) -> None:
        """Only a run that actually finished may claim completion."""
        assert not can_transition(SessionState.WAITING_NETWORK, SessionState.COMPLETED)

    def test_validate_transition_raises_on_an_illegal_move(self) -> None:
        with pytest.raises(IllegalSessionTransition) as excinfo:
            validate_transition(SessionState.COMPLETED, SessionState.RUNNING)
        error = excinfo.value
        assert error.current is SessionState.COMPLETED
        assert error.requested is SessionState.RUNNING
        assert error.allowed == frozenset()
        assert "completed" in str(error) and "running" in str(error)

    def test_the_error_lists_the_legal_alternatives(self) -> None:
        with pytest.raises(IllegalSessionTransition) as excinfo:
            validate_transition(SessionState.WAITING_NETWORK, SessionState.PLANNING)
        message = str(excinfo.value)
        assert "running" in message
        assert "planning" in message

    def test_running_can_reach_every_non_terminal_lifecycle_state(self) -> None:
        successors = allowed_transitions(SessionState.RUNNING)
        for state in (
            SessionState.COMPACTING,
            SessionState.RECOVERING,
            SessionState.RETRYING,
            SessionState.PAUSED,
            SessionState.WAITING_NETWORK,
            SessionState.WAITING_PROVIDER,
            SessionState.WAITING_PERMISSION,
            SessionState.WAITING_RESOURCE,
            SessionState.WAITING_USER,
        ):
            assert state in successors, f"running -> {state.value} must be legal"

    def test_compaction_returns_to_the_execution_phase_not_to_planning(self) -> None:
        successors = allowed_transitions(SessionState.COMPACTING)
        assert SessionState.RUNNING in successors
        assert SessionState.PLANNING not in successors


class TestReportSerialization:
    def test_to_dict_is_json_ready_and_reports_resumability(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, network_unavailable=True))
        payload = report.to_dict()
        assert payload == {
            "state": "waiting_network",
            "state_class": "waiting",
            "reasons": ["network_unavailable"],
            "primary_reason": "network_unavailable",
            "is_resumable": True,
            "metadata": {},
        }

    def test_metadata_carries_the_run_status_it_was_derived_from(self) -> None:
        assert derive_session_state(SessionStateSignal(run_status=RunStatus.running)).to_dict()["metadata"] == {"run_status": "running"}

    def test_reasons_are_ordered_by_precedence(self) -> None:
        report = derive_session_state(SessionStateSignal(run_status=RunStatus.running, recovering=True, retrying=True))
        assert report.reasons[0] in {"recovering", "retrying"}
        assert report.primary_reason == report.reasons[0]
