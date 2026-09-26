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
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
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
            self.report_id = "acc-" + hashlib.sha256(
                "|".join(f"{c.criterion}={c.verdict.value}" for c in self.criteria).encode("utf-8")
            ).hexdigest()[:12]

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
    normalized = {str(k).strip(): bool(v) for k, v in evidence.items()}
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
    if unused:
        all_notes.append(f"evidence supplied for {len(unused)} criterion key(s) that match no criterion: {sorted(unused)}")
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
                evidence[key] = bool(measured)
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
    "AcceptanceNotSatisfied",
    "AcceptanceRegistry",
    "AcceptanceReport",
    "CriterionResult",
    "CriterionVerdict",
    "REASON_NO_CRITERIA",
    "REASON_NOT_EVALUATED",
    "REASON_NOT_MET",
    "assert_acceptance_passed",
    "evaluate_acceptance",
    "get_acceptance_registry",
    "unevaluated_report",
]
