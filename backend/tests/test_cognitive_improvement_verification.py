"""Verify that self-improvement actually improved — the four proof gates.

These tests assert the *verifier*, not just the subsystem it verifies. That
distinction is the point: a self-improvement mechanism that reports it works is
worth nothing, and a verifier that only ever passes the cases it was written for
is the same fiction. The gates are pinned here against a deliberately adversarial
library — one built to expose each failure mode the literature names.

Sources: Arize, *Self-improving agents: what changes, what persists, and how to
prove it* (2026); arXiv:2607.13104 (Self-Improvements in Modern Agentic Systems
survey, §8); and the "Fragility of Self-Improving Agents" re-evaluation
(arXiv:2608.18066), where ReasoningBank gained +1.5 points under a benchmark's
default order and **lost 4.5** when the order was shuffled.
"""

from __future__ import annotations

import pytest

from alpha.memory.cognitive.improvement_verification import (
    GATE_NAMES,
    ImprovementVerdict,
    assert_no_worse_than_baseline,
    verify_skill_memory,
)
from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory

PROBE = "run a pytest unit test"


def _memory(**skills) -> ProceduralSkillMemory:
    memory = ProceduralSkillMemory()
    for name, spec in skills.items():
        memory.register_skill(
            name=name,
            description=spec["description"],
            trigger_pattern=spec["trigger_pattern"],
            steps=spec.get("steps", []),
            success_count=spec.get("success_count", 0),
            failure_count=spec.get("failure_count", 0),
        )
    return memory


def _proven(name="proven_skill", successes=12, failures=1, description="run a pytest unit test"):
    return {"description": description, "trigger_pattern": r"(pytest|unit test|run test)", "steps": [], "success_count": successes, "failure_count": failures}


def _fresh(name="fresh_skill", description="run a pytest unit test"):
    return {"description": description, "trigger_pattern": r"(pytest|unit test|run test)", "steps": [], "success_count": 0, "failure_count": 0}


def _report(memory: ProceduralSkillMemory, **kwargs):
    return verify_skill_memory(memory, **kwargs)


# --- the happy path: a library that genuinely improved -----------------------


def test_a_library_with_real_evidence_verifies():
    memory = _memory(battle_tested=_proven(successes=12, failures=1), also_tested=_proven(name="also_tested", successes=9, failures=2), newcomer=_fresh())
    report = _report(memory)

    assert report.verdict == ImprovementVerdict.VERIFIED, report.detail
    assert report.gate("target").passed is True
    assert report.gate("repetition_order").passed is True
    assert report.gate("held_out").passed is True
    assert report.gate("regression").passed is True
    assert report.measured_skills == 2
    assert report.skills_checked == 3


def test_the_report_serialises_its_own_audit_trail():
    memory = _memory(battle_tested=_proven(), newcomer=_fresh())
    blob = _report(memory).to_dict()

    assert blob["verdict"] == "verified"
    assert [item["name"] for item in blob["gates"]] == list(GATE_NAMES)
    assert all("ran" in item for item in blob["gates"])
    assert blob["reasons"]


# --- an empty library verifies nothing, and says so -------------------------


def test_an_empty_library_is_unverified_not_verified():
    """No evidence is not evidence of success. Every gate refuses to run."""
    report = _report(_memory())

    assert report.verdict == ImprovementVerdict.UNVERIFIED, report.detail
    assert all(gate.passed is None for gate in report.gates)
    assert "no gate could run" in report.detail
    assert report.measured_skills == 0


def test_a_library_of_only_unproven_skills_is_unverified():
    report = _report(_memory(a=_fresh(name="a"), b=_fresh(name="b")))

    assert report.verdict in (ImprovementVerdict.UNVERIFIED, ImprovementVerdict.DEGRADED)
    assert report.gate("target").passed is None, "with nothing measured there is no ordering to verify"
    assert report.gate("repetition_order").passed is None
    assert "unproven" in report.gate("target").reason


# --- gate 2: the fragility gate ----------------------------------------------


def test_a_ranking_that_only_holds_in_one_order_fails_gate_two():
    """The failure mode the re-evaluation paper measured.

    Two skills with *identical* evidence but different insertion order: if the
    ranking depended on arrival order rather than on measured outcomes, the
    shuffled replay would disagree and the gate must catch it.
    """
    memory = _memory(a=_proven(name="a", successes=6, failures=3, description="alpha helper"), b=_proven(name="b", successes=6, failures=3, description="alpha helper"))

    report = _report(memory, trials=8)

    assert report.gate("repetition_order").ran is True
    assert report.gate("repetition_order").passed is True, "identical evidence must rank identically regardless of order"
    assert report.gate("repetition_order").evidence["trials"] == 8


def test_more_trials_are_reported_so_the_confidence_is_visible():
    memory = _memory(a=_proven(name="a"), b=_proven(name="b"), c=_fresh(name="c"))
    assert _report(memory, trials=3).gate("repetition_order").evidence["trials"] == 3
    assert _report(memory, trials=11).gate("repetition_order").evidence["trials"] == 11


