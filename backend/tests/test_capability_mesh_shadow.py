"""Tests for shadow routing, canary gating and router self-evaluation (plan §13).

The load-bearing rules pinned here: the runner stores PREDICTIONS only
(no model budget, no live assignment), ``agreement`` is derived from the two
assignment rows' passport ids, ``agreement_rate`` is ``None`` at zero
comparisons (never 0.0), ``better_calls`` needs a MEASURED outcome, every
comparison is counted exactly once, the report discloses that no frozen replay
dataset exists, the canary slice is refused outside [0, 1], and promotion is
gated on the report's own numbers with the rollback baseline named.
"""

from __future__ import annotations

import json

import pytest

from alpha.capability_mesh.shadow import (
    MIN_AGREEMENT_FOR_PROMOTE,
    NO_REPLAY_DATASET_NOTE,
    SHADOW_FILE_NAME,
    CanaryPolicy,
    ShadowComparison,
    ShadowReport,
    ShadowRunner,
    ShadowStoreError,
)

PV = "p1"
OTHER_PV = "p2"

PRIMARY = {"passport_id": "agent:alpha", "role": "primary", "fit_score": 0.8}
OTHER = {"passport_id": "agent:beta", "role": "primary", "fit_score": 0.7}


def _comparison(
    fingerprint: str = "fp_0001",
    *,
    candidate: dict | None = None,
    predicted: float | None = 0.05,
    measured: float | None = None,
    profile_version: str = PV,
    task_category: str = "code",
) -> ShadowComparison:
    return ShadowComparison(
        task_fingerprint=fingerprint,
        task_category=task_category,
        profile_version=profile_version,
        primary_assignment=dict(PRIMARY),
        candidate_assignment=dict(OTHER if candidate is None else candidate),
        predicted_quality_delta=predicted,
        measured_quality_delta=measured,
    )


def _policy(**kwargs) -> CanaryPolicy:
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("slice", 0.1)
    kwargs.setdefault("baseline_policy", "balanced")
    kwargs.setdefault("rollback", "set policy to 'balanced' and restart the router")
    return CanaryPolicy(**kwargs)


def _runner(tmp_path, rows: list[ShadowComparison]) -> ShadowRunner:
    runner = ShadowRunner(tmp_path)
    for row in rows:
        runner.record(row)
    return runner


# ------------------------------------------------------------------- refusals


def test_comparison_refuses_an_empty_identity_field():
    with pytest.raises(ValueError, match="task_fingerprint must be non-empty"):
        ShadowComparison(task_fingerprint="", task_category="code", profile_version=PV, primary_assignment=dict(PRIMARY), candidate_assignment=dict(OTHER))
    with pytest.raises(ValueError, match="profile_version must be non-empty"):
        ShadowComparison(task_fingerprint="fp", task_category="code", profile_version="", primary_assignment=dict(PRIMARY), candidate_assignment=dict(OTHER))


def test_comparison_refuses_an_empty_assignment_mapping():
    with pytest.raises(ValueError, match="primary_assignment must be a non-empty mapping"):
        ShadowComparison(task_fingerprint="fp", task_category="code", profile_version=PV, primary_assignment={}, candidate_assignment=dict(OTHER))


def test_comparison_refuses_a_non_finite_delta():
    with pytest.raises(ValueError, match="predicted_quality_delta must be a finite number"):
        ShadowComparison(task_fingerprint="fp", task_category="code", profile_version=PV, primary_assignment=dict(PRIMARY), candidate_assignment=dict(OTHER), predicted_quality_delta=float("nan"))


def test_canary_refuses_a_slice_outside_the_unit_interval():
    with pytest.raises(ValueError, match="slice must be a finite number in \\[0, 1\\]"):
        CanaryPolicy(enabled=True, slice=1.5, baseline_policy="balanced", rollback="revert")
    with pytest.raises(ValueError, match="slice must be a finite number in \\[0, 1\\]"):
        CanaryPolicy(enabled=True, slice=-0.01, baseline_policy="balanced", rollback="revert")


def test_canary_refuses_a_missing_baseline_or_rollback():
    with pytest.raises(ValueError, match="baseline_policy must be non-empty"):
        CanaryPolicy(enabled=True, slice=0.1, baseline_policy="", rollback="revert")
    with pytest.raises(ValueError, match="rollback must be non-empty"):
        CanaryPolicy(enabled=True, slice=0.1, baseline_policy="balanced", rollback="")


