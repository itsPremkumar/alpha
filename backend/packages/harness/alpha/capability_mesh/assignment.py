"""Roles and routing: PRIMARY / CHALLENGER / VERIFIER assignments per microtask (plan §3.4).

A :class:`MicrotaskAssignment` names one entity doing one role on one
microtask — with the acceptance criteria copied from the microtask, so the
plan's "no assignment without explicit acceptance criteria" invariant (§25)
holds on BOTH records. A :class:`RoutingPlan` is the whole picture for one
fingerprint: assignments, hard-constraint refusals, and honest notes, with
enough structure to prove nothing was silently dropped.

Five rules decide the shape of this module:

* **Exactly one PRIMARY per microtask.** Two primaries would make "the main
  contribution" ambiguous, so the plan refuses one by name.
* **A challenger is earned, not automatic.** A runner-up becomes CHALLENGER
  only when the policy wants one AND its fit is within ``CHALLENGER_MARGIN``
  of the primary's AND the primary's evidence is not already
  RECENTLY_REVALIDATED — a well-measured leader does not need a challenger.
* **A verifier must be a different competence.** When the policy wants one,
  the verifier is the best-ranked entity whose passport knows a capability
  OTHER than the primary's weakest required capability, and never the
  primary or the challenger themselves.
* **INTEGRATOR is never auto-assigned here.** Composition — not routing —
  owns integration, so no :func:`route` run ever mints an INTEGRATOR; the
  plan carries that fact as a note instead of leaving its absence to be
  wondered about.
* **Every microtask is either assigned or refused — never silently
  dropped.** :func:`route` checks its own coverage against the
  decomposition before returning, and a microtask with no eligible entity
  gets a refusal row plus a note naming it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from alpha.capability_mesh.decomposition import Decomposition, Microtask
from alpha.capability_mesh.fingerprint import SituationFingerprint
from alpha.capability_mesh.fit import FitScore, HardConstraintRefusal, rank_entities
from alpha.capability_mesh.memory import CapabilityMemory
from alpha.capability_mesh.models import CapabilityPassport, EvidenceLevel, evidence_rank
from alpha.capability_mesh.policy import DecisionPolicy, PolicyProfile, resolve_policy

#: A runner-up within this many fit points of the PRIMARY becomes the
#: CHALLENGER (when the policy wants one and the primary is not already
#: recently revalidated). A margin too wide appoints a challenger to every
#: routable task; too narrow and close calls never get a second opinion.
CHALLENGER_MARGIN = 0.15

#: The ``entity_id`` of a refusal row minted when NO candidate passport was
#: eligible — there is no entity to name, so the absence is named instead.
#: Real passport ids are namespaced ("agent:x", "model:p/m"), so this
#: sentinel can never collide with one.
NO_ELIGIBLE_ENTITY = "no-eligible-entity"

_INTEGRATOR_NOTE = "integrator role is never auto-assigned by routing: composition phase owns integration"


class Role(StrEnum):
    """One entity's responsibility on one microtask (plan §3.4)."""

    PRIMARY = "primary"
    CHALLENGER = "challenger"
    VERIFIER = "verifier"
    INTEGRATOR = "integrator"


