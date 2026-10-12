"""Tests for Predictive RSI — the pre-execution outcome estimator.

The package exists because every selection mechanism in ``alpha/rsi`` is
post-hoc: S2–S5 admission, Algorithm 2's ``argmax``, the holdout gate and the
evidence composer all run *after* both scores exist. These tests pin the
properties that make adding a forecast safe rather than merely useful:

* **a forecast can never promote** — proved against the *real* gates, not by
  trusting a label;
* **unknown is never 0.0** — in contracts, calibration, and the ledger;
* **abstention is a reasoned first-class answer**, and a low-sample stratum
  produces one;
* **shrinkage keeps a small stratum off the rails**;
* **an unreadable ledger abstains** instead of predicting from nothing;
* **nothing unmeasured ever trains the predictor**;
* **no shipped mode changes a selection**;
* **mode escalation is one rung at a time**, and ``active`` needs a token.

The honesty tests assert the *refusal* side as often as the happy path,
because in this subsystem the refusal is the safety property.
"""

from __future__ import annotations

import json

import pytest

from alpha.rsi.predictive import (
    MODE_ORDER,
    CandidateFeatures,
    DeterministicPredictor,
    EmpiricalPredictor,
    ForecastLedger,
    ForecastStatus,
    ObservedOutcome,
    OutcomePrediction,
    PredictiveMode,
    PredictiveRSIService,
    PredictorHealth,
    PredictorIdentity,
    PredictorParams,
    RankingResult,
    assert_mode_transition,
    brier_score,
    calibration_error,
    calibration_report,
    coverage,
    forecast_ledger_path,
    resolve_predictor,
    score_forecast,
    wilson_interval,
)
from alpha.rsi.predictive.contracts import FORECAST_EVIDENCE_KIND, abstained, new_prediction_id
from alpha.rsi.predictive.store import LedgerWrite


def _ledger(tmp_path) -> ForecastLedger:
    return ForecastLedger(tmp_path / "rsi" / "predictions.jsonl")


def _service(tmp_path, *, backend: str = "deterministic", mode: PredictiveMode = PredictiveMode.OBSERVE_ONLY, **kwargs) -> PredictiveRSIService:
    return PredictiveRSIService(backend=backend, mode=mode, ledger=_ledger(tmp_path), **kwargs)


def _candidate(candidate_id: str = "cand-1", **kwargs) -> CandidateFeatures:
    params = {"component": "prompt", "regime": "coding", "steps": 2}
    params.update(kwargs)
    return CandidateFeatures(candidate_id=candidate_id, **params)


def _support(service: PredictiveRSIService, count: int, *, component: str = "prompt", regime: str = "coding", success_every: int = 2) -> None:
    """Feed ``count`` measured outcomes into a stratum."""
    for index in range(count):
        service.record_outcome(
            ObservedOutcome(
                candidate_id=f"seed-{component}-{regime}-{index}",
                success=index % success_every == 0,
                component=component,
                regime=regime,
            )
        )


# --- 1. a forecast can never promote (the load-bearing safety property) ----------


def test_a_forecast_is_refused_by_the_real_holdout_gate(tmp_path):
    """Label honesty is not enough — prove the actual enforcement point refuses it."""
    from alpha.rsi.holdout import holdout_gate

    prediction = _service(tmp_path).forecast(_candidate())
    passed, reason = holdout_gate(prediction.to_dict())
    assert passed is False, "a forecast must never satisfy the holdout gate"
    assert reason.startswith("holdout unverified")


