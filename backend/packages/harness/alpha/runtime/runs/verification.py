"""Deterministic evidence checks for a run's declared acceptance criteria.

This module deliberately does not decide a run's lifecycle status.  The existing
``RunManager`` remains the only lifecycle owner; this overlay answers the separate
question of whether the completed work has sufficient recorded evidence.

Two honesty rules, both pinned by tests:

- **No criteria is not a pass.**  A result with no declared criterion proves
  nothing, so ``verified`` is ``False`` rather than a vacuous ``True``.
- **Evidence is accounted for, not silently absorbed.**  Evidence that names no
  criterion, or names one that was never declared, is reported in
  :attr:`VerificationResult.notes` instead of vanishing — a typo in a
  ``criterion_id`` must not read as coverage while hiding the proof.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CriterionVerdict:
    """Verification outcome for one declared acceptance criterion."""

    criterion_id: str
    verified: bool
    reason: str
    evidence_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    """Aggregate verification result, safe to expose in a run response.

    ``notes`` discloses evidence that could not be attributed to a declared
    criterion; it never affects ``verified`` by itself, it names what was
    dropped so a missing-evidence verdict cannot hide the reason.
    """

    verified: bool
    verdicts: tuple[CriterionVerdict, ...]
    notes: tuple[str, ...] = ()


def verify_acceptance_criteria(
    criteria: Iterable[Mapping[str, object]] | None,
    evidence: Iterable[Mapping[str, object]] | None,
) -> VerificationResult:
    """Check whether each criterion has passing evidence of every required kind.

    Evidence is intentionally declarative here.  Collectors must independently
    validate a command result, artifact digest, HTTP response, or human decision
    before adding it.  A model's prose alone is never considered evidence.

    A ``reference`` is cited provenance, not provenance this module can check:
    it records where the caller says the measurement lives, so the caller (or an
    operator) is the one who must follow it.  With no declared criteria nothing
    was verified and the result says so.
    """
    normalized_criteria = tuple(criteria or ())
    evidence_by_criterion: dict[str, list[Mapping[str, object]]] = {}
    unattributed: list[str] = []
    for item in evidence or ():
        criterion_id = item.get("criterion_id")
        if isinstance(criterion_id, str) and criterion_id:
            evidence_by_criterion.setdefault(criterion_id, []).append(item)
        else:
            unattributed.append(str(item.get("kind", "evidence")))

    verdicts: list[CriterionVerdict] = []
    for criterion in normalized_criteria:
        criterion_id = criterion.get("id")
        if not isinstance(criterion_id, str) or not criterion_id:
            raise ValueError("acceptance criterion requires a non-empty id")
        required_kinds = criterion.get("required_evidence_kinds", ())
        if not isinstance(required_kinds, (list, tuple)) or not all(isinstance(kind, str) and kind for kind in required_kinds):
            raise ValueError(f"criterion {criterion_id!r} has invalid required_evidence_kinds")

        passing = [item for item in evidence_by_criterion.get(criterion_id, []) if item.get("passed") is True]
        present_kinds = {item.get("kind") for item in passing if isinstance(item.get("kind"), str)}
        missing_kinds = [kind for kind in required_kinds if kind not in present_kinds]
        references = tuple(reference for item in passing if isinstance(reference := item.get("reference"), str) and reference)
        if missing_kinds:
            verdicts.append(CriterionVerdict(criterion_id, False, f"Missing passing evidence: {', '.join(missing_kinds)}", references))
        elif not passing:
            verdicts.append(CriterionVerdict(criterion_id, False, "No passing evidence recorded", ()))
        else:
            verdicts.append(CriterionVerdict(criterion_id, True, "Required evidence recorded", references))

    declared_ids = {criterion_id for criterion in normalized_criteria if isinstance(criterion_id := criterion.get("id"), str)}
    undeclared = sorted(criterion_id for criterion_id in evidence_by_criterion if criterion_id not in declared_ids)
    notes = [f"evidence supplied for {len(undeclared)} criterion id(s) that are not declared: {undeclared}"] if undeclared else []
    if unattributed:
        notes.append(f"evidence for {len(unattributed)} item(s) names no criterion and was ignored: {sorted(unattributed)}")

    return VerificationResult(verified=bool(verdicts) and all(verdict.verified for verdict in verdicts), verdicts=tuple(verdicts), notes=tuple(notes))