def test_canary_refuses_a_negative_sample_floor():
    with pytest.raises(ValueError, match="min_sample_before_promote must be an int >= 0"):
        CanaryPolicy(enabled=True, slice=0.1, baseline_policy="balanced", rollback="revert", min_sample_before_promote=-1)


def test_a_zero_sample_floor_is_allowed_and_means_no_floor_declared():
    policy = _policy(min_sample_before_promote=0)
    assert policy.min_sample_before_promote == 0


def test_canary_refuses_an_empty_allowed_category():
    with pytest.raises(ValueError, match="allowed_task_categories entries must be non-empty"):
        CanaryPolicy(enabled=True, slice=0.1, baseline_policy="balanced", rollback="revert", allowed_task_categories=("code", ""))


# ------------------------------------------------------------------ the comparison


def test_agreement_is_derived_from_the_two_passport_ids():
    agreeing = _comparison(candidate=dict(PRIMARY))
    disagreeing = _comparison()

    assert agreeing.agrees is True
    assert disagreeing.agrees is False
    # The reason names both primaries, so the disagreement is inspectable.
    assert "production chose 'agent:alpha'" in disagreeing.reason
    assert "candidate would have chosen 'agent:beta'" in disagreeing.reason
    assert "same primary" in agreeing.reason


def test_a_row_without_a_passport_id_cannot_claim_agreement():
    row = ShadowComparison(
        task_fingerprint="fp",
        task_category="code",
        profile_version=PV,
        primary_assignment={"role": "primary"},
        candidate_assignment={"role": "primary"},
    )

    assert row.agrees is False
    assert "no passport id" in row.reason


def test_agreement_is_never_serialised_as_a_claim():
    agreeing = _comparison(candidate=dict(PRIMARY))

    document = agreeing.to_dict()

    assert "agrees" not in document
    # ...and a round-trip re-derives it rather than trusting the document.
    assert ShadowComparison.from_dict({**document, "agrees": False}).agrees is True


def test_the_entity_id_alias_is_understood():
    row = ShadowComparison(
        task_fingerprint="fp",
        task_category="code",
        profile_version=PV,
        primary_assignment={"entity_id": "agent:alpha"},
        candidate_assignment={"entity_id": "agent:alpha"},
    )
    assert row.agrees is True


# ---------------------------------------------------------------------- runner


def test_the_runner_reports_nothing_over_no_comparisons(tmp_path):
    runner = ShadowRunner(tmp_path)

    report = runner.report(PV)

    assert report.comparisons == ()
    assert report.agreement_rate is None
    assert (report.better_calls, report.worse_calls, report.undecided) == (0, 0, 0)
    assert NO_REPLAY_DATASET_NOTE in report.dataset_note
    assert "0 comparison(s)" in report.dataset_note


def test_the_runner_counts_agreement_and_measured_outcomes(tmp_path):
    runner = _runner(
        tmp_path,
        [
            _comparison("fp_1", candidate=dict(PRIMARY), measured=0.1),
            _comparison("fp_2", measured=-0.2),
            _comparison("fp_3", predicted=0.9, measured=None),
            _comparison("fp_4", candidate=dict(PRIMARY), measured=0.0),
        ],
    )

    report = runner.report(PV)

    assert len(report.comparisons) == 4
    assert report.agreement_rate == pytest.approx(0.5)
    assert report.better_calls == 1
    assert report.worse_calls == 1
    assert report.undecided == 2
    assert "4 comparison(s)" in report.dataset_note
    assert "3 of which carry a measured outcome" in report.dataset_note


def test_a_prediction_alone_never_feeds_better_calls(tmp_path):
    runner = _runner(tmp_path, [_comparison("fp_1", predicted=1.0)])

    report = runner.report(PV)

    assert report.better_calls == 0
    assert report.undecided == 1


def test_the_report_is_scoped_to_one_profile_version(tmp_path):
    runner = _runner(tmp_path, [_comparison("fp_1"), _comparison("fp_2", profile_version=OTHER_PV)])

    assert len(runner.report(PV).comparisons) == 1
    assert len(runner.report(OTHER_PV).comparisons) == 1
    assert len(runner.comparisons()) == 2
    assert len(runner.comparisons(profile_version=PV)) == 1
    assert len(runner.comparisons(task_category="code")) == 2
    assert runner.comparisons(task_category="research") == ()

    with pytest.raises(ValueError, match="profile_version filter must be non-empty"):
        runner.comparisons(profile_version="")
    with pytest.raises(ValueError, match="profile_version"):
        runner.report("")


