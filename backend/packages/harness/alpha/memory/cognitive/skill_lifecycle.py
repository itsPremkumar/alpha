"""Procedural-skill lifecycle: honest evidence, promotion, eviction, and coverage.

Skill memory is the tier that makes an agent *better at a task over time* rather
than merely better informed: Voyager's skill library (arXiv:2305.16291) is the
reference architecture, and its load-bearing rule is that a skill is admitted to
the library **only after self-verification**, so an unverified idea never
competes with a proven one. MemOS (arXiv:2507.03724) makes lifecycle tracking and
provenance first-class for the same reason.

The defect this module exists to fix
------------------------------------
`ProceduralSkill.success_rate` answers `1.0` when nothing has ever been measured
(the `total > 0` guard's else branch). That fabricated perfect score then drives
three decisions in `alpha.memory.cognitive.procedural_memory`:

* `find_matching_skills` scores `relevance * (0.5 + 0.5 * success_rate)`, so a
  never-executed skill is recalled at **full** weight — exactly the weight a
  skill proven over dozens of runs earns;
* `_enforce_capacity` trims by ascending `success_rate`, so the library can drop
  a well-tested skill for one nobody has ever run, breaking the tie with
  `created_at`;
* `to_dict()` persists `success_rate: 1.0`, publishing the fabrication.

"No evidence" and "100% success" lead to opposite decisions — the same shape as
`count: null` versus `count: 0`, which this repo already refuses to conflate.

What is asserted here instead
-----------------------------
`ProceduralSkill.effectiveness` is `None` while nothing has been measured, and
`smoothed_effectiveness` (a Laplace prior of 1 success in 2 uses) is the number
used for **ranking**. The prior puts an unproven skill in the middle of the
distribution: never above a well-tested one, but never treated as evidence of
failure either. `strength` is that ranking number, and it is what the memory
engine scores and evicts with.

`success_rate` itself is deliberately left alone and documented as a display
convenience; existing callers and tests keep working, and no second, silently
different formula is introduced underneath them.

The lifecycle
-------------
`proposed -> verified -> promoted <-> deprecated -> retired`, with every move
requiring a reason recorded on the skill. Illegal moves are refused rather than
clamped: a skill that has never been verified cannot be promoted, and one that
has never run cannot be deprecated.

Coverage-aware eviction
-----------------------
`retirement_priority` orders weakest-evidence-first, but a skill that is the
**only** one able to serve a pattern is moved to the end: dropping it removes a
capability, which is a different event from freeing a slot. This is the forgetting
side of the ledger — proactive interference grows with stored volume (SleepGate,
arXiv:2603.14517), so eviction policy is a correctness feature, not a capacity
optimisation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.memory.cognitive.models import ProceduralSkill

__all__ = [
    "DEPRECATED_STRENGTH_FLOOR",
    "MIN_PROMOTION_USES",
    "MIN_PROMOTION_EFFECTIVENESS",
    "PRIOR_USES",
    "PRIOR_SUCCESSES",
    "SKILL_LIFECYCLES",
    "LifecycleTransition",
    "SkillLifecycle",
    "SkillVerdict",
    "coverage_report",
    "evaluate",
    "rank_for_recall",
    "retirement_priority",
    "transition",
]

#: Laplace prior: one assumed success in two assumed uses. It fixes the
#: unproven point at the middle of the distribution instead of at either end,
#: which is what makes "no evidence" rankable without being a claim.
PRIOR_USES = 2
PRIOR_SUCCESSES = 1

#: A skill must have been used at least this often *and* be at least this
#: effective before it is promoted. The sample-size floor is what stops one
#: lucky run from promoting a skill.
MIN_PROMOTION_USES = 3
MIN_PROMOTION_EFFECTIVENESS = 0.70

#: Below this measured effectiveness a promoted skill is deprecated. The value
#: is deliberately below the promotion bar: demotion is not the mirror image of
#: promotion, because one bad run after ten good ones is noise and ten bad runs
#: after one good one is a pattern.
DEPRECATED_STRENGTH_FLOOR = 0.20

#: The lifecycle states, in the only order they can be traversed.
SKILL_LIFECYCLES = ("proposed", "verified", "promoted", "deprecated", "retired")


class SkillLifecycle(StrEnum):
    """Where a skill sits between "written down" and "earned its keep"."""

    PROPOSED = "proposed"
    VERIFIED = "verified"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


#: Legal moves. A missing key means "no move from here except retire".
_TRANSITIONS: dict[SkillLifecycle, frozenset[SkillLifecycle]] = {
    SkillLifecycle.PROPOSED: frozenset({SkillLifecycle.VERIFIED, SkillLifecycle.RETIRED}),
    SkillLifecycle.VERIFIED: frozenset({SkillLifecycle.PROMOTED, SkillLifecycle.DEPRECATED, SkillLifecycle.RETIRED}),
    SkillLifecycle.PROMOTED: frozenset({SkillLifecycle.DEPRECATED, SkillLifecycle.RETIRED}),
    SkillLifecycle.DEPRECATED: frozenset({SkillLifecycle.PROMOTED, SkillLifecycle.RETIRED}),
    SkillLifecycle.RETIRED: frozenset(),
}


@dataclass(frozen=True)
class SkillVerdict:
    """What the evidence currently supports for one skill.

    ``lifecycle`` is derived from the measurement, not from whatever the skill
    happens to have stored — a skill recorded as ``promoted`` that has since
    failed every run is reported as ``deprecated`` here, and ``stored_lifecycle``
    keeps the recorded value visible so the disagreement is not hidden.
    """

    lifecycle: SkillLifecycle
    stored_lifecycle: SkillLifecycle
    effectiveness: float | None
    evidence_count: int
    promotion_eligible: bool
    reason: str
    basis: str = "measured outcome"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle": self.lifecycle.value,
            "stored_lifecycle": self.stored_lifecycle.value,
            "effectiveness": self.effectiveness,
            "evidence_count": self.evidence_count,
            "promotion_eligible": self.promotion_eligible,
            "reason": self.reason,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class LifecycleTransition:
    """One auditable lifecycle move, with the reason it was made."""

    from_state: SkillLifecycle
    to_state: SkillLifecycle
    reason: str
    at: float

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_state.value, "to": self.to_state.value, "reason": self.reason, "at": self.at}


def _coerce(state: Any) -> SkillLifecycle:
    if isinstance(state, SkillLifecycle):
        return state
    if isinstance(state, str):
        try:
            return SkillLifecycle(state.strip().lower())
        except ValueError as exc:
            raise ValueError(f"unknown skill lifecycle {state!r}; expected one of {SKILL_LIFECYCLES}") from exc
    raise ValueError(f"a skill lifecycle must be a SkillLifecycle or its string value, got {type(state).__name__}")


def evaluate(
    skill: ProceduralSkill,
    *,
    min_uses: int = MIN_PROMOTION_USES,
    min_effectiveness: float = MIN_PROMOTION_EFFECTIVENESS,
    deprecation_floor: float = DEPRECATED_STRENGTH_FLOOR,
) -> SkillVerdict:
    """Derive the lifecycle the evidence supports, and whether promotion is earned.

    ``None`` effectiveness with a non-zero evidence count is impossible by
    construction (`evidence_count` is the denominator), so the unproven branch is
    the only one that can report an absent number.
    """
    if isinstance(min_uses, bool) or not isinstance(min_uses, int) or min_uses < 0:
        raise ValueError(f"min_uses must be an integer >= 0, got {min_uses!r}")
    if isinstance(min_effectiveness, bool) or not isinstance(min_effectiveness, (int, float)):
        raise ValueError(f"min_effectiveness must be a number, got {min_effectiveness!r}")
    if not 0.0 <= float(min_effectiveness) <= 1.0:
        raise ValueError(f"min_effectiveness must be within [0, 1], got {min_effectiveness!r}")

    stored = _coerce(getattr(skill, "lifecycle", "proposed") or "proposed")
    measured = skill.evidence_count
    effectiveness = skill.effectiveness

    if measured == 0 or effectiveness is None:
        return SkillVerdict(
            lifecycle=SkillLifecycle.PROPOSED,
            stored_lifecycle=stored,
            effectiveness=None,
            evidence_count=measured,
            promotion_eligible=False,
            reason="unproven: no outcome has ever been recorded, so there is no effectiveness to rank or promote on",
            basis="no measurement",
        )

    eligible = measured >= min_uses and effectiveness >= float(min_effectiveness)
    plural = "" if measured == 1 else "s"
    detail = f"{measured} measured use{plural}, {effectiveness:.0%} effective"

    if effectiveness < deprecation_floor:
        lifecycle, reason = SkillLifecycle.DEPRECATED, f"deprecated: {detail}, below the {deprecation_floor:.0%} floor"
    elif eligible:
        lifecycle = SkillLifecycle.PROMOTED
        reason = f"promoted: {detail} clears {min_effectiveness:.0%} over >= {min_uses} use{'' if min_uses == 1 else 's'}"
    else:
        lifecycle = SkillLifecycle.VERIFIED
        if measured < min_uses:
            reason = f"verified: {detail}; promotion needs {min_uses} measured uses, so the rate is not yet a ranking fact"
        else:
            reason = f"verified: {detail} is below the {min_effectiveness:.0%} promotion bar"

    return SkillVerdict(
        lifecycle=lifecycle,
        stored_lifecycle=stored,
        effectiveness=effectiveness,
        evidence_count=measured,
        promotion_eligible=eligible and lifecycle is not SkillLifecycle.DEPRECATED,
        reason=reason,
    )


def rank_for_recall(skill: ProceduralSkill, relevance: float, *, prior_uses: int = PRIOR_USES, prior_successes: int = PRIOR_SUCCESSES) -> float:
    """``relevance * strength`` — the score a skill should be recalled with.

    An unproven skill is recalled at the prior, so it neither leads a
    well-tested skill nor sits below a consistently failing one. ``relevance`` is
    the caller's own match score; this function contributes nothing to it besides
    the evidence weight, so a bad match cannot be rescued by a good record.
    """
    if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
        raise ValueError(f"relevance must be a number, got {relevance!r}")
    if not 0.0 <= float(relevance) <= 1.0:
        raise ValueError(f"relevance must be within [0, 1], got {relevance!r}")
    if isinstance(prior_uses, bool) or not isinstance(prior_uses, int) or prior_uses <= 0:
        raise ValueError(f"prior_uses must be a positive integer, got {prior_uses!r}")
    if isinstance(prior_successes, bool) or not isinstance(prior_successes, int) or not 0 <= prior_successes <= prior_uses:
        raise ValueError(f"prior_successes must be an integer in [0, prior_uses], got {prior_successes!r}")

    smoothed = (skill.success_count + prior_successes) / (skill.evidence_count + prior_uses)
    return float(relevance) * smoothed


def transition(skill: ProceduralSkill, to_state: Any, *, reason: str, at: float | None = None) -> LifecycleTransition:
    """Move a skill between lifecycle states, recording why.

    Raises for an illegal move, a missing reason, or an unknown state — a clamped
    transition would report a promotion the evidence never earned.
    """
    target = _coerce(to_state)
    current = _coerce(getattr(skill, "lifecycle", "proposed") or "proposed")
    text = (reason or "").strip()
    if not text:
        raise ValueError(f"a lifecycle transition into {target.value} requires a reason naming the evidence")
    if current is target:
        raise ValueError(f"a skill already in {current.value} cannot move to {target.value}")
    if target not in _TRANSITIONS[current]:
        allowed = sorted(item.value for item in _TRANSITIONS[current])
        raise ValueError(f"a {current.value} skill cannot move to {target.value}; permitted from here: {allowed or ['retired']}")

    skill.lifecycle = target.value
    skill.lifecycle_reason = text
    return LifecycleTransition(from_state=current, to_state=target, reason=text, at=time.time() if at is None else float(at))


def _covers(skill: ProceduralSkill, pattern: str) -> bool:
    needle = pattern.strip().lower()
    if not needle:
        return False
    haystacks = (skill.name, skill.description, skill.trigger_pattern)
    return any(needle in str(item).lower() for item in haystacks)


def coverage_report(skills: Any, *, patterns: Any = ()) -> dict[str, Any]:
    """Which patterns exactly one skill can serve, and therefore must not be evicted.

    ``unique_coverage`` maps pattern -> the single skill serving it. ``evictable_last``
    lists the skills that are somebody's last coverage: they are evicted only
    after every other candidate is gone, because removing them removes a
    capability rather than freeing a slot.
    """
    items = list(skills)
    if isinstance(patterns, (str, bytes)):
        raise ValueError("patterns must be a sequence of pattern strings, not a single string")
    needles = [str(item) for item in patterns]

    unique: dict[str, str] = {}
    for needle in needles:
        servers = [skill for skill in items if _covers(skill, needle)]
        if len(servers) == 1:
            unique[needle] = servers[0].name

    last = tuple(sorted({name for name in unique.values()}))
    return {
        "patterns": tuple(needles),
        "unique_coverage": unique,
        "evictable_last": last,
        "unserved_patterns": tuple(sorted(needle for needle in needles if not any(_covers(skill, needle) for skill in items))),
    }


def retirement_priority(skills: Any, *, patterns: Any = ()) -> list[ProceduralSkill]:
    """Order skills weakest-evidence-first, with sole-coverage skills held back.

    The returned order is an *eviction* order: the first entry is the first to
    drop. A skill that is the only server of some pattern is moved to the end so
    a capability is never the cheapest thing to delete.
    """
    items = [skill for skill in skills if isinstance(skill, ProceduralSkill)]
    if isinstance(patterns, (str, bytes)):
        raise ValueError("patterns must be a sequence of pattern strings, not a single string")
    protected_names = set(coverage_report(items, patterns=patterns)["unique_coverage"].values())

    def sort_key(skill: ProceduralSkill) -> tuple[int, float, int, float]:
        return (
            1 if skill.name in protected_names else 0,
            skill.smoothed_effectiveness,
            skill.evidence_count,
            skill.created_at,
        )

    return sorted(items, key=sort_key)
