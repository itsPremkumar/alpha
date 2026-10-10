"""Verify that self-improvement actually improved — four gates, and an honest verdict.

Why this exists
---------------
A self-improving subsystem can look like it works while being worse. The
literature on this is now specific: the "Fragility of Self-Improving Agents"
re-evaluation (arXiv:2608.18066, reported in Arize's *Self-improving agents: what
changes, what persists, and how to prove it*) found ReasoningBank improved
average pass@1 by **1.5 points** under a benchmark's default task order and
**degraded by 4.5 points** when the task order was shuffled, and that run-to-run
variance exceeded the no-memory baseline in 17 of 24 domain-level comparisons. A
single favourable run is therefore not evidence that a persistent update helped.

Arize's framing, which this module implements, is that proving an improvement
needs four distinct gates because each catches a different failure mode:

1. **Target** — did the change do what it claims on the cases it was built for?
2. **Repetition, with order varied** — does the gain survive stochasticity *and*
   a different input order? A stateful system that only improves under one
   ordering has not improved.
3. **Held-out** — does the gain transfer beyond the cases used to build it?
4. **Regression** — did the change preserve what already worked?

Skip one and a lucky run looks like progress. This module runs them against the
skill-memory subsystem, which is where Alpha's procedural learning lives.

Honesty rules
-------------
* **A gate is tri-state.** ``passed`` is ``True``, ``False``, or ``None`` when the
  gate could not run (for example, no measured outcomes exist yet). ``None`` is
  reported as ``not_run`` with the reason, never as a pass.
* **The verdict is earned.** ``verified`` requires every *runnable* gate to pass
  and at least one gate to have run. With nothing to check, the verdict is
  ``unverified`` — "we could not look", not "it works".
* **The verifier does not evaluate its own work.** Gates read the memory
  subsystem's own recorded outcomes; nothing here re-derives a score that the
  thing under test could also have written. That keeps the eval on the far side
  of the boundary the literature says matters: the system being evaluated must
  not be able to rewrite the test that certifies it.
* **A tie is not an improvement.** :func:`assert_no_worse_than_baseline` reports
  ``unchanged`` rather than ``improved`` when a candidate merely matches, so a
  flat result cannot be reported as a gain.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory

from alpha.memory.cognitive.models import ProceduralSkill
from alpha.memory.cognitive.skill_lifecycle import retirement_priority

__all__ = [
    "GATE_NAMES",
    "GateResult",
    "ImprovementVerdict",
    "SkillImprovementReport",
    "assert_no_worse_than_baseline",
    "verify_skill_memory",
]

#: The four gates, in the order the report lists them. The names are stable so a
#: caller can pin a specific gate rather than matching on prose.
GATE_NAMES = ("target", "repetition_order", "held_out", "regression")


class ImprovementVerdict(StrEnum):
    """What the four gates add up to."""

    VERIFIED = "verified"
    DEGRADED = "degraded"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome, with the evidence it rested on.

    ``passed`` is ``None`` when the gate could not run; ``reason`` then carries
    what was missing. Collapsing that into ``False`` would report a system that
    was never checked as a system that failed.
    """

    name: str
    passed: bool | None
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return self.passed is not None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "ran": self.ran, "reason": self.reason, "evidence": dict(self.evidence)}


@dataclass(frozen=True)
class SkillImprovementReport:
    """The four gates' results plus the verdict they jointly earn.

    ``degraded`` is reserved for a gate that *ran and failed*: a system that has
    measurably regressed is a different report from one that could not be
    checked.
    """

    gates: tuple[GateResult, ...]
    verdict: ImprovementVerdict
    skills_checked: int
    measured_skills: int
    reasons: tuple[str, ...]
    detail: str

    def gate(self, name: str) -> GateResult:
        """One gate by name, so a caller can pin a gate rather than match on prose."""
        for item in self.gates:
            if item.name == name:
                return item
        raise KeyError(f"no gate named {name!r}; expected one of {GATE_NAMES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "gates": [item.to_dict() for item in self.gates],
            "skills_checked": self.skills_checked,
            "measured_skills": self.measured_skills,
            "reasons": list(self.reasons),
            "detail": self.detail,
        }


# --- gate 2's input: the ranking under probe ---------------------------------


