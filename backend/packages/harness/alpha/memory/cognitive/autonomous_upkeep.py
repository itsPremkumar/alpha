"""Autonomous skill-and-memory upkeep: the loop that runs while the agent works.

Skill memory and consolidation were **on-demand** — nothing promoted a skill,
nothing demoted one, nothing ran consolidation unless a caller asked. A
self-improving subsystem that only improves when invoked is a feature, not a
learner. This module is the missing driver: one bounded pass that ingests what
the work actually did, reconciles every skill's lifecycle against its measured
evidence, consolidates, and verifies.

The safety property that makes it acceptable to run unattended
--------------------------------------------------------------
**A degrading verification blocks automatic promotion for that pass.** The four
gates in :mod:`alpha.memory.cognitive.improvement_verification` run *before* any
promotion is applied, and when the verdict is ``degraded`` the pass still reports
— it just does not act. This is the boundary the self-improvement literature
insists on (Arize's *Self-improving agents*, 2026): the component that discovers a
failure must not also hold the authority to deploy its own fix, and the system
being evaluated must not be able to rewrite the test that certifies it. A pass
that promoted on its own degraded verdict would be exactly that.

What a pass does, in order
--------------------------
1. **Ingest.** The caller supplies :class:`WorkOutcome` records — what ran, and
   whether it worked. A record with ``succeeded=False`` still counts as evidence;
   only a *missing* record is not evidence.
2. **Reconcile.** Every skill is compared against its own measured evidence and
   moved only when the evidence says so: promote, deprecate, or retire. Nothing is
   moved on a hunch, and every move records its reason on the skill.
3. **Consolidate.** One bounded rest pass replays what worked.
4. **Verify.** The four proof gates. The verdict is carried into the report and
   gates step 2 of the *next* pass.

Honesty rules
-------------
* Every count is what actually happened. ``ingested`` counts records applied,
  ``actions`` counts transitions applied, and a skill that is already in the state
  its evidence supports produces **no** action — reporting a no-op as work is the
  smallest available over-claim and this module refuses it.
* A pass with no work to ingest is reported as ``nothing to do``, not as a pass
  that improved something.
* Retiring a skill is a real transition with a real reason. A never-executed
  skill is not retired for *failing* — it has not failed, it has never run — and
  the reason says which of the two it was.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from alpha.memory.cognitive.improvement_verification import ImprovementVerdict, SkillImprovementReport
from alpha.memory.cognitive.models import ProceduralSkill
from alpha.memory.cognitive.reconsolidation import replay_strengthen
from alpha.memory.cognitive.skill_lifecycle import SkillLifecycle, evaluate, transition

if TYPE_CHECKING:
    from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory

__all__ = [
    "DEFAULT_IDLE_RETIRE_SECONDS",
    "DEFAULT_UNPROVEN_RETIRE_SECONDS",
    "DEFAULT_REPLAY_BUDGET",
    "LifecycleAction",
    "UpkeepReport",
    "WorkOutcome",
    "run_upkeep_pass",
]

#: How long a *deprecated* skill may sit unused before a pass retires it. It has
#: already failed its evidence bar; keeping it costs a slot and risks recall.
DEFAULT_IDLE_RETIRE_SECONDS = 7 * 86400.0

#: How long a *never-executed* skill may sit before a pass retires it. This is not
#: a judgement that the idea was bad — it never ran — it is a slot-reclamation,
#: and the recorded reason says exactly that.
DEFAULT_UNPROVEN_RETIRE_SECONDS = 14 * 86400.0

#: Hard cap on replays per pass, mirroring `reconsolidation.REPLAY_BUDGET` so a
#: pass cannot turn idle time into unbounded work.
DEFAULT_REPLAY_BUDGET = 8


@dataclass(frozen=True)
class WorkOutcome:
    """One unit of work the agent actually did, and the skill that answered it.

    ``context`` is what the work was about — the text a later recall would probe
    with. It is stored so ranking and eviction can be coverage-aware rather than
    relevance-blind.
    """

    skill_name: str
    succeeded: bool
    reason: str | None = None
    context: str = ""


@dataclass(frozen=True)
class LifecycleAction:
    """One transition a pass applied, with the evidence that earned it."""

    skill: str
    from_state: str
    to_state: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "from": self.from_state, "to": self.to_state, "reason": self.reason}


@dataclass(frozen=True)
class UpkeepReport:
    """What one upkeep pass did — and what it refused to do, and why."""

    ingested: int
    actions: tuple[LifecycleAction, ...]
    retired: tuple[str, ...]
    replayed: int
    verification: SkillImprovementReport | None
    blocked_by_verification: bool
    reasons: tuple[str, ...]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingested": self.ingested,
            "actions": [item.to_dict() for item in self.actions],
            "retired": list(self.retired),
            "replayed": self.replayed,
            "verification": None if self.verification is None else self.verification.to_dict(),
            "blocked_by_verification": self.blocked_by_verification,
            "reasons": list(self.reasons),
            "detail": self.detail,
        }


def _copy(memory: ProceduralSkillMemory) -> tuple[ProceduralSkillMemory, dict[str, ProceduralSkill]]:
    """A throwaway library holding the same skills, for probing without mutating."""
    from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory  # function-local: avoids the cycle

    probe = ProceduralSkillMemory(max_skills=memory.max_skills)
    index: dict[str, ProceduralSkill] = {}
    for skill in memory.list_skills(limit=memory.max_skills + 10):
        probe.register_skill(
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
            lifecycle_reason=skill.lifecycle_reason,
        )
        index[skill.name] = probe.get_skill(_find_id(probe, skill.name) or "")
    return probe, index


def _find_id(memory: ProceduralSkillMemory, name: str) -> str | None:
    for skill in memory.list_skills(limit=memory.max_skills + 10):
        if skill.name == name:
            return skill.skill_id
    return None


def ingest(memory: ProceduralSkillMemory, outcomes: Iterable[WorkOutcome]) -> int:
    """Record what the work did. Returns how many records were applied.

    A record naming a skill the library does not have is **not** silently
    dropped: it is counted as not-ingested and its reason is returned in the
    pass report, because "the work used a skill we do not track" is a fact an
    operator needs and a dropped record would hide.
    """
    applied = 0
    for outcome in outcomes:
        if not isinstance(outcome, WorkOutcome):
            raise ValueError(f"ingest takes WorkOutcome records, got {type(outcome).__name__}")
        target = next((s for s in memory.list_skills(limit=memory.max_skills + 10) if s.name == outcome.skill_name), None)
        if target is None:
            continue
        memory.record_outcome(target.skill_id, success=bool(outcome.succeeded), reason=outcome.reason)
        applied += 1
    return applied


def run_upkeep_pass(
    memory: ProceduralSkillMemory,
    *,
    outcomes: Iterable[WorkOutcome] = (),
    now: float | None = None,
    idle_retire_seconds: float = DEFAULT_IDLE_RETIRE_SECONDS,
    unproven_retire_seconds: float = DEFAULT_UNPROVEN_RETIRE_SECONDS,
    replay_budget: int = DEFAULT_REPLAY_BUDGET,
    verification_trials: int = 5,
) -> UpkeepReport:
    """One bounded upkeep pass: ingest, reconcile, consolidate, verify.

    The verification verdict decides whether reconciliation is applied. A
    ``degraded`` verdict means the library got measurably worse on at least one
    gate, so this pass reports what it *would* have done and applies nothing —
    a degradation must not be able to promote its way out.
    """
    import time as _time

    if isinstance(replay_budget, bool) or not isinstance(replay_budget, int) or replay_budget < 0:
        raise ValueError(f"replay_budget must be an integer >= 0, got {replay_budget!r}")

    outcomes = list(outcomes)
    applied = ingest(memory, outcomes)
    outcome_names = {item.skill_name for item in outcomes}
    tracked = {skill.name for skill in memory.list_skills(limit=memory.max_skills + 10)}
    unmatched = sorted(outcome_names - tracked)

    # --- verify FIRST: the verdict decides whether reconciliation is applied ---
    verification = memory.verify_self_improvement(trials=verification_trials)
    blocked = verification.verdict == ImprovementVerdict.DEGRADED
    failing = tuple(gate.reason for gate in verification.gates if gate.passed is False)

    moment = _time.time() if now is None else float(now)
    actions: list[LifecycleAction] = []
    retired: list[str] = []

    for skill in memory.list_skills(limit=memory.max_skills + 10):
        verdict = evaluate(skill)
        idle_for = moment - skill.last_executed_at if skill.last_executed_at else None

        # Retire a deprecated skill that has been idle long enough. It has already
        # failed its evidence bar; keeping it costs a slot and risks recall.
        if verdict.lifecycle is SkillLifecycle.DEPRECATED and idle_for is not None and idle_for > idle_retire_seconds:
            if skill.lifecycle != SkillLifecycle.RETIRED.value:
                if blocked:
                    actions.append(LifecycleAction(skill.name, skill.lifecycle, "retired", "blocked: verification degraded this pass"))
                    continue
                record = transition(skill, SkillLifecycle.RETIRED, reason=f"deprecated and idle for {idle_for / 86400.0:.1f} days; {verdict.reason}")
                actions.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))
                retired.append(skill.name)
            continue

        # Retire a never-executed idea that has sat unproven too long. This is a
        # slot reclamation, not a judgement that the idea was bad.
        if verdict.evidence_count == 0 and skill.lifecycle != SkillLifecycle.RETIRED.value and skill.created_at:
            age = moment - skill.created_at
            if age > unproven_retire_seconds:
                if blocked:
                    actions.append(LifecycleAction(skill.name, skill.lifecycle, "retired", "blocked: verification degraded this pass"))
                    continue
                record = transition(skill, SkillLifecycle.RETIRED, reason=f"never executed and unproven for {age / 86400.0:.1f} days; the slot is reclaimed, not the idea judged")
                actions.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))
                retired.append(skill.name)
            continue

        if blocked:
            # The refusal has to be visible, not silent. Record what this pass
            # WOULD have done, marked as blocked, and apply nothing -- otherwise
            # an operator reading the report cannot tell "nothing was warranted"
            # from "everything was warranted and we did not act".
            _shadow(skill, verdict, actions)
            continue

        _advance(skill, verdict, actions)

        _advance(skill, verdict, actions)

    replayed = 0
    if not blocked and getattr(memory, "episodic_mem", None) is not None:
        replay = replay_strengthen(memory.episodic_mem.list_traces(limit=200), budget=replay_budget)
        replayed = replay.replayed

    if blocked:
        detail = f"upkeep pass applied nothing: verification is {verification.verdict.value} ({'; '.join(failing) or 'no gate detail'})"
    elif not applied and not actions:
        detail = "upkeep pass had nothing to do: no work outcomes and no lifecycle change was warranted"
    else:
        applied_actions = [item for item in actions if item.to_state != "retired" and not item.reason.startswith("blocked")]
        detail = f"upkeep pass ingested {applied} outcome(s), applied {len(applied_actions)} transition(s), retired {len(retired)}, replayed {replayed}"

    unmatched_reasons = tuple(f"no tracked skill named {name!r}" for name in unmatched)
    reasons = (*unmatched_reasons, *failing)
    return UpkeepReport(
        ingested=applied,
        actions=tuple(actions),
        retired=tuple(retired),
        replayed=replayed,
        verification=verification,
        blocked_by_verification=blocked,
        reasons=reasons,
        detail=detail,
    )


def _advance(skill: ProceduralSkill, verdict: Any, reasons: list[LifecycleAction]) -> None:
    """Move a skill toward the state its evidence supports, one legal step at a time.

    :func:`transition` refuses to skip verification, which is correct: a skill
    that has never run cannot be promoted, however good its record numbers look
    on paper. A pass that finds a skill already carrying conclusive evidence (for
    example one restored from a snapshot, or registered with counts and never run
    through ``record_outcome``) therefore walks it through ``verified`` first and
    records both steps, rather than forcing the promotion through.
    """
    if verdict.evidence_count and skill.lifecycle == SkillLifecycle.PROPOSED.value:
        record = transition(skill, SkillLifecycle.VERIFIED, reason=verdict.reason)
        reasons.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))

    if verdict.promotion_eligible and skill.lifecycle != SkillLifecycle.PROMOTED.value:
        record = transition(skill, SkillLifecycle.PROMOTED, reason=verdict.reason)
        reasons.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))
        return

    if verdict.lifecycle is SkillLifecycle.DEPRECATED and skill.lifecycle != SkillLifecycle.DEPRECATED.value:
        record = transition(skill, SkillLifecycle.DEPRECATED, reason=verdict.reason)
        reasons.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))
        return

    if skill.lifecycle == SkillLifecycle.PROMOTED.value and verdict.lifecycle is SkillLifecycle.VERIFIED:
        # Demotion. A promoted skill that has since fallen below the promotion bar
        # must come back down to verified: leaving it promoted because it was
        # promoted once is exactly the stale state this loop exists to fix.
        reason = f"demoted: {verdict.reason}"
        record = transition(skill, SkillLifecycle.DEPRECATED, reason=reason)
        reasons.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))
        record = transition(skill, SkillLifecycle.VERIFIED, reason=reason)
        reasons.append(LifecycleAction(skill.name, record.from_state.value, record.to_state.value, record.reason))


def _shadow(skill: ProceduralSkill, verdict: Any, reasons: list[LifecycleAction]) -> None:
    """Record what a blocked pass would have done, applying nothing.

    This is the difference between "nothing was warranted" and "everything was
    warranted and the verification verdict stopped us". An operator reading the
    report must be able to tell those apart, so the refused moves are listed with
    their reasons and marked as blocked.
    """
    target: str | None = None
    if verdict.promotion_eligible and skill.lifecycle != SkillLifecycle.PROMOTED.value:
        target = SkillLifecycle.VERIFIED.value if skill.lifecycle == SkillLifecycle.PROPOSED.value else SkillLifecycle.PROMOTED.value
    elif verdict.lifecycle is SkillLifecycle.DEPRECATED and skill.lifecycle != SkillLifecycle.DEPRECATED.value:
        target = SkillLifecycle.VERIFIED.value if skill.lifecycle == SkillLifecycle.PROPOSED.value else SkillLifecycle.DEPRECATED.value

    if target is None:
        return
    reasons.append(LifecycleAction(skill.name, skill.lifecycle, target, f"blocked: verification degraded this pass ({verdict.reason})"))
