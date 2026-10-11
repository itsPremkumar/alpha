"""Capability calibration: does an agent's stated confidence mean anything? (plan §8)

Calibration answers "when this entity says 80% confidence on this capability,
how often was the measured quality actually ~0.8?" — by pairing each record's
self-stated ``confidence`` (the signal) with its measured ``quality`` (the
outcome) and grouping the pairs into confidence buckets. Three rules decide
the shape of this module:

* **An under-sampled panel says "unresolved", never "well calibrated".**
  Below ``min_samples_for_verdict`` comparable pairs the verdict is ``None``
  (the swarm-deliberation house rule), and a bucket must carry at least
  ``CALIBRATION_SAMPLE_FLOOR`` samples before it can flip the verdict — 20
  samples spread thin across a dozen buckets cannot answer anything.
* **Zero comparable pairs is a ``None`` Brier score, never 0.0.** A
  placeholder zero would read as "perfectly calibrated" when nothing was
  measured at all.
* **Comparability is scoped to ONE profile version first.** Records from a
  different ``profile_version`` are out of scope (a rubric change invalidates
  comparability) and are excluded BEFORE the exclusion counter runs, so a
  v0-era dataset can neither dilute nor inflate a v1 verdict. Records lacking
  ``confidence`` or ``quality`` are excluded and counted in
  ``CalibrationReport.excluded_count`` — an honest accounting of how much of
  the panel could not be compared.

This is a measuring instrument only: it reports how much the stated
confidence can be trusted; it never rewrites an estimate, a passport, or a
routing decision.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from alpha.capability_mesh.memory import CapabilityRecord

#: Default bucket width on the unit-confidence axis (0-0.1, 0.1-0.2, ...).
DEFAULT_BUCKET_WIDTH = 0.1

#: Below this many comparable pairs the well-calibrated question is unresolved.
DEFAULT_MIN_SAMPLES_FOR_VERDICT = 20

#: A bucket needs this many samples before its |predicted - observed| can flip
#: the verdict; a thin bucket is evidence for a human, not a verdict.
CALIBRATION_SAMPLE_FLOOR = 5

#: |predicted - observed| within this per bucket counts as calibrated.
CALIBRATION_TOLERANCE = 0.15

#: Epsilon for bucket indexing: 0.3/0.1 is 2.9999... in binary, and a naive
#: int() would file a 0.3 confidence under the 0.2-0.3 bucket.
_BUCKET_EPSILON = 1e-9


def _format_bound(value: float) -> str:
    """A compact label bound with at least one decimal: 0.8 -> '0.8', 1.0 -> '1.0'."""
    text = f"{value:.10f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


@dataclass(frozen=True)
class CalibrationBucket:
    """One confidence bucket: what was predicted vs what was observed.

    ``confidence_bucket`` is the human-readable label (e.g. ``"0.8-0.9"``),
    ``predicted`` the mean stated confidence inside it, ``observed`` the mean
    measured quality of the same contributions, ``count`` how many comparable
    pairs landed there.
    """

    confidence_bucket: str
    predicted: float
    observed: float
    count: int

    def __post_init__(self) -> None:
        _require_text(self.confidence_bucket, "confidence_bucket")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise ValueError(f"count must be an int >= 0, got {self.count!r}")
        for name in ("predicted", "observed"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number, got {value!r}")

    def to_dict(self) -> dict[str, object]:
        return {"confidence_bucket": self.confidence_bucket, "predicted": self.predicted, "observed": self.observed, "count": self.count}


@dataclass(frozen=True)
class CalibrationReport:
    """The honest result of calibrating one profile version's records.

    ``brier_score`` is the mean ``(confidence - quality)^2`` over comparable
    pairs — ``None`` when there are none, never a reassuring placeholder 0.0.
    ``well_calibrated`` is ``True``/``False`` only when the panel is large
    enough to answer and otherwise ``None``: an under-sampled panel reports
    "unresolved", never "well calibrated".
    """

    buckets: tuple[CalibrationBucket, ...]
    brier_score: float | None
    sample_count: int
    well_calibrated: bool | None
    profile_version: str
    excluded_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "buckets", tuple(self.buckets))
        for bucket in self.buckets:
            if not isinstance(bucket, CalibrationBucket):
                raise ValueError(f"buckets must contain CalibrationBucket entries, got {bucket!r}")
        _require_text(self.profile_version, "profile_version")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError(f"sample_count must be an int >= 0, got {self.sample_count!r}")
        if isinstance(self.excluded_count, bool) or not isinstance(self.excluded_count, int) or self.excluded_count < 0:
            raise ValueError(f"excluded_count must be an int >= 0, got {self.excluded_count!r}")
        if self.brier_score is not None and (not isinstance(self.brier_score, (int, float)) or isinstance(self.brier_score, bool) or not math.isfinite(float(self.brier_score)) or not 0.0 <= self.brier_score <= 1.0):
            raise ValueError(f"brier_score must be a finite number in [0, 1] or None (None when nothing was comparable), got {self.brier_score!r}")
        if self.well_calibrated is not None and not isinstance(self.well_calibrated, bool):
            raise ValueError(f"well_calibrated must be a bool or None (None = unresolved), got {self.well_calibrated!r}")

    def to_dict(self) -> dict[str, object]:
        return {
            "buckets": [bucket.to_dict() for bucket in self.buckets],
            "brier_score": self.brier_score,
            "sample_count": self.sample_count,
            "well_calibrated": self.well_calibrated,
            "profile_version": self.profile_version,
            "excluded_count": self.excluded_count,
        }


def _require_text(value: str, field: str) -> str:
    if not value:
        raise ValueError(f"{field} must be non-empty")
    return value


def _bucket_index(confidence: float, bucket_width: float, bucket_count: int) -> int:
    index = int(math.floor(confidence / bucket_width + _BUCKET_EPSILON))
    return min(max(index, 0), bucket_count - 1)


def calibration(
    records: Iterable[CapabilityRecord],
    *,
    profile_version: str,
    bucket_width: float = DEFAULT_BUCKET_WIDTH,
    min_samples_for_verdict: int = DEFAULT_MIN_SAMPLES_FOR_VERDICT,
) -> CalibrationReport:
    """Calibrate stated confidence against measured quality for one profile.

    Pairs every record that carries BOTH a ``confidence`` (the agent's own
    stated confidence) and a ``quality`` (the measured outcome), scoped to
    ``profile_version``; records lacking either are excluded and counted,
    records from another profile version are out of scope entirely. The Brier
    score is ``mean((confidence - quality)^2)`` over the comparable pairs, the
    buckets group them by stated confidence, and the verdict is ``True`` only
    if the panel has at least ``min_samples_for_verdict`` comparable pairs AND
    every bucket carrying at least ``CALIBRATION_SAMPLE_FLOOR`` samples is
    within ``CALIBRATION_TOLERANCE`` of its observed mean. Below the sample
    floor the verdict is ``None`` — unresolved is the only honest answer an
    under-sampled panel has.
    """
    _require_text(profile_version, "profile_version")
    if isinstance(bucket_width, bool) or not isinstance(bucket_width, (int, float)) or not 0.0 < float(bucket_width) <= 1.0 or not math.isfinite(float(bucket_width)):
        raise ValueError(f"bucket_width must be a finite number in (0, 1], got {bucket_width!r}")
    if isinstance(min_samples_for_verdict, bool) or not isinstance(min_samples_for_verdict, int) or min_samples_for_verdict < 1:
        raise ValueError(f"min_samples_for_verdict must be an int >= 1, got {min_samples_for_verdict!r}")
    width = float(bucket_width)

    in_scope = [record for record in records if record.profile_version == profile_version]
    comparable = [(float(record.confidence), float(record.quality)) for record in in_scope if record.confidence is not None and record.quality is not None]
    excluded_count = len(in_scope) - len(comparable)
    sample_count = len(comparable)

    brier_score: float | None = None
    if sample_count > 0:
        brier_score = sum((confidence - quality) ** 2 for confidence, quality in comparable) / sample_count

    bucket_count = max(1, math.ceil(1.0 / width - _BUCKET_EPSILON))
    grouped: dict[int, list[tuple[float, float]]] = {}
    for pair in comparable:
        grouped.setdefault(_bucket_index(pair[0], width, bucket_count), []).append(pair)

    buckets: list[CalibrationBucket] = []
    well_calibrated: bool | None = None
    for index in sorted(grouped):
        pairs = grouped[index]
        lower = round(index * width, 10)
        upper = round((index + 1) * width, 10)
        label = f"{_format_bound(lower)}-{_format_bound(upper)}"
        predicted = sum(confidence for confidence, _ in pairs) / len(pairs)
        observed = sum(quality for _, quality in pairs) / len(pairs)
        buckets.append(CalibrationBucket(confidence_bucket=label, predicted=predicted, observed=observed, count=len(pairs)))

    if sample_count >= min_samples_for_verdict:
        speaking = [bucket for bucket in buckets if bucket.count >= CALIBRATION_SAMPLE_FLOOR]
        well_calibrated = all(abs(bucket.predicted - bucket.observed) <= CALIBRATION_TOLERANCE for bucket in speaking)

    return CalibrationReport(
        buckets=tuple(buckets),
        brier_score=brier_score,
        sample_count=sample_count,
        well_calibrated=well_calibrated,
        profile_version=profile_version,
        excluded_count=excluded_count,
    )


__all__ = [
    "CALIBRATION_SAMPLE_FLOOR",
    "CALIBRATION_TOLERANCE",
    "DEFAULT_BUCKET_WIDTH",
    "DEFAULT_MIN_SAMPLES_FOR_VERDICT",
    "CalibrationBucket",
    "CalibrationReport",
    "calibration",
]
