"""Trusted acceptance evidence: provenance, measured collectors, fail-closed refusals.

`alpha.mission.acceptance` deliberately has no default-true path, and this suite
keeps that property while adding the part it was missing: evidence that carries
**where it came from**, and a narrow set of collectors that measure a fact
instead of restating a claim.

The rules each test pins:

- A collector either **measures** or returns ``None`` (unverified). A missing,
  unreadable or malformed source is never guessed into a verdict.
- A criterion with no record stays ``UNVERIFIED``; the report is not a pass and
  ``assert_acceptance_passed`` refuses with its own reason.
- Two records for one criterion â€” including two that contradict each other â€”
  decide nothing. Picking one would let a duplicate silently win.
- A record for a criterion nobody declared is a note, not coverage.
- ``measured`` must be a real ``bool``; a truthy string is refused at
  construction, so a sloppy caller cannot smuggle a verdict in.
- Provenance is required and bounded: an unknown ``kind``, an empty ``source``
  or a timestamp in the future is refused before it can be recorded.
- Path-confined collectors cannot be walked out of their root.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from alpha.mission.acceptance import (
    MAX_EVIDENCE_DETAIL_CHARS,
    MAX_EVIDENCE_SOURCE_CHARS,
    AcceptanceNotSatisfied,
    CriterionVerdict,
    EvidenceKind,
    EvidenceRecord,
    assert_acceptance_passed,
    collect_artifact_digest,
    collect_test_exit_report,
    evaluate_trusted_acceptance,
)


def _exit_report(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestEvidenceRecordRefusals:
    def test_measured_must_be_a_real_boolean(self) -> None:
        with pytest.raises(ValueError, match="boolean"):
            EvidenceRecord(criterion="x", kind=EvidenceKind.OBSERVED_FACT, measured="yes", source="operator")

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            EvidenceRecord(criterion="x", kind="vibes", measured=True, source="operator")

    def test_an_empty_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source"):
            EvidenceRecord(criterion="x", kind=EvidenceKind.OWNER_APPROVAL, measured=True, source="   ")

    def test_an_oversized_source_is_refused_with_the_bound_named(self) -> None:
        with pytest.raises(ValueError, match=str(MAX_EVIDENCE_SOURCE_CHARS)):
            EvidenceRecord(criterion="x", kind=EvidenceKind.OBSERVED_FACT, measured=True, source="s" * (MAX_EVIDENCE_SOURCE_CHARS + 1))

    def test_an_oversized_detail_is_refused(self) -> None:
        with pytest.raises(ValueError, match=str(MAX_EVIDENCE_DETAIL_CHARS)):
            EvidenceRecord(criterion="x", kind=EvidenceKind.OBSERVED_FACT, measured=True, source="operator", detail="d" * (MAX_EVIDENCE_DETAIL_CHARS + 1))

    def test_a_timestamp_from_the_future_is_refused_as_forged(self) -> None:
        with pytest.raises(ValueError, match="future"):
            EvidenceRecord(
                criterion="x",
                kind=EvidenceKind.OBSERVED_FACT,
                measured=True,
                source="operator",
                recorded_at=time.time() + 3600,
            )

    def test_a_record_carries_its_provenance_into_the_payload(self) -> None:
        record = EvidenceRecord(
            criterion="suite passes",
            kind=EvidenceKind.TEST_EXIT_REPORT,
            measured=True,
            source="pytest",
            scope="tests/test_example.py",
            detail="exit_code=0",
        )
        payload = record.to_dict()
        assert payload["kind"] == "test_exit_report"
        assert payload["source"] == "pytest"
        assert payload["scope"] == "tests/test_example.py"
        assert payload["detail"] == "exit_code=0"
        assert payload["recorded_at"] > 0


class TestTestExitReportCollector:
    def test_a_zero_exit_code_measures_the_criterion_as_met(self, tmp_path: Path) -> None:
        report = _exit_report(tmp_path / "exit.json", {"exit_code": 0, "passed": 12, "failed": 0})

        record = collect_test_exit_report(report, criterion="suite passes", scope="tests/test_example.py")

        assert record is not None
        assert record.kind is EvidenceKind.TEST_EXIT_REPORT
        assert record.measured is True
        assert "exit_code=0" in record.detail

    def test_a_nonzero_exit_code_measures_the_criterion_as_not_met(self, tmp_path: Path) -> None:
        report = _exit_report(tmp_path / "exit.json", {"exit_code": 1, "passed": 11, "failed": 1})

        record = collect_test_exit_report(report, criterion="suite passes")

        assert record is not None
        assert record.measured is False

    def test_a_missing_report_measures_nothing(self, tmp_path: Path) -> None:
        assert collect_test_exit_report(tmp_path / "absent.json", criterion="suite passes") is None

    def test_a_malformed_report_measures_nothing(self, tmp_path: Path) -> None:
        report = tmp_path / "exit.json"
        report.write_text("{not json", encoding="utf-8")

        assert collect_test_exit_report(report, criterion="suite passes") is None

    def test_a_report_without_an_integer_exit_code_measures_nothing(self, tmp_path: Path) -> None:
        report = _exit_report(tmp_path / "exit.json", {"exit_code": "zero"})

        assert collect_test_exit_report(report, criterion="suite passes") is None


class TestArtifactDigestCollector:
    def test_an_existing_non_empty_file_measures_the_criterion(self, tmp_path: Path) -> None:
        target = tmp_path / "outputs" / "index.html"
        target.parent.mkdir(parents=True)
        target.write_text("<html>APEX-DRILL-OK</html>", encoding="utf-8")

        record = collect_artifact_digest(tmp_path, "outputs/index.html", criterion="artifact exists")

        assert record is not None
        assert record.kind is EvidenceKind.ARTIFACT_DIGEST
        assert record.measured is True
        assert len(record.detail.split("sha256=")[1].split()[0]) == 64

    def test_a_missing_file_measures_nothing(self, tmp_path: Path) -> None:
        assert collect_artifact_digest(tmp_path, "outputs/absent.html", criterion="artifact exists") is None

    def test_an_empty_file_does_not_satisfy_an_existence_criterion(self, tmp_path: Path) -> None:
        target = tmp_path / "outputs" / "empty.html"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"")

        record = collect_artifact_digest(tmp_path, "outputs/empty.html", criterion="artifact exists")

        assert record is not None
        assert record.measured is False

    def test_a_path_escaping_the_root_is_refused_rather_than_followed(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        with pytest.raises(ValueError, match="outside"):
            collect_artifact_digest(tmp_path, "../outside.txt", criterion="artifact exists")

    def test_a_directory_is_not_an_artifact(self, tmp_path: Path) -> None:
        (tmp_path / "outputs").mkdir()

        assert collect_artifact_digest(tmp_path, "outputs", criterion="artifact exists") is None


class TestTrustedReportAssembly:
    def test_a_criterion_with_no_record_stays_unverified_and_blocks(self) -> None:
        report = evaluate_trusted_acceptance(["suite passes", "artifact exists"], [])

        assert report.all_evaluated is False
        assert report.passed is False
        with pytest.raises(AcceptanceNotSatisfied) as excinfo:
            assert_acceptance_passed(report)
        assert "not all evaluated" in str(excinfo.value)

    def test_a_fully_measured_passing_report_passes_the_same_gate(self, tmp_path: Path) -> None:
        target = tmp_path / "outputs" / "index.html"
        target.parent.mkdir(parents=True)
        target.write_text("ok", encoding="utf-8")
        criteria = ["suite passes", "artifact exists"]
        records = [
            EvidenceRecord(criterion="suite passes", kind=EvidenceKind.TEST_EXIT_REPORT, measured=True, source="pytest", detail="exit_code=0"),
            collect_artifact_digest(tmp_path, "outputs/index.html", criterion="artifact exists"),
        ]

        report = evaluate_trusted_acceptance(criteria, records)

        assert report.passed is True
        assert assert_acceptance_passed(report) is report

    def test_two_records_for_one_criterion_decide_nothing(self) -> None:
        records = [
            EvidenceRecord(criterion="suite passes", kind=EvidenceKind.TEST_EXIT_REPORT, measured=True, source="pytest"),
            EvidenceRecord(criterion="suite passes", kind=EvidenceKind.OWNER_APPROVAL, measured=False, source="operator"),
        ]

        report = evaluate_trusted_acceptance(["suite passes"], records)

        result = report.criteria[0]
        assert result.verdict is CriterionVerdict.UNVERIFIED
        assert report.passed is False
        assert any("conflict" in note for note in report.notes)

    def test_a_record_for_an_undeclared_criterion_is_noted_not_applied(self) -> None:
        records = [EvidenceRecord(criterion="something nobody declared", kind=EvidenceKind.OBSERVED_FACT, measured=True, source="operator")]

        report = evaluate_trusted_acceptance(["suite passes"], records)

        assert report.passed is False
        assert any("match no criterion" in note for note in report.notes)

    def test_a_failed_measurement_reports_the_failure_rather_than_dropping_it(self) -> None:
        records = [EvidenceRecord(criterion="suite passes", kind=EvidenceKind.TEST_EXIT_REPORT, measured=False, source="pytest", detail="exit_code=1")]

        report = evaluate_trusted_acceptance(["suite passes"], records)

        assert report.criteria[0].verdict is CriterionVerdict.NOT_MET
        assert report.not_met == ["suite passes"]
        with pytest.raises(AcceptanceNotSatisfied) as excinfo:
            assert_acceptance_passed(report)
        assert "did not hold" in str(excinfo.value)

    def test_the_report_names_the_provenance_it_used(self) -> None:
        records = [EvidenceRecord(criterion="suite passes", kind=EvidenceKind.TEST_EXIT_REPORT, measured=True, source="pytest", scope="tests/a.py")]

        report = evaluate_trusted_acceptance(["suite passes"], records)

        assert report.evaluator == "trusted_collectors"
        assert any("pytest" in note and "tests/a.py" in note for note in report.notes)