def test_a_forecast_is_refused_by_the_real_rrsi_selection_gate(tmp_path):
    """The RRSI selection conjunct must not admit a candidate on a forecast."""
    from alpha.rsi.rrsi.acceptance import Measurement
    from alpha.rsi.rrsi.selection import election_gate_for

    prediction = _service(tmp_path).forecast(_candidate())
    assert prediction.evidence_kind == FORECAST_EVIDENCE_KIND
    # Even handed every "signal" a forecast could supply, the gate cannot treat
    # it as measured: Measurement.measured requires evidence_kind == "measured".
    measured_as_forecast = Measurement(score=0.9, cost=1.0, evidence_kind=prediction.evidence_kind)
    assert measured_as_forecast.measured is False

    outcome = election_gate_for("cand-1", signals={"candidate_score": 0.9, "candidate_cost": 1.0, "incumbent_score": 0.5, "incumbent_cost": 1.0, "best_score": 0.5, "components": ["prompt"]})
    # The gate has its own complete-measurement contract; the assertion here is
    # that a forecast's own payload can never be assembled into a passing gate.
    assert not (outcome.admitted and outcome.screen is not None)


def test_the_forecast_evidence_kind_cannot_be_overridden(tmp_path):
    """``evidence_kind`` is pinned by the dataclass, not settable by a caller.

    If this were an ordinary init field, a caller could relabel a forecast as
    ``"measured"`` and the whole safety argument would rest on discipline.
    """
    prediction = _service(tmp_path).forecast(_candidate())
    assert prediction.evidence_kind == FORECAST_EVIDENCE_KIND
    with pytest.raises(TypeError):
        OutcomePrediction(
            prediction_id="p",
            candidate_id="c",
            identity=PredictorIdentity("x", "v1", 0),
            predicted_success_probability=0.9,
            evidence_kind="measured",
        )


# --- 2. unknown is never 0.0 ---------------------------------------------------


def test_an_unscored_calibration_report_is_none_not_zero(tmp_path):
    """An empty report must not be mistaken for a good one."""
    service = _service(tmp_path)
    service.forecast(_candidate())
    report = service.calibration_report()
    assert report.scored == 0
    assert report.brier is None, "no evidence is None, never 0.0 (which would read as a perfect score)"
    assert report.calibration_error is None
    # Coverage answers a different question — "did the predictor answer?" — so it
    # is legitimately 1.0 here while nothing has been scored. Collapsing the two
    # would let a predictor that answers everything and is checked against
    # nothing look calibrated.
    assert report.coverage == 1.0
    assert "UNKNOWN" in report.detail


def test_absent_forecast_quantities_are_none():
    """An abstention carries no number to read."""
    identity = PredictorIdentity("test", "v1", 0)
    prediction = abstained("cand-1", identity, "no support")
    assert prediction.predicted_success_probability is None
    assert prediction.uncertainty is None
    assert prediction.predicted_cost is None
    assert prediction.support is None
    assert prediction.usable is False


# --- 3. abstention is reasoned and first-class ---------------------------------


def test_an_abstention_without_a_reason_is_refused():
    """A refusal nobody can act on is not a disclosure."""
    with pytest.raises(ValueError, match="non-empty reason"):
        abstained("cand-1", PredictorIdentity("t", "v1", 0), "   ")


def test_an_abstained_forecast_cannot_carry_a_probability():
    """The coupling that stops a stale number being read off a refusal."""
    with pytest.raises(ValueError, match="must not carry a probability"):
        OutcomePrediction(
            prediction_id=new_prediction_id(),
            candidate_id="cand-1",
            identity=PredictorIdentity("t", "v1", 0),
            status=ForecastStatus.ABSTAINED,
            predicted_success_probability=0.9,
            abstain_reason="refused",
        )


def test_the_four_statuses_stay_distinct():
    """Refusing and "the caller sent nonsense" are different failures."""
    identity = PredictorIdentity("t", "v1", 0)
    for status, reason in ((ForecastStatus.ABSTAINED, "declined"), (ForecastStatus.INVALID, "malformed"), (ForecastStatus.EXPIRED, "stale")):
        prediction = abstained("cand-1", identity, reason, status=status)
        assert prediction.status is status
        assert prediction.usable is False
        assert status.usable is False
    assert ForecastStatus.PREDICTED.usable is True


