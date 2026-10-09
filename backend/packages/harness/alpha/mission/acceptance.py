"""Acceptance evaluation for durable missions.

The mission store can put a mission into ``completed`` today, and nothing in the
repository ever evaluated a single acceptance criterion: ``POST
/api/missions/{id}/transition?to=completed`` is a pure status write, and
``alpha.mission.Mission.proof_obligations[*].satisfied`` is a default-False
field that no code path ever flips.  A terminal "passed" mission therefore said
nothing about whether the work met its own bar.

This module is the missing evaluation seam, and it is deliberately narrow:

- A criterion is ``UNVERIFIED`` until a caller supplies **measured** evidence for
  it.  There is no default-true path, so an un-evaluated criterion can never be
  reported as met.
- ``AcceptanceReport.all_evaluated`` and ``.all_hold`` are separate facts.  A
  report with any un-evaluated criterion is not a pass, and
  :func:`assert_acceptance_passed` refuses it with the real reason.
- Evidence is a flat mapping of criterion -> boolean measured by the caller
  (a file probe, a test run, an operator decision).  This module never guesses
  one from a criterion's prose; that is the whole point of the honesty contract.
- A caller-supplied boolean says *that* a criterion was measured but not *who*
  measured it, so the trusted-evidence section below adds provenance
  (:class:`EvidenceRecord`) and two narrow collectors that read a real test
  exit report or a real artifact digest.  They assemble into the same report and
  the same gate: a criterion with no record, or with two conflicting records,
  stays ``UNVERIFIED``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class CriterionVerdict(StrEnum):
    """Outcome of evaluating one acceptance criterion.

    ``UNVERIFIED`` is a first-class value, not an error: it is what a criterion
    looks like when nobody has measured it yet.
    """

    UNVERIFIED = "unverified"
    MET = "met"
    NOT_MET = "not_met"


#: Refusal reasons are stable strings so callers can assert on them.
REASON_NO_CRITERIA = "mission has no acceptance criteria to evaluate"
REASON_NOT_EVALUATED = "acceptance criteria were not all evaluated"
REASON_NOT_MET = "one or more acceptance criteria were evaluated and did not hold"


@dataclass(frozen=True)
class CriterionResult:
    """The evaluated (or deliberately un-evaluated) state of one criterion."""

    criterion: str
    verdict: CriterionVerdict
    evidence: str = ""
    evaluated_at: float | None = None

    @property
    def evaluated(self) -> bool:
        return self.verdict is not CriterionVerdict.UNVERIFIED

    @property
    def holds(self) -> bool:
        return self.verdict is CriterionVerdict.MET

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "verdict": self.verdict.value,
            "evidence": self.evidence,
            "evaluated_at": self.evaluated_at,
            "evaluated": self.evaluated,
            "holds": self.holds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CriterionResult:
        raw = str(data.get("verdict", CriterionVerdict.UNVERIFIED.value))
        try:
            verdict = CriterionVerdict(raw)
        except ValueError:
            verdict = CriterionVerdict.UNVERIFIED
        evaluated_at = data.get("evaluated_at")
        return cls(
            criterion=str(data.get("criterion", "")),
            verdict=verdict,
            evidence=str(data.get("evidence", "")),
            evaluated_at=float(evaluated_at) if isinstance(evaluated_at, (int, float)) else None,
        )


@dataclass
class AcceptanceReport:
    """Every criterion's outcome, plus the two facts a pass depends on.

    ``all_evaluated`` and ``all_hold`` are deliberately distinct: the first asks
    "was anything actually checked?", the second asks "did the checks pass?".
    Only ``all_evaluated and all_hold`` is a pass.
    """

    criteria: list[CriterionResult] = field(default_factory=list)
    evaluator: str = "none"
    generated_at: float = field(default_factory=time.time)
    report_id: str = ""
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.report_id:
            self.report_id = "acc-" + hashlib.sha256("|".join(f"{c.criterion}={c.verdict.value}" for c in self.criteria).encode("utf-8")).hexdigest()[:12]

    @property
    def all_evaluated(self) -> bool:
        """Every criterion carries a measured verdict.

        An empty report is NOT all-evaluated: a mission with no criteria has
        proved nothing, and treating vacuous truth as a pass is exactly the bug
        class this module exists to close.
        """
        return bool(self.criteria) and all(c.evaluated for c in self.criteria)

    @property
    def all_hold(self) -> bool:
        return bool(self.criteria) and all(c.holds for c in self.criteria)

    @property
    def passed(self) -> bool:
        return self.all_evaluated and self.all_hold

    @property
    def unevaluated(self) -> list[str]:
        return [c.criterion for c in self.criteria if not c.evaluated]

    @property
    def not_met(self) -> list[str]:
        return [c.criterion for c in self.criteria if c.evaluated and not c.holds]

    def refusal_reason(self) -> str | None:
        """Why this report is not a pass, or ``None`` when it is."""
        if not self.criteria:
            return REASON_NO_CRITERIA
        if not self.all_evaluated:
            return f"{REASON_NOT_EVALUATED}: {len(self.unevaluated)} of {len(self.criteria)} unevaluated"
        if not self.all_hold:
            return f"{REASON_NOT_MET}: {len(self.not_met)} of {len(self.criteria)} failed"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "evaluator": self.evaluator,
            "generated_at": self.generated_at,
            "criteria": [c.to_dict() for c in self.criteria],
            "all_evaluated": self.all_evaluated,
            "all_hold": self.all_hold,
            "passed": self.passed,
            "unevaluated": self.unevaluated,
            "not_met": self.not_met,
            "refusal_reason": self.refusal_reason(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AcceptanceReport:
        return cls(
            criteria=[CriterionResult.from_dict(item) for item in data.get("criteria", []) or []],
            evaluator=str(data.get("evaluator", "none")),
            generated_at=float(data.get("generated_at", 0.0) or 0.0),
            report_id=str(data.get("report_id", "")),
            notes=[str(n) for n in data.get("notes", []) or []],
        )


def unevaluated_report(criteria: list[str], *, evaluator: str = "none", notes: list[str] | None = None) -> AcceptanceReport:
    """A report that evaluated nothing — every criterion UNVERIFIED."""
    return AcceptanceReport(
        criteria=[CriterionResult(criterion=c, verdict=CriterionVerdict.UNVERIFIED) for c in criteria],
        evaluator=evaluator,
        notes=list(notes or []),
    )


def evaluate_acceptance(
    criteria: list[str],
    evidence: Mapping[str, bool],
    *,
    evaluator: str = "recorded_evidence",
    notes: list[str] | None = None,
) -> AcceptanceReport:
    """Evaluate ``criteria`` against caller-measured ``evidence``.

    ``evidence`` is keyed by the exact criterion text (whitespace-insensitive
    at the ends) and valued with the measured boolean.  A criterion with no
    entry stays UNVERIFIED — it is never assumed true, and it never silently
    disappears from the report.  Evidence entries that match no criterion are
    reported as notes so a typo in a key cannot masquerade as coverage.
    """
    normalized: dict[str, bool] = {}
    invalid: list[str] = []
    for key, value in evidence.items():
        if type(value) is not bool:
            invalid.append(str(key).strip())
            continue
        normalized[str(key).strip()] = value
    results: list[CriterionResult] = []
    for criterion in criteria:
        key = str(criterion).strip()
        if key in normalized:
            measured = normalized[key]
            results.append(
                CriterionResult(
                    criterion=criterion,
                    verdict=CriterionVerdict.MET if measured else CriterionVerdict.NOT_MET,
                    evidence=f"measured evidence supplied for criterion ({'holds' if measured else 'does not hold'})",
                    evaluated_at=time.time(),
                )
            )
        else:
            results.append(CriterionResult(criterion=criterion, verdict=CriterionVerdict.UNVERIFIED))
    matched = {str(c).strip() for c in criteria}
    unused = [k for k in normalized if k not in matched]
    all_notes = list(notes or [])
    if invalid:
        all_notes.append(f"evidence for {len(invalid)} criterion key(s) was not a boolean measurement and was ignored: {sorted(invalid)}")
    if unused:
        all_notes.append(f"evidence supplied for {len(unused)} criterion key(s) that match no criterion: {sorted(unused)}")
    return AcceptanceReport(criteria=results, evaluator=evaluator, notes=all_notes)


# --------------------------------------------------------------------------- #
# Trusted evidence: provenance, and collectors that measure instead of asserting
# --------------------------------------------------------------------------- #
#
# ``evaluate_acceptance`` above takes a flat ``criterion -> bool`` mapping. That
# is enough to keep the gate honest, but it cannot answer the question a reviewer
# actually asks afterwards: *where did this boolean come from?* A report that
# says "passed" without naming a source is the same claim in a smaller box.
#
# This section adds exactly that, and nothing else:
#
# - :class:`EvidenceRecord` carries the provenance a verdict needs (kind,
#   source, scope, detail, timestamp) and refuses to be constructed without it.
# - Two narrow collectors (:func:`collect_test_exit_report`,
#   :func:`collect_artifact_digest`) *measure* a fact from the filesystem.
#   Neither reads prose, and each returns ``None`` — unverified, never a guess —
#   when the source is missing, unreadable or malformed.
# - :func:`evaluate_trusted_acceptance` assembles records into the same
#   :class:`AcceptanceReport` the gate already accepts, so
#   :func:`assert_acceptance_passed` is unchanged and still the only way to
#   reach ``passed``.
#
# It is deliberately *not* a way to make arbitrary natural-language criteria look
# verified. A criterion with no record stays ``UNVERIFIED``; two records for one
# criterion — including two that contradict each other — decide nothing, because
# silently picking one would let a duplicate win the verdict.


class EvidenceKind(StrEnum):
    """What kind of measurement produced a verdict.

    A closed vocabulary on purpose: an unknown kind means the collector is not
    one this module can vouch for, and an unvouched source is not evidence.
    """

    TEST_EXIT_REPORT = "test_exit_report"
    ARTIFACT_DIGEST = "artifact_digest"
    OWNER_APPROVAL = "owner_approval"
    OBSERVED_FACT = "observed_fact"


#: Provenance strings are bounded so a report cannot smuggle a payload.
MAX_EVIDENCE_SOURCE_CHARS = 200
MAX_EVIDENCE_DETAIL_CHARS = 500
#: A collector reads at most this much of a file; beyond it the verdict is
#: unknown rather than computed from a partial read.
MAX_EVIDENCE_FILE_BYTES = 1 << 20
#: Timestamps this far in the future are a clock error or a forgery.
MAX_EVIDENCE_CLOCK_SKEW_SECONDS = 300.0


@dataclass(frozen=True)
class EvidenceRecord:
    """One measured verdict plus the provenance that makes it reviewable.

    Frozen because a verdict that can be edited after the fact is not evidence.
    """

    criterion: str
    kind: EvidenceKind
    measured: bool
    source: str
    scope: str = ""
    detail: str = ""
    recorded_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            try:
                object.__setattr__(self, "kind", EvidenceKind(str(self.kind)))
            except ValueError as exc:
                raise ValueError(f"unknown evidence kind {self.kind!r}; expected one of {[k.value for k in EvidenceKind]}") from exc
        # `type(...) is not bool` rather than a truthiness test: a caller passing
        # the string "true" must be refused, not quietly counted as a pass.
        if type(self.measured) is not bool:
            raise ValueError("measured must be a boolean measurement, not a truthy value")
        source = str(self.source).strip()
        if not source:
            raise ValueError("evidence requires a non-empty source describing what measured it")
        if len(source) > MAX_EVIDENCE_SOURCE_CHARS:
            raise ValueError(f"evidence source exceeds {MAX_EVIDENCE_SOURCE_CHARS} characters")
        object.__setattr__(self, "source", source)
        detail = str(self.detail)
        if len(detail) > MAX_EVIDENCE_DETAIL_CHARS:
            raise ValueError(f"evidence detail exceeds {MAX_EVIDENCE_DETAIL_CHARS} characters")
        if self.recorded_at > time.time() + MAX_EVIDENCE_CLOCK_SKEW_SECONDS:
            raise ValueError("evidence timestamp is in the future; refusing a forged measurement time")

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "kind": self.kind.value,
            "measured": self.measured,
            "source": self.source,
            "scope": self.scope,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceRecord:
        return cls(
            criterion=str(data.get("criterion", "")),
            kind=EvidenceKind(str(data.get("kind", ""))),
            measured=data.get("measured"),  # type: ignore[arg-type]
            source=str(data.get("source", "")),
            scope=str(data.get("scope", "")),
            detail=str(data.get("detail", "")),
            recorded_at=float(data.get("recorded_at", 0.0) or 0.0),
        )


def _read_bounded(path: Path) -> bytes | None:
    """Read at most :data:`MAX_EVIDENCE_FILE_BYTES`, or return ``None``.

    ``None`` means "not measured" for every caller: a missing file, an
    unreadable one, and one too large to read within the bound are the same
    honest outcome, and none of them is a failure verdict.
    """
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def collect_test_exit_report(path: str | Path, *, criterion: str, scope: str = "") -> EvidenceRecord | None:
    """Measure one criterion against a real test exit report.

    ``path`` must be a JSON document carrying an integer ``exit_code`` (plus
    optional counts). ``exit_code == 0`` measures as met; anything else measures
    as not met. A missing, oversized, unparsable or non-integer report returns
    ``None``, which leaves the criterion ``UNVERIFIED`` — this collector never
    decides a verdict from a file it could not read.

    It reads a *report*, never runs a command: executing the suite is the host's
    decision, and this module is the thing that records what the host measured.
    """
    payload = _read_bounded(Path(path))
    if payload is None:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    exit_code = data.get("exit_code")
    if type(exit_code) is not int:  # bool is an int subclass; "0" is not a measurement
        return None
    counts = {key: data[key] for key in ("passed", "failed", "skipped", "error") if type(data.get(key)) is int}
    detail = " ".join([f"exit_code={exit_code}", *[f"{key}={value}" for key, value in counts.items()]])
    return EvidenceRecord(
        criterion=criterion,
        kind=EvidenceKind.TEST_EXIT_REPORT,
        measured=exit_code == 0,
        source=str(path),
        scope=scope,
        detail=detail[:MAX_EVIDENCE_DETAIL_CHARS],
    )


def collect_artifact_digest(root: str | Path, relative_path: str, *, criterion: str, scope: str = "") -> EvidenceRecord | None:
    """Measure one criterion against a file that exists inside ``root``.

    Existence plus a content digest, both read from the host: the point is that
    "the artifact exists and is not empty" is a fact with a hash attached, not
    something the worker's summary asserts. A missing path or a directory
    measures nothing (``None``); a path that resolves outside ``root`` is a
    caller error and raises, because a collector that quietly follows an escape
    is worse than one that refuses.
    """
    base = Path(root).resolve()
    candidate = (base / str(relative_path)).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"artifact path {relative_path!r} resolves outside the collection root {base}")
    payload = _read_bounded(candidate)
    if payload is None:
        return None
    return EvidenceRecord(
        criterion=criterion,
        kind=EvidenceKind.ARTIFACT_DIGEST,
        measured=len(payload) > 0,
        source=str(candidate),
        scope=scope or str(relative_path),
        detail=f"bytes={len(payload)} sha256={hashlib.sha256(payload).hexdigest()}"[:MAX_EVIDENCE_DETAIL_CHARS],
    )


def evaluate_trusted_acceptance(
    criteria: list[str],
    records: list[EvidenceRecord],
    *,
    evaluator: str = "trusted_collectors",
    notes: list[str] | None = None,
) -> AcceptanceReport:
    """Assemble provenance-bearing records into an :class:`AcceptanceReport`.

    Coverage rules, chosen so no input can manufacture a pass:

    - one record for a declared criterion decides it, and the report names the
      kind, source and scope that decided it;
    - no record leaves the criterion ``UNVERIFIED``;
    - two or more records for one criterion leave it ``UNVERIFIED`` and are
      disclosed as a conflict — duplicate or contradictory evidence is a reason
      to ask a human, not to pick the convenient row;
    - a record naming a criterion that was never declared is a note, so a typo
      cannot masquerade as coverage.
    """
    declared = {str(item).strip(): str(item) for item in criteria}
    by_criterion: dict[str, list[EvidenceRecord]] = {}
    undeclared: list[EvidenceRecord] = []
    for record in records:
        key = str(record.criterion).strip()
        if key in declared:
            by_criterion.setdefault(key, []).append(record)
        else:
            undeclared.append(record)

    all_notes = list(notes or [])
    results: list[CriterionResult] = []
    for key, original in declared.items():
        matching = by_criterion.get(key, [])
        if len(matching) == 1:
            record = matching[0]
            provenance = f"[{record.kind.value}] {record.source}" + (f" scope={record.scope}" if record.scope else "")
            detail = f"{record.detail} (source: {provenance})" if record.detail else f"source: {provenance}"
            all_notes.append(f"criterion {original!r} measured by {provenance}")
            results.append(
                CriterionResult(
                    criterion=original,
                    verdict=CriterionVerdict.MET if record.measured else CriterionVerdict.NOT_MET,
                    evidence=detail[:MAX_EVIDENCE_DETAIL_CHARS],
                    evaluated_at=record.recorded_at,
                )
            )
        elif len(matching) > 1:
            sources = sorted({f"[{item.kind.value}] {item.source}" for item in matching})
            all_notes.append(f"evidence conflict for criterion {original!r}: {len(matching)} records agree on nothing ({sources}); it stays UNVERIFIED")
            results.append(CriterionResult(criterion=original, verdict=CriterionVerdict.UNVERIFIED))
        else:
            results.append(CriterionResult(criterion=original, verdict=CriterionVerdict.UNVERIFIED))

    if undeclared:
        named = sorted({str(item.criterion).strip() for item in undeclared})
        all_notes.append(f"evidence supplied for {len(named)} criterion key(s) that match no criterion: {named}")

    return AcceptanceReport(criteria=results, evaluator=evaluator, notes=all_notes)


class AcceptanceRegistry:
    """Process-wide registry of acceptance-criterion evaluators.

    A host registers a probe per criterion and this module calls it.  The
    registry is additive: with no evaluator registered, a mission can still be
    created and progressed, but its criteria stay UNVERIFIED and it therefore
    cannot reach ``passed`` — the refusal is a real one, not a stub.
    """

    def __init__(self) -> None:
        self._evaluators: dict[str, Callable[[str], bool | None]] = {}
        self._lock = threading.Lock()

    def register(self, name: str, probe: Callable[[str], bool | None]) -> None:
        """Register a probe.  It returns ``None`` to say "cannot decide"."""
        with self._lock:
            self._evaluators[name] = probe

    def unregister(self, name: str) -> None:
        with self._lock:
            self._evaluators.pop(name, None)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._evaluators)

    def evaluate(self, criteria: list[str]) -> AcceptanceReport:
        """Run every registered probe; any ``None``/error leaves a criterion UNVERIFIED."""
        with self._lock:
            evaluators = dict(self._evaluators)
        if not evaluators:
            return unevaluated_report(criteria, evaluator="none", notes=["no acceptance evaluator is registered"])
        evidence: dict[str, bool] = {}
        notes: list[str] = []
        for criterion in criteria:
            key = str(criterion).strip()
            for name, probe in sorted(evaluators.items()):
                try:
                    measured = probe(criterion)
                except Exception as exc:  # noqa: BLE001 - a broken probe must not decide anything
                    notes.append(f"evaluator {name!r} raised for criterion {criterion!r}: {type(exc).__name__}: {exc}")
                    continue
                if measured is None:
                    continue
                if type(measured) is not bool:
                    notes.append(f"evaluator {name!r} returned {type(measured).__name__} for criterion {criterion!r}; expected bool or None")
                    continue
                evidence[key] = measured
                break
            else:
                if evaluators:
                    notes.append(f"no evaluator could decide criterion {criterion!r}; it stays UNVERIFIED")
        report = evaluate_acceptance(criteria, evidence, evaluator="+".join(sorted(evaluators)), notes=notes)
        return report


_REGISTRY = AcceptanceRegistry()


def get_acceptance_registry() -> AcceptanceRegistry:
    """The process-wide acceptance-evaluator registry."""
    return _REGISTRY


def assert_acceptance_passed(report: AcceptanceReport | None) -> AcceptanceReport:
    """Return ``report`` when it is a real pass, else raise with the real reason.

    A ``None`` report, an empty report, a report with an un-evaluated criterion
    and a report with a failed criterion are all refused, each with its own
    reason.  Nothing here can be satisfied by a criterion that was never
    measured.
    """
    if report is None:
        raise AcceptanceNotSatisfied(REASON_NO_CRITERIA)
    reason = report.refusal_reason()
    if reason is not None:
        raise AcceptanceNotSatisfied(reason)
    return report


class AcceptanceNotSatisfied(RuntimeError):
    """Raised when a terminal 'passed' outcome is claimed without real evidence."""


__all__ = [
    "MAX_EVIDENCE_CLOCK_SKEW_SECONDS",
    "MAX_EVIDENCE_DETAIL_CHARS",
    "MAX_EVIDENCE_FILE_BYTES",
    "MAX_EVIDENCE_SOURCE_CHARS",
    "AcceptanceNotSatisfied",
    "AcceptanceRegistry",
    "AcceptanceReport",
    "CriterionResult",
    "CriterionVerdict",
    "EvidenceKind",
    "EvidenceRecord",
    "REASON_NO_CRITERIA",
    "REASON_NOT_EVALUATED",
    "REASON_NOT_MET",
    "assert_acceptance_passed",
    "collect_artifact_digest",
    "collect_test_exit_report",
    "evaluate_acceptance",
    "evaluate_trusted_acceptance",
    "get_acceptance_registry",
    "unevaluated_report",
]
