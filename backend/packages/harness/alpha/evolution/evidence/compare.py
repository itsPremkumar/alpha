"""Candidate vs incumbent: a delta only counts when it beats a declared floor.

The classic failure of a self-evolution gate is a comparison that reads
"candidate 0.801, incumbent 0.800" and calls it an improvement. On a benchmark
with any variance at all, that number is noise. This module therefore refuses
to call a delta an improvement unless it **exceeds a noise floor that was
declared in configuration before the comparison ran**, and it refuses to
compare at all when the two sides are not commensurable.

Rules (all deterministic, all pure):

1. **No floor, no verdict.** A metric with no declared noise floor is
   ``incomparable`` - a delta without a stated floor is not evidence.
2. **Floor comparison is strict.** ``abs(delta) <= floor`` is ``within_noise``,
   and ``within_noise`` is *not* an improvement.
3. **Harness identity must match.** A candidate measured by a different harness
   revision than the incumbent is ``incomparable``; the two numbers describe
   different experiments.
4. **Sample-size regimes must match.** A gap larger than the configured
   ``max_sample_size_gap`` is a different regime, not an improvement. A side
   below ``min_sample_size`` is ``insufficient_evidence``.
5. **Units must match.** Comparing a ratio to a latency is meaningless.
6. **Direction is declared, never assumed.** ``lower_is_better`` metrics
   (latency, error counts) improve when the delta is negative.
7. **A regression anywhere blocks acceptance.** The report exposes every
   regression so the decision function can refuse the change even when the
   primary metric improved.

The statistics here are deliberately trivial (absolute and relative delta
against a floor). Statistical significance testing belongs to the harness that
produced the numbers; this layer only refuses to over-claim what it is given.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpha.evolution.evidence.models import (
    DIRECTIONS,
    HIGHER_IS_BETTER,
    Comparison,
    ComparisonReport,
    Measurement,
)

__all__ = [
    "ComparisonPolicy",
    "FLOOR_ABSOLUTE_TOLERANCE",
    "compare_measurements",
    "comparison_status_for",
    "index_measurements",
    "regression_reasons",
]

#: Absolute tolerance for the noise-floor comparison. Two floats that are
#: mathematically equal are rarely bit-equal (``0.810 - 0.800`` is
#: ``0.010000000000000009``), and a strict ``>`` on that bit pattern would let a
#: pure rounding artefact be reported as an improvement. The tolerance is
#: 1e-12 of a metric unit - far below any floor anyone would declare.
FLOOR_ABSOLUTE_TOLERANCE = 1e-12


@runtime_checkable
class ComparisonPolicy(Protocol):
    """The comparison-relevant slice of :class:`EvolutionEvidenceConfig`.

    Declared as a protocol so this module never imports the config object (no
    import cycle) while still requiring every knob to be read *from config*
    rather than from a module constant.
    """

    @property
    def primary_metric(self) -> str: ...

    @property
    def noise_floors(self) -> dict[str, float]: ...

    @property
    def metric_directions(self) -> dict[str, str]: ...

    @property
    def min_sample_size(self) -> int: ...

    @property
    def max_sample_size_gap(self) -> int: ...


def index_measurements(measurements: Sequence[Measurement], *, side: str) -> dict[str, Measurement]:
    """Index one side's measurements by metric name; duplicates are a hard error."""

    indexed: dict[str, Measurement] = {}
    for measurement in measurements:
        if measurement.side != side:
            raise ValueError(f"expected {side!r} measurements, got {measurement.metric!r} on side {measurement.side!r}")
        if measurement.metric in indexed:
            raise ValueError(f"metric {measurement.metric!r} was measured more than once on the {side} side; a silent average would hide the duplicate")
        indexed[measurement.metric] = measurement
    return indexed


def _incomparable(
    metric: str,
    unit: str,
    direction: str,
    candidate: Measurement,
    incumbent: Measurement,
    floor: float,
    reason: str,
) -> Comparison:
    return Comparison(
        metric=metric,
        unit=unit,
        direction=direction,
        candidate=candidate,
        incumbent=incumbent,
        noise_floor=floor,
        status="incomparable",
        reason=reason,
    )