def test_disabled_mode_abstains_with_the_reason_naming_the_mode(tmp_path):
    service = _service(tmp_path, mode=PredictiveMode.DISABLED)
    prediction = service.forecast(_candidate())
    assert prediction.status is ForecastStatus.ABSTAINED
    assert "disabled" in prediction.abstain_reason


# --- 4. a low-sample stratum abstains ------------------------------------------


def test_the_empirical_tier_abstains_below_the_stratum_floor(tmp_path):
    """A stratum with one observation is a memory, not a rate."""
    service = _service(tmp_path, backend="empirical_baseline", params=PredictorParams(min_samples_global=2, min_samples_per_stratum=5))
    _support(service, 6, component="prompt")  # global floor met
    prediction = service.forecast(_candidate(component="memory"))  # different stratum
    assert prediction.status is ForecastStatus.ABSTAINED
    assert "insufficient stratum support" in prediction.abstain_reason
    assert prediction.predicted_success_probability is None


def test_the_empirical_tier_abstains_below_the_global_floor(tmp_path):
    service = _service(tmp_path, backend="empirical_baseline", params=PredictorParams(min_samples_global=50))
    _support(service, 10)
    prediction = service.forecast(_candidate())
    assert prediction.status is ForecastStatus.ABSTAINED
    assert "insufficient global sample support" in prediction.abstain_reason


# --- 5. shrinkage keeps a small stratum off the rails ---------------------------


def test_shrinkage_pulls_a_small_stratum_toward_the_prior(tmp_path):
    """1-of-1 must not report 1.0."""
    service = _service(tmp_path, backend="empirical_baseline", params=PredictorParams(prior=0.5, prior_weight=4.0, min_samples_global=1, min_samples_per_stratum=1))
    service.record_outcome(ObservedOutcome(candidate_id="only", success=True, component="prompt", regime="coding"))
    prediction = service.forecast(_candidate())
    assert prediction.status is ForecastStatus.PREDICTED
    assert prediction.predicted_success_probability == pytest.approx((1 + 4 * 0.5) / (1 + 4))
    assert prediction.predicted_success_probability < 1.0
    assert prediction.uncertainty is not None and prediction.uncertainty > 0


def test_wilson_interval_refuses_a_zero_sample_stratum():
    """A zero-sample stratum has an abstention, not an interval."""
    with pytest.raises(ValueError, match="total > 0"):
        wilson_interval(0, 0)
    low, high = wilson_interval(1, 1)
    assert 0.0 <= low < 1.0 <= high <= 1.0


def test_wilson_interval_refuses_impossible_counts():
    with pytest.raises(ValueError, match="0 <= successes <= total"):
        wilson_interval(5, 3)


# --- 6. an unreadable ledger abstains, never predicts from nothing -------------


def test_an_unreadable_ledger_abstains_and_reports_the_real_error(tmp_path):
    """'no evidence' and 'evidence I could not read' are different states.

    A malformed *line* is not this case — that is skipped and counted, and the
    surrounding records stay trusted (see the sibling test). Unreadable means the
    file cannot be read at all, which is what forces an abstention rather than
    a forecast nobody could later calibrate.
    """
    path = tmp_path / "rsi" / "predictions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # a directory at the ledger's path makes the read fail

    service = _service(tmp_path)
    assert service._ledger.readable is False  # noqa: SLF001 - asserting the precondition honestly
    prediction = service.forecast(_candidate())
    assert prediction.status is ForecastStatus.ABSTAINED
    assert "unreadable" in prediction.abstain_reason
    report = service.calibration_report()
    assert report.brier is None
    assert "UNKNOWN" in report.detail


