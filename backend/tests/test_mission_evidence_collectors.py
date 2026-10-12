"""Automatic evidence collectors: a criterion is measured, never asserted.

`alpha.mission.acceptance` already knew how to *read* evidence — an
`EvidenceRecord` with provenance, one record deciding a criterion, none leaving
it `UNVERIFIED`, two disclosing a conflict. What it never did was produce any:
the two collectors there are readers of a file the host had already written, so
"no automatic evidence collectors" was a true sentence about the tree.

`alpha.mission.collectors` is the seam that runs a measurement and hands the
result to that same gate. The rules this suite pins are the ones that keep it
from turning into a verdict factory:

- exit 0 measures `MET`; a real non-zero exit measures `NOT_MET`.
- everything that *could not run* measures nothing at all: a timeout, an
  unrunnable command, an empty command, and exit 126/127 (not executable /
  command not found) all return no record, so the criterion stays
  `UNVERIFIED` instead of being reported as a failed run that never happened.
- a collector that raises is a note, not a verdict.
- a record naming a criterion its collector did not declare is dropped with a
  note, so a copy/paste cannot move evidence from one criterion to another.
- two collectors for one criterion still decide nothing (the acceptance
  module's conflict rule), and no collectors at all is never a pass.
- a non-collector input is a `TypeError`, not a silently skipped row.
"""

from __future__ import annotations

import subprocess
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
)
from alpha.mission.collectors import (
    ArtifactEvidenceCollector,
    CommandCheck,
    CommandEvidenceCollector,
    EvidenceCollection,
    EvidenceCollector,
    Measurement,
    collect_and_evaluate,
    collect_evidence,
)
from alpha.mission.goalloop.gates import REASON_NO_COMMAND, QualityGate

# --------------------------------------------------------------------------- #
# ProcessRunner doubles: no test in this file ever shells out.
# --------------------------------------------------------------------------- #


def _zero(gate: QualityGate) -> tuple[int | None, str]:
    return 0, "42 passed in 1.1s"


def _one(gate: QualityGate) -> tuple[int | None, str]:
    return 1, "1 failed, 41 passed in 1.1s"


def _not_found(gate: QualityGate) -> tuple[int | None, str]:
    return 127, f"{gate.command}: command not found"


def _not_executable(gate: QualityGate) -> tuple[int | None, str]:
    return 126, f"{gate.command}: Permission denied"


def _hangs(gate: QualityGate) -> tuple[int | None, str]:
    raise subprocess.TimeoutExpired(gate.command, gate.timeout_seconds)


def _explodes(gate: QualityGate) -> tuple[int | None, str]:
    raise RuntimeError("no interpreter on this host")


def _command(criterion: str, command: str = "pytest -q", *, runner, **kwargs) -> CommandEvidenceCollector:
    return CommandEvidenceCollector(CommandCheck(criterion=criterion, command=command, **kwargs), runner=runner)


# --------------------------------------------------------------------------- #
# Collector doubles for the failure paths that are about *collectors*, not about
# commands.
# --------------------------------------------------------------------------- #


class _RaisingCollector:
    criterion = "suite passes"

    def measure(self) -> Measurement:
        raise RuntimeError("the measurement source caught fire")


class _MismatchedCollector:
    """Returns a record for a criterion it never declared."""

    criterion = "suite passes"

    def measure(self) -> Measurement:
        return Measurement.of(
            EvidenceRecord(
                criterion="something nobody declared",
                kind=EvidenceKind.OBSERVED_FACT,
                measured=True,
                source="copy-paste",
            )
        )


class _WrongShapeCollector:
    criterion = "suite passes"

    def measure(self):  # noqa: ANN201 - deliberately not a Measurement
        return "passed"


class _SilentCollector:
    """Measures nothing and does not say why."""

    criterion = "suite passes"

    def measure(self) -> Measurement:
        return Measurement.unverified("")


