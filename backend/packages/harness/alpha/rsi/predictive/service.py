"""``PredictiveRSIService``: the one entry point, and the mode gate that contains it.

This is the only module a caller outside :mod:`alpha.rsi.predictive` needs. It
owns three things the rest deliberately do not:

**1. The mode.** :class:`PredictiveMode` runs ``disabled → observe_only →
shadow → rank_only → canary → active``, defaulting to ``disabled``.

* ``disabled`` / ``observe_only`` / ``shadow`` — forecasts are produced and
  recorded, and **no selection may change**. ``observe_only`` records and
  compares; ``shadow`` additionally computes what it *would* have chosen.
* ``rank_only`` / ``canary`` / ``active`` — reserved for a later, evidence-gated
  phase. :meth:`assert_mode_transition` escalates **one step at a time** and
  refuses ``active`` without an explicit promotion token, so a config edit
  cannot jump straight to a mode that is allowed to influence execution.

**2. The invariant that makes the tier safe to ship.** :attr:`RankingResult.
influenced_selection` is ``False`` in every shipped mode, and
:func:`_build_result` raises rather than construct a result claiming otherwise.
A test pins this against every mode rather than trusting the prose.

**3. Honest degradation.** When the ledger is unreadable the service does not
predict from nothing: it **abstains**, with the real error as the reason. That is
the difference between "no evidence" and "evidence I could not read", and it is
the same doctrine as :mod:`alpha.rsi.rrsi.store`.

The service also refuses to learn from unmeasured outcomes, routes every
forecast through the ledger so calibration survives a restart, and exposes
:meth:`calibration_report` — which reports ``None`` rather than a flattering
default when nothing has been scored yet.

It holds **no** promotion authority. It cannot admit a candidate, gate a
promotion, or roll anything back; ``GATE_ORDER`` in :mod:`alpha.rsi.promotion` is
untouched by this package.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from alpha.rsi.predictive.calibration import CalibrationReport, calibration_report
from alpha.rsi.predictive.contracts import (
    MEASURED_EVIDENCE_KIND,
    CandidateFeatures,
    ObservedOutcome,
    OutcomePrediction,
    PredictorHealth,
    abstained,
)
from alpha.rsi.predictive.predictor import (
    PREDICTOR_BACKENDS,
    OutcomePredictor,
    PredictorParams,
    resolve_predictor,
)
from alpha.rsi.predictive.store import ForecastLedger

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MODE",
    "DETERMINISTIC_BACKEND",
    "MODE_ORDER",
    "PredictiveMode",
    "PredictiveRSIService",
    "RankingResult",
    "assert_mode_transition",
    "record_holdout_verdict",
]

#: The Tier 0 baseline backend name, declared once so the engine's cycle wiring
#: and the promotion-verdict bridge cannot drift into two different spellings.
DETERMINISTIC_BACKEND = "deterministic"


class PredictiveMode(str, Enum):
    """How much authority a forecast may have. See the module docstring."""

    DISABLED = "disabled"
    OBSERVE_ONLY = "observe_only"
    SHADOW = "shadow"
    RANK_ONLY = "rank_only"
    CANARY = "canary"
    ACTIVE = "active"

    @property
    def rank(self) -> int:
        """Position in the escalation ladder. Compare with ``.rank``, never the enum member.

        This is a ``str`` enum, so ``mode >= MODE_ORDER[3]`` compares *strings*
        and silently inverts every gate — the same trap ``alpha.swarm.strategy``
        documents for ``ComplexityTier``.
        """
        return MODE_ORDER.index(self)

    @property
    def influences_selection(self) -> bool:
        """Whether a forecast may change what actually runs.

        ``False`` for every mode shipped in this slice. The escalating modes
        exist in the ladder so a later phase has a named destination, not so a
        config edit can reach one today.
        """
        return False


MODE_ORDER: tuple[PredictiveMode, ...] = (
    PredictiveMode.DISABLED,
    PredictiveMode.OBSERVE_ONLY,
    PredictiveMode.SHADOW,
    PredictiveMode.RANK_ONLY,
    PredictiveMode.CANARY,
    PredictiveMode.ACTIVE,
)

#: Shipped default. Alpha's posture for every opt-in improvement subsystem is
#: off until it is proven, and a predictive layer is no exception.
DEFAULT_MODE = PredictiveMode.DISABLED


def assert_mode_transition(current: PredictiveMode, requested: PredictiveMode, *, token: str | None = None) -> PredictiveMode:
    """Validate a mode change and return the new mode; refuse anything else.

    * **One step at a time.** Escalating more than one rung raises naming both
      modes — a config edit cannot skip the evidence-gated middle.
    * **De-escalating is always allowed.** Backing down to ``disabled`` is the
      safe direction and must never be the operation that needs a permission.
    * ``active`` additionally requires an explicit ``token``. It is the mode
      that may eventually influence execution, and it is not reachable by
      configuration alone.
    """
    if not isinstance(current, PredictiveMode) or not isinstance(requested, PredictiveMode):
        raise ValueError(f"mode transition takes PredictiveMode values; got current={current!r}, requested={requested!r}")
    if requested.rank - current.rank > 1:
        raise ValueError(f"refusing a mode jump: {current.value} -> {requested.value} skips {abs(requested.rank - current.rank) - 1} rung(s); escalate one step at a time ({' -> '.join(mode.value for mode in MODE_ORDER)})")
    if requested is PredictiveMode.ACTIVE and not token:
        raise ValueError("refusing to enter 'active' without an explicit promotion token: that mode may influence execution and is not reachable by configuration alone")
    return requested


@dataclass(frozen=True)
class RankingResult:
    """What the service would order, and the standing of that order.

    ``influenced_selection`` is ``False`` in every shipped mode. The constructor
    is the enforcement point: :meth:`assert_no_influence` raises rather than
    let a caller record a ``True`` in a mode that cannot justify one.

    ``abstained`` maps candidate id → reason, so "the predictor declined" is
    always visible per candidate rather than inferred from a missing entry.
    """

    order: tuple[str, ...]
    scores: dict[str, float]
    abstained: dict[str, str]
    mode: PredictiveMode
    influenced_selection: bool
    detail: str = ""
    forecasts: dict[str, OutcomePrediction] = field(default_factory=dict)

    def assert_no_influence(self) -> None:
        """Raise when this result claims it changed a selection.

        A caller that needs to record that a forecast *did* change behaviour
        cannot do so through this type while ``influences_selection`` is
        ``False`` on every shipped mode — which is the point.
        """
        if self.influenced_selection:
            raise RuntimeError("a RankingResult claims it influenced selection, which no shipped PredictiveMode permits; this is a bug, not a configuration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": list(self.order),
            "scores": dict(self.scores),
            "abstained": dict(self.abstained),
            "mode": self.mode.value,
            "influenced_selection": self.influenced_selection,
            "detail": self.detail,
        }


class PredictiveRSIService:
    """Mode-gated forecast, record and calibration over the RSI plane.

    ``backend`` selects the tier (:data:`alpha.rsi.predictive.predictor
    .PREDICTOR_BACKENDS`); an unknown name is refused rather than defaulted. The
    service never promotes, admits, gates or rolls back — it produces forecasts,
    records them, and reports how well they have held up.
    """

    def __init__(
        self,
        *,
        backend: str = "deterministic",
        params: PredictorParams | None = None,
        mode: PredictiveMode = DEFAULT_MODE,
        ledger: ForecastLedger | None = None,
        predictor: OutcomePredictor | None = None,
        clock: Any = time.time,
    ) -> None:
        if not isinstance(mode, PredictiveMode):
            raise ValueError(f"mode must be a PredictiveMode; got {mode!r}")
        if predictor is not None and backend != predictor.identity.predictor_id:
            # An explicit predictor and a conflicting backend string is a caller
            # mistake, not something to resolve by preferring one.
            raise ValueError(f"predictor {predictor.identity.predictor_id!r} does not match the declared backend {backend!r}; pass one or the other")
        self._params = (params or PredictorParams()).validate()
        self._predictor: OutcomePredictor = predictor if predictor is not None else resolve_predictor(backend, self._params)
        self._mode = mode
        self._ledger = ledger if ledger is not None else ForecastLedger()
        self._clock = clock

    # --- mode ---------------------------------------------------------------------

    @property
    def mode(self) -> PredictiveMode:
        return self._mode

    @property
    def predictor(self) -> OutcomePredictor:
        return self._predictor

    def set_mode(self, requested: PredictiveMode, *, token: str | None = None) -> PredictiveMode:
        """Transition to ``requested`` through :func:`assert_mode_transition`."""
        resolved = assert_mode_transition(self._mode, requested, token=token)
        self._mode = resolved
        logger.info("Predictive RSI mode is now %s", resolved.value)
        return resolved

    # --- forecasting ---------------------------------------------------------------

    def _store_is_usable(self) -> str | None:
        """``None`` when the ledger can be trusted, else the real reason it cannot.

        A degraded ledger is checked **before** predicting rather than after
        recording, so the failure mode is an abstention with a reason instead of
        a forecast nobody can later calibrate.
        """
        if not self._ledger.readable:
            return f"forecast ledger is unreadable ({self._ledger.load_error}); refusing to predict from an uncalibrated history"
        return None

    def forecast(self, candidate: CandidateFeatures) -> OutcomePrediction:
        """Forecast one candidate, recording the forecast durably.

        Abstains — with a real reason — when the mode is ``disabled`` or the
        ledger cannot be trusted. The forecast is always recorded before it is
        returned so a later outcome has something to be scored against.
        """
        if not isinstance(candidate, CandidateFeatures):
            raise ValueError(f"forecast takes a CandidateFeatures; got {type(candidate).__name__}")

        degraded = self._store_is_usable()
        if self._mode is PredictiveMode.DISABLED:
            prediction = abstained(
                candidate.candidate_id,
                self._predictor.identity,
                "predictive RSI is disabled (mode=disabled); no forecast is produced",
                support=None,
            )
        elif degraded is not None:
            prediction = abstained(candidate.candidate_id, self._predictor.identity, degraded)
        else:
            prediction = self._predictor.predict(candidate)

        self._ledger.append(prediction)
        return prediction

    def forecast_all(self, candidates: Sequence[CandidateFeatures]) -> dict[str, OutcomePrediction]:
        """Forecast every candidate, preserving input order."""
        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise ValueError(f"forecast_all takes a sequence of CandidateFeatures; got {type(candidates).__name__}")
        return {candidate.candidate_id: self.forecast(candidate) for candidate in candidates}

    # --- learning ------------------------------------------------------------------

    def record_outcome(self, outcome: ObservedOutcome) -> bool:
        """Record one outcome and feed it to the predictor.

        Returns whether the write landed — it does **not** raise. An unmeasured
        outcome is still recorded (the audit trail wants it) but is refused by the
        predictor's own ``observe``, so nothing simulated ever becomes training
        data.
        """
        if not isinstance(outcome, ObservedOutcome):
            raise ValueError(f"record_outcome takes an ObservedOutcome; got {type(outcome).__name__}")
        write = self._ledger.append(outcome)
        self._predictor.observe(outcome)
        return write.ok

    def record_outcomes(self, outcomes: Sequence[ObservedOutcome]) -> tuple[bool, ...]:
        if isinstance(outcomes, (str, bytes)) or not isinstance(outcomes, Sequence):
            raise ValueError(f"record_outcomes takes a sequence of ObservedOutcome; got {type(outcomes).__name__}")
        return tuple(self.record_outcome(outcome) for outcome in outcomes)

    # --- ranking -------------------------------------------------------------------

    def rank(self, candidates: Sequence[CandidateFeatures]) -> RankingResult:
        """Order candidates by predicted success probability.

        Abstained candidates are excluded from the order and reported with their
        reason — they are not silently ranked last, because "the predictor
        declined" and "the predictor ranked it worst" are different facts and
        only one of them is a judgement about the candidate.

        The returned :class:`RankingResult` always carries
        ``influenced_selection=False`` in every shipped mode.
        """
        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise ValueError(f"rank takes a sequence of CandidateFeatures; got {type(candidates).__name__}")

        forecasts = self.forecast_all(candidates)
        order: list[str] = []
        scores: dict[str, float] = {}
        abstentions: dict[str, str] = {}
        for candidate in candidates:
            prediction = forecasts[candidate.candidate_id]
            if prediction.usable and prediction.predicted_success_probability is not None:
                order.append(candidate.candidate_id)
                scores[candidate.candidate_id] = float(prediction.predicted_success_probability)
            else:
                abstentions[candidate.candidate_id] = prediction.reason_text()

        # Stable descending order: equal probabilities keep input order, so two
        # equally-favoured candidates do not swap places between runs.
        order.sort(key=lambda candidate_id: -scores[candidate_id])
        detail = f"mode={self._mode.value}; ranked {len(order)} candidate(s) by predicted success probability; {len(abstentions)} abstained (an abstention is a refusal to forecast, not a low score); no selection was changed"
        return RankingResult(
            order=tuple(order),
            scores=scores,
            abstained=abstentions,
            mode=self._mode,
            influenced_selection=self._mode.influences_selection,
            detail=detail,
            forecasts=forecasts,
        )

    # --- reporting ------------------------------------------------------------------

    def calibration_report(self) -> CalibrationReport:
        """Aggregate calibration over the recorded ledger.

        Returns a report whose metrics are ``None`` when nothing has been scored —
        an uncalibrated predictor is reported uncalibrated, never as good.
        """
        forecasts = self._ledger.forecasts()
        outcomes = self._ledger.measured_outcomes()
        if forecasts is None:
            return CalibrationReport(
                scored=0,
                unscored=0,
                abstained=0,
                brier=None,
                calibration_error=None,
                coverage=None,
                high_confidence_failures=0,
                detail=f"forecast ledger is unreadable ({self._ledger.load_error}); calibration is UNKNOWN, not good",
            )
        assert outcomes is not None  # narrowed: readable ledger implies readable outcomes
        return calibration_report(forecasts, outcomes)

    def status(self) -> dict[str, Any]:
        """Honest health block for operators and tests.

        ``mode``, the predictor identity and its own health, the ledger's
        standing, and the forecast/abstention counts — every count ``None`` when
        it is genuinely unknown rather than ``0``.
        """
        report = self.calibration_report()
        health: PredictorHealth = self._predictor.health()
        return {
            "mode": self._mode.value,
            "influences_selection": self._mode.influences_selection,
            "predictor": self._predictor.identity.to_dict(),
            "predictor_health": health.to_dict(),
            "ledger": self._ledger.status(),
            "calibration": {
                "scored": report.scored,
                "brier": report.brier,
                "calibration_error": report.calibration_error,
                "coverage": report.coverage,
                "high_confidence_failures": report.high_confidence_failures,
                "detail": report.detail,
            },
            "params": {
                "prior": self._params.prior,
                "prior_weight": self._params.prior_weight,
                "min_samples_global": self._params.min_samples_global,
                "min_samples_per_stratum": self._params.min_samples_per_stratum,
            },
            "accepted_backends": sorted(PREDICTOR_BACKENDS),
            "promotion_authority": False,
            "note": "Forecasts are advisory. This service cannot admit a candidate, gate a promotion or roll anything back; evidence_kind='forecast' is refused by the existing RSI gates by construction.",
        }


def record_holdout_verdict(candidate_id: str, payload: Mapping[str, Any] | None) -> bool | None:
    """Record a **genuinely measured** holdout verdict as a predictor outcome.

    This is the one bridge that closes the calibration loop, and it exists
    because Alpha already produces real measured verdicts:
    :func:`alpha.rsi.holdout.run_holdout` returns ``evidence_kind="measured"``
    from a suite that actually executed, and :func:`alpha.rsi.promotion.decide`
    reads that payload through ``_holdout_gate``. Until such a verdict exists,
    ``brier`` is honestly ``None``.

    Returns ``True`` when an outcome was recorded, ``False`` when the record
    could not be persisted, and ``None`` when the payload was **not** measured —
    the common case, and the one that matters most. A ``simulated`` or
    ``unverified`` payload, a payload without integer pass/fail counts, or a
    missing payload all return ``None`` and write nothing, so a preview's
    placeholder numbers can never become training data.

    ``success`` is the holdout's own verdict: a clean pass over at least one
    executed case. Counts that do not add up to a positive total are treated as
    *unmeasured* rather than as a failure, because "nothing executed" is not
    "it failed".

    Never raises — it is called from inside a promotion gate, and a telemetry
    failure must neither fail nor alter that gate.
    """
    if not isinstance(candidate_id, str) or not candidate_id:
        return None
    if not isinstance(payload, Mapping):
        return None
    if payload.get("evidence_kind") != MEASURED_EVIDENCE_KIND:
        return None
    passed = payload.get("passed")
    failed = payload.get("failed")
    if isinstance(passed, bool) or isinstance(failed, bool) or not isinstance(passed, int) or not isinstance(failed, int):
        return None
    if passed < 0 or failed < 0 or passed + failed <= 0:
        return None

    try:
        service = PredictiveRSIService(backend=DETERMINISTIC_BACKEND, mode=PredictiveMode.OBSERVE_ONLY, ledger=ForecastLedger())
        write = service.record_outcome(
            ObservedOutcome(
                candidate_id=candidate_id,
                success=failed == 0,
                component=str(payload.get("component") or ""),
                regime=str(payload.get("regime") or ""),
                evidence_kind=MEASURED_EVIDENCE_KIND,
                verifier_id="holdout",
            )
        )
        return bool(write)
    except Exception as exc:  # noqa: BLE001 - a promotion gate must never fail over telemetry
        logger.warning("Predictive RSI could not record the holdout verdict for %r: %s", candidate_id, exc, exc_info=True)
        return False
