from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from alpha.reasoning.events import (
    EmitterAuthority,
    ReasoningActionIntent,
    ReasoningEvent,
    ReasoningEventType,
    parse_event,
    serialize_event,
)
from alpha.reasoning.loopguard import (
    LoopGuardCaps,
    LoopIntervention,
    LoopObservation,
    LoopSignal,
    choose_intervention,
    detect_loop_signals,
    evaluate_loopguard,
    project_loop_observations,
)

CAPS = LoopGuardCaps(enabled=True, signal_threshold=2, max_attempts=4, max_reflections=2, max_replans=1)


def observation(index: int, **values: object) -> LoopObservation:
    return LoopObservation(
        sequence=index,
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
        **values,
    )


def test_event_taxonomy_is_closed_and_matches_the_plan() -> None:
    assert {item.value for item in ReasoningEventType} == {
        "started",
        "policy.selected",
        "plan.created",
        "step.started",
        "hypothesis.created",
        "action.proposed",
        "action.approved",
        "action.executed",
        "observation.received",
        "evidence.added",
        "contradiction.detected",
        "verification.started",
        "verification.completed",
        "reflection.started",
        "reflection.completed",
        "replan.started",
        "replan.completed",
        "branch.created",
        "branch.pruned",
        "consensus.started",
        "consensus.completed",
        "context.compacted",
        "budget.warning",
        "budget.exhausted",
        "checkpoint.created",
        "completed",
        "blocked",
        "failed",
    }


def test_action_events_are_untrusted_until_the_existing_runtime_stamps_them() -> None:
    action = ReasoningActionIntent(
        tool_name="read_file",
        purpose="Inspect the failing boundary.",
        expected_observation="The exact failing line is identified.",
        arguments_digest="sha256:abc",
    )
    assert action.trust == "untrusted"
    proposed = ReasoningEvent(
        run_id="run-1",
        event_type=ReasoningEventType.ACTION_PROPOSED,
        action=action,
    )
    payload = serialize_event(proposed)
    assert parse_event(payload) == proposed

    with pytest.raises(ValidationError, match="existing runtime"):
        ReasoningEvent(
            run_id="run-1",
            event_type=ReasoningEventType.ACTION_APPROVED,
            authority=EmitterAuthority.REASONING_PLANE,
            action=action,
        )
    approved = ReasoningEvent(
        run_id="run-1",
        event_type=ReasoningEventType.ACTION_APPROVED,
        authority=EmitterAuthority.EXISTING_RUNTIME,
        action=action,
    )
    assert approved.status.value == "info"


def test_event_contract_requires_the_structural_fields() -> None:
    with pytest.raises(ValidationError, match="action intent"):
        ReasoningEvent(run_id="run-1", event_type=ReasoningEventType.ACTION_PROPOSED)
    with pytest.raises(ValidationError, match="observation_id"):
        ReasoningEvent(run_id="run-1", event_type=ReasoningEventType.OBSERVATION_RECEIVED)
    with pytest.raises(ValidationError, match="evidence id"):
        ReasoningEvent(run_id="run-1", event_type=ReasoningEventType.EVIDENCE_ADDED)
    with pytest.raises(ValidationError, match="budget_dimension"):
        ReasoningEvent(run_id="run-1", event_type=ReasoningEventType.BUDGET_EXHAUSTED)
    with pytest.raises(ValidationError, match="checkpoint_id"):
        ReasoningEvent(run_id="run-1", event_type=ReasoningEventType.CHECKPOINT_CREATED)


@pytest.mark.parametrize(
    ("observations", "signal"),
    [
        ([observation(0, action_signature="search"), observation(1, action_signature="search")], LoopSignal.SAME_ACTION),
        ([observation(0, tool_args_digest="a"), observation(1, tool_args_digest="a")], LoopSignal.SAME_TOOL_ARGS),
        ([observation(0, error_digest="timeout"), observation(1, error_digest="timeout")], LoopSignal.SAME_ERROR),
        ([observation(0, hypothesis_id="h1"), observation(1, hypothesis_id="h1")], LoopSignal.SAME_HYPOTHESIS),
        ([observation(0, plan_revision="p1"), observation(1, plan_revision="p1")], LoopSignal.PLAN_UNCHANGED),
        (
            [observation(0, evidence_ids=()), observation(1, evidence_ids=()), observation(2, evidence_ids=())],
            LoopSignal.NO_NEW_EVIDENCE,
        ),
        (
            [observation(0, verification_score=0.5), observation(1, verification_score=0.5), observation(2, verification_score=0.4)],
            LoopSignal.NO_VERIFICATION_IMPROVEMENT,
        ),
    ],
)
def test_each_loop_signal_fires_on_a_crafted_stream(
    observations: list[LoopObservation],
    signal: LoopSignal,
) -> None:
    analysis = detect_loop_signals(observations, CAPS)
    assert signal in analysis.signals
    assert analysis.disabled is False