def test_a_report_whose_summary_contradicts_its_rows_is_refused():
    rows = (_comparison("fp_1"),)
    with pytest.raises(ValueError, match="every comparison is counted exactly once"):
        ShadowReport(comparisons=rows, agreement_rate=0.0, better_calls=0, worse_calls=0, undecided=2, profile_version=PV, dataset_note=NO_REPLAY_DATASET_NOTE)
    with pytest.raises(ValueError, match="contradicts the 1 comparison"):
        ShadowReport(comparisons=rows, agreement_rate=1.0, better_calls=0, worse_calls=0, undecided=1, profile_version=PV, dataset_note=NO_REPLAY_DATASET_NOTE)
    with pytest.raises(ValueError, match="better_calls 1/worse_calls 0 contradict"):
        ShadowReport(comparisons=rows, agreement_rate=0.0, better_calls=1, worse_calls=0, undecided=0, profile_version=PV, dataset_note=NO_REPLAY_DATASET_NOTE)


def test_an_empty_report_may_not_claim_an_agreement_rate():
    """Zero comparisons has one honest answer — None — and 0.0 is a lie.

    The runner never produces one, but a hand-built report could, and
    "agreement 0%" over no comparisons is a measurement nobody took.
    """
    with pytest.raises(ValueError, match="agreement_rate must be None"):
        ShadowReport(comparisons=(), agreement_rate=0.0, better_calls=0, worse_calls=0, undecided=0, profile_version=PV, dataset_note=NO_REPLAY_DATASET_NOTE)
    empty = ShadowReport(comparisons=(), agreement_rate=None, better_calls=0, worse_calls=0, undecided=0, profile_version=PV, dataset_note=NO_REPLAY_DATASET_NOTE)
    assert empty.agreement_rate is None


def test_a_report_refuses_an_empty_dataset_note_or_profile_version():
    rows = (_comparison("fp_1"),)
    with pytest.raises(ValueError, match="dataset_note must be non-empty"):
        ShadowReport(comparisons=rows, agreement_rate=0.0, better_calls=0, worse_calls=0, undecided=1, profile_version=PV, dataset_note="")
    with pytest.raises(ValueError, match="profile_version must be non-empty"):
        ShadowReport(comparisons=rows, agreement_rate=0.0, better_calls=0, worse_calls=0, undecided=1, profile_version="", dataset_note=NO_REPLAY_DATASET_NOTE)


# ---------------------------------------------------------------- durability


def test_the_runner_round_trips_through_json(tmp_path):
    rows = [_comparison("fp_1", measured=0.1), _comparison("fp_2", candidate=dict(PRIMARY))]

    ShadowRunner(tmp_path).record(rows[0])
    runner = ShadowRunner(tmp_path)
    runner.record(rows[1])

    reloaded = ShadowRunner(tmp_path)
    assert reloaded.comparisons() == tuple(rows)
    assert reloaded.path is not None and reloaded.path.name == SHADOW_FILE_NAME
    assert reloaded.durable is True


def test_an_in_memory_runner_never_touches_the_filesystem(tmp_path):
    runner = ShadowRunner()
    runner.record(_comparison("fp_1"))

    assert runner.durable is False
    assert runner.path is None
    assert len(runner.comparisons()) == 1
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="no directory"):
        runner.save()


def test_a_corrupt_document_is_refused_by_name(tmp_path):
    (tmp_path / SHADOW_FILE_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ShadowStoreError, match="not valid JSON"):
        ShadowRunner(tmp_path)

    (tmp_path / SHADOW_FILE_NAME).write_text(json.dumps({"comparisons": [{"task_fingerprint": "fp"}]}), encoding="utf-8")
    with pytest.raises(ShadowStoreError, match="comparison #0 is invalid"):
        ShadowRunner(tmp_path)


def test_a_dishonest_document_shape_is_refused(tmp_path):
    (tmp_path / SHADOW_FILE_NAME).write_text(json.dumps({"comparisons": {}}), encoding="utf-8")
    with pytest.raises(ShadowStoreError, match="must be a JSON object with a 'comparisons' list"):
        ShadowRunner(tmp_path)


def test_record_refuses_a_non_comparison():
    with pytest.raises(ValueError, match="record\\(\\) expects a ShadowComparison"):
        ShadowRunner().record({"task_fingerprint": "fp"})


# ------------------------------------------------------------------- canary policy


