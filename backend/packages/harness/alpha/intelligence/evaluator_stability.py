"""Phase B — evaluator stability: measure the verifier's own noise before trusting it.

Why this exists
---------------
The literature's clearest negative result about self-improvement is that
**internal verification is fragile** (arXiv 2607.04277). Alpha already has the
right instinct — :mod:`alpha.evolution.evidence.compare` applies per-metric
*noise floors* — but a floor is a **declared constant**, not a measurement of
Alpha's own verifier.

So when a candidate "improves" by +0.03, Alpha cannot say whether that is
signal or its own evaluator wobbling, because it has never characterised the
wobble. This module does that characterisation, once, and makes it reusable.

What is measured
----------------
``measure_noise_floor`` runs the **same** probe repeatedly and looks at the
spread of its results. Two statistics, because they answer different questions:

* :attr:`NoiseFloor.mean_absolute_deviation` — typical wobble magnitude. Answers
  "how big a delta is noise?"
* :attr:`NoiseFloor.range` — worst observed wobble. Answers "how bad has this
  evaluator ever been on identical input?"

A gate wants the former (typical) and a diagnostic wants the latter (worst), so
both are reported rather than one being chosen.

The honesty rules
------------------
* Fewer than ``min_repeats`` observations yields ``observed=False`` with a real
  reason. **A floor estimated from one sample is not a floor.**
* A single repeated observation (``spread == 0``) yields ``observed=False`` too,
  with the reason "every sample was identical, so no spread was observed". One
  identical reading cannot distinguish "deterministic" from "lucky".
* A probe that raises produces ``observed=False`` with the exception text. A
  crashing evaluator has no measurable noise; it has a broken one, and the two
  are reported differently.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "NoiseFloor",
    "StabilityReport",
    "measure_noise_floor",
    "resolve_noise_floor",
    "MIN_REPEATS_FOR_OBSERVATION",
]

#: Two agreeing observations are the minimum that can distinguish a spread from
#: a single lucky reading. Below this, nothing is claimed.
MIN_REPEATS_FOR_OBSERVATION = 2


@dataclass(frozen=True)
class NoiseFloor:
    """A measured (or refused) noise floor for one metric."""

    metric: str
    floor: float
    samples: int
    observed: bool
    reason: str
    source: str = "measured"
    declared: float | None = None
    """The configured floor this measurement is being compared against."""

    mean_absolute_deviation: float | None = None
    range: float | None = None
    values: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "floor": round(self.floor, 6),
            "samples": self.samples,
            "observed": self.observed,
            "reason": self.reason,
            "source": self.source,
            "declared": self.declared,
            "mean_absolute_deviation": None if self.mean_absolute_deviation is None else round(self.mean_absolute_deviation, 6),
            "range": None if self.range is None else round(self.range, 6),
            "values": [round(value, 6) for value in self.values],
        }


@dataclass(frozen=True)
class StabilityReport:
    """Every metric's noise floor, plus the verdict."""

    floors: dict[str, NoiseFloor] = field(default_factory=dict)
    all_observed: bool = False

    def floor_for(self, metric: str) -> float | None:
        """The effective floor for ``metric``, or ``None`` when none is known.

        ``None`` is not the same as ``0.0``: a caller receiving ``0.0`` would
        treat every delta as significant, which is the exact overconfidence this
        module exists to remove.
        """
        entry = self.floors.get(metric)
        return entry.floor if entry is not None and entry.observed else None

    def unobserved_metrics(self) -> list[str]:
        return sorted(metric for metric, entry in self.floors.items() if not entry.observed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_observed": self.all_observed,
            "unobserved_metrics": self.unobserved_metrics(),
            "floors": {metric: entry.to_dict() for metric, entry in sorted(self.floors.items())},
        }


def _score_of(result: Any) -> float | None:
    """Extract a comparable score from whatever the probe returned."""
    if isinstance(result, bool):
        return 1.0 if result else 0.0
    if isinstance(result, (int, float)):
        return float(result)
    score = getattr(result, "score", None)
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return float(score)
    if isinstance(result, dict) and isinstance(result.get("score"), (int, float)):
        return float(result["score"])
    return None