class TestCommandCollectorMeasures:
    def test_exit_zero_measures_the_criterion_as_met(self) -> None:
        collector = _command("suite passes", runner=_zero)

        measurement = collector.measure()

        record = measurement.record
        assert record is not None, "a real exit 0 is a measurement"
        assert record.measured is True
        assert record.kind is EvidenceKind.OBSERVED_FACT
        assert record.criterion == "suite passes"
        assert record.source == "pytest -q"
        assert "exit=0" in record.detail

    def test_a_nonzero_exit_measures_the_criterion_as_not_met(self) -> None:
        collector = _command("suite passes", runner=_one)

        record = collector.measure().record

        assert record is not None, "a real non-zero exit is a measurement too"
        assert record.measured is False

    def test_the_collector_satisfies_the_declared_protocol(self) -> None:
        assert isinstance(_command("suite passes", runner=_zero), EvidenceCollector)

    def test_the_command_is_truncated_to_the_source_bound(self) -> None:
        long_command = "pytest -q " + "--flag=" + "x" * 400

        record = _command("suite passes", command=long_command, runner=_zero).measure().record

        assert record is not None
        assert len(record.source) <= MAX_EVIDENCE_SOURCE_CHARS

    def test_the_detail_stays_within_the_evidence_bound(self) -> None:
        record = _command("suite passes", command="x" * 400, runner=_zero).measure().record

        assert record is not None
        assert len(record.detail) <= MAX_EVIDENCE_DETAIL_CHARS

    def test_a_label_travels_as_the_scope_of_the_record(self) -> None:
        record = _command("suite passes", label="unit suite", runner=_zero).measure().record

        assert record is not None
        assert record.scope == "unit suite"


class TestCommandCollectorRefusesToGuess:
    def test_a_timeout_measures_nothing(self) -> None:
        collector = _command("suite passes", command="sleep 999", timeout_seconds=0.5, runner=_hangs)

        measurement = collector.measure()

        assert measurement.record is None
        assert "timed out" in measurement.reason

    def test_an_unrunnable_command_measures_nothing(self) -> None:
        collector = _command("suite passes", runner=_explodes)

        measurement = collector.measure()

        assert measurement.record is None
        assert "RuntimeError" in measurement.reason

    def test_an_empty_command_measures_nothing(self) -> None:
        collector = _command("suite passes", command="   ", runner=_zero)

        measurement = collector.measure()

        assert measurement.record is None
        assert REASON_NO_COMMAND in measurement.reason

    @pytest.mark.parametrize(
        ("runner", "phrase"),
        [(_not_found, "127"), (_not_executable, "126")],
        ids=["command-not-found", "not-executable"],
    )
    def test_an_exit_code_that_means_the_command_never_ran_measures_nothing(self, runner, phrase: str) -> None:
        """126/127 are the shell saying "I could not run it", not "it failed".

        Reading either as NOT_MET would claim a suite ran and failed on a host
        that never had it — the exact fabrication this module exists to avoid.
        """
        collector = _command("suite passes", runner=runner)

        measurement = collector.measure()

        assert measurement.record is None, "a command that never started cannot be a failure verdict"
        assert phrase in measurement.reason


class TestArtifactCollector:
    def test_an_existing_file_measures_the_criterion(self, tmp_path: Path) -> None:
        target = tmp_path / "outputs" / "report.html"
        target.parent.mkdir(parents=True)
        target.write_text("<html>ok</html>", encoding="utf-8")

        measurement = ArtifactEvidenceCollector(criterion="artifact exists", root=tmp_path, relative_path="outputs/report.html").measure()

        record = measurement.record
        assert record is not None
        assert record.kind is EvidenceKind.ARTIFACT_DIGEST
        assert record.measured is True

    def test_a_missing_file_measures_nothing_and_says_why(self, tmp_path: Path) -> None:
        measurement = ArtifactEvidenceCollector(criterion="artifact exists", root=tmp_path, relative_path="outputs/absent.html").measure()

        assert measurement.record is None
        assert "missing" in measurement.reason

    def test_a_path_escaping_the_root_is_refused_rather_than_noted(self, tmp_path: Path) -> None:
        collector = ArtifactEvidenceCollector(criterion="artifact exists", root=tmp_path, relative_path="../outside.txt")

        with pytest.raises(ValueError, match="outside"):
            collector.measure()


class TestCollectEvidence:
    def test_records_and_notes_are_returned_separately(self) -> None:
        collection = collect_evidence([_command("suite passes", runner=_zero), _command("second suite", runner=_one)])

        assert len(collection.records) == 2
        assert collection.notes == []

    def test_an_empty_sequence_measures_nothing_at_all(self) -> None:
        collection = collect_evidence([])

        assert collection == EvidenceCollection(records=[], notes=[])

    def test_a_raising_collector_becomes_a_note_rather_than_an_exception(self) -> None:
        collection = collect_evidence([_RaisingCollector()])

        assert collection.records == []
        assert len(collection.notes) == 1
        assert "suite passes" in collection.notes[0]
        assert "RuntimeError" in collection.notes[0]

    def test_an_unverified_measurement_is_noted_with_its_reason(self) -> None:
        collection = collect_evidence([_SilentCollector()])

        assert collection.records == []
        assert len(collection.notes) == 1
        assert "measured nothing" in collection.notes[0]

    def test_a_record_for_a_criterion_the_collector_never_declared_is_dropped(self) -> None:
        collection = collect_evidence([_MismatchedCollector()])

        assert collection.records == []
        assert any("declared" in note for note in collection.notes)

    def test_a_collector_returning_something_that_is_not_a_measurement_is_noted(self) -> None:
        collection = collect_evidence([_WrongShapeCollector()])

        assert collection.records == []
        assert any("Measurement" in note for note in collection.notes)

    def test_a_bare_string_is_refused_instead_of_being_iterated(self) -> None:
        with pytest.raises(TypeError, match="EvidenceCollector"):
            collect_evidence(["pytest -q"])

    def test_a_sequence_containing_a_non_collector_is_refused_whole(self) -> None:
        with pytest.raises(TypeError, match="EvidenceCollector"):
            collect_evidence([_command("suite passes", runner=_zero), object()])

    def test_a_generator_of_collectors_is_accepted(self) -> None:
        generators = (_command("suite passes", runner=_zero) for _ in range(1))

        collection = collect_evidence(generators)

        assert len(collection.records) == 1


