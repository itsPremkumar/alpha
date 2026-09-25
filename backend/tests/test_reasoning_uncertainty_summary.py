from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.reasoning.config import ReasoningConfig
from alpha.reasoning.models import (
    EvidenceRecord,
    EvidenceSourceType,
    ReasoningState,
    ReflectionFailureClass,
    ReflectionRecord,
    UncertaintyCalibration,
    UncertaintyEstimate,
    UncertaintyRecord,
    VerificationRecord,
    VerificationStatus,
)
from alpha.reasoning.summary import (
    PrivateReasoningRejected,
    PrivateReasoningViolation,
    SafeRecordKind,
    assert_no_private_reasoning,
    build_safe_reasoning_record,
    generate_reasoning_summary,
    validated_lesson_handoffs,
    write_safe_reasoning_payload,
)
from alpha.reasoning.uncertainty import (
    UncertaintyAction,
    UncertaintyContext,
    decide_uncertainty,
)


def enabled_config(*, require_calibration: bool = False) -> ReasoningConfig:
    return ReasoningConfig(enabled=True, uncertainty_enabled=True, uncertainty_require_calibration_for_stop=require_calibration)


def estimate(value: float, *, calibrated: bool = False) -> UncertaintyEstimate:
    return UncertaintyEstimate(
        value=value,
        calibration_status=UncertaintyCalibration.CALIBRATED if calibrated else UncertaintyCalibration.HEURISTIC,
        method="fixture evaluation" if calibrated else "fixture heuristic",
        calibration_ref="fixture-suite-v1" if calibrated else None,
    )


def test_uncertainty_decision_rules_are_explicit() -> None:
    config = enabled_config()
    assert (
        decide_uncertainty(
            UncertaintyRecord(factual=estimate(0.7)),
            UncertaintyContext(can_continue_research=True),
            config=config,
        ).action
        is UncertaintyAction.CONTINUE_RESEARCH
    )
    assert (
        decide_uncertainty(
            UncertaintyRecord(factual=estimate(0.2)),
            UncertaintyContext(user_input_required=True),
            config=config,
        ).action
        is UncertaintyAction.ASK_USER
    )
    assert (
        decide_uncertainty(
            UncertaintyRecord(model=estimate(0.8)),
            UncertaintyContext(alternative_model_available=True),
            config=config,
        ).action
        is UncertaintyAction.SWITCH_MODEL
    )
    assert (
        decide_uncertainty(
            UncertaintyRecord(plan=estimate(0.7)),
            UncertaintyContext(branch_budget_available=True),
            config=config,
        ).action
        is UncertaintyAction.BRANCH
    )
    assert (
        decide_uncertainty(
            UncertaintyRecord(verification=estimate(0.6)),
            UncertaintyContext(independent_verifier_available=True),
            config=config,
        ).action
        is UncertaintyAction.REQUIRE_INDEPENDENT_VERIFICATION
    )
    stop = decide_uncertainty(
        UncertaintyRecord(factual=estimate(0.1), model=estimate(0.1), tool=estimate(0.1)),
        UncertaintyContext(verification_passed=True),
        config=config,
    )
    assert stop.action is UncertaintyAction.STOP
    assert stop.delegates_completion_to_existing_runtime is True


def test_uncalibrated_low_uncertainty_cannot_stop_when_calibration_is_required() -> None:
    decision = decide_uncertainty(
        UncertaintyRecord(factual=estimate(0.1)),
        UncertaintyContext(verification_passed=True),
        config=enabled_config(require_calibration=True),
    )
    assert decision.action is UncertaintyAction.REQUIRE_INDEPENDENT_VERIFICATION
    assert "not calibrated" in " ".join(decision.reasons)


def test_default_off_uncertainty_delegates_instead_of_deciding() -> None:
    decision = decide_uncertainty(
        UncertaintyRecord(factual=estimate(0.9)),
        UncertaintyContext(can_continue_research=True),
        config=ReasoningConfig(),
    )
    assert decision.action is UncertaintyAction.DELEGATE_EXISTING_RUNTIME
    assert "default-off" in decision.reasons[0]