def _insufficient(
    metric: str,
    unit: str,
    direction: str,
    candidate: Measurement,
    incumbent: Measurement,
    floor: float,
    reason: str,
) -> Comparison:
    return Comparison(
        metric=metric,
        unit=unit,
        direction=direction,
        candidate=candidate,
        incumbent=incumbent,
        noise_floor=floor,
        status="insufficient_evidence",
        reason=reason,
    )


def comparison_status_for(delta: float, floor: float, direction: str) -> tuple[str, bool]:
    """Classify a delta against a floor. Returns ``(status, exceeds_floor)``.

    The floor comparison is strict: a delta exactly equal to the floor is
    ``within_noise``. The comparison is done with an absolute tolerance of
    :data:`FLOOR_ABSOLUTE_TOLERANCE` so float rounding cannot manufacture an
    improvement out of a tie.
    """

    if direction not in DIRECTIONS:
        raise ValueError(f"unknown metric direction {direction!r}; expected one of {list(DIRECTIONS)}")
    exceeds = abs(delta) - floor > FLOOR_ABSOLUTE_TOLERANCE
    if not exceeds:
        return "within_noise", False
    better = delta > 0 if direction == HIGHER_IS_BETTER else delta < 0
    return ("improved" if better else "regressed"), True


def _compare_one(
    metric: str,
    candidate: Measurement,
    incumbent: Measurement,
    *,
    config: ComparisonPolicy,
) -> Comparison:
    direction = str(config.metric_directions.get(metric, HIGHER_IS_BETTER))
    if direction not in DIRECTIONS:
        raise ValueError(f"metric {metric!r} declares unknown direction {direction!r}; expected one of {list(DIRECTIONS)}")
    floor_value = config.noise_floors.get(metric)
    floor = 0.0 if floor_value is None else float(floor_value)

    if candidate.status != "measured" or incumbent.status != "measured":
        bad = candidate if candidate.status != "measured" else incumbent
        side = candidate.side if candidate.status != "measured" else incumbent.side
        return _insufficient(
            metric,
            candidate.unit,
            direction,
            candidate,
            incumbent,
            floor,
            f"metric {metric!r} was not measured on the {side} side (status {bad.status!r}): {bad.detail or 'no detail disclosed'}; an unmeasured metric is never read as a number",
        )
    if candidate.evidence != "measured" or incumbent.evidence != "measured":
        labelled = candidate if candidate.evidence != "measured" else incumbent
        side = candidate.side if candidate.evidence != "measured" else incumbent.side
        return _incomparable(
            metric,
            candidate.unit,
            direction,
            candidate,
            incumbent,
            floor,
            f"metric {metric!r} carries evidence label {labelled.evidence!r} on the {side} side; only 'measured' evidence can gate a promotion",
        )
    if candidate.unit != incumbent.unit:
        return _incomparable(
            metric,
            candidate.unit,
            direction,
            candidate,
            incumbent,
            floor,
            f"metric {metric!r} units are incomparable: candidate {candidate.unit!r} vs incumbent {incumbent.unit!r}",
        )
    if candidate.harness_id != incumbent.harness_id:
        return _incomparable(
            metric,
            candidate.unit,
            direction,
            candidate,
            incumbent,
            floor,
            f"metric {metric!r} was measured by different harnesses: candidate {candidate.harness_id!r} vs incumbent {incumbent.harness_id!r}",
        )
    min_samples = int(config.min_sample_size)
    for measurement in (candidate, incumbent):
        if measurement.sample_size < min_samples:
            return _insufficient(
                metric,
                candidate.unit,
                direction,
                candidate,
                incumbent,
                floor,
                f"metric {metric!r} has only {measurement.sample_size} sample(s) on the {measurement.side} side; policy requires >= {min_samples}",
            )
    gap = abs(candidate.sample_size - incumbent.sample_size)
    max_gap = int(config.max_sample_size_gap)
    if gap > max_gap:
        return _incomparable(
            metric,
            candidate.unit,
            direction,
            candidate,
            incumbent,
            floor,
            f"metric {metric!r} was measured in different sample-size regimes: candidate n={candidate.sample_size} vs incumbent n={incumbent.sample_size} (gap {gap} > max_sample_size_gap {max_gap})",
        )
    if floor_value is None:
        return _incomparable(
            metric,
            candidate.unit,
            direction,
            candidate,
            incumbent,
            0.0,
            f"metric {metric!r} has no declared noise floor in config; a delta without a stated floor is not an improvement",
        )
    candidate_value = candidate.value
    incumbent_value = incumbent.value
    if candidate_value is None or incumbent_value is None:
        return _insufficient(metric, candidate.unit, direction, candidate, incumbent, floor, f"metric {metric!r} lost its value before comparison; nothing was measured")
    if not math.isfinite(candidate_value) or not math.isfinite(incumbent_value):
        return _incomparable(metric, candidate.unit, direction, candidate, incumbent, floor, f"metric {metric!r} carries a non-finite value and cannot be ordered honestly")

    delta = candidate_value - incumbent_value
    relative = None if incumbent_value == 0 else delta / abs(incumbent_value)
    status, exceeds = comparison_status_for(delta, floor, direction)
    reason = f"candidate {candidate_value!r} vs incumbent {incumbent_value!r} (unit {candidate.unit!r}): delta {delta!r}"
    if status == "within_noise":
        reason += f" is within the declared noise floor {floor!r} - within_noise is not an improvement"
    elif status == "improved":
        reason += f" exceeds the declared noise floor {floor!r} in the {direction} direction"
    else:
        reason += f" exceeds the declared noise floor {floor!r} in the {direction} direction - this is a regression"
    return Comparison(
        metric=metric,
        unit=candidate.unit,
        direction=direction,
        candidate=candidate,
        incumbent=incumbent,
        noise_floor=floor,
        status=status,
        delta=delta,
        relative_delta=relative,
        exceeds_noise_floor=exceeds,
        reason=reason,
    )