class TestCollectAndEvaluate:
    def test_fully_measured_criteria_pass_the_existing_gate(self, tmp_path: Path) -> None:
        target = tmp_path / "outputs" / "report.html"
        target.parent.mkdir(parents=True)
        target.write_text("<html>ok</html>", encoding="utf-8")
        collectors = [
            _command("suite passes", runner=_zero),
            ArtifactEvidenceCollector(criterion="artifact exists", root=tmp_path, relative_path="outputs/report.html"),
        ]

        report = collect_and_evaluate(["suite passes", "artifact exists"], collectors)

        assert report.passed is True
        assert assert_acceptance_passed(report) is report

    def test_a_measured_failure_is_not_met_not_unverified(self) -> None:
        report = collect_and_evaluate(["suite passes"], [_command("suite passes", runner=_one)])

        assert report.criteria[0].verdict is CriterionVerdict.NOT_MET
        assert report.refusal_reason() is not None
        with pytest.raises(AcceptanceNotSatisfied):
            assert_acceptance_passed(report)

    def test_no_collectors_is_never_a_pass(self) -> None:
        report = collect_and_evaluate(["suite passes"], [])

        assert report.all_evaluated is False
        assert report.passed is False
        assert report.criteria[0].verdict is CriterionVerdict.UNVERIFIED
        with pytest.raises(AcceptanceNotSatisfied) as excinfo:
            assert_acceptance_passed(report)
        assert "not all evaluated" in str(excinfo.value)

    def test_a_criterion_nobody_claimed_stays_unverified(self) -> None:
        report = collect_and_evaluate(["suite passes", "docs updated"], [_command("suite passes", runner=_zero)])

        verdicts = {c.criterion: c.verdict for c in report.criteria}
        assert verdicts["suite passes"] is CriterionVerdict.MET
        assert verdicts["docs updated"] is CriterionVerdict.UNVERIFIED
        assert report.passed is False

    def test_two_collectors_for_one_criterion_decide_nothing(self) -> None:
        collectors = [_command("suite passes", command="pytest -q", runner=_zero), _command("suite passes", command="ruff check .", runner=_zero)]

        report = collect_and_evaluate(["suite passes"], collectors)

        assert report.criteria[0].verdict is CriterionVerdict.UNVERIFIED
        assert report.passed is False
        assert any("conflict" in note for note in report.notes)

    def test_a_raising_collector_leaves_its_criterion_unverified_with_the_reason(self) -> None:
        report = collect_and_evaluate(["suite passes"], [_RaisingCollector()])

        assert report.criteria[0].verdict is CriterionVerdict.UNVERIFIED
        assert any("RuntimeError" in note for note in report.notes)
        with pytest.raises(AcceptanceNotSatisfied):
            assert_acceptance_passed(report)

    def test_collection_notes_reach_the_report(self) -> None:
        report = collect_and_evaluate(["suite passes"], [_command("suite passes", runner=_hangs)])

        assert report.criteria[0].verdict is CriterionVerdict.UNVERIFIED
        assert any("timed out" in note for note in report.notes)

    def test_caller_notes_are_preserved_alongside_the_collector_notes(self) -> None:
        report = collect_and_evaluate(["suite passes"], [_command("suite passes", runner=_zero)], notes=["collected by the nightly job"])

        assert "collected by the nightly job" in report.notes
        assert report.passed is True

    def test_the_evaluator_names_who_assembled_the_report(self) -> None:
        report = collect_and_evaluate(["suite passes"], [_command("suite passes", runner=_zero)])

        assert report.evaluator == "automatic_evidence_collectors"
