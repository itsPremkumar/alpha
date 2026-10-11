"""Tests for capability calibration (plan §8): does stated confidence mean anything?

Every input is a :class:`alpha.capability_mesh.memory.CapabilityRecord` with both
a stated ``confidence`` and a measured ``quality``; records lacking either are
excluded and *counted*, never silently folded into a reassuring average. An
under-sampled panel answers "unresolved" (``well_calibrated is None``), never
"well calibrated" — the same house rule as swarm deliberation.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from alpha.capability_mesh.calibration import (
    CALIBRATION_SAMPLE_FLOOR,
    CALIBRATION_TOLERANCE,
    CalibrationBucket,
    CalibrationReport,
    calibration,
)
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind

_ids = itertools.count(1)


def _record(*, confidence: float | None, quality: float | None, profile_version: str = "p1") -> CapabilityRecord:
    return CapabilityRecord(
        record_id=f"r{next(_ids)}",
        task_fingerprint="fp_0011223344556677",
        task_category="coding",
        entity_id="agent:a",
        capability="implement",
        outcome=OutcomeKind.SELECTED,
        recorded_at="2025-06-01T00:00:00+00:00",
        quality=quality,
        confidence=confidence,
        profile_version=profile_version,
    )


class TestReportShape:
    def test_zero_comparable_records_is_an_unresolved_panel(self) -> None:
        report = calibration([], profile_version="p1")
        assert report.brier_score is None  # NEVER a reassuring 0.0
        assert report.sample_count == 0
        assert report.buckets == ()
        assert report.well_calibrated is None

    def test_report_is_frozen(self) -> None:
        report = calibration([], profile_version="p1")
        with pytest.raises(Exception):
            report.sample_count = 5  # type: ignore[misc]

    def test_empty_profile_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            calibration([], profile_version="")

    def test_profile_version_is_stamped_on_the_report(self) -> None:
        report = calibration([_record(confidence=0.9, quality=0.9)], profile_version="p1")
        assert report.profile_version == "p1"

    def test_bucket_width_is_validated(self) -> None:
        with pytest.raises(ValueError, match="bucket_width"):
            calibration([], profile_version="p1", bucket_width=0)
        with pytest.raises(ValueError, match="bucket_width"):
            calibration([], profile_version="p1", bucket_width=1.5)

    def test_min_samples_is_validated(self) -> None:
        with pytest.raises(ValueError, match="min_samples"):
            calibration([], profile_version="p1", min_samples_for_verdict=0)


class TestBucketing:
    def test_records_land_in_confidence_buckets(self) -> None:
        records = [_record(confidence=0.85, quality=0.8) for _ in range(3)]
        records += [_record(confidence=0.25, quality=0.3) for _ in range(2)]
        report = calibration(records, profile_version="p1")
        assert [bucket.confidence_bucket for bucket in report.buckets] == ["0.2-0.3", "0.8-0.9"]
        low, high = report.buckets
        assert (low.count, low.predicted, low.observed) == (2, pytest.approx(0.25), pytest.approx(0.3))
        assert (high.count, high.predicted, high.observed) == (3, pytest.approx(0.85), pytest.approx(0.8))

    def test_confidence_one_belongs_to_the_top_bucket(self) -> None:
        report = calibration([_record(confidence=1.0, quality=1.0)], profile_version="p1")
        assert [bucket.confidence_bucket for bucket in report.buckets] == ["0.9-1.0"]

    def test_mid_bucket_labels_survive_float_arithmetic(self) -> None:
        """0.3/0.1 is 2.9999... in binary: naive int() would file a 0.3
        confidence under the 0.2-0.3 bucket. The index must be epsilon-floored,
        and 0.35 must land in the same 0.3-0.4 bucket as 0.3."""
        report = calibration([_record(confidence=0.35, quality=0.0), _record(confidence=0.3, quality=0.0)], profile_version="p1")
        assert [bucket.confidence_bucket for bucket in report.buckets] == ["0.3-0.4"]
        assert report.buckets[0].count == 2


class TestBrierScore:
    def test_known_tiny_input(self) -> None:
        records = [_record(confidence=0.9, quality=0.8), _record(confidence=0.4, quality=0.6)]
        report = calibration(records, profile_version="p1")
        # ((0.9-0.8)^2 + (0.4-0.6)^2) / 2 = (0.01 + 0.04) / 2
        assert report.brier_score == pytest.approx(0.025)
        assert report.sample_count == 2

    def test_perfect_calibration_scores_zero_and_that_is_a_measurement(self) -> None:
        report = calibration([_record(confidence=0.5, quality=0.5)], profile_version="p1")
        assert report.brier_score == 0.0
        # ... but a single comparable pair still cannot answer the verdict.
        assert report.well_calibrated is None


class TestExclusion:
    def test_missing_confidence_or_quality_is_excluded_and_counted(self) -> None:
        records = [
            _record(confidence=0.9, quality=0.9),
            _record(confidence=0.8, quality=0.8),
            _record(confidence=0.7, quality=0.7),
            _record(confidence=None, quality=0.9),  # no stated confidence
            _record(confidence=0.9, quality=None),  # no measured outcome
            _record(confidence=None, quality=None),
        ]
        report = calibration(records, profile_version="p1")
        assert report.sample_count == 3
        assert report.excluded_count == 3

    def test_records_from_another_profile_are_out_of_scope_not_excluded(self) -> None:
        """Comparability is scoped BEFORE exclusion is counted."""
        records = [_record(confidence=0.9, quality=0.9) for _ in range(3)]
        records += [_record(confidence=0.1, quality=1.0, profile_version="p0") for _ in range(50)]
        report = calibration(records, profile_version="p1")
        assert report.sample_count == 3
        assert report.excluded_count == 0
        # The v0 bomb would have wrecked the verdict had it been pooled in.
        assert report.well_calibrated is None  # 3 < 20 samples for p1


class TestVerdict:
    def test_under_sampled_panel_says_unresolved(self) -> None:
        records = [_record(confidence=0.9, quality=0.9) for _ in range(19)]
        report = calibration(records, profile_version="p1")
        assert report.sample_count == 19
        assert report.well_calibrated is None

    def test_min_samples_boundary_answers(self) -> None:
        records = [_record(confidence=0.9, quality=0.88) for _ in range(20)]
        report = calibration(records, profile_version="p1")
        assert report.sample_count == 20
        assert report.well_calibrated is True

    def test_overconfident_bucket_is_not_well_calibrated(self) -> None:
        records = [_record(confidence=0.9, quality=0.6) for _ in range(20)]
        report = calibration(records, profile_version="p1")
        assert report.well_calibrated is False

    def test_thin_bucket_cannot_flip_the_verdict(self) -> None:
        """A bucket needs CALIBRATION_SAMPLE_FLOOR samples to speak."""
        records = [_record(confidence=0.1, quality=0.9) for _ in range(CALIBRATION_SAMPLE_FLOOR - 1)]
        records += [_record(confidence=0.5, quality=0.5) for _ in range(5)]
        report = calibration(records, profile_version="p1", min_samples_for_verdict=9)
        assert report.sample_count == 9
        assert report.well_calibrated is True  # the badly-calibrated thin bucket is evidence, not a verdict

    def test_bucket_at_the_floor_flips_the_verdict(self) -> None:
        records = [_record(confidence=0.1, quality=0.9) for _ in range(CALIBRATION_SAMPLE_FLOOR)]
        records += [_record(confidence=0.5, quality=0.5) for _ in range(5)]
        report = calibration(records, profile_version="p1", min_samples_for_verdict=CALIBRATION_SAMPLE_FLOOR + 5)
        assert report.well_calibrated is False

    def test_tolerance_neighbourhood_decides_the_verdict(self) -> None:
        inside = round(0.9 - (CALIBRATION_TOLERANCE - 0.01), 10)  # ~0.76: |0.9-0.76| ~ 0.14
        outside = round(0.9 - (CALIBRATION_TOLERANCE + 0.01), 10)  # ~0.74: |0.9-0.74| ~ 0.16
        records = [_record(confidence=0.9, quality=inside) for _ in range(20)]
        assert calibration(records, profile_version="p1").well_calibrated is True
        records = [_record(confidence=0.9, quality=outside) for _ in range(20)]
        assert calibration(records, profile_version="p1").well_calibrated is False

    def test_spread_but_under_sampled_stays_unresolved(self) -> None:
        """Many buckets with few samples each cannot answer, even pooled."""
        records = [_record(confidence=0.05 + 0.1 * index, quality=0.05 + 0.1 * index) for index in range(10)]
        report = calibration(records, profile_version="p1")
        assert report.sample_count == 10
        assert report.well_calibrated is None

    def test_bucket_rows_are_constructed_honestly(self) -> None:
        bucket = CalibrationBucket(confidence_bucket="0.8-0.9", predicted=0.85, observed=0.8, count=3)
        assert bucket.count == 3
        with pytest.raises(ValueError):
            CalibrationBucket(confidence_bucket="0.8-0.9", predicted=float("nan"), observed=0.8, count=3)
        with pytest.raises(ValueError):
            CalibrationBucket(confidence_bucket="0.8-0.9", predicted=0.85, observed=0.8, count=-1)

    def test_report_rejects_a_fabricated_brier(self) -> None:
        with pytest.raises(ValueError, match="brier_score"):
            CalibrationReport(buckets=(), brier_score=1.5, sample_count=1, well_calibrated=None, profile_version="p1")


def test_calibration_reads_from_a_durable_memory(tmp_path: Path) -> None:
    """The function takes any record iterable — a durable memory feeds it."""
    memory = CapabilityMemory(tmp_path)
    for index in range(20):
        memory.record(_record(confidence=0.9, quality=0.9))
    report = calibration(memory.records(), profile_version="p1")
    assert report.sample_count == 20
    assert report.well_calibrated is True