def test_promotion_is_a_caller_decision_and_says_so():
    policy = _policy()

    refusal = policy.refuse_promotion()

    assert "CALLER decision" in refusal
    assert "baseline 'balanced'" in refusal
    assert "no candidate router" in refusal


def test_an_empty_allowlist_declares_no_restriction():
    assert _policy().covers_task_category("research") is True
    assert _policy(allowed_task_categories=("code",)).covers_task_category("code") is True
    assert _policy(allowed_task_categories=("code",)).covers_task_category("research") is False
    with pytest.raises(ValueError, match="task_category must be non-empty"):
        _policy().covers_task_category("")


def test_may_promote_refuses_a_disabled_canary(tmp_path):
    runner = _runner(tmp_path, [_comparison("fp_1", measured=0.2)])
    report = runner.report(PV)

    may, reason = _policy(enabled=False).may_promote(report)
    assert may is False
    assert "disabled" in reason


def test_may_promote_returns_its_reason_with_the_verdict(tmp_path):
    """A refusal must travel with its cause, never as a bare False."""
    report = _runner(tmp_path, [_comparison("fp_1", measured=0.2)]).report(PV)

    may, reason = _policy().may_promote(report)

    assert may is False
    assert reason.startswith("canary promotion refused:")
    assert "below min_sample_before_promote" in reason


def test_may_promote_refuses_below_the_sample_floor(tmp_path):
    rows = [_comparison(f"fp_{index}", measured=0.2) for index in range(3)]
    report = _runner(tmp_path, rows).report(PV)

    policy = _policy(min_sample_before_promote=5)

    may, reason = policy.may_promote(report)
    assert may is False
    assert "3 comparison(s) is below min_sample_before_promote 5" in reason


def test_may_promote_refuses_when_no_measured_outcome_shows_a_win(tmp_path):
    rows = [_comparison(f"fp_{index}", candidate=dict(PRIMARY), predicted=0.9) for index in range(25)]
    report = _runner(tmp_path, rows).report(PV)

    may, reason = _policy().may_promote(report)
    assert may is False
    assert "better_calls is 0" in reason


def test_may_promote_refuses_when_the_candidate_loses_more_than_it_wins(tmp_path):
    # Agreement clears the floor (0.8) while the measured outcomes do not:
    # 20 measured losses against 5 measured wins.
    rows = [_comparison(f"fp_agree_{index}", candidate=dict(PRIMARY), measured=-0.1) for index in range(20)]
    rows += [_comparison(f"fp_diff_{index}", measured=0.2) for index in range(5)]
    report = _runner(tmp_path, rows).report(PV)

    may, reason = _policy().may_promote(report)
    assert may is False
    assert "does not beat worse_calls" in reason
    assert "better_calls 5" in reason


def test_may_promote_refuses_a_router_that_barely_agrees(tmp_path):
    rows = [_comparison(f"fp_{index}", measured=0.2) for index in range(25)]
    report = _runner(tmp_path, rows).report(PV)

    may, reason = _policy().may_promote(report)
    assert may is False
    assert f"below MIN_AGREEMENT_FOR_PROMOTE {MIN_AGREEMENT_FOR_PROMOTE}" in reason


def test_may_promote_refuses_a_category_outside_the_envelope(tmp_path):
    rows = [_comparison(f"fp_{index}", candidate=dict(PRIMARY), measured=0.2, task_category="research") for index in range(25)]
    report = _runner(tmp_path, rows).report(PV)

    policy = _policy(allowed_task_categories=("code",))

    may, reason = policy.may_promote(report)
    assert may is False
    assert "outside allowed_task_categories" in reason


def test_may_promote_true_only_on_the_reports_own_numbers(tmp_path):
    rows = [_comparison(f"fp_{index}", candidate=dict(PRIMARY), measured=0.2) for index in range(25)]
    report = _runner(tmp_path, rows).report(PV)

    policy = _policy(min_sample_before_promote=25)

    may, reason = policy.may_promote(report)
    assert may is True
    assert reason.startswith("canary MAY")
    assert "remains the caller's decision" in reason
    assert "rolls back to baseline 'balanced'" in reason


def test_may_promote_refuses_a_non_report():
    with pytest.raises(ValueError, match="report must be a ShadowReport"):
        _policy().may_promote({"comparisons": ()})


def test_the_policy_round_trips_through_dict():
    policy = _policy(allowed_task_categories=("code", "research"), min_sample_before_promote=30)
    assert CanaryPolicy.from_dict(policy.to_dict()) == policy
