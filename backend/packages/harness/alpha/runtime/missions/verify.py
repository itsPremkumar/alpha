"""Turn measured evidence into a milestone verdict — reusing the acceptance plane.

Why this exists
--------------
A milestone is only ``VERIFIED`` on a *measured* result, never on the agent's
say-so. This module is the bridge between that rule and the provenance-bearing
collectors that already exist in :mod:`alpha.mission.acceptance`
(:func:`~alpha.mission.acceptance.collect_test_exit_report`,
:func:`~alpha.mission.acceptance.collect_artifact_digest`). It does not run a
command and it does not import that module: the *caller* (the mission tool
boundary) collects an :class:`~alpha.mission.acceptance.EvidenceRecord` — a
report the host already produced — and hands it here.

Keeping the import out of this package is deliberate. ``alpha.runtime.missions``
stays dependency-light and free of a package cycle; ``alpha.mission.acceptance``
is imported lazily at the tool boundary, never at runtime-package import time.
The record is consumed by duck-type (``measured`` / ``kind`` / ``source`` /
``detail``), so this module needs no hard dependency to honour a verdict.

The three honest outcomes
--------------------------
``MET`` and ``NOT_MET`` come straight from a record's boolean ``measured``. A
missing or malformed source yields **no record**, which is ``UNVERIFIED`` — the
milestone is left exactly as it was (``ACTIVE``), never quietly passed and never
quietly failed. That is the same refusal :func:`collect_test_exit_report` makes:
a verdict it could not measure is not a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from alpha.runtime.missions.milestones import InvalidMilestonePlan, MilestonePlan

__all__ = [
    "EvidenceLike",
    "MilestoneVerification",
    "MilestoneVerdict",
    "verify_milestone",
]


class MilestoneVerdict(StrEnum):
    """The outcome a measured evidence record implies for one milestone.

    Kept separate from :class:`~alpha.runtime.missions.milestones.MilestoneStatus`
    on purpose: the verdict is what the *evidence* says, the status is what the
    plan records. ``UNVERIFIED`` never becomes a status -- it means "the milestone
    is untouched".
    """

    MET = "met"
    NOT_MET = "not_met"
    UNVERIFIED = "unverified"


class EvidenceLike(Protocol):
    """The slice of :class:`~alpha.mission.acceptance.EvidenceRecord` we consume."""

    kind: object
    measured: bool
    source: str
    detail: str


#: How the milestone's evidence text labels the record that produced it.
_EVIDENCE_MAX: Final[int] = 4000


@dataclass(frozen=True, slots=True)
class MilestoneVerification:
    """A verdict for one milestone, plus the evidence text to store on it."""

    milestone_id: str
    verdict: MilestoneVerdict
    evidence: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is MilestoneVerdict.MET

    def to_dict(self) -> dict[str, object]:
        return {"milestone_id": self.milestone_id, "verdict": self.verdict.value, "evidence": self.evidence}


def _format_evidence(record: EvidenceLike) -> str:
    kind = getattr(getattr(record, "kind", None), "value", None) or str(getattr(record, "kind", "") or "fact")
    source = str(getattr(record, "source", "") or "")
    detail = str(getattr(record, "detail", "") or "").strip()
    joined = " ".join(part for part in (detail, f"[{kind}:{source}]" if source else f"[{kind}]") if part)
    return joined[:_EVIDENCE_MAX]


def verify_milestone(
    plan: MilestonePlan,
    *,
    milestone_id: str,
    record: EvidenceLike | None,
) -> tuple[MilestonePlan, MilestoneVerification]:
    """Apply a measured evidence *record* to one milestone of *plan*.

    With ``record=None`` the milestone is left untouched and the verdict is
    :attr:`MilestoneVerdict.UNVERIFIED` -- a source that could not be measured
    is not a failure, and it is certainly not a pass. Otherwise the record's
    boolean ``measured`` decides: ``True`` marks the milestone ``VERIFIED``,
    ``False`` marks it ``FAILED``, both carrying the record's provenance as the
    stored evidence text.
    """
    milestone = plan.get(milestone_id)
    if milestone is None:
        raise InvalidMilestonePlan(f"unknown milestone id {milestone_id!r}")

    if record is None:
        return plan, MilestoneVerification(milestone_id, MilestoneVerdict.UNVERIFIED, evidence="no measurable evidence; the named source was missing, unreadable or malformed")

    measured = bool(getattr(record, "measured"))
    evidence = _format_evidence(record)
    updated = plan.verify(milestone_id, passed=measured, evidence=evidence)
    verdict = MilestoneVerdict.MET if measured else MilestoneVerdict.NOT_MET
    return updated, MilestoneVerification(milestone_id, verdict, evidence=evidence)