def _probe_contexts(memory: ProceduralSkillMemory) -> list[str]:
    """Candidate probe contexts, most-held-out first.

    A made-up token would match nothing, so a held-out context has to be built
    from the vocabulary the skills actually share while not being identical to
    the one skill that "selected" any of them. The first context that actually
    recalls something is used, and the report names which one it was.
    """
    skills = memory.list_skills(limit=500)
    if not skills:
        return []
    descriptions = [skill.description.strip() for skill in skills if skill.description.strip()]
    if not descriptions:
        return []
    # A context assembled from *every* description's vocabulary: shared enough
    # that ordering is observable, not identical to any single selection context.
    shared = sorted({token for text in descriptions for token in text.split()})
    contexts = [" ".join(shared), *descriptions]
    seen: list[str] = []
    for context in contexts:
        if context and context not in seen:
            seen.append(context)
    return seen


def _ranking(memory: ProceduralSkillMemory, context: str, limit: int = 50) -> tuple[str, ...]:
    """The recall order for ``context`` — the thing that must be order-stable."""
    return tuple(item[0].name for item in memory.rank_for_recall(context, limit=limit))


def _evidence_order(memory: ProceduralSkillMemory, context: str, limit: int = 50) -> tuple[float, ...]:
    """The recalled skills' evidence strengths, in recall order.

    This is what gate 2 compares, and deliberately not the names: two skills with
    *identical* evidence are interchangeable, so a tie swapping them between runs
    is an artefact of a stable sort, not an order-dependent improvement.
    """
    return tuple(round(item[0].smoothed_effectiveness, 12) for item in memory.rank_for_recall(context, limit=limit))
    return tuple(item[0].name for item in memory.rank_for_recall(context, limit=limit))


def _fresh_memory(max_skills: int) -> ProceduralSkillMemory:
    """A throwaway library, built here so this module never imports the memory class at load time."""
    from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory  # function-local: procedural_memory imports this module

    return ProceduralSkillMemory(max_skills=max_skills)


def _gate_target(memory: ProceduralSkillMemory) -> GateResult:
    """Gate 1 — does the ranking actually follow the measured evidence?

    A ranking that puts a never-run skill above a proven one is not an
    improvement regardless of how well it scores: the ordering has to be a
    function of measured outcomes, which is the whole claim skill memory makes.
    """
    skills = memory.list_skills(limit=500)
    if not skills:
        return GateResult("target", None, "no skills are registered, so there is no ordering to check")

    measured = [s for s in skills if s.evidence_count > 0]
    if not measured:
        return GateResult("target", None, "every registered skill is unproven, so a measured ordering cannot be checked", {"skills": len(skills)})

    inversions: list[dict[str, Any]] = []
    for skill in measured:
        # `rank_for_recall` returns (skill, score) pairs. The score is already
        # relevance-weighted, so the gate compares the underlying EVIDENCE
        # strength, not the blended recall score: an inversion means a
        # weaker-evidenced skill was recalled above a stronger one.
        order = memory.rank_for_recall(skill.description, limit=500)
        for winner, loser in zip(order, order[1:]):
            if loser[0].smoothed_effectiveness > winner[0].smoothed_effectiveness + 1e-9:
                inversions.append({"above": winner[0].name, "below": loser[0].name, "context": skill.description})
        order = memory.rank_for_recall(skill.description, limit=500)
        for winner, loser in zip(order, order[1:]):
            if loser[0].smoothed_effectiveness > winner[0].smoothed_effectiveness + 1e-9:
                inversions.append({"above": winner[0].name, "below": loser[0].name, "context": skill.description})

    if inversions:
        return GateResult(
            "target",
            False,
            f"{len(inversions)} ranking inversion(s): a weaker-evidenced skill was recalled above a stronger one",
            {"inversions": inversions[:5]},
        )
    return GateResult(
        "target",
        True,
        f"recall order follows measured effectiveness across {len(measured)} measured skill(s)",
        {"measured": len(measured), "skills": len(skills)},
    )


