"""Predictive RSI: forecast a candidate's likely outcome before choosing it.

The one responsibility :mod:`alpha.rsi` provably lacked. Every selection
mechanism in the RSI plane is post-hoc — S2–S5 admission, Algorithm 2's
``argmax``, the holdout gate and the evidence composer all run *after* both
scores exist — so the search explores candidates in emission order and learns
only once the budget is already spent.

This package adds the estimate, not a second loop. It is **advisory**: it
forecasts, records and reports, and it cannot admit a candidate, gate a
promotion or roll anything back.

The norm of the contract is in ``AGENTS.md`` beside this file. Three properties
are load-bearing and each exists because a plausible alternative produces a
specific lie:

* **A forecast is not a measurement.** Every forecast carries
  ``evidence_kind="forecast"``, a label the existing RSI gates do not accept, so
  a forecast is structurally incapable of promoting.
* **Unknown is ``None``, never ``0.0``.** An absent probability, an absent
  outcome and an uncalibrated report are all ``None``.
* **Abstention is a first-class answer**, always carrying a reason, because a
  refusal that looks like a number is the failure this package exists to
  prevent.

The loop closes deliberately and honestly: measure a predictor against real
outcomes first, and only then let it influence anything.
"""

from __future__ import annotations

from alpha.rsi.predictive import calibration as calibration
from alpha.rsi.predictive import contracts as contracts
from alpha.rsi.predictive import predictor as predictor
from alpha.rsi.predictive import service as service
from alpha.rsi.predictive import store as store
from alpha.rsi.predictive.calibration import (
    CalibrationReport,
    ForecastScore,
    brier_score,
    calibration_error,
    calibration_report,
    coverage,
    score_forecast,
)
from alpha.rsi.predictive.contracts import (
    CandidateFeatures,
    ForecastStatus,
    ObservedOutcome,
    OutcomePrediction,
    PredictorHealth,
    PredictorIdentity,
)
from alpha.rsi.predictive.predictor import (
    DeterministicPredictor,
    EmpiricalPredictor,
    OutcomePredictor,
    PredictorParams,
    resolve_predictor,
    wilson_interval,
)
from alpha.rsi.predictive.service import (
    MODE_ORDER,
    PredictiveMode,
    PredictiveRSIService,
    RankingResult,
    assert_mode_transition,
)
from alpha.rsi.predictive.store import ForecastLedger, LedgerWrite, forecast_ledger_path

__all__ = [
    # submodules
    "calibration",
    "contracts",
    "predictor",
    "service",
    "store",
    # contracts
    "CandidateFeatures",
    "ForecastStatus",
    "ObservedOutcome",
    "OutcomePrediction",
    "PredictorHealth",
    "PredictorIdentity",
    # predictors
    "DeterministicPredictor",
    "EmpiricalPredictor",
    "OutcomePredictor",
    "PredictorParams",
    "resolve_predictor",
    "wilson_interval",
    # calibration
    "CalibrationReport",
    "ForecastScore",
    "brier_score",
    "calibration_error",
    "calibration_report",
    "coverage",
    "score_forecast",
    # service
    "MODE_ORDER",
    "PredictiveMode",
    "PredictiveRSIService",
    "RankingResult",
    "assert_mode_transition",
    # store
    "ForecastLedger",
    "LedgerWrite",
    "forecast_ledger_path",
]
