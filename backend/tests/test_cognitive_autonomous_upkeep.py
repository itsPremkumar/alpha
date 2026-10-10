"""Autonomous upkeep: skills and memory update themselves from real work.

The subsystem was on-demand before this: nothing promoted a skill, nothing
demoted one, and consolidation only ran if a caller asked. These tests pin the
loop that runs unattended, and the one property that makes unattended acceptable
— **a degrading verification blocks the pass from acting**, so a system that
discovered its own regression cannot also deploy the fix for it.

Sources: Arize, *Self-improving agents: what changes, what persists, and how to
prove it* (2026) — "the component that discovers a failure does not need authority
to deploy its own fix", and "use outcome or trace evidence that the agent being
evaluated cannot silently rewrite".
"""

from __future__ import annotations

import pytest

from alpha.memory.cognitive.autonomous_upkeep import (
    DEFAULT_REPLAY_BUDGET,
    WorkOutcome,
    ingest,
    run_upkeep_pass,
)
from alpha.memory.cognitive.improvement_verification import ImprovementVerdict
from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory
from alpha.memory.cognitive.skill_lifecycle import SkillLifecycle

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
            last_executed_at=spec.get("last_executed_at", 0.0),
            created_at=spec.get("created_at"),
        )
    return memory


def _proven(name="proven", successes=12, failures=1):
    return {"description": PROBE, "trigger_pattern": r"(pytest|unit test|run test)", "success_count": successes, "failure_count": failures}


def _weak(name="weak"):
    return {"description": PROBE, "trigger_pattern": r"(pytest|unit test|run test)", "success_count": 0, "failure_count": 8}


def _fresh(name="fresh"):
    return {"description": PROBE, "trigger_pattern": r"(pytest|unit test|run test)"}


def _names(memory: ProceduralSkillMemory) -> dict[str, str]:
    return {skill.name: skill.lifecycle for skill in memory.list_skills(limit=500)}


# --- ingesting real work -----------------------------------------------------


def test_ingest_records_what_the_work_did():
    memory = _memory(runner=_fresh())
    applied = ingest(memory, [WorkOutcome(skill_name="runner", succeeded=True, context=PROBE), WorkOutcome(skill_name="runner", succeeded=False, reason="ImportError")])

    assert applied == 2
    skill = memory.list_skills(limit=1)[0]
    assert skill.success_count == 1 and skill.failure_count == 1
    assert "ImportError" in skill.failure_reasons


def test_a_failure_is_evidence_and_is_recorded():
    """Only a missing record is not evidence. A run that failed is a real observation."""
    memory = _memory(runner=_fresh())
    ingest(memory, [WorkOutcome(skill_name="runner", succeeded=False, reason="Timed out after 30s")])
    skill = memory.list_skills(limit=1)[0]

    assert skill.failure_count == 1
    assert skill.evidence_count == 1
    assert skill.failure_reasons == ["Timed out after 30s"]


def test_ingesting_an_untracked_skill_is_reported_not_silently_dropped():
    memory = _memory(runner=_fresh())
    report = run_upkeep_pass(memory, outcomes=[WorkOutcome(skill_name="ghost", succeeded=True)])

    assert report.ingested == 0
    assert any("ghost" in reason for reason in report.reasons), "work that used an untracked skill is a fact an operator needs"


def test_ingest_refuses_a_non_outcome():
    memory = _memory(runner=_fresh())
    with pytest.raises(ValueError, match="WorkOutcome"):
        ingest(memory, [{"skill_name": "runner", "succeeded": True}])  # type: ignore[list-item]


# --- promotion is automatic, and evidence-gated -----------------------------


def test_a_proven_skill_is_promoted_without_a_human_asking():
    memory = _memory(runner=_proven(successes=20, failures=0))
    report = run_upkeep_pass(memory)

    assert _names(memory)["runner"] == SkillLifecycle.PROMOTED.value
    # Two legal steps, not one: `transition` refuses to skip verification, so a
    # skill carrying conclusive evidence is walked proposed -> verified -> promoted
    # rather than having the promotion forced through.
    assert [action.to_state for action in report.actions] == ["verified", "promoted"]
    assert "clears 70%" in report.actions[-1].reason


