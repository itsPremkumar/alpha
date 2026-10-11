"""Tests for the cross-run capability memory (plan §12): records, estimates, credit, retention.

Everything here is offline and fast: no model calls, no network, files only under
``tmp_path``. The honesty rules mirror ``models.py`` — a record that cannot serve
as evidence is refused at construction (and again on the way back off disk), and
an estimate never guesses what it did not measure.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpha.capability_mesh.memory import (
    MEMORY_FILE_NAME,
    CapabilityMemory,
    CapabilityRecord,
    CreditReport,
    CreditRow,
    MemoryStoreError,
    OutcomeKind,
    fingerprint,
)
from alpha.capability_mesh.models import EvidenceLevel

#: A fixed clock for every "now"-relative assertion. Mid-2025 keeps every
#: happy-path ``recorded_at`` in the past for any plausible wall clock, while
#: still allowing a "recently revalidated" record dated a day earlier.
NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

_ids = itertools.count(1)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _record(**overrides: object) -> CapabilityRecord:
    # The default outcome is UNSELECTED so a bare stream is a plain attempt
    # history: tests that need delivered outcomes must ask for them by name.
    fields: dict[str, object] = {
        "record_id": f"r{next(_ids)}",
        "task_fingerprint": "fp_0011223344556677",
        "task_category": "coding",
        "entity_id": "agent:a",
        "capability": "implement",
        "outcome": OutcomeKind.UNSELECTED,
        "quality": 0.9,
        "profile_version": "p1",
        "recorded_at": "2025-06-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return CapabilityRecord(**fields)  # type: ignore[arg-type]


def _memory(tmp_path: Path, records: Iterable[CapabilityRecord] = ()) -> CapabilityMemory:
    """A durable memory holding ``records`` through the real public path."""
    memory = CapabilityMemory(tmp_path)
    for record in records:
        memory.record(record)
    return memory


class TestRecordHonesty:
    """Every ``__post_init__`` refusal, one rule per test."""

    def test_empty_record_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="record_id"):
            _record(record_id="")

    def test_identity_fields_must_be_non_empty(self) -> None:
        for field in ("task_fingerprint", "task_category", "entity_id", "capability"):
            with pytest.raises(ValueError):
                _record(**{field: ""})

    def test_quality_out_of_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="quality"):
            _record(quality=1.5)
        with pytest.raises(ValueError, match="quality"):
            _record(quality=-0.01)

    def test_non_finite_quality_is_refused(self) -> None:
        with pytest.raises(ValueError, match="quality"):
            _record(quality=math.nan)
        with pytest.raises(ValueError, match="quality"):
            _record(quality=math.inf)

    def test_quality_without_its_profile_version_is_refused(self) -> None:
        """The Phase A 'no score without its profile version' rule, inherited."""
        with pytest.raises(ValueError, match="profile_version"):
            _record(quality=0.5, profile_version="")

    def test_confidence_is_validated_like_quality(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _record(confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            _record(confidence=math.nan)
        # Stated confidence alone does not require a profile version: it is a
        # claim by the agent, not a score produced by a rubric.
        assert _record(quality=None, confidence=0.8, profile_version="").confidence == 0.8

    def test_negative_latency_and_cost_are_refused(self) -> None:
        with pytest.raises(ValueError, match="latency_ms"):
            _record(latency_ms=-1)
        with pytest.raises(ValueError, match="cost_usd"):
            _record(cost_usd=-0.5)

    def test_future_dated_record_is_refused(self) -> None:
        """A record dated ahead of the clock cannot be evidence of anything."""
        future = datetime.now(UTC) + timedelta(hours=1)
        with pytest.raises(ValueError, match="future"):
            _record(recorded_at=_iso(future))

    def test_unparseable_timestamp_is_refused(self) -> None:
        with pytest.raises(ValueError, match="recorded_at"):
            _record(recorded_at="yesterday-ish")

    def test_past_and_present_timestamps_are_accepted(self) -> None:
        assert _record(recorded_at="2000-01-01T00:00:00+00:00").recorded_at == "2000-01-01T00:00:00+00:00"
        now_string = _iso(datetime.now(UTC))
        # 'now' is never 'future': validation runs after the caller stamped it.
        assert _record(recorded_at=now_string).recorded_at == now_string

    def test_unknown_outcome_is_refused_and_string_form_is_coerced(self) -> None:
        with pytest.raises(ValueError, match="outcome"):
            _record(outcome="teleported")
        assert _record(outcome="rejected").outcome is OutcomeKind.REJECTED

    def test_passed_hard_gate_must_be_bool_or_none(self) -> None:
        """None means 'not checked' — a truthy string must never become a pass."""
        with pytest.raises(ValueError, match="passed_hard_gate"):
            _record(passed_hard_gate="yes")
        assert _record(passed_hard_gate=None).passed_hard_gate is None
        assert _record(passed_hard_gate=False).passed_hard_gate is False

    def test_optional_measurements_default_to_honest_none(self) -> None:
        record = _record(quality=None)
        assert record.quality is None
        assert record.confidence is None
        assert record.latency_ms is None
        assert record.cost_usd is None
        assert record.failure_mode is None
        assert record.passed_hard_gate is None

    def test_to_dict_from_dict_roundtrip(self) -> None:
        original = _record(slot="plan", quality=0.42, confidence=0.7, latency_ms=12, cost_usd=0.01, failure_mode="timeout", passed_hard_gate=True)
        restored = CapabilityRecord.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored == original

    def test_from_dict_revalidates_a_hand_edited_future_date(self) -> None:
        payload = _record().to_dict()
        payload["recorded_at"] = _iso(datetime.now(UTC) + timedelta(days=2))
        with pytest.raises(ValueError, match="future"):
            CapabilityRecord.from_dict(payload)

    def test_outcome_values_match_the_plan_vocabulary(self) -> None:
        assert [kind.value for kind in OutcomeKind] == ["selected", "repaired", "rejected", "materially_edited", "unselected"]


class TestFingerprint:
    def test_shape(self) -> None:
        value = fingerprint("fix the failing test in auth module")
        assert value.startswith("fp_")
        body = value.removeprefix("fp_")
        assert len(body) == 16
        assert all(char in "0123456789abcdef" for char in body)

    def test_deterministic(self) -> None:
        assert fingerprint("same input") == fingerprint("same input")

    def test_different_inputs_differ(self) -> None:
        assert fingerprint("task one") != fingerprint("task two")

    def test_non_reversible(self) -> None:
        """The helper exists so the raw text never has to be stored."""
        secret = "the password is hunter2 and the customer is Acme"
        value = fingerprint(secret)
        assert secret not in value
        assert "hunter2" not in value
        assert "Acme" not in value

    def test_empty_input_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fingerprint"):
            fingerprint("")


class TestRecordsFiltering:
    def test_filters_by_entity_capability_and_category(self, tmp_path: Path) -> None:
        memory = _memory(
            tmp_path,
            [
                _record(entity_id="agent:a", capability="implement"),
                _record(entity_id="agent:b", capability="implement"),
                _record(entity_id="agent:a", capability="review"),
                _record(entity_id="agent:a", capability="implement", task_category="writing"),
            ],
        )
        assert len(memory.records(entity_id="agent:a")) == 3
        assert len(memory.records(entity_id="agent:a", capability="implement")) == 2
        assert len(memory.records(entity_id="agent:a", capability="implement", task_category="coding")) == 1

    def test_since_is_inclusive(self, tmp_path: Path) -> None:
        early = _record(recorded_at="2025-01-01T00:00:00+00:00")
        late = _record(recorded_at="2025-06-01T00:00:00+00:00")
        memory = _memory(tmp_path, [early, late])
        assert [r.record_id for r in memory.records(since="2025-01-01T00:00:00+00:00")] == [early.record_id, late.record_id]
        assert [r.record_id for r in memory.records(since="2025-03-01T00:00:00+00:00")] == [late.record_id]

    def test_since_accepts_a_datetime_and_refuses_garbage(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, [_record(recorded_at="2025-06-01T00:00:00+00:00")])
        assert len(memory.records(since=datetime(2025, 1, 1, tzinfo=UTC))) == 1
        with pytest.raises(ValueError, match="since"):
            memory.records(since="not-a-time")

    def test_order_is_deterministic_not_insertion_order(self, tmp_path: Path) -> None:
        second = _record(recorded_at="2025-06-02T00:00:00+00:00")
        first = _record(recorded_at="2025-06-01T00:00:00+00:00")
        memory = _memory(tmp_path, [second, first])
        assert [r.record_id for r in memory.records()] == [first.record_id, second.record_id]


class TestEstimateFor:
    def test_zero_records_is_an_honest_unmeasured_estimate(self, tmp_path: Path) -> None:
        estimate = _memory(tmp_path).estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.UNMEASURED
        assert estimate.quality_mean is None
        assert estimate.uncertainty is None
        assert estimate.sample_count == 0
        assert estimate.last_evaluated_at is None

    @pytest.mark.parametrize("n", [1, 3, 9])
    def test_observed_below_the_benchmark_floor(self, tmp_path: Path, n: int) -> None:
        memory = _memory(tmp_path, (_record(quality=0.5) for _ in range(n)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.OBSERVED
        assert estimate.sample_count == n
        assert estimate.quality_mean == 0.5

    def test_ten_samples_is_benchmarked(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(quality=0.5) for _ in range(10)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.BENCHMARKED

    def test_selected_outcomes_are_outcome_verified(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(outcome=OutcomeKind.SELECTED, recorded_at="2000-01-01T00:00:00+00:00") for _ in range(3)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.OUTCOME_VERIFIED

    def test_materially_edited_also_counts_as_an_outcome(self, tmp_path: Path) -> None:
        """The contribution made it into the deliverable — that is the outcome."""
        memory = _memory(tmp_path, [_record(outcome=OutcomeKind.MATERIALLY_EDITED, recorded_at="2000-01-01T00:00:00+00:00")])
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.OUTCOME_VERIFIED

    def test_repaired_alone_is_not_an_outcome(self, tmp_path: Path) -> None:
        """Repair credit is real, but a repaired fragment alone is not delivered work."""
        memory = _memory(tmp_path, (_record(outcome=OutcomeKind.REPAIRED) for _ in range(3)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.OBSERVED

    def test_recent_outcomes_are_recently_revalidated(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(outcome=OutcomeKind.SELECTED, recorded_at=_iso(NOW - timedelta(days=1))) for _ in range(3)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.evidence_level is EvidenceLevel.RECENTLY_REVALIDATED

    def test_records_from_another_profile_version_do_not_count(self, tmp_path: Path) -> None:
        """A rubric change invalidates comparability: v1 samples earn a v2 nothing."""
        memory = _memory(tmp_path, (_record(profile_version="p1", quality=0.9) for _ in range(12)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p2", now=NOW)
        assert estimate.sample_count == 0
        assert estimate.evidence_level is EvidenceLevel.UNMEASURED
        assert estimate.quality_mean is None
        # ... while the same records still answer for their own version.
        own = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert own.sample_count == 12
        assert own.evidence_level is EvidenceLevel.BENCHMARKED

    def test_categories_are_never_pooled_into_the_wildcard(self, tmp_path: Path) -> None:
        """plan §8.2: do not pool narrow task groups blindly."""
        memory = _memory(tmp_path, (_record(task_category="coding") for _ in range(5)))
        wildcard = memory.estimate_for("agent:a", "implement", "*", profile_version="p1", now=NOW)
        assert wildcard.sample_count == 0
        assert wildcard.evidence_level is EvidenceLevel.UNMEASURED
        assert memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW).sample_count == 5

    def test_quality_mean_and_uncertainty_over_measured_records(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, [_record(quality=0.5), _record(quality=1.0)])
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.quality_mean == pytest.approx(0.75)
        # Sample standard error of {0.5, 1.0}: sqrt(0.125)/sqrt(2) = 0.25.
        assert estimate.uncertainty == pytest.approx(0.25)

    def test_single_measured_sample_has_zero_spread_but_that_is_not_precision(self, tmp_path: Path) -> None:
        """n==1 reports uncertainty 0.0 because there is no spread to estimate.

        It is the *absence* of a spread estimate, NOT a claim of exactness;
        the ``estimate_for`` docstring pins that reading.
        """
        memory = _memory(tmp_path, [_record(quality=0.7)])
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.uncertainty == 0.0

    def test_unmeasured_quality_records_still_count_as_observations(self, tmp_path: Path) -> None:
        """An attempt nobody scored is still something that was observed."""
        memory = _memory(tmp_path, [_record(quality=None, outcome=OutcomeKind.UNSELECTED), _record(quality=None, outcome=OutcomeKind.UNSELECTED), _record(quality=0.8)])
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.sample_count == 3
        assert estimate.quality_mean == 0.8
        assert estimate.evidence_level is EvidenceLevel.OBSERVED

    def test_only_quality_less_records_leave_the_mean_unmeasured(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(quality=None, outcome=OutcomeKind.REJECTED) for _ in range(4)))
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.sample_count == 4
        assert estimate.quality_mean is None
        assert estimate.uncertainty is None
        assert estimate.evidence_level is EvidenceLevel.OBSERVED

    def test_last_evaluated_at_is_the_newest_record(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, [_record(recorded_at="2025-01-01T00:00:00+00:00"), _record(recorded_at="2025-03-01T00:00:00+00:00")])
        estimate = memory.estimate_for("agent:a", "implement", "coding", profile_version="p1", now=NOW)
        assert estimate.last_evaluated_at == "2025-03-01T00:00:00+00:00"

    def test_empty_profile_version_is_refused_up_front(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            _memory(tmp_path).estimate_for("agent:a", "implement", "coding", profile_version="")


class TestCredit:
    def test_all_selected_is_full_credit(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(outcome=OutcomeKind.SELECTED) for _ in range(4)))
        report = memory.credit_for("agent:a")
        row = report.row_for("implement")
        assert row is not None
        assert row.attempts == 4
        assert row.selected == 4
        assert row.credit == 1.0

    def test_the_conservative_formula(self, tmp_path: Path) -> None:
        """credit = (selected + 0.5*repaired + 0.25*materially_edited) / attempts."""
        memory = _memory(
            tmp_path,
            [
                _record(outcome=OutcomeKind.SELECTED),
                _record(outcome=OutcomeKind.REPAIRED),
                _record(outcome=OutcomeKind.MATERIALLY_EDITED),
                _record(outcome=OutcomeKind.REJECTED),
                _record(outcome=OutcomeKind.UNSELECTED),
            ],
        )
        row = memory.credit_for("agent:a").row_for("implement")
        assert row is not None
        assert (row.attempts, row.selected, row.repaired, row.rejected, row.materially_edited) == (5, 1, 1, 1, 1)
        assert row.credit == pytest.approx(1.75 / 5)

    def test_rejected_and_unselected_earn_nothing(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(outcome=kind) for kind in (OutcomeKind.REJECTED, OutcomeKind.UNSELECTED)))
        row = memory.credit_for("agent:a").row_for("implement")
        assert row is not None
        assert row.credit == 0.0
        assert row.attempts == 2

    def test_an_unmeasured_attempt_is_a_denominator_not_a_numerator(self, tmp_path: Path) -> None:
        """'Appeared in a success' earns nothing without a selection outcome."""
        memory = _memory(tmp_path, [_record(outcome=OutcomeKind.SELECTED), _record(quality=None, outcome=OutcomeKind.UNSELECTED)])
        report = memory.credit_for("agent:a")
        row = report.row_for("implement")
        assert row is not None
        assert row.attempts == 2
        assert row.credit == pytest.approx(0.5)
        assert report.total_credit() == pytest.approx(0.5)

    def test_rows_are_per_capability_and_sorted(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, [_record(capability="review", outcome=OutcomeKind.SELECTED), _record(capability="implement", outcome=OutcomeKind.SELECTED)])
        report = memory.credit_for("agent:a")
        assert [row.capability for row in report.rows] == ["implement", "review"]
        assert report.total_credit() == 1.0

    def test_other_entities_records_are_not_credited(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, [_record(entity_id="agent:a", outcome=OutcomeKind.SELECTED), _record(entity_id="agent:b", outcome=OutcomeKind.REJECTED)])
        report = memory.credit_for("agent:a")
        assert len(report.rows) == 1
        assert report.rows[0].attempts == 1

    def test_since_filters_what_is_credited(self, tmp_path: Path) -> None:
        memory = _memory(
            tmp_path,
            [
                _record(recorded_at="2025-01-01T00:00:00+00:00", outcome=OutcomeKind.SELECTED),
                _record(recorded_at="2025-06-01T00:00:00+00:00", outcome=OutcomeKind.REJECTED),
            ],
        )
        row = memory.credit_for("agent:a", since="2025-03-01T00:00:00+00:00").row_for("implement")
        assert row is not None
        assert row.attempts == 1
        assert row.credit == 0.0

    def test_report_without_any_record_is_empty_not_zero_credit(self, tmp_path: Path) -> None:
        report = _memory(tmp_path).credit_for("agent:ghost")
        assert report.rows == ()
        assert report.total_credit() is None

    def test_credit_out_of_the_unit_interval_is_refused(self) -> None:
        with pytest.raises(ValueError, match="credit"):
            CreditRow(capability="x", attempts=4, selected=4, repaired=0, rejected=0, materially_edited=0, credit=2.0)

    def test_kind_counts_cannot_exceed_attempts(self) -> None:
        with pytest.raises(ValueError, match="attempts"):
            CreditRow(capability="x", attempts=1, selected=2, repaired=0, rejected=0, materially_edited=0, credit=1.0)

    def test_credit_report_roundtrip(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, [_record(outcome=OutcomeKind.SELECTED)])
        report = memory.credit_for("agent:a")
        assert CreditReport.from_dict(report.to_dict()) == report


class TestPrune:
    def test_drops_the_oldest_beyond_the_limit(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(recorded_at=f"2025-01-{index:02d}T00:00:00+00:00") for index in range(1, 6)))
        assert memory.prune(2) == 3
        kept = [r.recorded_at for r in memory.records()]
        assert kept == ["2025-01-04T00:00:00+00:00", "2025-01-05T00:00:00+00:00"]

    def test_under_the_limit_drops_nothing(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(recorded_at=f"2025-01-{index:02d}T00:00:00+00:00") for index in range(1, 4)))
        assert memory.prune(10) == 0
        assert len(memory.records()) == 3

    def test_keep_predicate_is_never_overruled_by_the_limit(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record(recorded_at=f"2025-01-{index:02d}T00:00:00+00:00") for index in range(1, 5)))
        protected = memory.records()[1]  # the second-oldest
        dropped = memory.prune(2, keep=lambda record: record.record_id == protected.record_id)
        assert dropped == 1  # only the very oldest went
        kept_ids = {r.record_id for r in memory.records()}
        assert protected.record_id in kept_ids

    def test_keep_predicate_protecting_everything_drops_nothing(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record() for _ in range(3)))
        assert memory.prune(1, keep=lambda record: True) == 0
        assert len(memory.records()) == 3

    def test_negative_limit_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_records"):
            _memory(tmp_path, [_record()]).prune(-1)


class TestDurability:
    def test_missing_file_is_empty_and_creates_nothing(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        assert memory.records() == []
        assert list(tmp_path.iterdir()) == []

    def test_record_persists_as_jsonl_and_reloads(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        record = _record()
        memory.record(record)
        raw = (tmp_path / MEMORY_FILE_NAME).read_text(encoding="utf-8")
        lines = [line for line in raw.splitlines() if line]
        assert len(lines) == 1
        assert json.loads(lines[0])["record_id"] == record.record_id
        assert CapabilityMemory(tmp_path).records() == [record]

    def test_several_records_roundtrip_in_order(self, tmp_path: Path) -> None:
        records = [_record(recorded_at="2025-01-01T00:00:00+00:00"), _record(recorded_at="2025-01-02T00:00:00+00:00")]
        _memory(tmp_path, records)
        assert CapabilityMemory(tmp_path).records() == sorted(records, key=lambda r: (r.recorded_at, r.record_id))

    def test_duplicate_record_id_is_refused_and_not_persisted(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path)
        record = _record()
        memory.record(record)
        with pytest.raises(ValueError, match="duplicate"):
            memory.record(record)
        assert len(CapabilityMemory(tmp_path).records()) == 1

    def test_non_record_objects_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="CapabilityRecord"):
            CapabilityMemory(tmp_path).record({"record_id": "x"})  # type: ignore[arg-type]

    def test_corrupt_line_raises_naming_the_line(self, tmp_path: Path) -> None:
        (tmp_path / MEMORY_FILE_NAME).write_text(json.dumps(_record().to_dict()) + "\n{oops\n", encoding="utf-8")
        with pytest.raises(MemoryStoreError, match="line 2"):
            CapabilityMemory(tmp_path)

    def test_honesty_violation_on_disk_is_refused_not_dropped(self, tmp_path: Path) -> None:
        """A hand-edited future-dated line must not become memory."""
        payload = _record().to_dict()
        payload["recorded_at"] = _iso(datetime.now(UTC) + timedelta(days=3))
        (tmp_path / MEMORY_FILE_NAME).write_text(json.dumps(payload) + "\n", encoding="utf-8")
        with pytest.raises(MemoryStoreError, match="invalid"):
            CapabilityMemory(tmp_path)

    def test_blank_interior_line_is_corruption_not_a_row(self, tmp_path: Path) -> None:
        """A trailing newline is normal; a blank line in the middle is truncation."""
        (tmp_path / MEMORY_FILE_NAME).write_text(json.dumps(_record().to_dict()) + "\n\n" + json.dumps(_record().to_dict()) + "\n", encoding="utf-8")
        with pytest.raises(MemoryStoreError):
            CapabilityMemory(tmp_path)

    def test_bounded_tail_is_written_on_disk(self, tmp_path: Path) -> None:
        memory = CapabilityMemory(tmp_path, max_records=3)
        for _ in range(5):
            memory.record(_record())
        assert len(memory.records()) == 3
        raw = (tmp_path / MEMORY_FILE_NAME).read_text(encoding="utf-8")
        assert len([line for line in raw.splitlines() if line]) == 3

    def test_prune_persists(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record() for _ in range(4)))
        assert memory.prune(1) == 3
        assert len(CapabilityMemory(tmp_path).records()) == 1

    def test_save_rewrites_the_current_tail(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path, (_record() for _ in range(2)))
        memory.save()
        assert len(CapabilityMemory(tmp_path).records()) == 2

    def test_zero_max_records_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_records"):
            CapabilityMemory(tmp_path, max_records=0)
