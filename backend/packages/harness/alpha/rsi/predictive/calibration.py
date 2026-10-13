"""Calibration: measure the *predictor*, not the system it sits in.

A predictor that emits numbers is not thereby useful. This module answers the
only question that matters before one is trusted: **when it says 0.8, how often
is it right?**

Four quantities, all standard and all deliberately reported as ``None`` rather
than as a flattering default:

* :func:`brier_score` — ``mean((p − y)²)``. Lower is better; it punishes both
  overconfidence and over-pessimism.
* :func:`calibration_error` — |mean predicted − observed rate| per bucket. A
  predictor can have a decent Brier score and still be systematically
  overconfident, and only this catches that.
* :func:`coverage` — the fraction of requests that produced a forecast rather
  than an abstention. A predictor that abstains on everything scores a perfect
  Brier score by answering nothing, which is why coverage is reported beside it
  and never omitted.
* **High-confidence failures** — forecasts at or above 0.8 that then failed.
  For an autonomous system this is the number that decides whether the tier is
  allowed to influence anything, because a confident wrong answer is worse than
  an honest abstention.

The load-bearing honesty rule: :func:`calibration_report` returns ``None`` metrics
when no forecast has been scored against a measured outcome. An uncalibrated
predictor is reported **uncalibrated**, not as perfectly calibrated — an empty
report must never be mistaken for a good one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.rsi.predictive.contracts import ObservedOutcome, OutcomePrediction

__all__ = [
    "HIGH_CONFIDENCE_THRESHOLD",
    "CalibrationReport",
    "ForecastScore",
    "brier_score",
    "calibration_error",
    "calibration_report",
    "coverage",
    "score_forecast",
]

#: A forecast at or above this probability counts as high-confidence. 0.8 is the
#: conventional Brier "sharpness" boundary and is declared here so the
#: high-confidence failure count has one stated definition rather than an
#: argument-dependent one.
HIGH_CONFIDENCE_THRESHOLD = 0.8

#: Buckets for :func:`calibration_error`. Ten is the conventional default and
#: gives each bucket enough expected mass to mean something at realistic sample
#: sizes, which a finer partition would not.
DEFAULT_BUCKETS = 10


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class ForecastScore:
    """One forecast paired with the outcome that later measured it.

    ``scored`` is ``False`` — with a real ``reason`` — when the pair cannot be
    scored: the forecast was an abstention, or the outcome was never measured.
    An unscored pair has ``None`` on every number rather than zeros, so a caller
    averaging over scores can tell "scored and wrong" from "never scored".
    """

    prediction_id: str
    candidate_id: str
    predicted: float | None
    observed: bool | None
    brier: float | None
    scored: bool
    reason: str = ""
    high_confidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "candidate_id": self.candidate_id,
            "predicted": self.predicted,
            "observed": self.observed,
            "brier": self.brier,
            "scored": self.scored,
            "reason": self.reason,
            "high_confidence": self.high_confidence,
        }


def score_forecast(prediction: OutcomePrediction, outcome: ObservedOutcome | None) -> ForecastScore:
    """Pair one forecast with one measured outcome.

    Refuses to score — with the real reason — when the forecast abstained, the
    outcome is absent, or the outcome is not measured. It also refuses a
    **mismatched pair**: a forecast and an observation naming different
    candidates are two different facts, and scoring them together would measure
    nothing.
    """
    if not isinstance(prediction, OutcomePrediction):
        raise ValueError(f"score_forecast takes an OutcomePrediction; got {type(prediction).__name__}")
    if not prediction.usable:
        return ForecastScore(
            prediction_id=prediction.prediction_id,
            candidate_id=prediction.candidate_id,
            predicted=None,
            observed=None,
            brier=None,
            scored=False,
            reason=f"forecast did not predict: status={prediction.status.value} ({prediction.abstain_reason})",
        )
    if outcome is None:
        return ForecastScore(
            prediction_id=prediction.prediction_id,
            candidate_id=prediction.candidate_id,
            predicted=prediction.predicted_success_probability,
            observed=None,
            brier=None,
            scored=False,
            reason="no outcome was recorded for this forecast, so it cannot be scored (an unobserved outcome is not a failed prediction)",
        )
    if not isinstance(outcome, ObservedOutcome):
        raise ValueError(f"score_forecast takes an ObservedOutcome or None; got {type(outcome).__name__}")
    if outcome.candidate_id and outcome.candidate_id != prediction.candidate_id:
        return ForecastScore(
            prediction_id=prediction.prediction_id,
            candidate_id=prediction.candidate_id,
            predicted=prediction.predicted_success_probability,
            observed=None,
            brier=None,
            scored=False,
            reason=f"forecast and outcome name different candidates ({prediction.candidate_id!r} vs {outcome.candidate_id!r}); scoring them together would measure nothing",
        )
    if not outcome.measured:
        return ForecastScore(
            prediction_id=prediction.prediction_id,
            candidate_id=prediction.candidate_id,
            predicted=prediction.predicted_success_probability,
            observed=None,
            brier=None,
            scored=False,
            reason=f"outcome is not measured: evidence_kind={outcome.evidence_kind!r}; a simulated result may not calibrate a predictor",
        )

    assert prediction.predicted_success_probability is not None  # narrowed by `.usable`
    assert outcome.success is not None  # narrowed by `.measured`
    probability = float(prediction.predicted_success_probability)
    actual = 1.0 if outcome.success else 0.0
    return ForecastScore(
        prediction_id=prediction.prediction_id,
        candidate_id=prediction.candidate_id,
        predicted=probability,
        observed=outcome.success,
        brier=(probability - actual) ** 2,
        scored=True,
        high_confidence=probability >= HIGH_CONFIDENCE_THRESHOLD,
    )


def brier_score(scores: Iterable[ForecastScore]) -> float | None:
    """Mean Brier score over the **scored** pairs; ``None`` when there are none.

    Unscored pairs are excluded rather than counted as 0 — an unscored pair is a
    missing measurement, and averaging a missing measurement in as a perfect
    score would make abstention look like accuracy.
    """
    values = [score.brier for score in scores if score.scored and score.brier is not None]
    if not values:
        return None
    return sum(values) / len(values)


def calibration_error(scores: Iterable[ForecastScore], buckets: int = DEFAULT_BUCKETS) -> float | None:
    """Expected calibration error over the scored pairs; ``None`` when there are none.

    Buckets the predictions, then takes ``|mean predicted − observed rate|``
    weighted by bucket mass. This is the systematic-bias detector: a predictor
    can hold an acceptable Brier score while consistently overclaiming, and only
    this surfaces it.
    """
    if isinstance(buckets, bool) or not isinstance(buckets, int) or buckets < 1:
        raise ValueError(f"buckets must be an integer >= 1; got {buckets!r}")
    scored = [score for score in scores if score.scored and score.predicted is not None and score.observed is not None]
    if not scored:
        return None
    grouped: dict[int, list[ForecastScore]] = {}
    for score in scored:
        assert score.predicted is not None
        index = min(buckets - 1, int(float(score.predicted) * buckets))
        grouped.setdefault(index, []).append(score)
    total = len(scored)
    error = 0.0
    for group in grouped.values():
        mass = len(group) / total
        mean_predicted = sum(float(item.predicted) for item in group if item.predicted is not None) / len(group)
        observed_rate = sum(1.0 if item.observed is True else 0.0 for item in group) / len(group)
        error += mass * abs(mean_predicted - observed_rate)
    return error


def coverage(predictions: Sequence[OutcomePrediction]) -> float | None:
    """Fraction of requests answered with a forecast; ``None`` for an empty sequence."""
    if not predictions:
        return None
    return sum(1 for prediction in predictions if prediction.usable) / len(predictions)


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate calibration over a set of scored forecast/outcome pairs.

    Every aggregate is ``None`` when nothing has been scored — ``brier=None``
    means *no evidence*, not *a good score*. ``high_confidence_failures`` is
    the number that decides whether a predictor may ever influence a selection,
    so it is reported first and never rounded away.
    """

    scored: int
    unscored: int
    abstained: int
    brier: float | None
    calibration_error: float | None
    coverage: float | None
    high_confidence_failures: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "unscored": self.unscored,
            "abstained": self.abstained,
            "brier": self.brier,
            "calibration_error": self.calibration_error,
            "coverage": self.coverage,
            "high_confidence_failures": self.high_confidence_failures,
            "detail": self.detail,
        }


