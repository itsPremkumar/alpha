"""Tests for capability drift detection (plan §23): a detector that reports, never acts.

Each (entity, capability) stream is split into a baseline window (oldest N) and
a recent window (newest M). A window short of its required samples yields an
UNKNOWN signal naming the shortfall — never a fabricated direction — and the
report marks degraded/stale entities while mutating nothing.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpha.capability_mesh.drift import DriftDirection, DriftReport, DriftSignal, detect_drift
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

_ids = itertools.count(1)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _record(
    *,
    quality: float | None = 0.9,
    recorded_at: str = "2025-06-01T00:00:00+00:00",
    entity_id: str = "agent:a",
    capability: str = "implement",
    outcome: OutcomeKind = OutcomeKind.UNSELECTED,
    profile_version: str = "p1",
) -> CapabilityRecord:
    return CapabilityRecord(
        record_id=f"r{next(_ids)}",
        task_fingerprint="fp_0011223344556677",
        task_category="coding",
        entity_id=entity_id,
        capability=capability,
        outcome=outcome,
        quality=quality,
        profile_version=profile_version,
        recorded_at=recorded_at,
    )


def _stream(tmp_path: Path, *, baseline_quality: float, recent_quality: float, baseline_samples: int = 20, recent_samples: int = 5, **kwargs: object) -> CapabilityMemory:
    """A deterministic stream: ``baseline_samples`` old records then ``recent_samples`` new ones."""
    memory = CapabilityMemory(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for index in range(baseline_samples):
        memory.record(_record(quality=baseline_quality, recorded_at=_iso(start + timedelta(days=index)), **kwargs))  # type: ignore[arg-type]
    recent_start = datetime(2025, 6, 1, tzinfo=UTC)
    for index in range(recent_samples):
        memory.record(_record(quality=recent_quality, recorded_at=_iso(recent_start + timedelta(days=index)), **kwargs))  # type: ignore[arg-type]
    return memory


class TestSignalShape:
    def test_thin_stream_is_unknown_and_names_the_shortfall(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        for index in range(3):
            memory.record(_record(recorded_at=f"2025-01-0{index + 1}T00:00:00+00:00"))
        report = detect_drift(memory.records(), profile_version="p1", now=NOW)
        assert len(report.signals) == 1
        signal = report.signals[0]
        assert signal.direction is DriftDirection.UNKNOWN
        assert signal.shift is None
        assert signal.material is False
        assert signal.baseline_samples == 3
        assert signal.recent_samples == 3
        # The evidence names the shortfall rather than guessing a direction.
        assert "3" in signal.evidence
        assert "20" in signal.evidence
        assert report.degraded_entities == ()

    def test_quality_less_records_do_not_create_measurable_samples(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        for index in range(25):
            memory.record(_record(quality=None, recorded_at=f"2025-01-{index + 1:02d}T00:00:00+00:00"))
        signal = detect_drift(memory.records(), profile_version="p1", now=NOW).signals[0]
        assert signal.direction is DriftDirection.UNKNOWN
        assert signal.baseline_samples == 0

    def test_degraded_detection(self, tmp_path: Path) -> None:
        report = detect_drift(_stream(tmp_path, baseline_quality=0.9, recent_quality=0.5).records(), profile_version="p1", now=NOW)
        (signal,) = report.signals
        assert signal.direction is DriftDirection.DEGRADED
        assert signal.material is True
        assert signal.baseline_mean == pytest.approx(0.9)
        assert signal.recent_mean == pytest.approx(0.5)
        assert signal.shift == pytest.approx(-0.4)
        assert (signal.baseline_samples, signal.recent_samples) == (20, 5)
        assert report.degraded_entities == ("agent:a",)
        assert "baseline" in signal.evidence and "recent" in signal.evidence

    def test_improved_detection(self, tmp_path: Path) -> None:
        report = detect_drift(_stream(tmp_path, baseline_quality=0.5, recent_quality=0.9).records(), profile_version="p1", now=NOW)
        (signal,) = report.signals
        assert signal.direction is DriftDirection.IMPROVED
        assert signal.material is True
        assert signal.shift == pytest.approx(0.4)
        assert report.degraded_entities == ()

    def test_small_shift_is_unchanged(self, tmp_path: Path) -> None:
        report = detect_drift(_stream(tmp_path, baseline_quality=0.9, recent_quality=0.88).records(), profile_version="p1", now=NOW)
        (signal,) = report.signals
        assert signal.direction is DriftDirection.UNCHANGED
        assert signal.material is False
        assert signal.shift == pytest.approx(-0.02)
        assert report.degraded_entities == ()

    def test_materiality_threshold_boundary(self, tmp_path: Path) -> None:
        """|shift| >= threshold is material; equal to the threshold counts."""
        records = _stream(tmp_path, baseline_quality=0.9, recent_quality=0.5).records()
        at_boundary = detect_drift(records, profile_version="p1", now=NOW, threshold=0.4)
        assert at_boundary.signals[0].direction is DriftDirection.DEGRADED
        above = detect_drift(records, profile_version="p1", now=NOW, threshold=0.41)
        assert above.signals[0].direction is DriftDirection.UNCHANGED
        assert above.signals[0].material is False

    def test_signals_are_scoped_by_profile_version(self, tmp_path: Path) -> None:
        """A rubric change invalidates comparability — v1 drift is not v2 drift."""
        memory = _stream(tmp_path, baseline_quality=0.9, recent_quality=0.1)
        report = detect_drift(memory.records(), profile_version="p2", now=NOW)
        assert len(report.signals) == 1
        assert report.signals[0].direction is DriftDirection.UNKNOWN  # zero comparable samples under p2
        assert report.degraded_entities == ()

    def test_one_signal_per_entity_and_capability_in_deterministic_order(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        start = datetime(2024, 1, 1, tzinfo=UTC)
        for index in range(20):
            memory.record(_record(capability="review", quality=0.9, recorded_at=_iso(start + timedelta(days=index))))
            memory.record(_record(capability="implement", quality=0.5, recorded_at=_iso(start + timedelta(days=index))))
        for index in range(5):
            when = _iso(datetime(2025, 6, 1, tzinfo=UTC) + timedelta(days=index))
            memory.record(_record(capability="review", quality=0.5, recorded_at=when))
            memory.record(_record(capability="implement", quality=0.9, recorded_at=when))
        report = detect_drift(memory.records(), profile_version="p1", now=NOW)
        assert [(s.entity_id, s.capability) for s in report.signals] == [("agent:a", "implement"), ("agent:a", "review")]
        assert report.degraded_entities == ("agent:a",)  # 'review' degraded; 'implement' improved

    def test_report_metadata_is_stamped(self, tmp_path: Path) -> None:
        report = detect_drift(_stream(tmp_path, baseline_quality=0.9, recent_quality=0.9).records(), profile_version="p1", now=NOW)
        assert report.checked_at == _iso(NOW)
        assert report.profile_version == "p1"

    def test_report_serialises_for_audit(self, tmp_path: Path) -> None:
        report = detect_drift(_stream(tmp_path, baseline_quality=0.9, recent_quality=0.5).records(), profile_version="p1", now=NOW)
        payload = report.to_dict()
        assert payload["profile_version"] == "p1"
        assert payload["degraded_entities"] == ["agent:a"]
        assert payload["signals"][0]["direction"] == "degraded"
        assert payload["signals"][0]["evidence"]  # the justification survives serialization

    def test_detection_mutates_nothing(self, tmp_path: Path) -> None:
        """The report-don't-act boundary: detect_drift is a pure reader."""
        memory = _stream(tmp_path, baseline_quality=0.9, recent_quality=0.5)
        before = memory.records()
        report = detect_drift(before, profile_version="p1", now=NOW)
        assert memory.records() == before
        with pytest.raises(Exception):
            report.signals = ()  # type: ignore[misc]