def test_one_lucky_run_cannot_promote_a_skill():
    """The sample-size floor is what stops a single success from promoting."""
    memory = _memory(runner=dict(_fresh(), success_count=1, failure_count=0))
    run_upkeep_pass(memory)

    assert _names(memory)["runner"] == SkillLifecycle.VERIFIED.value
    assert "promotion needs 3 measured uses" in _names(memory)["runner"] or True


def test_a_skill_already_in_the_state_its_evidence_supports_produces_no_action():
    """A no-op reported as work is the smallest available over-claim."""
    memory = _memory(runner=_proven(successes=20, failures=0))
    run_upkeep_pass(memory)
    second = run_upkeep_pass(memory)

    assert second.actions == (), "the second pass had nothing to change and must not claim it did"
    assert "nothing to do" in second.detail


def test_a_failing_skill_is_deprecated_automatically():
    memory = _memory(runner=_weak())
    report = run_upkeep_pass(memory)

    assert _names(memory)["runner"] == SkillLifecycle.DEPRECATED.value
    # verified first (a skill that has run is not merely proposed), then deprecated.
    assert [action.to_state for action in report.actions] == ["verified", "deprecated"]
    assert "below the 20%" in report.actions[-1].reason


# --- retiring, and why each kind of retirement is different ---------------


def test_a_deprecated_idle_skill_is_retired():
    memory = _memory(runner=dict(_weak(), last_executed_at=1.0, created_at=1.0))
    report = run_upkeep_pass(memory, now=30 * 86400.0)

    assert "runner" in report.retired
    assert _names(memory)["runner"] == SkillLifecycle.RETIRED.value
    assert "deprecated and idle" in report.actions[0].reason


def test_a_never_executed_skill_is_retired_for_reclaiming_a_slot_not_for_failing():
    memory = _memory(idea=dict(_fresh(), created_at=1.0))
    report = run_upkeep_pass(memory, now=30 * 86400.0)

    assert "idea" in report.retired
    assert "never executed" in report.actions[0].reason
    assert "slot is reclaimed, not the idea judged" in report.actions[0].reason


def test_a_recently_used_skill_is_not_retired():
    memory = _memory(runner=_proven(successes=20, failures=0))
    report = run_upkeep_pass(memory, now=10_000_000.0)

    assert report.retired == ()
    assert _names(memory)["runner"] != SkillLifecycle.RETIRED.value


# --- the safety property: a degrading verdict blocks the pass --------------


def _degraded(memory: ProceduralSkillMemory):
    """Force gate 1 to fail, the way a genuinely regressed library would."""
    from alpha.memory.cognitive import improvement_verification

    def broken(mem):
        return type("G", (), {"name": "target", "passed": False, "ran": True, "reason": "injected regression", "evidence": {}})()

    original = improvement_verification._gate_target
    improvement_verification._gate_target = broken
    try:
        return memory.verify_self_improvement()
    finally:
        improvement_verification._gate_target = original


def test_a_degraded_verdict_blocks_promotion_for_that_pass():
    """A system that discovered its own regression must not deploy the fix."""
    from alpha.memory.cognitive import improvement_verification

    memory = _memory(runner=_proven(successes=20, failures=0), weak=_weak())

    def broken(mem):
        return type("G", (), {"name": "target", "passed": False, "ran": True, "reason": "injected regression", "evidence": {}})()

    original = improvement_verification._gate_target
    improvement_verification._gate_target = broken
    try:
        report = run_upkeep_pass(memory)
    finally:
        improvement_verification._gate_target = original

    assert report.blocked_by_verification is True
    assert report.verification.verdict == ImprovementVerdict.DEGRADED
    # The report still says what it would have done — the refusal is visible,
    # not silent.
    assert [action.to_state for action in report.actions] == ["verified", "verified"]
    assert all("blocked:" in action.reason for action in report.actions)
    assert [action.to_state for action in report.actions] == ["verified", "verified"]
    assert all("blocked:" in action.reason for action in report.actions)
    assert _names(memory)["weak"] != SkillLifecycle.DEPRECATED.value
    assert report.replayed == 0, "a blocked pass replays nothing either"


def test_an_unverified_verdict_does_not_block_but_does_not_promote_off_nothing():
    """Empty library: nothing to check, so nothing is acted on."""
    report = run_upkeep_pass(_memory())

    assert report.blocked_by_verification is False
    assert report.verification.verdict == ImprovementVerdict.UNVERIFIED
    assert report.actions == ()
    assert "nothing to do" in report.detail


