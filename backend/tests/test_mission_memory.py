"""Durable mission memory: plan verification, scratchpad, scope-safe storage, anchor.

Offline. Exercises the value objects and the path-safe, atomic manager directly —
no Gateway, no model, no network. The honesty properties this file pins are the
ones that keep a multi-day run honest: evidence (not intent) decides a milestone,
stop-and-fix is structural, a mis-shaped scope is refused, and a corrupt file is
fail-open-with-disclosure rather than silently empty.
"""

from __future__ import annotations

import json

import pytest

from alpha.config.paths import Paths
from alpha.runtime.missions import (
    MAX_MILESTONES,
    InvalidMilestonePlan,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
    MissionManager,
    MissionStack,
)

# ---------------------------------------------------------------------------
# Milestone plan: evidence decides, stop-and-fix is structural
# ---------------------------------------------------------------------------


def _plan() -> MilestonePlan:
    return MilestonePlan.create(
        "Reduce p95 below 120ms",
        [
            {"id": "m1", "title": "Profile hot path", "acceptance": "flamegraph captured", "validation": "go test -bench"},
            {"id": "m2", "title": "Optimize checkout", "acceptance": "p95<120ms", "validation": "make bench"},
        ],
    )


def test_plan_create_starts_first_active() -> None:
    plan = _plan()
    assert plan.current is not None and plan.current.id == "m1"
    assert plan.active_index == 0
    assert plan.milestones[1].status is MilestoneStatus.PENDING


def test_advance_refuses_before_verify_stop_and_fix() -> None:
    plan = _plan()
    with pytest.raises(InvalidMilestonePlan, match="stop-and-fix"):
        plan.advance()


def test_verify_then_advance_is_evidence_driven() -> None:
    plan = _plan()
    plan = plan.verify("m1", passed=True, evidence="exit=0")
    assert plan.current.status is MilestoneStatus.VERIFIED
    advanced = plan.advance()
    assert advanced.active_index == 1
    assert advanced.current is not None and advanced.current.id == "m2"
    assert advanced.current.status is MilestoneStatus.ACTIVE


def test_advance_refuses_on_failed_milestone() -> None:
    plan = _plan().verify("m1", passed=False, evidence="bench failed")
    assert plan.get("m1").status is MilestoneStatus.FAILED
    with pytest.raises(InvalidMilestonePlan, match="stop-and-fix"):
        plan.advance()


def test_verify_is_idempotent_once_verified() -> None:
    plan = _plan().verify("m1", passed=True, evidence="exit=0")
    again = plan.verify("m1", passed=True, evidence="exit=0")
    assert again.get("m1").evidence == "exit=0"


def test_plan_refuses_duplicate_ids_and_unknown_status() -> None:
    with pytest.raises(InvalidMilestonePlan, match="unique"):
        MilestonePlan.create("obj", [{"id": "x", "title": "a"}, {"id": "x", "title": "b"}])
    with pytest.raises(InvalidMilestonePlan, match="unknown milestone status"):
        MilestonePlan.from_dict({"objective": "o", "milestones": [{"id": "m", "title": "t", "status": "weird"}]})


def test_plan_refuses_more_than_max_and_is_not_clamped() -> None:
    many = [{"id": f"m{i}", "title": "t"} for i in range(MAX_MILESTONES + 1)]
    with pytest.raises(InvalidMilestonePlan, match="at most"):
        MilestonePlan.create("obj", many)


def test_milestone_empty_title_refused() -> None:
    with pytest.raises(InvalidMilestonePlan):
        Milestone(id="m", title="   ")


def test_plan_roundtrip_and_complete() -> None:
    plan = _plan().verify("m1", passed=True, evidence="e").advance().verify("m2", passed=True, evidence="e")
    restored = MilestonePlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert restored.to_dict() == plan.to_dict()
    assert restored.complete and restored.verified_count == 2
    with pytest.raises(InvalidMilestonePlan, match="already complete"):
        restored.advance()


# ---------------------------------------------------------------------------
# Scratchpad: bounded ring, honest drop count
# ---------------------------------------------------------------------------


