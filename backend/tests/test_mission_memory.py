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
