"""Procedural-skill lifecycle: honest evidence, promotion, and eviction.

The defect these tests pin
--------------------------
`ProceduralSkill.success_rate` returns **1.0 when no outcome has ever been
recorded** (`models.py`: `total > 0` guard, else `1.0`). That default then
flows into three decisions that are not cosmetic:

* `find_matching_skills` multiplies relevance by ``(0.5 + 0.5 * success_rate)``,
  so a never-executed skill is ranked with **full** weight — the same weight a
  skill with a 100%-over-many-runs record earns;
* `_enforce_capacity` sorts by ``success_rate`` when trimming, so a brand-new
  skill ties with a proven one and the tie is broken by ``created_at`` — i.e.
  the library can drop a well-tested skill and keep one nobody has ever run;
* `to_dict()`` serialises ``success_rate: 1.0``, which is a fabricated
  measurement rather than a disclosure of an absence.

This is the same class of defect the rest of the repo bans explicitly: an
unreadable source reports ``count: null``, never ``0``, because the two lead to
opposite decisions. Here "no evidence" and "100% success" lead to opposite
decisions too, and the code chose the confident one.

What replaces it
----------------
`effectiveness` is `None` when nothing was ever measured, and `smoothed_effectiveness`
(Laplace, prior 1 success in 2 uses) is the number used for *ranking*, so an
unproven skill sits in the middle of the distribution — never above a
well-tested one, never treated as proven. `strength` exposes that ranking
number and is what `ProceduralSkillMemory` now scores with.

The lifecycle (`proposed -> verified -> promoted <-> deprecated -> retired`)
records *why* a skill changed state, which is what Voyager's self-verification
gate and MemOS's lifecycle tracking both make first-class. Eviction additionally
carries a coverage term: a skill that is the only one matching a pattern is not
an ordinary eviction candidate, because dropping it leaves the capability
uncovered rather than merely less-used.
"""

from __future__ import annotations

import pytest

from alpha.memory.cognitive.models import ProceduralSkill
from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory
from alpha.memory.cognitive.skill_lifecycle import (
    DEPRECATED_STRENGTH_FLOOR,
    SKILL_LIFECYCLES,
    SkillLifecycle,
    SkillVerdict,
    coverage_report,
    evaluate,
    rank_for_recall,
    retirement_priority,
    transition,
)


def test_a_verdict_serialises_its_own_honesty():
    """`SkillVerdict.to_dict` keeps an absent measurement absent."""
    verdict = evaluate(ProceduralSkill(name="n", description="d", trigger_pattern="t"))
    assert isinstance(verdict, SkillVerdict)
    blob = verdict.to_dict()
    assert blob["effectiveness"] is None
    assert blob["evidence_count"] == 0
    assert blob["lifecycle"] == "proposed"
    assert blob["basis"] == "no measurement"


def _skill(**overrides) -> ProceduralSkill:
    base = {
        "name": "pytest_targeted_runner",
        "description": "Run an isolated pytest unit test on a changed file",
        "trigger_pattern": r"(pytest|unit test|run test)",
        "steps": ["uv run pytest tests/target.py", "check output"],
    }
    base.update(overrides)
    return ProceduralSkill(**base)


# --- the fabricated score -----------------------------------------------------


def test_a_never_executed_skill_has_no_effectiveness_not_a_perfect_one():
    skill = _skill()
    assert skill.evidence_count == 0
    assert skill.effectiveness is None, "no measurement is not a measurement of 100%"
    assert skill.smoothed_effectiveness == 0.5, "the Laplace prior puts unproven in the middle"


def test_a_measured_skill_reports_its_real_rate():
    skill = _skill(success_count=2, failure_count=1)
    assert skill.evidence_count == 3
    assert skill.effectiveness == pytest.approx(2 / 3)
    assert skill.smoothed_effectiveness == pytest.approx(3 / 5)


def test_an_unproven_skill_never_outranks_a_tested_one():
    untested = _skill(name="untested")
    proven = _skill(name="proven", success_count=9, failure_count=1)
    assert rank_for_recall(untested, relevance=1.0) < rank_for_recall(proven, relevance=1.0)


