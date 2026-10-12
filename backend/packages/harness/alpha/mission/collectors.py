"""Automatic evidence collectors: run a measurement, never a verdict.

:mod:`alpha.mission.acceptance` already knew how to *read* evidence. An
:class:`~alpha.mission.acceptance.EvidenceRecord` carries provenance, one record
decides a declared criterion, no record leaves it ``UNVERIFIED``, and two
records for one criterion disclose a conflict instead of picking a winner. Its
two collectors (:func:`~alpha.mission.acceptance.collect_test_exit_report`,
:func:`~alpha.mission.acceptance.collect_artifact_digest`) are **readers** of a
file the host had already written: nothing in the tree ever ran a measurement
and handed the result to that gate, so the sentence "no automatic evidence
collectors" was true, and every criterion still needed a caller-supplied
boolean measured somewhere else.

This module is that missing producer, and it is deliberately the thinnest thing
that can close the gap:

- :class:`CommandEvidenceCollector` reuses :class:`alpha.mission.goalloop.gates.GateRunner`
  — the same bounded, never-cached, injectable-process executor the goal loop
  already trusts — so a test never shells out and a host can interpose its own
  sandbox. It runs **one** attempt and reports what happened.
- :class:`ArtifactEvidenceCollector` wraps the existing confined-root digest
  reader rather than re-implementing path confinement.
- :func:`collect_evidence` runs a set of collectors and splits the result into
  *records* and *notes*; :func:`collect_and_evaluate` feeds both into
  :func:`alpha.mission.acceptance.evaluate_trusted_acceptance`, so the report,
  the vocabulary (``MET`` / ``NOT_MET`` / ``UNVERIFIED``) and
  :func:`~alpha.mission.acceptance.assert_acceptance_passed` are unchanged.

Three rules decide whether this module can manufacture a pass. None of them is
an implementation detail:

1. **Only an exit code that the shell actually produced is a measurement.**
   ``0`` measures met and a real non-zero exit measures not met. Everything
   else measures *nothing* and returns no record: a timeout, an unrunnable
   command, an empty command, a runner that reported no exit code, and — the
   two that are easy to get wrong — exit ``126`` (not executable) and ``127``
   (command not found). Those two are the shell saying it never started the
   command, so reading them as ``NOT_MET`` would claim a suite ran and failed
   on a host that never had it.
2. **A collector that cannot answer says so.** A raised exception, a
   ``Measurement`` with no record, and a record naming a criterion its
   collector never declared all become *notes* on the report. They are never
   silently dropped and never turned into a verdict.
3. **Nothing here is wired into a run on its own.** A host (or an operator)
   chooses which collectors back which criteria. With no collectors a
   criterion stays ``UNVERIFIED`` and the acceptance gate refuses — which is
   exactly the behaviour it had before this module existed.

Output *text* deliberately does not travel in a record: the gate's bounded
output tail belongs to the continuation prompt an agent iterates against, while
an evidence record is a durable provenance row. The record carries what
measured it (the command, the artifact path), how it ended, and how long it
took.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from alpha.mission.acceptance import (
    MAX_EVIDENCE_SOURCE_CHARS,
    AcceptanceReport,
    EvidenceKind,
    EvidenceRecord,
    collect_artifact_digest,
    evaluate_trusted_acceptance,
)
from alpha.mission.goalloop.gates import (
    DEFAULT_TIMEOUT_SECONDS,
    GateRunner,
    GateStatus,
    ProcessRunner,
    QualityGate,
)

#: Exit codes that mean the shell never started the command: 126 is
#: "not executable", 127 is "command not found". Reporting either as a
#: measurement would claim work that did not happen.
SHELL_NEVER_RAN_EXIT_CODES = frozenset({126, 127})

#: Who assembled a report produced by :func:`collect_and_evaluate`.
DEFAULT_EVALUATOR = "automatic_evidence_collectors"

#: Stable fragments of the notes, so a caller can assert on them.
NOTE_RAISED = "raised"
NOTE_MEASURED_NOTHING = "measured nothing"
NOTE_NOT_A_MEASUREMENT = "not a Measurement"


@dataclass(frozen=True)
class Measurement:
    """One collector's answer: a record, or the reason there is none.

    Exactly one of the two fields is expected to carry information, but neither
    is required — an empty :class:`Measurement` is itself a legitimate "I could
    not measure this" answer, and :func:`collect_evidence` will disclose it as
    such rather than inventing a reason.
    """

    record: EvidenceRecord | None = None
    reason: str = ""

    @classmethod
    def of(cls, record: EvidenceRecord) -> Measurement:
        """Wrap a real measurement."""
        return cls(record=record)

    @classmethod
    def unverified(cls, reason: str) -> Measurement:
        """Report that nothing was measured, and why."""
        return cls(record=None, reason=str(reason))

    @property
    def measured(self) -> bool:
        """Whether this answer carries a record (not whether it measured *true*)."""
        return self.record is not None


@runtime_checkable
class EvidenceCollector(Protocol):
    """One automatic measurement bound to exactly one acceptance criterion.

    Structural on purpose: a host may implement this with a class, a closure or
    a dataclass. :func:`collect_evidence` refuses anything that does not
    satisfy it, so a stray object cannot be skipped silently and leave a
    criterion looking measured-by-absence.
    """

    criterion: str

    def measure(self) -> Measurement:
        """Measure the criterion once, or explain why it could not be."""
        ...


@dataclass(frozen=True)
class CommandCheck:
    """A deterministic command bound to one acceptance criterion.

    ``command`` mirrors the operator-facing syntax of ``/goal gate add
    <command>`` because it is the same kind of thing: a command line, an exit
    code and an output tail. It is intentionally *not* refused when empty —
    an unrunnable check must be reported as ``UNVERIFIED`` by the collector,
    not hidden by a constructor that never lets the caller try.
    """

    criterion: str
    command: str
    label: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    cwd: str = ""

    def __post_init__(self) -> None:
        if not str(self.criterion).strip():
            raise ValueError("a command check needs a non-empty criterion to measure")
        if self.timeout_seconds <= 0:
            raise ValueError("a command check needs a positive timeout")

    def to_gate(self) -> QualityGate:
        """Render this check as the gate the shared runner executes."""
        return QualityGate(
            command=self.command,
            label=self.label,
            timeout_seconds=self.timeout_seconds,
            cwd=self.cwd,
        )


@dataclass(frozen=True)
class CommandEvidenceCollector:
    """Measure a criterion by running its command and reading the exit code.

    One attempt, no cache, no retry: the point is to record what a single run
    of *current* inputs produced. ``runner`` is the injectable
    :data:`~alpha.mission.goalloop.gates.ProcessRunner`; leaving it ``None``
    means the real subprocess executor, and every test in the tree passes a
    double instead.
    """

    check: CommandCheck
    runner: ProcessRunner | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.check, CommandCheck):
            raise TypeError(f"CommandEvidenceCollector needs a CommandCheck, got {type(self.check).__name__}")

    @property
    def criterion(self) -> str:
        return self.check.criterion

    def measure(self) -> Measurement:
        """Run once and translate the outcome honestly.

        ``PASS`` measures met, ``FAIL`` measures not met, and every state in
        which the command did not actually complete measures nothing.
        """
        result = GateRunner(self.runner).run(self.check.to_gate())
        if result.status is GateStatus.TIMEOUT:
            return Measurement.unverified(f"{result.reason}; a command that timed out measures nothing")
        if result.status is GateStatus.ERROR:
            return Measurement.unverified(f"the command could not run: {result.reason}")
        if result.exit_code is None:
            return Measurement.unverified("the runner reported no exit code, so nothing was measured")
        if result.exit_code in SHELL_NEVER_RAN_EXIT_CODES:
            return Measurement.unverified(f"exit code {result.exit_code} means the shell never started the command; nothing was measured")
        detail = f"exit={result.exit_code} status={result.status.value} duration_seconds={result.duration_seconds:.3f}"
        return Measurement.of(
            EvidenceRecord(
                criterion=self.check.criterion,
                # OBSERVED_FACT, never TEST_EXIT_REPORT: this runs an operator's
                # command, which is not necessarily a test suite, and a kind is
                # a claim about what produced the measurement.
                kind=EvidenceKind.OBSERVED_FACT,
                measured=result.status is GateStatus.PASS,
                source=self.check.command[:MAX_EVIDENCE_SOURCE_CHARS],
                scope=self.check.label,
                detail=detail,
            )
        )


@dataclass(frozen=True)
class ArtifactEvidenceCollector:
    """Measure a criterion against a file that must exist inside ``root``.

    A thin, path-confined wrapper over
    :func:`alpha.mission.acceptance.collect_artifact_digest`, kept separate so
    the confinement rules live in exactly one place. A path that resolves
    outside ``root`` still raises rather than being reported as "not found":
    a collector that quietly follows an escape is worse than one that refuses.
    """

    criterion: str
    root: str | Path
    relative_path: str

    def __post_init__(self) -> None:
        if not str(self.criterion).strip():
            raise ValueError("an artifact check needs a non-empty criterion to measure")

    def measure(self) -> Measurement:
        """Stat, hash and bound the artifact, or explain why it could not."""
        record = collect_artifact_digest(self.root, self.relative_path, criterion=self.criterion)
        if record is None:
            return Measurement.unverified(f"artifact {self.relative_path!r} under {self.root} is missing, unreadable, not a file, or over the read bound")
        return Measurement.of(record)


@dataclass
class EvidenceCollection:
    """What a pass over a set of collectors produced.

    ``records`` and ``notes`` are deliberately separate lists: a note is
    something the reviewer needs to read, not a measurement, and folding it
    into one bucket would either fake coverage or hide the reason a criterion
    is still ``UNVERIFIED``.
    """

    records: list[EvidenceRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _require_collectors(collectors: Iterable[EvidenceCollector]) -> list[EvidenceCollector]:
    """Materialise and type-check the input, refusing the whole batch on bad shape.

    A ``str`` is iterable, so without the explicit guard
    ``collect_evidence("pytest -q")`` would walk the characters and report a
    dozen confusing failures instead of one correct refusal.
    """
    if isinstance(collectors, (str, bytes)) or not isinstance(collectors, Iterable):
        raise TypeError(f"collect_evidence expects an iterable of EvidenceCollector, got {type(collectors).__name__}")
    items = list(collectors)
    for item in items:
        if isinstance(item, (str, bytes)) or not isinstance(item, EvidenceCollector):
            raise TypeError(f"{type(item).__name__} is not an EvidenceCollector: it needs a criterion and a measure() method")
        if not str(item.criterion).strip():
            raise TypeError(f"{type(item).__name__} declares an empty criterion, so its record could never match anything")
    return items


def collect_evidence(collectors: Iterable[EvidenceCollector]) -> EvidenceCollection:
    """Run every collector once and split the outcome into records and notes.

    Nothing here short-circuits: one broken collector must not hide the rest of
    the batch, and none of its failures may be mistaken for a measurement. A
    raised exception, a non-:class:`Measurement` return, an unexplained
    measurement and a record whose criterion does not match the one its
    collector declared are each recorded as a note and contribute no record.

    Input is validated before anything runs, so a bad batch is refused whole
    rather than measured half-way.
    """
    items = _require_collectors(collectors)
    records: list[EvidenceRecord] = []
    notes: list[str] = []
    for item in items:
        criterion = str(item.criterion).strip()
        try:
            measurement = item.measure()
        except Exception as exc:  # noqa: BLE001 - a broken collector decides nothing
            notes.append(f"collector for criterion {criterion!r} {NOTE_RAISED} {type(exc).__name__}: {exc}; the criterion stays UNVERIFIED")
            continue
        if not isinstance(measurement, Measurement):
            notes.append(f"collector for criterion {criterion!r} returned {type(measurement).__name__}, {NOTE_NOT_A_MEASUREMENT}; nothing was recorded")
            continue
        record = measurement.record
        if record is None:
            reason = str(measurement.reason).strip() or "no reason given"
            notes.append(f"collector for criterion {criterion!r} {NOTE_MEASURED_NOTHING}: {reason}; the criterion stays UNVERIFIED")
            continue
        if str(record.criterion).strip() != criterion:
            notes.append(f"collector declared criterion {criterion!r} but its record names {record.criterion!r}; the record was not used")
            continue
        records.append(record)
    return EvidenceCollection(records=records, notes=notes)


def collect_and_evaluate(
    criteria: Sequence[str],
    collectors: Iterable[EvidenceCollector],
    *,
    evaluator: str = DEFAULT_EVALUATOR,
    notes: Sequence[str] | None = None,
) -> AcceptanceReport:
    """Collect evidence for ``criteria`` and assemble the acceptance report.

    The report is the one :func:`alpha.mission.acceptance.assert_acceptance_passed`
    already accepts, so the pass condition does not move: a criterion with no
    record stays ``UNVERIFIED``, two records for one criterion disclose a
    conflict, and a report with any of those is not a pass.

    ``notes`` supplied by the caller are kept alongside the collection notes
    rather than replacing them — the reason a criterion is unverified has to
    survive into whatever the reviewer reads.
    """
    collection = collect_evidence(collectors)
    return evaluate_trusted_acceptance(
        list(criteria),
        collection.records,
        evaluator=evaluator,
        notes=[*(notes or []), *collection.notes],
    )


__all__ = [
    "DEFAULT_EVALUATOR",
    "NOTE_MEASURED_NOTHING",
    "NOTE_NOT_A_MEASUREMENT",
    "NOTE_RAISED",
    "SHELL_NEVER_RAN_EXIT_CODES",
    "ArtifactEvidenceCollector",
    "CommandCheck",
    "CommandEvidenceCollector",
    "EvidenceCollection",
    "EvidenceCollector",
    "Measurement",
    "collect_and_evaluate",
    "collect_evidence",
]
