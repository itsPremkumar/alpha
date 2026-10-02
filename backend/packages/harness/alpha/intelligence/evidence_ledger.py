"""Phase E — cross-subsystem evidence ledger: six subsystems become one verdict.

The problem
-----------
Alpha has **six** subsystems that can pass or reject a candidate, and none of
them can see the others:

============================ ==================================================
Subsystem                    Owns
============================ ==================================================
``avo``                      scorer authority, invariant oracle, commit gate
``evolution_evidence``       the improvement bar (integrity, noise floors)
``rsi_promotion``            promotion routing
``self_tuning``              typed config change-sets, canary, rollback
``evolution``                bounded surface evolution
``intelligence``             replay, regression, pathway, diversity
============================ ==================================================

A candidate can pass ``alpha.intelligence.evaluate_gate`` and be entirely
unknown to ``alpha.avo``'s invariant oracle. Nothing reconciles them, so the
**union of evidence is never assembled** — only the last gate consulted decides.
"Promoted" currently means "promoted by whichever gate was asked last", which is
not a claim anyone can audit.

Three convergence rules, and the reasoning behind each
------------------------------------------------------
**1. Absence is not approval.** A subsystem that has not evaluated a candidate is
:attr:`SubsystemVerdict.NOT_EVALUATED`, never ``PASS``. Rounding "we didn't look"
to "it's fine" is how an unexamined surface ships.

**2. A rejection is final.** There is no averaging across subsystems. A candidate
that breaks an invariant oracle has not passed, and a 5–1 vote does not make it
have passed. This is the opposite of an ensemble, deliberately: subsystems are
not voters, they are gates.

**3. Unreconciled is a first-class outcome.** :attr:`ConvergenceStatus.PARTIAL`
with a populated ``unreconciled`` list is correct and expected. It is not an error
state to be smoothed away, and it never rounds up to ``APPROVED``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Subsystem",
    "SubsystemVerdict",
    "ConvergenceStatus",
    "SubsystemRecord",
    "ConvergedVerdict",
    "EvidenceLedger",
    "get_evidence_ledger",
]


class Subsystem(StrEnum):
    """Every subsystem that can hold an opinion about a candidate."""

    AVO = "avo"
    EVOLUTION_EVIDENCE = "evolution_evidence"
    RSI_PROMOTION = "rsi_promotion"
    SELF_TUNING = "self_tuning"
    EVOLUTION = "evolution"
    INTELLIGENCE = "intelligence"


class SubsystemVerdict(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    NOT_EVALUATED = "NOT_EVALUATED"
    INCONCLUSIVE = "INCONCLUSIVE"


class ConvergenceStatus(StrEnum):
    """The combined outcome."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"
    """Some enabled subsystems have no opinion. Never rounds up to APPROVED."""

    INCONCLUSIVE = "INCONCLUSIVE"
    """A subsystem tried and could not decide."""


@dataclass(frozen=True)
class SubsystemRecord:
    """One subsystem's opinion about one candidate."""

    subsystem: Subsystem
    verdict: SubsystemVerdict
    reason: str = ""
    evidence_kind: str = "measured"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem.value,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evidence_kind": self.evidence_kind,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ConvergedVerdict:
    """The single verdict across every subsystem that has an opinion."""

    candidate_id: str
    status: ConvergenceStatus
    records: tuple[SubsystemRecord, ...]
    rejecting: tuple[str, ...] = ()
    unreconciled: tuple[str, ...] = ()
    inconclusive: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_approvable(self) -> bool:
        return self.status is ConvergenceStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "reason": self.reason,
            "rejecting": list(self.rejecting),
            "unreconciled": list(self.unreconciled),
            "inconclusive": list(self.inconclusive),
            "records": [record.to_dict() for record in self.records],
        }