def test_scratchpad_ring_drops_oldest_and_counts() -> None:
    from alpha.runtime.missions import Scratchpad

    pad = Scratchpad()
    for i in range(65):
        pad = pad.append(f"note {i}")
    assert pad.count == 60
    assert pad.dropped == 5
    assert pad.entries[-1] == "note 64"
    assert pad.tail(2)[0] == "note 63"


def test_scratchpad_blank_note_is_noop() -> None:
    from alpha.runtime.missions import Scratchpad

    pad = Scratchpad().append("real")
    assert pad.append("   ").count == 1


# ---------------------------------------------------------------------------
# Mission stack: mutators return new values; status ring is bounded
# ---------------------------------------------------------------------------


def test_stack_is_active_only_with_content() -> None:
    assert not MissionStack().is_active()
    assert MissionStack().with_spec(objective="x").spec_objective == "x"
    assert MissionStack().log_status("started").status_lines == ("started",)


def test_stack_status_ring_is_bounded() -> None:
    stack = MissionStack(max_status_lines=3)
    for i in range(10):
        stack = stack.log_status(f"s{i}")
    assert stack.status_lines == ("s7", "s8", "s9")


def test_stack_roundtrip() -> None:

    stack = MissionStack(scope_key="t").with_spec(objective="ship it", constraints=["no API change"], done_when=["tests green"]).with_runbook("run tests").with_plan(_plan()).log_status("profiling").note("tried cache")
    restored = MissionStack.from_dict(json.loads(json.dumps(stack.to_dict())))
    assert restored.spec_objective == "ship it"
    assert restored.plan.current.id == "m1"
    assert restored.status_lines == ("profiling",)
    assert restored.scratchpad.count == 1


# ---------------------------------------------------------------------------
# Manager: scope safety, atomic save/load, fail-open corrupt read
# ---------------------------------------------------------------------------


def test_manager_rejects_unsafe_scope_on_save(tmp_path) -> None:
    mgr = MissionManager(Paths(str(tmp_path)))
    for bad in ("../escape", "a/b", ".hidden", "", ".."):
        with pytest.raises(ValueError):
            mgr.save("owner", bad, MissionStack().with_spec(objective="x"))


def test_manager_save_load_roundtrip_and_active(tmp_path) -> None:
    mgr = MissionManager(Paths(str(tmp_path)))
    assert not mgr.has_mission("u1", "t1")
    assert not mgr.active("u1", "t1")
    mgr.save("u1", "t1", MissionStack().with_spec(objective="objective"))
    assert mgr.has_mission("u1", "t1")
    loaded = mgr.load("u1", "t1")
    assert loaded.spec_objective == "objective"