def compare_measurements(
    candidate: Sequence[Measurement],
    incumbent: Sequence[Measurement],
    *,
    config: ComparisonPolicy,
) -> ComparisonReport:
    """Compare every measured metric and return the full per-metric report.

    Raises ``ValueError`` on a duplicate metric on either side (averaging two
    measurements of the same metric would hide the conflict) or on a mismatched
    side label. Every other problem - unmeasured, wrong evidence label, unit or
    harness mismatch, sample-size regime, missing floor - is reported on the
    affected :class:`Comparison` with a real reason instead of raising, so the
    decision layer can disclose exactly which metric was unusable.
    """

    candidate_by_metric = index_measurements(candidate, side="candidate")
    incumbent_by_metric = index_measurements(incumbent, side="incumbent")
    comparisons: list[Comparison] = []
    for metric in sorted(set(candidate_by_metric) | set(incumbent_by_metric)):
        candidate_measurement = candidate_by_metric.get(metric)
        incumbent_measurement = incumbent_by_metric.get(metric)
        if candidate_measurement is None or incumbent_measurement is None:
            missing_side = "candidate" if candidate_measurement is None else "incumbent"
            present = candidate_measurement if candidate_measurement is not None else incumbent_measurement
            assert present is not None  # exactly one side is present in this branch
            placeholder = Measurement(
                metric=metric,
                side=missing_side,
                value=None,
                unit=present.unit,
                sample_size=0,
                harness_id=present.harness_id,
                evidence="unverified",
                status="unavailable",
                detail=f"no {missing_side} measurement was supplied for metric {metric!r}; nothing was measured on that side",
            )
            comparisons.append(
                _insufficient(
                    metric,
                    present.unit,
                    str(config.metric_directions.get(metric, HIGHER_IS_BETTER)),
                    candidate_measurement if candidate_measurement is not None else placeholder,
                    incumbent_measurement if incumbent_measurement is not None else placeholder,
                    float(config.noise_floors.get(metric, 0.0)),
                    f"metric {metric!r} has no {missing_side} measurement; a one-sided comparison cannot decide anything",
                )
            )
            continue
        comparisons.append(_compare_one(metric, candidate_measurement, incumbent_measurement, config=config))
    return ComparisonReport(comparisons=tuple(comparisons), primary_metric=str(config.primary_metric))


def regression_reasons(report: ComparisonReport) -> list[str]:
    """Human-readable reasons for every regression in ``report``."""

    return [f"metric {comparison.metric!r} regressed beyond its noise floor: {comparison.reason}" for comparison in report.regressions]