def test_an_unproven_skill_is_not_ranked_below_a_consistently_failing_one():
    """Middle-of-the-distribution, not bottom: an untested skill is not evidence of failure either."""
    untested = _skill(name="untested")
    failing = _skill(name="failing", success_count=0, failure_count=9)
    assert rank_for_recall(untested, relevance=1.0) > rank_for_recall(failing, relevance=1.0)


def test_the_ranking_number_is_reported_alongside_the_honest_one():
    skill = _skill()
    blob = skill.to_dict()
    assert blob["evidence_count"] == 0
    assert blob["effectiveness"] is None
    assert blob["evidence_basis"] == "unproven"
    assert blob["smoothed_effectiveness"] == 0.5

    measured = _skill(success_count=2, failure_count=1).to_dict()
    assert measured["effectiveness"] == pytest.approx(0.667)
    assert measured["evidence_basis"] == "measured"


# --- lifecycle ---------------------------------------------------------------


def test_a_new_skill_is_proposed_and_says_why():
    verdict = evaluate(_skill())
    assert verdict.lifecycle == SkillLifecycle.PROPOSED
    assert verdict.effectiveness is None
    assert verdict.evidence_count == 0
    assert "unproven" in verdict.reason
    assert verdict.basis == "no measurement", "the basis must say nothing was measured, not that a rule ran"


def test_one_use_makes_a_skill_verified():
    skill = _skill(success_count=1, failure_count=0)
    verdict = evaluate(skill)
    assert verdict.lifecycle == SkillLifecycle.VERIFIED
    assert verdict.effectiveness == 1.0
    assert "1 measured use" in verdict.reason


def test_clearing_the_bar_promotes_a_skill():
    skill = _skill(success_count=8, failure_count=2)  # 0.8
    verdict = evaluate(skill, min_uses=5, min_effectiveness=0.75)
    assert verdict.lifecycle == SkillLifecycle.PROMOTED
    assert verdict.promotion_eligible is True


def test_a_high_rate_with_no_uses_is_not_promotable():
    """The bar has a sample-size floor, so a single lucky run cannot promote."""
    verdict = evaluate(_skill(success_count=1, failure_count=0), min_uses=5, min_effectiveness=0.75)
    assert verdict.promotion_eligible is False
    assert "needs 5 measured uses" in verdict.reason


def test_a_promoted_skill_that_starts_failing_is_deprecated():
    skill = _skill(success_count=1, failure_count=9)  # 0.1
    verdict = evaluate(skill, min_effectiveness=DEPRECATED_STRENGTH_FLOOR)
    assert verdict.lifecycle == SkillLifecycle.DEPRECATED
    assert verdict.promotion_eligible is False
    assert "below" in verdict.reason


def test_every_lifecycle_state_is_named():
    assert {item.value for item in SkillLifecycle} == {"proposed", "verified", "promoted", "deprecated", "retired"}
    assert SKILL_LIFECYCLES == ("proposed", "verified", "promoted", "deprecated", "retired")


def test_transitions_are_refused_not_clamped():
    skill = _skill()
    with pytest.raises(ValueError, match="cannot move"):
        transition(skill, SkillLifecycle.PROMOTED, reason="bar cleared")
    with pytest.raises(ValueError, match="cannot move"):
        transition(skill, SkillLifecycle.DEPRECATED, reason="not failing yet")


def test_a_transition_records_its_reason_and_is_idempotent_in_state():
    skill = _skill(lifecycle="verified")
    record = transition(skill, SkillLifecycle.PROMOTED, reason="8 of 10 measured uses succeeded")
    assert skill.lifecycle == "promoted"
    assert skill.lifecycle_reason == "8 of 10 measured uses succeeded"
    assert record.from_state == SkillLifecycle.VERIFIED
    assert record.to_state == SkillLifecycle.PROMOTED
    assert record.reason == "8 of 10 measured uses succeeded"


def test_a_transition_needs_a_reason():
    skill = _skill(lifecycle="verified")
    with pytest.raises(ValueError, match="requires a reason"):
        transition(skill, SkillLifecycle.PROMOTED, reason="   ")


def test_any_state_can_retire_with_a_reason():
    for start in SKILL_LIFECYCLES:
        if start == "retired":
            # Retiring an already-retired skill is a no-op, not a move.
            with pytest.raises(ValueError, match="already in retired"):
                transition(_skill(lifecycle=start), SkillLifecycle.RETIRED, reason="operator removed it")
            continue
        skill = _skill(lifecycle=start)
        record = transition(skill, SkillLifecycle.RETIRED, reason="operator removed it")
        assert skill.lifecycle == "retired"
        assert record.from_state == SkillLifecycle(start)