def measure_noise_floor(
    probe: Callable[[], Any],
    *,
    metric: str = "score",
    repeats: int = 3,
    min_repeats: int = MIN_REPEATS_FOR_OBSERVATION,
    declared: float | None = None,
) -> NoiseFloor:
    """Run ``probe`` ``repeats`` times and characterise the spread of its scores.

    Args:
        probe: A **deterministic** callable returning the same candidate's score.
            A probe whose result legitimately varies between runs (because it
            samples a model) measures the *system's* variance, not the
            *evaluator's* — which is a real measurement, but it must be labelled
            as such by the caller, not silently mislabelled as evaluator noise.
        repeats: How many times to run it. More is better; this is bounded by the
            caller because it costs real work.
        min_repeats: Below this, nothing is claimed as observed.

    Returns:
        A :class:`NoiseFloor`. ``observed=False`` carries the real reason.
    """
    if repeats < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats}")
    if min_repeats < 2:
        raise ValueError(f"min_repeats must be >= 2 (one sample has no spread), got {min_repeats}")

    values: list[float] = []
    for _ in range(repeats):
        try:
            extracted = _score_of(probe())
        except Exception as exc:  # noqa: BLE001 - disclosed, not swallowed
            return NoiseFloor(
                metric=metric,
                floor=declared if declared is not None else 0.0,
                samples=len(values),
                observed=False,
                reason=f"the evaluator raised {type(exc).__name__} while being measured: {exc}",
                source="unmeasured",
                declared=declared,
                values=tuple(values),
            )
        if extracted is None:
            return NoiseFloor(
                metric=metric,
                floor=declared if declared is not None else 0.0,
                samples=len(values),
                observed=False,
                reason="the evaluator returned a result with no comparable numeric score; its noise cannot be characterised",
                source="unmeasured",
                declared=declared,
                values=tuple(values),
            )
        values.append(extracted)

    if len(values) < min_repeats:
        return NoiseFloor(
            metric=metric,
            floor=declared if declared is not None else 0.0,
            samples=len(values),
            observed=False,
            reason=(f"{len(values)} observation(s) is below the {min_repeats} required to distinguish a spread from a single lucky reading, so no noise floor is claimed"),
            source="unmeasured",
            declared=declared,
            values=tuple(values),
        )

    mean = statistics.fmean(values)
    mad = statistics.fmean(abs(value - mean) for value in values)
    spread = max(values) - min(values)

    if spread == 0.0:
        return NoiseFloor(
            metric=metric,
            floor=declared if declared is not None else 0.0,
            samples=len(values),
            observed=False,
            reason=("every repeated observation was byte-identical, so no spread was observed; a deterministic-looking evaluator is not evidence of a zero noise floor and the declared value is retained"),
            source="unmeasured",
            declared=declared,
            mean_absolute_deviation=0.0,
            range=0.0,
            values=tuple(values),
        )

    return NoiseFloor(
        metric=metric,
        floor=mad,
        samples=len(values),
        observed=True,
        reason=(f"measured over {len(values)} identical-input evaluations: mean absolute deviation {mad:.6f}, range {spread:.6f}"),
        source="measured",
        declared=declared,
        mean_absolute_deviation=mad,
        range=spread,
        values=tuple(values),
    )


def resolve_noise_floor(measured: NoiseFloor, declared: float | None, *, source: str) -> float:
    """Combine a measured and a declared floor according to ``source``.

    ``max_of_both`` is the default and the important one: a measurement can only
    ever make the gate **stricter** than the declared constant, never looser. A
    measured floor can therefore never be used to justify accepting a smaller
    improvement than the configuration already forbids — which is the asymmetry
    that stops "we measured our verifier and it is great" from becoming a way to
    lower the bar.
    """
    if source not in {"measured", "declared", "max_of_both"}:
        raise ValueError(f"unknown noise floor source {source!r}; expected measured, declared, or max_of_both")
    declared_value = float(declared) if declared is not None else 0.0
    if not measured.observed:
        return declared_value
    if source == "declared":
        return declared_value
    if source == "measured":
        return measured.floor
    return max(measured.floor, declared_value)


def stability_report(floors: dict[str, NoiseFloor]) -> StabilityReport:
    """Bundle floors into a report."""
    return StabilityReport(floors=dict(floors), all_observed=bool(floors) and all(entry.observed for entry in floors.values()))