def test_no_false_signal_when_the_stream_makes_progress() -> None:
    observations = [
        observation(0, action_signature="search", evidence_ids=("ev-1",), verification_score=0.2),
        observation(1, action_signature="read", evidence_ids=("ev-1", "ev-2"), verification_score=0.6),
    ]
    analysis = detect_loop_signals(observations, CAPS)
    assert LoopSignal.SAME_ACTION not in analysis.signals
    assert LoopSignal.NO_NEW_EVIDENCE not in analysis.signals
    assert LoopSignal.NO_VERIFICATION_IMPROVEMENT not in analysis.signals


def test_intervention_escalation_is_ordered() -> None:
    expected = [
        LoopIntervention.WARNING,
        LoopIntervention.CHANGE_CONTEXT,
        LoopIntervention.REQUEST_DISCRIMINATING_EVIDENCE,
        LoopIntervention.CHANGE_STRATEGY,
        LoopIntervention.CHANGE_MODEL,
        LoopIntervention.DELEGATE_CRITIC,
        LoopIntervention.TERMINATE,
    ]
    assert [choose_intervention(count, CAPS) for count in range(1, 8)] == expected


def test_hard_caps_and_cooldown_prevent_an_infinite_retry_recommendation() -> None:
    observations = [observation(0, action_signature="same"), observation(1, action_signature="same")]
    capped = evaluate_loopguard(
        observations,
        attempts=4,
        reflections=0,
        replans=0,
        caps=CAPS,
    )
    assert capped.terminal is True
    assert capped.intervention is LoopIntervention.BLOCK

    cooldown = evaluate_loopguard(
        observations,
        attempts=1,
        reflections=0,
        replans=0,
        caps=CAPS,
        now=10.1,
        last_intervention_at=10.0,
    )
    assert cooldown.cooldown_active is True
    assert cooldown.intervention is None

    terminal_signal = LoopGuardCaps(enabled=True, signal_threshold=2, terminal_signal_count=1, max_attempts=10)
    signals = evaluate_loopguard(
        observations,
        attempts=1,
        reflections=0,
        replans=0,
        caps=terminal_signal,
    )
    assert signals.terminal is True
    assert signals.intervention is LoopIntervention.TERMINATE


def test_events_project_into_the_loop_signal_stream() -> None:
    action = ReasoningActionIntent(
        purpose="Retry the focused probe.",
        expected_observation="A stable failure signature.",
    )
    events = [
        ReasoningEvent(
            run_id="run-1",
            event_type=ReasoningEventType.ACTION_PROPOSED,
            action=action,
            action_signature="probe",
            tool_args_digest="args-1",
            plan_revision="plan-1",
        ),
        ReasoningEvent(
            run_id="run-1",
            event_type=ReasoningEventType.ACTION_PROPOSED,
            action=action,
            action_signature="probe",
            tool_args_digest="args-1",
            plan_revision="plan-1",
        ),
    ]
    observations = project_loop_observations(events)
    analysis = detect_loop_signals(observations, CAPS)
    assert LoopSignal.SAME_ACTION in analysis.signals
    assert LoopSignal.SAME_TOOL_ARGS in analysis.signals
    assert LoopSignal.PLAN_UNCHANGED in analysis.signals


def test_default_loop_guard_is_off_and_delegates() -> None:
    observations = [observation(0, action_signature="same"), observation(1, action_signature="same")]
    decision = evaluate_loopguard(observations, attempts=1, reflections=0, replans=0)
    assert decision.intervention is None
    assert "default-off" in decision.reason