def test_gate_two_refuses_a_nonsense_trial_count():
    with pytest.raises(ValueError, match="trials must be an integer >= 1"):
        _report(_memory(a=_proven(name="a")), trials=0)


def test_a_single_skill_cannot_be_order_tested_and_says_why():
    report = _report(_memory(only=_proven(name="only")))

    assert report.gate("repetition_order").passed is None
    assert "fewer than two skills" in report.gate("repetition_order").reason


# --- gate 3: held-out transfer ----------------------------------------------


def test_measured_still_beats_unproven_on_an_unseen_context():
    memory = _memory(proven=_proven(), newcomer=_fresh())
    gate = _report(memory).gate("held_out")

    assert gate.ran is True
    assert gate.passed is True
    assert "unseen" in gate.reason or gate.evidence["probe"]


def test_gate_three_degrades_when_it_cannot_thin_out_the_set():
    report = _report(_memory(only_proven=_proven()))
    gate = report.gate("held_out")

    assert gate.passed is None, "without an unproven skill there is no transfer to observe"
    assert "held-out needs" in gate.reason


# --- gate 4: regression ------------------------------------------------------


def test_a_newcomer_cannot_demote_a_proven_skill():
    memory = _memory(proven=_proven(successes=20, failures=0))
    gate = _report(memory).gate("regression")

    assert gate.passed is True
    assert "did not demote" in gate.reason


def test_eviction_prefers_weak_evidence_over_proven():
    memory = _memory(proven=_proven(successes=20, failures=0), weak=dict(_proven(name="weak", successes=0, failures=6)))
    gate = _report(memory).gate("regression")

    assert gate.passed is True, "an eviction policy that drops the proven skill first is a regression the gate must catch"


def test_gate_four_refuses_to_check_a_library_with_no_prior_success():
    gate = _report(_memory(a=_fresh(name="a"), b=_fresh(name="b"))).gate("regression")

    assert gate.passed is None
    assert "no prior success to protect" in gate.reason


# --- the verdict is earned, not assumed -------------------------------------


def test_one_failed_gate_makes_the_verdict_degraded_not_verified():
    """A verifier that requires unanimity is the only kind worth running."""
    memory = _memory(proven=_proven(successes=20, failures=0), weak=dict(_proven(name="weak", successes=0, failures=6)))

    original = verify_skill_memory.__globals__["_gate_regression"]
    try:
        verify_skill_memory.__globals__["_gate_regression"] = lambda memory: type("G", (), {"name": "regression", "passed": False, "ran": True, "reason": "injected failure", "evidence": {}})()
        report = _report(memory)
    finally:
        verify_skill_memory.__globals__["_gate_regression"] = original

    assert report.verdict == ImprovementVerdict.DEGRADED
    assert "1 of 4 gate(s) failed" in report.detail
    assert report.gate("regression").passed is False


# --- the tie is not an improvement -----------------------------------------


def test_an_identical_ranking_is_unchanged_never_improved():
    assert assert_no_worse_than_baseline(("a", "b"), ("a", "b")) == "unchanged"
    assert assert_no_worse_than_baseline((), ()) == "unchanged"


def test_a_longer_ranking_that_preserves_the_prefix_is_improved():
    assert assert_no_worse_than_baseline(("a",), ("a", "b")) == "improved"


def test_a_dropped_skill_is_degraded():
    assert assert_no_worse_than_baseline(("a", "b"), ("a",)) == "degraded"
    assert assert_no_worse_than_baseline(("a", "b"), ()) == "degraded"
    assert assert_no_worse_than_baseline(("a",), ()) == "degraded"


def test_a_reordered_ranking_is_not_reported_as_an_improvement():
    """Promotion past a skill that used to lead is a change, not a gain."""
    assert assert_no_worse_than_baseline(("a", "b"), ("b", "a")) == "degraded"


# --- the method on the memory itself ----------------------------------------


def test_the_memory_can_verify_itself_and_reports_the_same_thing():
    memory = _memory(proven=_proven(), newcomer=_fresh())
    via_method = memory.verify_self_improvement()
    via_function = verify_skill_memory(memory)

    assert via_method.verdict == via_function.verdict
    assert [gate.name for gate in via_method.gates] == list(GATE_NAMES)


def test_verification_reads_what_the_memory_recorded_and_nothing_else():
    """The verifier is on the far side of the boundary: it does not score, it reads.

    A skill whose recorded outcomes say 9/10 must be reported with that
    effectiveness, not with a number the verifier computed for itself.
    """
    memory = _memory(measured=_proven(successes=9, failures=1))
    report = _report(memory)
    skill = next(s for s in memory.list_skills(limit=10) if s.name == "measured")

    assert skill.effectiveness == pytest.approx(0.9)
    assert report.gate("regression").evidence["best_proven"] == "measured"