class EvidenceLedger:
    """Collects subsystem verdicts per candidate and converges them.

    In-memory and process-local. Multi-worker deployments would need this on the
    shared store; the same declared limitation the journal and reservoir carry,
    stated here rather than implied.
    """

    def __init__(self, *, enabled_subsystems: Iterable[Subsystem] | None = None) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, list[SubsystemRecord]] = {}
        self._enabled = set(enabled_subsystems) if enabled_subsystems is not None else set(Subsystem)

    def configure(self, enabled: Iterable[Subsystem]) -> None:
        """Declare which subsystems are *required* to have an opinion.

        A subsystem that is not enabled is not part of the quorum, so its absence
        does not produce ``PARTIAL``. That is the difference between "not
        applicable here" and "we forgot to ask".
        """
        with self._lock:
            self._enabled = set(enabled)

    @property
    def enabled_subsystems(self) -> set[Subsystem]:
        with self._lock:
            return set(self._enabled)

    def record(self, candidate_id: str, record: SubsystemRecord) -> SubsystemRecord:
        """Record one verdict. A subsystem's later verdict replaces its earlier one.

        Replacement rather than accumulation is deliberate: a subsystem that
        re-evaluated and changed its mind must not leave both opinions on file,
        or the ledger would report a contradiction that no longer exists.
        """
        key = (candidate_id or "").strip()
        if not key:
            raise ValueError("candidate_id must be a non-empty string")
        with self._lock:
            existing = self._records.setdefault(key, [])
            for index, item in enumerate(existing):
                if item.subsystem is record.subsystem:
                    existing[index] = record
                    return record
            existing.append(record)
            return record

    def records_for(self, candidate_id: str) -> tuple[SubsystemRecord, ...]:
        with self._lock:
            return tuple(self._records.get(candidate_id, []))

    def converge(self, candidate_id: str) -> ConvergedVerdict:
        """Assemble the one verdict.

        Rules, in severity order:

        1. Any ``REJECT`` -> ``REJECTED``. Final, unappealable by vote count.
        2. Any ``INCONCLUSIVE`` -> ``INCONCLUSIVE``. A subsystem that tried and
           could not decide is not a subsystem that passed.
        3. Any **enabled** subsystem with no record -> ``PARTIAL``, named in
           ``unreconciled``.
        4. Otherwise ``APPROVED``.
        """
        with self._lock:
            records = list(self._records.get(candidate_id, []))
            enabled = set(self._enabled)

        by_subsystem = {record.subsystem: record for record in records}
        rejecting = tuple(sorted(s.value for s, r in by_subsystem.items() if r.verdict is SubsystemVerdict.REJECT))
        inconclusive = tuple(sorted(s.value for s, r in by_subsystem.items() if r.verdict is SubsystemVerdict.INCONCLUSIVE))
        unreconciled = tuple(sorted(s.value for s in enabled if s not in by_subsystem))

        if rejecting:
            names = ", ".join(rejecting)
            detail = "; ".join(r.reason for r in records if r.verdict is SubsystemVerdict.REJECT and r.reason)
            return ConvergedVerdict(
                candidate_id=candidate_id,
                status=ConvergenceStatus.REJECTED,
                records=tuple(records),
                rejecting=rejecting,
                unreconciled=unreconciled,
                inconclusive=inconclusive,
                reason=(f"{names} rejected this candidate. A rejection is final and is not outvoted by the subsystems that passed" + (f": {detail}" if detail else "")),
            )

        if inconclusive:
            names = ", ".join(inconclusive)
            return ConvergedVerdict(
                candidate_id=candidate_id,
                status=ConvergenceStatus.INCONCLUSIVE,
                records=tuple(records),
                unreconciled=unreconciled,
                inconclusive=inconclusive,
                reason=(f"{names} could not reach a verdict. A subsystem that tried and could not decide has not passed, so this is not an approval"),
            )

        if unreconciled:
            names = ", ".join(unreconciled)
            return ConvergedVerdict(
                candidate_id=candidate_id,
                status=ConvergenceStatus.PARTIAL,
                records=tuple(records),
                unreconciled=unreconciled,
                reason=(f"{names} never evaluated this candidate. Absence of a verdict is not approval, so this is PARTIAL and will not promote until every enabled subsystem has recorded one"),
            )

        return ConvergedVerdict(
            candidate_id=candidate_id,
            status=ConvergenceStatus.APPROVED,
            records=tuple(records),
            reason=f"all {len(enabled)} enabled subsystem(s) recorded an approving verdict",
        )

    def clear(self, candidate_id: str | None = None) -> int:
        """Drop records for one candidate, or all. Returns how many were removed."""
        with self._lock:
            if candidate_id is None:
                removed = sum(len(entries) for entries in self._records.values())
                self._records.clear()
                return removed
            entries = self._records.pop(candidate_id, [])
            return len(entries)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            candidates = len(self._records)
            records = sum(len(entries) for entries in self._records.values())
        return {
            "candidates": candidates,
            "records": records,
            "enabled_subsystems": sorted(s.value for s in self.enabled_subsystems),
        }


_LEDGER_LOCK = threading.Lock()
_LEDGER: EvidenceLedger | None = None


def get_evidence_ledger() -> EvidenceLedger:
    """Process-wide ledger. Cached; call :meth:`EvidenceLedger.configure` to set the quorum."""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = EvidenceLedger()
        return _LEDGER