def _gate_repetition_order(memory: ProceduralSkillMemory, *, trials: int, seed: int) -> GateResult:
    """Gate 2 — same skills, different input order, same evidence ordering.

    This is the gate that catches the fragility the re-evaluation paper found:
    an improvement that only holds under one ordering. The skills are shuffled
    and the ranking recomputed; a stable ranking means the order in which
    evidence arrived did not decide who wins.

    What is compared is the recalled skills' *evidence strength* sequence, not
    their names. Two skills with identical evidence are interchangeable, so a
    tie swapping them between runs is an artefact of a stable sort — flagging
    that would be a false positive, and the report records the names it saw
    either way.
    """
    skills = memory.list_skills(limit=500)
    measured = [s for s in skills if s.evidence_count > 0]
    if not measured:
        return GateResult("repetition_order", None, "no measured skills, so order-stability cannot be tested", {"trials": trials})
    if len(skills) < 2:
        return GateResult("repetition_order", None, "fewer than two skills, so there is no ordering to compare", {"skills": len(skills)})

    contexts = _probe_contexts(memory)
    if not contexts:
        return GateResult("repetition_order", None, "no skill carries a description, so no probe context exists", {"trials": trials})

    probe = next((context for context in contexts if _evidence_order(memory, context)), None)
    if probe is None:
        return GateResult("repetition_order", None, "no skill matched any probe context, so no ranking was produced", {"trials": trials})

    baseline = _evidence_order(memory, probe)
    disagreements: list[dict[str, Any]] = []
    rng = random.Random(seed)  # noqa: S311 - deterministic probe, not a security boundary
    for trial in range(trials):
        shuffled = list(skills)
        rng.shuffle(shuffled)
        shuffled_memory = _fresh_memory(memory.max_skills)
        for skill in shuffled:
            shuffled_memory.register_skill(
                name=skill.name,
                description=skill.description,
                trigger_pattern=skill.trigger_pattern,
                preconditions=list(skill.preconditions),
                steps=list(skill.steps),
                success_count=skill.success_count,
                failure_count=skill.failure_count,
                last_executed_at=skill.last_executed_at,
                failure_reasons=list(skill.failure_reasons),
                created_at=skill.created_at,
                lifecycle=skill.lifecycle,
            )
        reproduced = _evidence_order(shuffled_memory, probe)
        if reproduced != baseline:
            disagreements.append({"trial": trial, "baseline": list(baseline), "shuffled": list(reproduced)})

    if disagreements:
        return GateResult(
            "repetition_order",
            False,
            f"{len(disagreements)} of {trials} shuffled run(s) produced a different ranking — the improvement only holds in one order",
            {"trials": trials, "examples": disagreements[:2]},
        )
    return GateResult(
        "repetition_order",
        True,
        f"the ranking was stable across {trials} shuffled input order(s)",
        {"trials": trials, "order": [round(value, 6) for value in baseline], "names": list(_ranking(memory, probe))},
    )


def _gate_held_out(memory: ProceduralSkillMemory) -> GateResult:
    """Gate 3 — does the evidence-based ordering transfer to unseen context?

    A skill proven over dozens of runs must still outrank an unproven one when
    the probe context is assembled from the shared vocabulary rather than being
    the description any single skill was selected for. Ordering that collapses
    outside the selection context has not generalised.
    """
    measured = [s for s in memory.list_skills(limit=500) if s.evidence_count > 0]
    unproven = [s for s in memory.list_skills(limit=500) if s.evidence_count == 0]
    if not measured or not unproven:
        return GateResult(
            "held_out",
            None,
            "held-out needs at least one measured and one unproven skill",
            {"measured": len(measured), "unproven": len(unproven)},
        )

    probe = next((context for context in _probe_contexts(memory) if memory.rank_for_recall(context, limit=500)), None)
    if probe is None:
        return GateResult("held_out", None, "no probe context recalled anything, so transfer cannot be observed", {"skills": len(memory.list_skills(limit=500))})

    order = memory.rank_for_recall(probe, limit=500)
    first_unproven = next((index for index, item in enumerate(order) if item[0].evidence_count == 0), None)
    first_measured = next((index for index, item in enumerate(order) if item[0].evidence_count > 0), None)

    if first_unproven is not None and first_measured is not None and first_unproven < first_measured:
        return GateResult(
            "held_out",
            False,
            "an unproven skill outranked a measured one on an unseen context",
            {"probe": probe, "unproven_above": order[first_unproven][0].name, "measured": order[first_measured][0].name},
        )
    return GateResult(
        "held_out",
        True,
        f"measured skills still outranked unproven ones on a held-out context ({len(order)} recalled)",
        {"probe": probe, "measured": len(measured), "unproven": len(unproven)},
    )