@pytest.mark.parametrize(
    ("payload", "violation"),
    [
        ({"raw_reasoning": "hidden provider tokens"}, PrivateReasoningViolation.RAW_HIDDEN_REASONING),
        ({"internal_monologue": ["step one", "step two"]}, PrivateReasoningViolation.UNFILTERED_MONOLOGUE),
        (
            {"blocks": [{"type": "thinking", "data": "encrypted provider block"}]},
            PrivateReasoningViolation.PROVIDER_THINKING_BLOCK,
        ),
        ({"locator": "https://example.invalid/source?access_token=TEST_CREDENTIAL"}, PrivateReasoningViolation.CREDENTIAL_CONTENT),
        ({"chain_log": [{"step": str(index)} for index in range(100)]}, PrivateReasoningViolation.UNBOUNDED_CHAIN_LOG),
    ],
)
def test_guard_rejects_each_of_the_five_prohibited_payload_classes(
    payload: dict[str, object],
    violation: PrivateReasoningViolation,
) -> None:
    with pytest.raises(PrivateReasoningRejected) as excinfo:
        assert_no_private_reasoning(payload)
    assert excinfo.value.violation is violation


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (SafeRecordKind.GOAL, {"objective": "Ship the verified change."}),
        (SafeRecordKind.PLAN, {"steps": ["Reproduce", "Patch", "Verify"]}),
        (SafeRecordKind.DECISION, {"selected": "Minimal patch", "reason": "Smallest reversible boundary."}),
        (SafeRecordKind.OBSERVATION_SUMMARY, {"summary": "Focused test failed before the patch."}),
        (SafeRecordKind.EVIDENCE, {"claim": "Reproducer passes", "source": "pytest fixture"}),
        (SafeRecordKind.VERIFICATION, {"target": "focused test", "status": "pass"}),
        (SafeRecordKind.FAILURE_REASON, {"reason": "The first patch changed the wrong branch."}),
        (SafeRecordKind.REFLECTION_LESSON, {"lesson": "Verify the branch before editing."}),
        (SafeRecordKind.ARTIFACT_PROVENANCE, {"artifact": "diff-abc123", "digest": "sha256:abc123"}),
        (SafeRecordKind.USER_VISIBLE_SUMMARY, {"summary": "Reproduced, patched, and verified the change."}),
    ],
)
def test_guard_accepts_all_ten_legitimate_record_classes(kind: SafeRecordKind, payload: dict[str, str]) -> None:
    record = build_safe_reasoning_record(kind, payload)
    assert record.record_kind is kind
    assert_no_private_reasoning(record)


def test_summary_is_built_only_from_structured_records() -> None:
    state = ReasoningState(
        objective="Fix the rollback bug",
        plan=["Reproduce", "Patch", "Verify"],
        completed_step_ids=["Reproduce"],
        result="Focused rollback reproducer passes.",
        evidence=[
            EvidenceRecord(
                id="ev-1",
                claim="Focused reproducer passes",
                source="pytest",
                source_type=EvidenceSourceType.TEST,
                verified=True,
            )
        ],
        verifications=[
            VerificationRecord(
                id="vr-1",
                target="focused reproducer",
                method="pytest",
                status=VerificationStatus.PASS,
                evidence_ids=["ev-1"],
                independent=True,
            )
        ],
        unresolved_questions=["Does the broader regression suite remain green?"],
    )
    summary = generate_reasoning_summary(state, config=enabled_config())
    assert "Reproduce" in " ".join(summary.what_was_done)
    assert "Focused reproducer passes" in " ".join(summary.evidence)
    assert "PASS" in " ".join(summary.verification)
    assert "broader regression suite" in " ".join(summary.uncertainty_and_limitations)
    assert summary.result == "Focused rollback reproducer passes."
    assert "ev-1" in summary.source_record_ids
    assert_no_private_reasoning(summary)


def test_summary_never_invents_completion() -> None:
    state = ReasoningState(objective="Unfinished work")
    summary = generate_reasoning_summary(state)
    assert summary.result == "No final result has been recorded."
    assert "No completed plan step" in " ".join(summary.what_was_done)


def test_only_validated_lessons_cross_the_memory_handoff() -> None:
    state = ReasoningState(
        objective="Validate lesson handoff",
        evidence=[
            EvidenceRecord(
                id="ev-1",
                claim="A verifier observed the failure",
                source="runtime",
                source_type=EvidenceSourceType.RUNTIME,
            )
        ],
        reflection_events=[
            ReflectionRecord(
                id="r-1",
                failure_class=ReflectionFailureClass.ASSUMPTION_WRONG,
                what_failed="Endpoint existence was assumed.",
                evidence_ids=["ev-1"],
                lesson="Check endpoint availability before coding against it.",
                next_change="Read the official reference.",
                should_persist_to_memory=True,
            ),
            ReflectionRecord(
                id="r-2",
                failure_class=ReflectionFailureClass.UNKNOWN,
                what_failed="An unsupported lesson was proposed.",
                evidence_ids=["ev-2"],
                lesson="This must not be offered to memory.",
                next_change="Discard it.",
                should_persist_to_memory=True,
            ),
        ],
    )
    assert validated_lesson_handoffs(state) == ()

    state.verifications.append(
        VerificationRecord(
            id="vr-1",
            target="failure evidence",
            method="deterministic probe",
            status=VerificationStatus.PASS,
            evidence_ids=["ev-1"],
            independent=True,
        )
    )
    handoffs = validated_lesson_handoffs(state)
    assert len(handoffs) == 1
    assert handoffs[0].reflection_id == "r-1"
    assert handoffs[0].independent_verification is True


def test_prohibited_payload_cannot_reach_the_persistence_helper(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "state.json"
    with pytest.raises(PrivateReasoningRejected):
        write_safe_reasoning_payload(
            destination,
            {"chain_log": [{"internal_monologue": "hidden"}]},  # type: ignore[arg-type]
            config=enabled_config(),
        )
    assert not destination.exists()
    assert not destination.parent.exists()


def test_guarded_persistence_writes_only_bounded_structured_state(tmp_path: Path) -> None:
    state = ReasoningState(
        objective="Persist structured control state",
        reasoning_summary=["Verified fixture summary"],
        plan=["one", "two"],
    )
    destination = write_safe_reasoning_payload(tmp_path / "state.json", state, config=enabled_config())
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["objective"] == "Persist structured control state"
    assert "thinking" not in destination.read_text(encoding="utf-8").casefold()