# --- recall ranking and eviction --------------------------------------------


def test_recall_score_is_relevance_times_the_ranking_strength():
    unproven = rank_for_recall(_skill(), relevance=0.8)
    assert unproven == pytest.approx(0.4)  # 0.8 * 0.5 prior
    strong = rank_for_recall(_skill(success_count=19, failure_count=1), relevance=0.8)
    assert strong == pytest.approx(0.8 * 20 / 22)


def test_retirement_priority_evicts_the_weakest_evidence_first():
    weak = _skill(name="weak", success_count=0, failure_count=5)
    strong = _skill(name="strong", success_count=20, failure_count=0)
    fresh = _skill(name="fresh")
    order = [item.name for item in retirement_priority([weak, strong, fresh])]
    assert order.index("weak") < order.index("fresh")
    assert order.index("fresh") < order.index("strong")


def test_eviction_never_drops_the_last_coverage_of_a_pattern():
    """A unique skill is not an ordinary eviction candidate: dropping it removes the capability."""
    only = _skill(name="only_match", success_count=0, failure_count=1)
    others = [_skill(name=f"other_{index}", success_count=10) for index in range(3)]

    evictable = retirement_priority([only, *others], patterns=[only.name])
    assert evictable[-1].name == "only_match", "a uniquely-covering skill is evicted last, never first"
    assert [item.name for item in evictable[:3]] == ["other_0", "other_1", "other_2"]

    report = coverage_report([only, *others], patterns=[only.name])
    assert report["unique_coverage"] == {only.name: only.name}
    assert report["evictable_last"] == (only.name,)
    assert report["unserved_patterns"] == ()

    # A pattern two skills can both serve is not unique coverage, so neither is held back.
    shared = coverage_report([only, others[0]], patterns=["pytest"])
    assert shared["unique_coverage"] == {}
    assert shared["evictable_last"] == ()

    # A pattern nothing serves is reported as unserved, not silently ignored.
    missing = coverage_report([only], patterns=["nobody_covers_this"])
    assert missing["unique_coverage"] == {}
    assert missing["unserved_patterns"] == ("nobody_covers_this",)


# --- the live memory, not the dataclass -------------------------------------


def test_recording_outcomes_builds_evidence():
    memory = ProceduralSkillMemory()
    skill = memory.register_skill(name="pytest_targeted_runner", description="run a test", trigger_pattern=r"(pytest|unit test)")

    assert skill.evidence_count == 0
    assert memory.rank_for_recall("run a unit test")[0][0].name == "pytest_targeted_runner"

    memory.record_outcome(skill.skill_id, success=True)
    memory.record_outcome(skill.skill_id, success=True)
    memory.record_outcome(skill.skill_id, success=False, reason="ModuleNotFoundError")

    assert skill.evidence_count == 3
    assert skill.effectiveness == pytest.approx(2 / 3)


def test_the_memory_ranks_a_proven_skill_above_an_untested_one():
    memory = ProceduralSkillMemory()
    unproven = memory.register_skill(name="fresh_idea", description="run a unit test", trigger_pattern=r"(pytest|unit test)")
    proven = memory.register_skill(name="battle_tested", description="run a unit test", trigger_pattern=r"(pytest|unit test)")
    for _ in range(10):
        memory.record_outcome(proven.skill_id, success=True)

    ranked = memory.rank_for_recall("run a unit test")
    names = [item[0].name for item in ranked]
    assert names.index("battle_tested") < names.index("fresh_idea")
    assert unproven.evidence_count == 0


def test_the_lifecycle_summary_counts_states_it_actually_saw():
    memory = ProceduralSkillMemory()
    memory.register_skill(name="a", description="x", trigger_pattern="a")
    b = memory.register_skill(name="b", description="x", trigger_pattern="b")
    memory.record_outcome(b.skill_id, success=True)

    summary = memory.lifecycle_summary()
    assert summary["total"] == 2
    assert summary[SkillLifecycle.PROPOSED.value] == 1
    assert summary[SkillLifecycle.VERIFIED.value] == 1
    assert isinstance(summary["unproven_skills"], list)
    assert summary["unproven_skills"] == ["a"]