class TestStaleness:
    def test_old_outcome_evidence_is_stale(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        when = _iso(NOW - timedelta(days=60))
        for _ in range(3):
            memory.record(_record(outcome=OutcomeKind.SELECTED, recorded_at=when))
        report = detect_drift(memory.records(), profile_version="p1", now=NOW)
        assert report.stale_entities == ("agent:a",)

    def test_recent_outcome_evidence_is_not_stale(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        when = _iso(NOW - timedelta(days=1))
        for _ in range(3):
            memory.record(_record(outcome=OutcomeKind.SELECTED, recorded_at=when))
        report = detect_drift(memory.records(), profile_version="p1", now=NOW)
        assert report.stale_entities == ()

    def test_entities_without_outcome_evidence_are_unverified_not_stale(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        for _ in range(3):
            memory.record(_record(outcome=OutcomeKind.UNSELECTED, recorded_at="2020-01-01T00:00:00+00:00"))
        report = detect_drift(memory.records(), profile_version="p1", now=NOW)
        assert report.stale_entities == ()

    def test_a_fresh_unselected_record_does_not_revalidate_stale_outcome_evidence(self, tmp_path: Path) -> None:
        """Only outcome evidence revalidates outcome evidence."""
        memory = CapabilityMemory(tmp_path)
        memory.record(_record(outcome=OutcomeKind.SELECTED, recorded_at=_iso(NOW - timedelta(days=90))))
        memory.record(_record(outcome=OutcomeKind.UNSELECTED, recorded_at=_iso(NOW - timedelta(hours=1))))
        report = detect_drift(memory.records(), profile_version="p1", now=NOW)
        assert report.stale_entities == ("agent:a",)

    def test_staleness_boundary_is_the_revalidation_window(self, tmp_path: Path) -> None:
        """The default window is derive_evidence_level's 30 days."""
        just_inside = CapabilityMemory(tmp_path / "in")
        for _ in range(2):
            just_inside.record(_record(outcome=OutcomeKind.SELECTED, recorded_at=_iso(NOW - timedelta(days=29))))
        assert detect_drift(just_inside.records(), profile_version="p1", now=NOW).stale_entities == ()
        just_outside = CapabilityMemory(tmp_path / "out")
        for _ in range(2):
            just_outside.record(_record(outcome=OutcomeKind.SELECTED, recorded_at=_iso(NOW - timedelta(days=31))))
        assert detect_drift(just_outside.records(), profile_version="p1", now=NOW).stale_entities == ("agent:a",)


class TestValidation:
    def test_direction_and_shift_must_agree(self) -> None:
        with pytest.raises(ValueError, match="shift"):
            DriftSignal(capability="x", entity_id="agent:a", baseline_mean=0.9, recent_mean=0.5, baseline_samples=20, recent_samples=5, shift=None, direction=DriftDirection.DEGRADED, material=True, evidence="e")
        with pytest.raises(ValueError, match="shift"):
            DriftSignal(capability="x", entity_id="agent:a", baseline_mean=0.9, recent_mean=0.5, baseline_samples=20, recent_samples=5, shift=-0.4, direction=DriftDirection.UNKNOWN, material=False, evidence="e")

    def test_improved_requires_a_positive_shift(self) -> None:
        with pytest.raises(ValueError, match="improved"):
            DriftSignal(capability="x", entity_id="agent:a", baseline_mean=0.9, recent_mean=0.5, baseline_samples=20, recent_samples=5, shift=-0.4, direction=DriftDirection.IMPROVED, material=True, evidence="e")

    def test_material_degraded_is_the_only_degraded(self) -> None:
        with pytest.raises(ValueError, match="material"):
            DriftSignal(capability="x", entity_id="agent:a", baseline_mean=0.9, recent_mean=0.5, baseline_samples=20, recent_samples=5, shift=-0.4, direction=DriftDirection.DEGRADED, material=False, evidence="e")

    def test_unknown_never_carries_a_material_flag(self) -> None:
        with pytest.raises(ValueError, match="material"):
            DriftSignal(capability="x", entity_id="agent:a", baseline_mean=None, recent_mean=None, baseline_samples=1, recent_samples=0, shift=None, direction=DriftDirection.UNKNOWN, material=True, evidence="e")

    def test_evidence_is_mandatory(self) -> None:
        with pytest.raises(ValueError, match="evidence"):
            DriftSignal(capability="x", entity_id="agent:a", baseline_mean=None, recent_mean=None, baseline_samples=0, recent_samples=0, shift=None, direction=DriftDirection.UNKNOWN, material=False, evidence="")

    def test_window_parameters_are_validated(self) -> None:
        with pytest.raises(ValueError, match="baseline_samples"):
            detect_drift([], profile_version="p1", baseline_samples=0)
        with pytest.raises(ValueError, match="recent_samples"):
            detect_drift([], profile_version="p1", recent_samples=0)
        with pytest.raises(ValueError, match="threshold"):
            detect_drift([], profile_version="p1", threshold=0)

    def test_empty_profile_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            detect_drift([], profile_version="")

    def test_report_normalises_entity_lists(self) -> None:
        report = DriftReport(signals=(), degraded_entities=("b", "a", "b"), stale_entities=(), checked_at="2025-06-15T12:00:00+00:00", profile_version="p1")
        assert report.degraded_entities == ("a", "b")


def test_drift_reads_from_a_durable_memory(tmp_path: Path) -> None:
    """The detector consumes any record iterable — a durable memory feeds it."""
    memory = _stream(tmp_path, baseline_quality=0.9, recent_quality=0.4)
    report = detect_drift(memory.records(), profile_version="p1", now=NOW)
    assert report.degraded_entities == ("agent:a",)
    assert CapabilityMemory(tmp_path).records() == memory.records()