def test_manager_corrupt_file_is_fail_open_with_disclosure(tmp_path) -> None:
    mgr = MissionManager(Paths(str(tmp_path)))
    path = mgr._path("u1", "t1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    loaded = mgr.load("u1", "t1")
    assert loaded.spec_objective == ""  # no invented content
    assert loaded.load_error  # the disclosure
    assert not loaded.is_active()


# ---------------------------------------------------------------------------
# verify: a measured EvidenceRecord decides the milestone
# ---------------------------------------------------------------------------


def _record(*, measured: bool, kind: str = "test_exit_report", detail: str = "exit_code=0"):
    from alpha.mission.acceptance import EvidenceKind, EvidenceRecord

    return EvidenceRecord(criterion="check", kind=EvidenceKind(kind), measured=measured, source="/tmp/report.json", detail=detail)


def test_verify_met_marks_verified_with_provenance() -> None:
    from alpha.runtime.missions import MilestoneVerdict, verify_milestone

    plan = _plan()
    updated, verification = verify_milestone(plan, milestone_id="m1", record=_record(measured=True, detail="exit_code=0 passed=12"))
    assert verification.verdict is MilestoneVerdict.MET and verification.passed
    assert updated.get("m1").status is MilestoneStatus.VERIFIED
    assert "passes" not in updated.get("m1").evidence  # stores the measured fact, not a verdict word
    assert "exit_code=0" in updated.get("m1").evidence and "test_exit_report" in updated.get("m1").evidence


def test_verify_not_met_marks_failed() -> None:
    from alpha.runtime.missions import MilestoneVerdict, verify_milestone

    updated, verification = verify_milestone(_plan(), milestone_id="m1", record=_record(measured=False, detail="exit_code=1 failed=2"))
    assert verification.verdict is MilestoneVerdict.NOT_MET and not verification.passed
    assert updated.get("m1").status is MilestoneStatus.FAILED


def test_verify_no_record_leaves_milestone_untouched() -> None:
    from alpha.runtime.missions import MilestoneVerdict, verify_milestone

    plan = _plan()
    updated, verification = verify_milestone(plan, milestone_id="m1", record=None)
    assert verification.verdict is MilestoneVerdict.UNVERIFIED
    assert updated.get("m1").status is MilestoneStatus.ACTIVE  # not quietly passed or failed
    assert updated == plan


def test_verify_unknown_milestone_raises() -> None:
    from alpha.runtime.missions import verify_milestone

    try:
        verify_milestone(_plan(), milestone_id="nope", record=_record(measured=True))
        raise AssertionError("expected InvalidMilestonePlan")
    except InvalidMilestonePlan:
        pass


def test_evidence_verify_is_idempotent_once_verified() -> None:
    from alpha.runtime.missions import verify_milestone

    once = verify_milestone(_plan(), milestone_id="m1", record=_record(measured=True, detail="exit_code=0"))[0]
    twice, verification = verify_milestone(once, milestone_id="m1", record=_record(measured=True, detail="exit_code=0"))
    assert twice.get("m1").evidence == "exit_code=0 [test_exit_report:/tmp/report.json]"
    assert verification.verdict.value == "met"


# ---------------------------------------------------------------------------
# Resume checkpoint, steer/reject/defer, and the anchor that surfaces them
# ---------------------------------------------------------------------------


def test_resume_checkpoint_renders_structured_fields_and_do_not_repeat() -> None:
    from alpha.runtime.missions import ResumeCheckpoint, render_resume_checkpoint

    assert render_resume_checkpoint(ResumeCheckpoint()) == ""
    rendered = render_resume_checkpoint(
        ResumeCheckpoint(
            current_phase="optimize checkout",
            next_action="rerun the benchmark",
            stop_condition="p95 < 120ms",
            rejected_paths="cache-first attempt regressed the suite",
            do_not_repeat="do not loosen the benchmark to pass",
        )
    )
    assert "## Resume checkpoint" in rendered
    assert "Current phase: optimize checkout" in rendered
    assert "Next action: rerun the benchmark" in rendered
    assert "Do not repeat: do not loosen the benchmark to pass" in rendered
    # A rejected path stays visible; it is never smoothed out of the view.
    assert "Rejected paths: cache-first" in rendered


def test_stack_rings_are_bounded() -> None:
    stack = MissionStack(max_ring=2)
    for i in range(5):
        stack = stack.steer(f"s{i}").reject(f"r{i}").defer(f"d{i}")
    assert stack.steers == ("s3", "s4")
    assert stack.rejected == ("r3", "r4")
    assert stack.deferred == ("d3", "d4")


def test_stack_roundtrip_preserves_checkpoint_and_rings() -> None:
    import json

    from alpha.runtime.missions import ResumeCheckpoint

    checkpoint = ResumeCheckpoint(current_phase="phase-Two", next_action="run tests", stop_condition="suite green")
    stack = MissionStack(scope_key="t").with_spec(objective="ship").steer("focus on the happy path first").reject("inline styles").defer("dark mode").with_checkpoint(checkpoint)
    restored = MissionStack.from_dict(json.loads(json.dumps(stack.to_dict())))
    assert restored.steers == ("focus on the happy path first",)
    assert restored.rejected == ("inline styles",)
    assert restored.deferred == ("dark mode",)
    assert restored.checkpoint is not None and restored.checkpoint.current_phase == "phase-Two"


def test_anchor_surfaces_steer_checkpoint_reject_defer() -> None:
    from alpha.runtime.missions import ResumeCheckpoint, render_anchor

    stack = (
        MissionStack(scope_key="t")
        .with_spec(objective="objective")
        .steer("prioritize correctness")
        .reject("skip tests to go faster")
        .defer("nice error messages")
        .with_checkpoint(ResumeCheckpoint(current_phase="writing tests", next_action="run pytest"))
    )
    anchor = render_anchor(stack, max_chars=6000)
    assert "## Operator steer" in anchor and "prioritize correctness" in anchor
    assert "## Resume checkpoint" in anchor and "Next action: run pytest" in anchor
    assert "## Do not repeat" in anchor and "skip tests to go faster" in anchor
    assert "## Deferred (not now)" in anchor and "nice error messages" in anchor


# ---------------------------------------------------------------------------
# The fail-closed loop brake: continue / done / park, never loop forever
# ---------------------------------------------------------------------------


def test_decide_done_only_when_all_verified() -> None:
    from alpha.runtime.missions import MissionAction, decide_mission

    plan = _plan()
    # Not done while a milestone is unverified, even mid-flight.
    partial = plan.verify("m1", passed=True, evidence="e").advance()
    assert decide_mission(MissionStack(plan=partial)).action is MissionAction.CONTINUE
    complete = partial.verify("m2", passed=True, evidence="e")
    decision = decide_mission(MissionStack(plan=complete))
    assert decision.action is MissionAction.DONE and "verified" in decision.reason


def test_decide_parks_on_repeated_signature_no_new_information() -> None:
    from alpha.runtime.missions import MissionAction, decide_mission

    stack = MissionStack(plan=_plan())
    for _ in range(3):
        stack = stack.tick(progressed=False, signature="m1:deadbeef")
    decision = decide_mission(stack)
    assert decision.action is MissionAction.PARK and decision.repeated_signature == "m1:deadbeef"


def test_decide_parks_on_no_progress_but_progress_resets_it() -> None:
    from alpha.runtime.missions import MissionAction, decide_mission

    stack = MissionStack(plan=_plan())
    stack = stack.tick(progressed=False, signature="a").tick(progressed=False, signature="b")
    assert decide_mission(stack).action is MissionAction.CONTINUE
    for _ in range(6):
        stack = stack.tick(progressed=False, signature=f"c{_}")
    assert decide_mission(stack).action is MissionAction.PARK

    # A real step forward resets the counter; an activity pulse does not.
    reset = stack.tick(progressed=True, signature="x")
    assert reset.no_progress_cycles == 0 and decide_mission(reset).action is MissionAction.CONTINUE


def test_decide_parks_on_explicit_block_and_unblock_releases() -> None:
    from alpha.runtime.missions import MissionAction, decide_mission

    blocked = MissionStack(plan=_plan()).block("waiting on a credential")
    decision = decide_mission(blocked)
    assert decision.action is MissionAction.PARK and "blocked" in decision.reason
    assert decide_mission(blocked.unblock()).action is MissionAction.CONTINUE


# ---------------------------------------------------------------------------
# progress() and the resume brief: the "step away and understand what happened"
# ---------------------------------------------------------------------------


def test_progress_is_measured_only() -> None:
    progress = MissionStack(plan=_plan().verify("m1", passed=True, evidence="e").advance()).progress()
    assert progress["milestones_total"] == 2
    assert progress["milestones_verified"] == 1
    assert progress["complete"] is False
    assert progress["current_milestone"] == "m2"


def test_resume_brief_is_bounded_and_names_the_next_action() -> None:
    from alpha.runtime.missions import ResumeCheckpoint, render_resume_brief

    assert render_resume_brief(MissionStack()) == ""
    stack = MissionStack(scope_key="t").with_spec(objective="reduce p95").with_plan(_plan()).with_checkpoint(ResumeCheckpoint(current_phase="profiling", next_action="rerun the benchmark"))
    brief = render_resume_brief(stack)
    assert brief.startswith("# Mission resume brief")
    assert "reduce p95" in brief and "rerun the benchmark" in brief and "0/2 milestones verified" in brief
    blocked = stack.block("need a credential")
    assert "Blocked: need a credential" in render_resume_brief(blocked)