def calibration_report(
    predictions: Sequence[OutcomePrediction],
    outcomes: Sequence[ObservedOutcome],
    *,
    buckets: int = DEFAULT_BUCKETS,
) -> CalibrationReport:
    """Pair forecasts with outcomes by ``candidate_id`` and aggregate.

    Pairing is by candidate id, the only stable key both sides carry. A forecast
    with no matching outcome is counted **unscored**, never as a failure — an
    outcome that was never observed is missing data, and scoring it as a wrong
    prediction would penalise the predictor for the system's incompleteness.
    """
    if not isinstance(predictions, Sequence) or isinstance(predictions, (str, bytes)):
        raise ValueError(f"calibration_report takes a sequence of predictions; got {type(predictions).__name__}")
    if not isinstance(outcomes, Sequence) or isinstance(outcomes, (str, bytes)):
        raise ValueError(f"calibration_report takes a sequence of outcomes; got {type(outcomes).__name__}")

    by_candidate: dict[str, ObservedOutcome] = {}
    for outcome in outcomes:
        if isinstance(outcome, ObservedOutcome) and outcome.candidate_id:
            by_candidate[outcome.candidate_id] = outcome

    scores: list[ForecastScore] = []
    abstained_count = 0
    for prediction in predictions:
        if not isinstance(prediction, OutcomePrediction):
            raise ValueError(f"calibration_report takes OutcomePrediction instances; got {type(prediction).__name__}")
        if not prediction.usable:
            abstained_count += 1
        scores.append(score_forecast(prediction, by_candidate.get(prediction.candidate_id)))

    scored_scores = [score for score in scores if score.scored]
    unscored = len(scores) - len(scored_scores)
    high_confidence_failures = sum(1 for score in scored_scores if score.high_confidence and score.observed is False)

    if scored_scores:
        detail = f"{len(scored_scores)} scored forecast(s); {unscored} unscored; {abstained_count} abstained; {high_confidence_failures} high-confidence failure(s) at p >= {HIGH_CONFIDENCE_THRESHOLD}"
    else:
        detail = f"no forecast has been scored against a measured outcome ({unscored} unscored, {abstained_count} abstained) — calibration is UNKNOWN, not good"

    return CalibrationReport(
        scored=len(scored_scores),
        unscored=unscored,
        abstained=abstained_count,
        brier=brier_score(scored_scores),
        calibration_error=calibration_error(scored_scores, buckets=buckets),
        coverage=coverage(list(predictions)),
        high_confidence_failures=high_confidence_failures,
        detail=detail,
    )