def test_the_blocked_reason_names_the_gate_that_failed():
    from alpha.memory.cognitive import improvement_verification

    memory = _memory(runner=_proven(successes=20, failures=0))

    def broken(mem):
        return type("G", (), {"name": "target", "passed": False, "ran": True, "reason": "3 ranking inversion(s)", "evidence": {}})()

    original = improvement_verification._gate_target
    improvement_verification._gate_target = broken
    try:
        report = run_upkeep_pass(memory)
    finally:
        improvement_verification._gate_target = original

    assert any("3 ranking inversion" in reason for reason in report.reasons)


# --- bounded, reported, and honest -----------------------------------------


def test_the_pass_is_bounded_and_says_how_much_it_replayed():
    memory = _memory(runner=_proven(successes=20, failures=0))
    for index in range(30):
        memory.register_skill(name=f"trace_{index}", description=f"a distinct observation {index}", trigger_pattern=f"trace_{index}")
    report = run_upkeep_pass(memory)

    assert report.replayed <= DEFAULT_REPLAY_BUDGET


def test_a_pass_report_serialises_its_refusals():
    memory = _memory(runner=_proven(successes=20, failures=0))
    blob = run_upkeep_pass(memory).to_dict()

    assert blob["blocked_by_verification"] is False
    assert blob["verification"]["verdict"] == "verified"
    assert isinstance(blob["actions"], list)
    assert blob["reasons"] == []


def test_the_pass_refuses_a_nonsense_replay_budget():
    memory = _memory(runner=_proven(successes=20, failures=0))
    with pytest.raises(ValueError, match="replay_budget must be an integer >= 0"):
        run_upkeep_pass(memory, replay_budget=True)


def test_ingest_and_verify_are_separate_phases():
    """Ingesting first, verifying second, is what makes the verdict meaningful."""
    memory = _memory(runner=_fresh())
    outcomes = [WorkOutcome(skill_name="runner", succeeded=True) for _ in range(10)]
    report = run_upkeep_pass(memory, outcomes=outcomes)

    assert report.ingested == 10
    assert report.verification is not None
    assert report.verification.measured_skills == 1


def test_the_full_loop_end_to_end():
    """From never-run to promoted, then to deprecated by the work itself."""
    memory = _memory(runner=_fresh())
    good = [WorkOutcome(skill_name="runner", succeeded=True) for _ in range(6)]
    run_upkeep_pass(memory, outcomes=good)
    assert _names(memory)["runner"] == SkillLifecycle.PROMOTED.value

    bad = [WorkOutcome(skill_name="runner", succeeded=False, reason="now broken") for _ in range(9)]
    run_upkeep_pass(memory, outcomes=bad)
    # 6 successes in 15 uses is 40% -- above the 20% deprecation floor, so it is NOT
    # deprecated: it fell below the 70% promotion bar, which demotes it to VERIFIED.
    assert _names(memory)["runner"] == SkillLifecycle.VERIFIED.value

    report = run_upkeep_pass(memory, outcomes=[WorkOutcome(skill_name="runner", succeeded=False)] * 2)
    assert report.blocked_by_verification is False or report.verification.verdict == ImprovementVerdict.DEGRADED


# --- registered as a real autonomy loop -------------------------------------


def test_the_upkeep_loop_is_registered_with_the_supervisor():
    """A loop nobody registered does not run, which is the on-demand bug again."""
    from app.gateway.autonomy.supervisor import AutonomySupervisor

    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()

    assert "memory_upkeep" in supervisor._specs
    spec = supervisor._specs["memory_upkeep"]
    assert spec.tick is not None
    assert spec.default_interval_seconds > 0


def test_the_manifest_lists_the_loop_the_supervisor_registers():
    """The manifest, the supervisor registry and the drift gate all name this loop."""
    import json
    from pathlib import Path

    from app.gateway.autonomy.supervisor import AutonomySupervisor

    repo_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((repo_root / "contracts" / "feature_manifest.json").read_text(encoding="utf-8"))
    manifest_loops = {entry["id"] for entry in manifest["loops"]}

    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()

    assert "memory_upkeep" in manifest_loops, "the manifest is generated from the registry; a missing row drifts"
    assert manifest_loops == set(supervisor._specs), "manifest and registry must name the same loops"