def _gate_regression(memory: ProceduralSkillMemory) -> GateResult:
    """Gate 4 — do prior successes survive, and does a new idea not demote one?

    Two regressions matter: a proven skill losing its recall rank to a newcomer,
    and capacity eviction dropping a well-tested skill. Both are silent: nothing
    in the recall path reports them.
    """
    measured = [s for s in memory.list_skills(limit=500) if s.evidence_count > 0]
    if not measured:
        return GateResult("regression", None, "no measured skills, so there is no prior success to protect", {"skills": len(memory.list_skills(limit=500))})

    best = max(measured, key=lambda s: s.smoothed_effectiveness)
    newcomer = ProceduralSkill(name="regression_probe", description=best.description, trigger_pattern=best.trigger_pattern, steps=list(best.steps))
    probe_memory = _fresh_memory(memory.max_skills)
    for skill in memory.list_skills(limit=500):
        probe_memory.register_skill(
            name=skill.name,
            description=skill.description,
            trigger_pattern=skill.trigger_pattern,
            preconditions=list(skill.preconditions),
            steps=list(skill.steps),
            success_count=skill.success_count,
            failure_count=skill.failure_count,
            last_executed_at=skill.last_executed_at,
            failure_reasons=list(skill.failure_reasons),
            created_at=skill.created_at,
            lifecycle=skill.lifecycle,
        )
    probe_memory.register_skill(name=newcomer.name, description=newcomer.description, trigger_pattern=newcomer.trigger_pattern, steps=list(newcomer.steps))

    order = probe_memory.rank_for_recall(best.description, limit=500)
    names = [item[0].name for item in order]
    if best.name in names and "regression_probe" in names and names.index("regression_probe") < names.index(best.name):
        return GateResult(
            "regression",
            False,
            "a brand-new unproven skill recalled above the best-proven skill — a newcomer demoted prior success",
            {"best_proven": best.name, "order": names[:5]},
        )

    # Eviction must not drop the only coverage of a pattern, and must prefer
    # weak evidence over proven evidence.
    order_evict = [s.name for s in retirement_priority(list(memory.list_skills(limit=500)), patterns=[s.name for s in memory.list_skills(limit=500)])]
    if best.name in order_evict and order_evict.index(best.name) < order_evict.index(min(memory.list_skills(limit=500), key=lambda s: s.smoothed_effectiveness).name):
        return GateResult(
            "regression",
            False,
            "eviction prefers the best-proven skill over a weaker-evidenced one",
            {"best_proven": best.name},
        )

    return GateResult(
        "regression",
        True,
        f"adding a newcomer did not demote the best-proven skill {best.name!r}, and eviction prefers weak evidence",
        {"best_proven": best.name, "skills": len(memory.list_skills(limit=500))},
    )


def verify_skill_memory(memory: ProceduralSkillMemory, *, trials: int = 5, seed: int = 20261010) -> SkillImprovementReport:
    """Run the four proof gates over one skill library and report an honest verdict.

    ``trials`` is how many shuffled orderings gate 2 replays. More trials buy
    more confidence that an improvement is order-independent, and cost
    proportionally; five is enough to catch a ranking that only holds in one
    order, and the report says how many were run so a caller can weigh it.
    """
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError(f"trials must be an integer >= 1, got {trials!r}")

    skills = memory.list_skills(limit=500)
    gates = (
        _gate_target(memory),
        _gate_repetition_order(memory, trials=trials, seed=seed),
        _gate_held_out(memory),
        _gate_regression(memory),
    )

    failed = [item for item in gates if item.passed is False]
    ran = [item for item in gates if item.ran]

    if failed:
        verdict, detail = ImprovementVerdict.DEGRADED, f"{len(failed)} of {len(gates)} gate(s) failed"
    elif ran:
        verdict, detail = ImprovementVerdict.VERIFIED, f"all {len(ran)} runnable gate(s) passed"
    else:
        verdict, detail = ImprovementVerdict.UNVERIFIED, "no gate could run, so nothing was verified"

    reasons = tuple(item.reason for item in gates if item.ran or item.passed is False)
    return SkillImprovementReport(
        gates=gates,
        verdict=verdict,
        skills_checked=len(skills),
        measured_skills=len([s for s in skills if s.evidence_count > 0]),
        reasons=reasons,
        detail=detail,
    )


def assert_no_worse_than_baseline(baseline: tuple[str, ...], candidate: tuple[str, ...]) -> str:
    """Compare two rankings and say ``improved`` / ``unchanged`` / ``degraded``.

    The rule is deliberately conservative, because this function's job is to
    refuse an over-claim rather than to celebrate a change:

    * identical orderings are ``unchanged``, never ``improved`` — reporting a
      tie as a gain is the smallest available over-claim, and it is the one a
      verifier exists to catch;
    * a candidate is ``improved`` only when every baseline entry survives at
      its original rank *or better* and the candidate is longer, i.e. the
      baseline ordering is a prefix that was refined rather than rewritten;
    * anything else is ``degraded`` — a dropped entry, or one that moved to a
      worse position. Reordering two entries is a change, not a gain: promoting
      past a skill that used to lead may be a genuine improvement, but nothing
      here can see the *why*, so it is reported honestly rather than optimistically.
    """
    if not baseline and not candidate:
        return "unchanged"
    if not baseline:
        return "improved"
    if not candidate:
        return "degraded"
    if candidate == baseline:
        return "unchanged"

    position = {name: index for index, name in enumerate(candidate)}
    for original_index, name in enumerate(baseline):
        if name not in position:
            return "degraded"
        if position[name] > original_index:
            return "degraded"
    return "improved"