def test_a_malformed_line_does_not_make_the_whole_ledger_unreadable(tmp_path):
    """One bad line is skipped and counted; the rest stay trusted (house pattern)."""
    path = tmp_path / "rsi" / "predictions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json\n", encoding="utf-8")

    ledger = _ledger(tmp_path)
    assert ledger.readable is True
    assert ledger.entries == ()
    assert ledger.status()["skipped_lines"] == 1


def test_the_ledger_path_sits_under_runtime_home_and_reads_ALPHA_HOME(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    assert forecast_ledger_path() == tmp_path / "rsi" / "predictions.jsonl"


def test_a_first_run_ledger_is_readable_and_empty_not_corrupt(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.readable is True
    assert ledger.entries == ()
    assert ledger.forecasts() == ()
    status = ledger.status()
    assert status["forecast_count"] == 0
    assert status["load_error"] is None


def test_a_malformed_line_is_skipped_and_counted(tmp_path):
    path = tmp_path / "rsi" / "predictions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind": "forecast"}\nNOT JSON\n{"kind": "nonsense"}\n', encoding="utf-8")
    ledger = _ledger(tmp_path)
    assert ledger.readable is True
    assert ledger.status()["skipped_lines"] == 2
    assert ledger.status()["record_count"] == 1, "a malformed line is skipped, not fatal"


# --- 7. nothing unmeasured trains the predictor --------------------------------


def test_a_simulated_outcome_cannot_train_the_predictor(tmp_path):
    """Otherwise a preview's placeholder numbers become the training set."""
    predictor = EmpiricalPredictor(PredictorParams(min_samples_global=1, min_samples_per_stratum=1))
    predictor.observe(ObservedOutcome(candidate_id="x", success=True, component="prompt", regime="coding", evidence_kind="simulated"))
    predictor.observe(ObservedOutcome(candidate_id="y", success=True, component="prompt", regime="coding", evidence_kind="unverified"))
    assert predictor.health().observed_outcomes == 0
    assert "2 unmeasured outcome(s) refused" in predictor.health().detail
    prediction = predictor.predict(_candidate())
    assert prediction.status is ForecastStatus.ABSTAINED


def test_a_measured_outcome_without_attribution_is_disclosed_not_bucketed():
    """An outcome with no component cannot be stratified.

    Filing it under a fabricated bucket, or dropping it silently, would hide a
    gap in the caller's instrumentation instead of surfacing it.
    """
    predictor = EmpiricalPredictor(PredictorParams(min_samples_global=1, min_samples_per_stratum=1))
    predictor.observe(ObservedOutcome(candidate_id="x", success=True, component="", regime="coding"))
    assert predictor.health().observed_outcomes == 0
    assert "excluded for lacking a component attribution" in predictor.health().detail


def test_an_unmeasured_outcome_records_the_audit_trail_but_not_the_evidence(tmp_path):
    service = _service(tmp_path)
    service.record_outcome(ObservedOutcome(candidate_id="cand-1", success=True, evidence_kind="simulated"))
    ledger = service._ledger  # noqa: SLF001 - asserting the split between recorded and trusted
    assert len(ledger.outcomes() or ()) == 1, "recorded for the audit trail"
    assert ledger.measured_outcomes() == (), "but excluded from anything that calibrates"


# --- 8. no shipped mode changes a selection ------------------------------------


@pytest.mark.parametrize("mode", list(MODE_ORDER))
def test_no_shipped_mode_claims_to_influence_selection(tmp_path, mode):
    """Every mode, including the escalating ones, must report False today."""
    assert mode.influences_selection is False
    result = _service(tmp_path, mode=mode).rank([_candidate()])
    assert result.influenced_selection is False
    result.assert_no_influence()


def test_a_result_claiming_influence_is_refused(tmp_path):
    """The enforcement point must raise rather than record an unjustified True."""
    result = RankingResult(order=("cand-1",), scores={"cand-1": 0.9}, abstained={}, mode=PredictiveMode.OBSERVE_ONLY, influenced_selection=True)
    with pytest.raises(RuntimeError, match="influenced selection"):
        result.assert_no_influence()


def test_an_abstained_candidate_is_reported_not_silently_ranked_last(tmp_path):
    """'the predictor declined' is not 'the predictor ranked it worst'."""
    service = _service(tmp_path, backend="empirical_baseline", params=PredictorParams(min_samples_global=99))
    result = service.rank([_candidate("good"), _candidate("declined")])
    assert result.abstained and set(result.abstained) == {"good", "declined"}
    assert result.order == ()


def test_ranking_is_stable_for_equal_probabilities(tmp_path):
    service = _service(tmp_path)
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    first = service.rank(candidates).order
    second = service.rank(candidates).order
    assert first == second == ("a", "b", "c")


# --- 9. mode escalation is one rung at a time ----------------------------------


def test_mode_transition_refuses_a_jump():
    with pytest.raises(ValueError, match="refusing a mode jump"):
        assert_mode_transition(PredictiveMode.DISABLED, PredictiveMode.ACTIVE)


def test_mode_transition_allows_one_step():
    assert assert_mode_transition(PredictiveMode.DISABLED, PredictiveMode.OBSERVE_ONLY) is PredictiveMode.OBSERVE_ONLY


def test_active_requires_an_explicit_token():
    with pytest.raises(ValueError, match="promotion token"):
        assert_mode_transition(PredictiveMode.CANARY, PredictiveMode.ACTIVE)
    assert assert_mode_transition(PredictiveMode.CANARY, PredictiveMode.ACTIVE, token="approved") is PredictiveMode.ACTIVE


def test_de_escalating_is_always_allowed():
    """Backing down must never be the operation that needs a permission."""
    for mode in MODE_ORDER:
        assert assert_mode_transition(mode, PredictiveMode.DISABLED) is PredictiveMode.DISABLED


def test_the_default_mode_is_disabled(tmp_path):
    assert PredictiveRSIService().mode is PredictiveMode.DISABLED


def test_mode_ranking_is_numeric_not_string(tmp_path):
    """A str-enum compared as a string inverts every gate — pin the rank."""
    assert MODE_ORDER[-1].rank > MODE_ORDER[0].rank
    assert PredictiveMode.SHADOW.rank - PredictiveMode.OBSERVE_ONLY.rank == 1


# --- 10. bounds are refused, never clamped -------------------------------------


def test_predictor_params_refuse_a_zero_prior_weight():
    """Clamping 0 would disable shrinkage, the opposite of what was written."""
    with pytest.raises(ValueError, match="prior_weight"):
        PredictorParams(prior_weight=0).validate()


def test_predictor_params_refuse_a_non_probability_prior():
    with pytest.raises(ValueError, match="prior"):
        PredictorParams(prior=1.5).validate()


def test_an_unknown_backend_is_refused_by_name(tmp_path):
    """A silent fallback would let an operator think they configured Tier 1."""
    with pytest.raises(ValueError, match="unknown predictor backend"):
        resolve_predictor("jepa_adapter")


def test_candidate_features_refuse_an_unbounded_plan():
    with pytest.raises(ValueError, match="bounded plan"):
        CandidateFeatures(candidate_id="c", component="prompt", steps=10_000)


def test_a_boolean_is_never_accepted_as_a_probability():
    with pytest.raises(ValueError, match="probability"):
        OutcomePrediction(
            prediction_id="p",
            candidate_id="c",
            identity=PredictorIdentity("t", "v1", 0),
            predicted_success_probability=True,
        )


# --- 11. calibration measures the predictor ------------------------------------


def test_a_perfect_and_a_worst_forecast_score_their_true_brier():
    identity = PredictorIdentity("t", "v1", 0)
    right = OutcomePrediction(prediction_id="p1", candidate_id="c", identity=identity, predicted_success_probability=1.0)
    wrong = OutcomePrediction(prediction_id="p2", candidate_id="c", identity=identity, predicted_success_probability=0.0)
    good = score_forecast(right, ObservedOutcome(candidate_id="c", success=True))
    bad = score_forecast(wrong, ObservedOutcome(candidate_id="c", success=True))
    assert good.scored is True and good.brier == pytest.approx(0.0)
    assert bad.scored is True and bad.brier == pytest.approx(1.0)


def test_a_mismatched_pair_is_refused_rather_than_scored():
    identity = PredictorIdentity("t", "v1", 0)
    prediction = OutcomePrediction(prediction_id="p", candidate_id="mine", identity=identity, predicted_success_probability=0.7)
    score = score_forecast(prediction, ObservedOutcome(candidate_id="theirs", success=True))
    assert score.scored is False
    assert "different candidates" in score.reason


def test_an_unobserved_outcome_is_missing_data_not_a_failure():
    identity = PredictorIdentity("t", "v1", 0)
    prediction = OutcomePrediction(prediction_id="p", candidate_id="c", identity=identity, predicted_success_probability=0.7)
    score = score_forecast(prediction, None)
    assert score.scored is False
    assert "not a failed prediction" in score.reason


def test_unscored_pairs_are_excluded_from_the_brier_average():
    """Averaging a missing measurement in as 0.0 makes abstention look like accuracy."""
    identity = PredictorIdentity("t", "v1", 0)
    scored = OutcomePrediction(prediction_id="p1", candidate_id="a", identity=identity, predicted_success_probability=0.5)
    unscored = OutcomePrediction(prediction_id="p2", candidate_id="b", identity=identity, predicted_success_probability=0.5)
    scores = [score_forecast(scored, ObservedOutcome(candidate_id="a", success=True)), score_forecast(unscored, None)]
    assert brier_score(scores) == pytest.approx(0.25)
    assert brier_score([scores[1]]) is None


def test_high_confidence_failures_are_counted_separately():
    """A confident wrong answer is worse than an honest abstention."""
    identity = PredictorIdentity("t", "v1", 0)
    confident = OutcomePrediction(prediction_id="p1", candidate_id="a", identity=identity, predicted_success_probability=0.95)
    confident_fail = score_forecast(confident, ObservedOutcome(candidate_id="a", success=False))
    assert confident_fail.high_confidence is True
    report = calibration_report([confident], [ObservedOutcome(candidate_id="a", success=False)])
    assert report.high_confidence_failures == 1
    assert report.scored == 1


def test_a_forecast_that_abstained_is_counted_as_abstained_not_failed():
    identity = PredictorIdentity("t", "v1", 0)
    prediction = abstained("a", identity, "no support")
    report = calibration_report([prediction], [])
    assert report.abstained == 1
    assert report.scored == 0
    assert report.high_confidence_failures == 0


def test_coverage_is_none_for_no_requests_and_zero_for_all_abstentions():
    assert coverage([]) is None
    identity = PredictorIdentity("t", "v1", 0)
    assert coverage([abstained("a", identity, "no"), abstained("b", identity, "no")]) == 0.0


def test_calibration_error_is_none_without_scored_pairs():
    assert calibration_error([]) is None


# --- durable round-trip -------------------------------------------------------


def test_a_forecast_survives_a_restart(tmp_path):
    """Calibration needs history that outlives the process."""
    service = _service(tmp_path)
    original = service.forecast(_candidate())
    service.record_outcome(ObservedOutcome(candidate_id="cand-1", success=True))

    reloaded = ForecastLedger(_ledger(tmp_path).path)
    forecasts = reloaded.forecasts()
    assert forecasts is not None and len(forecasts) == 1
    assert forecasts[0].prediction_id == original.prediction_id
    assert forecasts[0].evidence_kind == FORECAST_EVIDENCE_KIND
    assert reloaded.measured_outcomes() is not None and len(reloaded.measured_outcomes()) == 1


def test_a_forecast_round_trips_through_json():
    identity = PredictorIdentity("t", "v1", 1)
    original = OutcomePrediction(prediction_id="p", candidate_id="c", identity=identity, predicted_success_probability=0.42, uncertainty=0.1, support=7)
    payload = json.loads(json.dumps(original.to_dict()))
    assert OutcomePrediction.from_dict(payload).to_dict() == payload


def test_an_unknown_schema_version_is_refused_not_reinterpreted():
    identity = PredictorIdentity("t", "v1", 0)
    original = OutcomePrediction(prediction_id="p", candidate_id="c", identity=identity, predicted_success_probability=0.42)
    payload = original.to_dict()
    payload["schema_version"] = 999
    with pytest.raises(ValueError, match="schema_version"):
        OutcomePrediction.from_dict(payload)


def test_an_appended_record_is_counted_exactly_once(tmp_path):
    """Regression: adopting an append in memory *after* re-reading the file
    double-counted every record for the life of the process, inflating the
    predictor's sample support and the calibration denominator."""
    ledger = _ledger(tmp_path)
    identity = PredictorIdentity("t", "v1", 0)
    ledger.append(OutcomePrediction(prediction_id="p1", candidate_id="a", identity=identity, predicted_success_probability=0.5))
    ledger.append(OutcomePrediction(prediction_id="p2", candidate_id="b", identity=identity, predicted_success_probability=0.5))
    assert len(ledger.forecasts() or ()) == 2

    # The same ledger re-read from disk must agree with the in-memory view.
    reloaded = ForecastLedger(ledger.path)
    assert len(reloaded.forecasts() or ()) == 2, "an append must not be counted twice"


def test_append_reports_whether_the_write_landed(tmp_path):
    """Never raise over telemetry; never claim durability that did not happen."""
    ledger = _ledger(tmp_path)
    identity = PredictorIdentity("t", "v1", 0)
    write = ledger.append(OutcomePrediction(prediction_id="p", candidate_id="c", identity=identity, predicted_success_probability=0.5))
    assert isinstance(write, LedgerWrite) and write.ok is True
    refused = ledger.append({"not": "a record"})
    assert refused.ok is False and "OutcomePrediction" in (refused.error or "")


# --- 12. the RSI cycle observes (live wiring) ---------------------------------


def test_a_live_cycle_records_a_forecast_without_changing_any_verdict(tmp_path, monkeypatch):
    """The wiring that makes calibration possible at all.

    A predictor no live cycle ever calls can never be calibrated, so
    ``run_rsi_cycle`` observes. ``observe_only`` is provably inert, so the
    verdict, the stage and the evidence kind must be exactly what they were
    before the forecast existed.
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    from alpha.rsi.engine import RSIEngine

    engine = RSIEngine()
    result = engine.run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    text = " ".join(result.evidence)

    assert "Predictive RSI forecast (observe_only, evidence_kind='forecast')" in text
    assert "This is a forecast made BEFORE execution, not a measurement of it." in text
    # Nothing about the verdict may move.
    assert result.promoted is False
    assert result.stage.value == "preview"
    assert result.evidence_kind == "simulated"
    assert result.ab_test is not None and result.ab_test.candidate_score is None
    assert result.holdout is not None and result.holdout.regressed is None
    # The forecast must land before the closing lines, so evidence[-1] keeps the
    # meaning _last_summary has always quoted.
    forecast_lines = [index for index, line in enumerate(result.evidence) if "Predictive RSI forecast" in line]
    promotion_lines = [index for index, line in enumerate(result.evidence) if line.startswith("Promotion blocked")]
    assert forecast_lines and promotion_lines
    assert forecast_lines[0] < promotion_lines[0]
    assert result.evidence[-1].startswith("To obtain a real verdict")


def test_a_live_cycle_may_not_smuggle_a_fabricated_score_back_in(tmp_path, monkeypatch):
    """The existing fabricated-score ban applies to forecast lines too."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    from alpha.rsi.engine import RSIEngine

    result = RSIEngine().run_rsi_cycle(bottleneck="Latency", target_component="tool_router")
    text = " ".join(result.evidence + result.holdout.evidence)
    for fabricated in ("0.88", "0.89", "0.72", "0.80", "0.92"):
        assert fabricated not in text


def test_a_live_cycle_records_no_outcome_because_it_measures_nothing(tmp_path, monkeypatch):
    """A preview must not put a fabricated label into the calibrating ledger."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    from alpha.rsi.engine import RSIEngine
    from alpha.rsi.predictive import ForecastLedger

    RSIEngine().run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    ledger = ForecastLedger()
    assert ledger.status()["forecast_count"] == 1, "the forecast is recorded"
    assert ledger.status()["outcome_count"] == 0, "no outcome exists, because nothing measured this candidate"


def test_cycles_accumulate_forecasts_across_restarts(tmp_path, monkeypatch):
    """Calibration needs history that outlives the process."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    from alpha.rsi.engine import RSIEngine
    from alpha.rsi.predictive import ForecastLedger

    RSIEngine().run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    RSIEngine().run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    assert ForecastLedger().status()["forecast_count"] == 2


def test_a_degraded_forecast_store_is_disclosed_not_dropped(tmp_path, monkeypatch):
    """A missing forecast must say so; returning [] would read as 'nothing to say'."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ALPHA_HOME", str(home))
    # A directory at the ledger's path makes the read fail.
    blocked = home / "rsi" / "predictions.jsonl"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.mkdir()

    from alpha.rsi.engine import RSIEngine

    result = RSIEngine().run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    text = " ".join(result.evidence)
    assert "unreadable" in text
    assert result.promoted is False, "a broken ledger must not fail or alter the cycle"


def test_the_cycle_forecast_is_a_forecast_not_a_gate_payload(tmp_path, monkeypatch):
    """The recorded forecast must itself be refused by the holdout gate."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    from alpha.rsi.engine import RSIEngine
    from alpha.rsi.holdout import holdout_gate
    from alpha.rsi.predictive import ForecastLedger

    RSIEngine().run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    forecasts = ForecastLedger().forecasts() or ()
    assert forecasts
    passed, reason = holdout_gate(forecasts[0].to_dict())
    assert passed is False
    assert reason.startswith("holdout unverified")


# --- status honesty ------------------------------------------------------------


def test_status_reports_no_promotion_authority(tmp_path):
    status = _service(tmp_path).status()
    assert status["promotion_authority"] is False
    assert status["influences_selection"] is False
    assert status["mode"] == PredictiveMode.OBSERVE_ONLY.value
    assert status["predictor"]["tier"] == 0


def test_tier_zero_is_untrained_by_design(tmp_path):
    """It is the fixed baseline every later tier must beat."""
    predictor = DeterministicPredictor()
    before = predictor.health().observed_outcomes
    predictor.observe(ObservedOutcome(candidate_id="x", success=True))
    assert predictor.health().observed_outcomes == before + 1
    assert "untrained by design" in predictor.health().detail


def test_predictor_health_reports_uncalibrated_as_none():
    health = PredictorHealth(available=True, detail="fresh")
    assert health.calibrated is None, "never calibrated is None, never True"


def test_predictor_health_identity_requires_a_tier():
    with pytest.raises(ValueError, match="tier"):
        PredictorIdentity("t", "v1", True)
    with pytest.raises(ValueError, match="tier"):
        PredictorIdentity("t", "v1", -1)


def test_the_service_refuses_a_predictor_that_contradicts_its_backend(tmp_path):
    predictor = DeterministicPredictor()
    with pytest.raises(ValueError, match="does not match"):
        PredictiveRSIService(backend="empirical_baseline", predictor=predictor, ledger=_ledger(tmp_path))