@dataclass(frozen=True)
class MicrotaskAssignment:
    """One entity assigned one role on one microtask (plan §25).

    ``acceptance_checks`` is REQUIRED non-empty and copied from the
    microtask: an assignment without explicit acceptance criteria cannot
    exist, even if the microtask it came from somehow had none. Role-specific
    ``expected_outputs`` are REQUIRED for the same reason — an assignment
    that does not name what it must produce cannot be verified.
    ``max_attempts`` is a bound, refused below 1 rather than clamped.
    """

    microtask_id: str
    passport_id: str
    role: Role
    fit_score: float
    score_explanation: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    max_attempts: int = 1

    def __post_init__(self) -> None:
        for field in ("microtask_id", "passport_id"):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty")
        try:
            object.__setattr__(self, "role", Role(str(self.role)))
        except ValueError as error:
            allowed = ", ".join(role.value for role in Role)
            raise ValueError(f"unknown role {self.role!r}; expected one of: {allowed}") from error
        if isinstance(self.fit_score, bool) or not isinstance(self.fit_score, (int, float)) or not math.isfinite(float(self.fit_score)) or not 0.0 <= float(self.fit_score) <= 1.0:
            raise ValueError(f"fit_score must be a finite number in [0, 1], got {self.fit_score!r}")
        object.__setattr__(self, "fit_score", float(self.fit_score))
        explanation = tuple(self.score_explanation)
        for line in explanation:
            if not line:
                raise ValueError("score_explanation entries must be non-empty strings")
        object.__setattr__(self, "score_explanation", explanation)
        for field in ("expected_outputs", "acceptance_checks"):
            entries = tuple(getattr(self, field))
            if not entries:
                raise ValueError(f"{field} must be non-empty: an assignment must name what it produces and how it will be accepted")
            for entry in entries:
                if not entry:
                    raise ValueError(f"{field} entries must be non-empty strings")
            object.__setattr__(self, field, entries)
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError(f"max_attempts must be an int >= 1, got {self.max_attempts!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "microtask_id": self.microtask_id,
            "passport_id": self.passport_id,
            "role": self.role.value,
            "fit_score": self.fit_score,
            "score_explanation": list(self.score_explanation),
            "expected_outputs": list(self.expected_outputs),
            "acceptance_checks": list(self.acceptance_checks),
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MicrotaskAssignment:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            microtask_id=str(data["microtask_id"]),
            passport_id=str(data["passport_id"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            role=data["role"],
            fit_score=float(data["fit_score"]),
            score_explanation=tuple(str(line) for line in data.get("score_explanation", ())),
            expected_outputs=tuple(str(entry) for entry in data["expected_outputs"]),
            acceptance_checks=tuple(str(entry) for entry in data["acceptance_checks"]),
            max_attempts=int(data.get("max_attempts", 1)),
        )


@dataclass(frozen=True)
class RoutingPlan:
    """The complete routing result for one fingerprint: who does what, who refused, and why.

    Validation enforces what is checkable from the plan alone: at most one
    PRIMARY per microtask, no duplicate (microtask, role, entity) row, a
    CHALLENGER/VERIFIER only where a PRIMARY exists, and no microtask that
    is BOTH assigned and refused. Full coverage ("every microtask assigned
    or refused") needs the decomposition and is therefore checked inside
    :func:`route`, which has it — this record cannot invent a microtask set
    it was never given.
    """

    fingerprint: SituationFingerprint
    assignments: tuple[MicrotaskAssignment, ...]
    refusals: tuple[HardConstraintRefusal, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fingerprint, SituationFingerprint):
            raise ValueError(f"fingerprint must be a SituationFingerprint, got {self.fingerprint!r}")
        assignments = tuple(self.assignments)
        for assignment in assignments:
            if not isinstance(assignment, MicrotaskAssignment):
                raise ValueError(f"assignments must contain MicrotaskAssignment rows, got {assignment!r}")
        object.__setattr__(self, "assignments", assignments)
        refusals = tuple(self.refusals)
        for refusal in refusals:
            if not isinstance(refusal, HardConstraintRefusal):
                raise ValueError(f"refusals must contain HardConstraintRefusal rows, got {refusal!r}")
        object.__setattr__(self, "refusals", refusals)
        notes = tuple(self.notes)
        for note in notes:
            if not note:
                raise ValueError("notes entries must be non-empty strings")
        object.__setattr__(self, "notes", notes)
        if not assignments and not refusals:
            raise ValueError("a RoutingPlan must assign or refuse something: a plan that routes nothing is a silent no-op")

        by_microtask: dict[str, list[MicrotaskAssignment]] = {}
        seen_rows: set[tuple[str, str, str]] = set()
        for assignment in assignments:
            row = (assignment.microtask_id, assignment.role.value, assignment.passport_id)
            if row in seen_rows:
                raise ValueError(f"duplicate assignment: {assignment.passport_id!r} is already {assignment.role.value} on microtask {assignment.microtask_id!r}")
            seen_rows.add(row)
            by_microtask.setdefault(assignment.microtask_id, []).append(assignment)
        for microtask_id, rows in by_microtask.items():
            primaries = [row for row in rows if row.role is Role.PRIMARY]
            if len(primaries) > 1:
                ids = ", ".join(sorted(row.passport_id for row in primaries))
                raise ValueError(f"microtask {microtask_id!r} has {len(primaries)} PRIMARY assignments ({ids}): exactly one primary per microtask")
            for row in rows:
                if row.role in (Role.CHALLENGER, Role.VERIFIER) and not primaries:
                    raise ValueError(f"microtask {microtask_id!r} has a {row.role.value} but no PRIMARY: secondary roles presuppose a primary")
        refused = {refusal.microtask_id for refusal in refusals if refusal.microtask_id is not None}
        overlap = sorted(set(by_microtask) & refused)
        if overlap:
            raise ValueError(f"microtask(s) {', '.join(overlap)} are BOTH assigned and refused: one microtask has one honest fate")

    def assignments_for(self, microtask_id: str) -> tuple[MicrotaskAssignment, ...]:
        """Every assignment on one microtask, in plan order."""
        return tuple(assignment for assignment in self.assignments if assignment.microtask_id == microtask_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "assignments": [assignment.to_dict() for assignment in self.assignments],
            "refusals": [refusal.to_dict() for refusal in self.refusals],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingPlan:
        return cls(
            fingerprint=SituationFingerprint.from_dict(data["fingerprint"]),
            assignments=tuple(MicrotaskAssignment.from_dict(entry) for entry in data["assignments"]),
            refusals=tuple(HardConstraintRefusal.from_dict(entry) for entry in data.get("refusals", ())),
            notes=tuple(str(note) for note in data.get("notes", ())),
        )


def _expected_output(role: Role, microtask: Microtask) -> tuple[str, ...]:
    """One role-appropriate expected output naming the microtask's deliverable."""
    if role is Role.PRIMARY:
        return (f"completed deliverable for '{microtask.title}'",)
    if role is Role.CHALLENGER:
        return (f"challenger's independent take on '{microtask.title}'",)
    return (f"verification evidence for '{microtask.title}'",)


def _assignment(microtask: Microtask, score: FitScore, role: Role) -> MicrotaskAssignment:
    """Build one assignment, copying the acceptance criteria from the microtask."""
    return MicrotaskAssignment(
        microtask_id=microtask.microtask_id,
        passport_id=score.entity_id,
        role=role,
        fit_score=score.fit_score,
        score_explanation=score.score_explanation,
        expected_outputs=_expected_output(role, microtask),
        acceptance_checks=microtask.acceptance_checks,
        max_attempts=microtask.max_attempts,
    )


def _weakest_required(passport: CapabilityPassport, microtask: Microtask) -> str | None:
    """The primary's weakest required capability (worst evidence; ties keep declaration order)."""
    ranked: list[tuple[int, int, str]] = []
    for index, capability in enumerate(microtask.required_capabilities):
        estimate = passport.estimate_for(capability)
        ranked.append((evidence_rank(estimate.evidence_level) if estimate is not None else 0, index, capability))
    if not ranked:
        return None
    return min(ranked)[2]


def _knows_other_capability(passport: CapabilityPassport, weakest: str) -> bool:
    """Does this passport know a capability OTHER than ``weakest``?

    "Know" is a declared capability with an estimate — the evidence level
    travels in the fit score; this gate asks only whether the candidate
    brings a *different competence* to check the primary's work, which is
    what makes a verifier independent rather than a duplicate.
    """
    return any(estimate.capability != weakest for estimate in passport.capabilities)


def route(
    fingerprint: SituationFingerprint,
    decomposition: Decomposition,
    passports: Iterable[CapabilityPassport],
    *,
    policy: DecisionPolicy | PolicyProfile | str,
    memory: CapabilityMemory | None = None,
    profile_version: str,
    now: datetime | None = None,
) -> RoutingPlan:
    """Route every microtask of one decomposition to exactly one PRIMARY (plan §3).

    For each microtask: hard gates first, then deterministic ranking, then
    role selection from the resolved :class:`PolicyProfile` — PRIMARY is the
    top fit; CHALLENGER the runner-up when the policy wants one, the margin
    fits and the primary is not already RECENTLY_REVALIDATED; VERIFIER the
    best *different-competence* candidate when the policy wants one; and
    never an INTEGRATOR (composition owns that — stated in ``notes``). A
    microtask with no eligible entity gets a refusal row plus a note, and
    before returning, every microtask is proved assigned or refused — this
    function refuses to silently drop one.
    """
    if not profile_version:
        raise ValueError("route requires a non-empty profile_version (no score without its profile version)")
    if not isinstance(decomposition, Decomposition):
        raise ValueError(f"route expects a Decomposition, got {decomposition!r}")
    profile = policy if isinstance(policy, PolicyProfile) else resolve_policy(policy)
    passports_by_id = {passport.entity_id: passport for passport in passports}

    assignments: list[MicrotaskAssignment] = []
    refusals: list[HardConstraintRefusal] = []
    notes: list[str] = [_INTEGRATOR_NOTE]

    for microtask in decomposition.microtasks:
        scores, gate_refusals = rank_entities(microtask, passports_by_id.values(), memory=memory, profile_version=profile_version, fingerprint=fingerprint, now=now)
        refusals.extend(gate_refusals)
        if not scores:
            refusals.append(
                HardConstraintRefusal(
                    entity_id=NO_ELIGIBLE_ENTITY,
                    unmet=(f"no eligible entity for microtask {microtask.microtask_id!r}: every candidate failed a hard gate or none was available",),
                    microtask_id=microtask.microtask_id,
                )
            )
            notes.append(f"microtask '{microtask.microtask_id}' left unassigned: no candidate passed the hard gates")
            continue

        primary = scores[0]
        assignments.append(_assignment(microtask, primary, Role.PRIMARY))

        challenger = None
        if profile.wants_challenger:
            if len(scores) < 2:
                notes.append(f"microtask '{microtask.microtask_id}': no challenger — only one entity passed the hard gates")
            elif primary.evidence_level is EvidenceLevel.RECENTLY_REVALIDATED:
                notes.append(f"microtask '{microtask.microtask_id}': no challenger — the primary's evidence is recently_revalidated; a well-measured leader needs no second opinion")
            elif primary.fit_score - scores[1].fit_score > CHALLENGER_MARGIN:
                notes.append(f"microtask '{microtask.microtask_id}': no challenger — runner-up {scores[1].entity_id} trails by {primary.fit_score - scores[1].fit_score:.3f}, beyond the challenger margin {CHALLENGER_MARGIN}")
            else:
                challenger = scores[1]
                assignments.append(_assignment(microtask, challenger, Role.CHALLENGER))

        if profile.wants_verifier:
            weakest = _weakest_required(passports_by_id[primary.entity_id], microtask)
            if weakest is None:
                notes.append(f"microtask '{microtask.microtask_id}': no verifier — no required capability for a verifier to differ from")
            else:
                used = {primary.entity_id} | ({challenger.entity_id} if challenger is not None else set())
                verifier_score = next((score for score in scores if score.entity_id not in used and _knows_other_capability(passports_by_id[score.entity_id], weakest)), None)
                if verifier_score is None:
                    notes.append(f"microtask '{microtask.microtask_id}': no verifier — no other eligible entity knows a capability distinct from the primary's weakest '{weakest}'")
                else:
                    assignments.append(_assignment(microtask, verifier_score, Role.VERIFIER))

    covered = {assignment.microtask_id for assignment in assignments} | {refusal.microtask_id for refusal in refusals if refusal.microtask_id}
    dropped = [microtask.microtask_id for microtask in decomposition.microtasks if microtask.microtask_id not in covered]
    if dropped:
        # Internal invariant: the loop above assigns or refuses every
        # microtask, so this cannot fire — unless someone "simplifies" the
        # loop into silently skipping one. Fail loud, never drop work.
        raise RuntimeError(f"routing dropped microtask(s): {', '.join(dropped)} — every microtask must be assigned or refused")

    return RoutingPlan(fingerprint=fingerprint, assignments=tuple(assignments), refusals=tuple(refusals), notes=tuple(notes))


__all__ = [
    "CHALLENGER_MARGIN",
    "NO_ELIGIBLE_ENTITY",
    "MicrotaskAssignment",
    "Role",
    "RoutingPlan",
    "route",
]
