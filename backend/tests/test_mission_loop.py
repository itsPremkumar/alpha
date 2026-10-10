"""The mission watchdog loop: fail-closed parking, never a dispatcher.

Offline. Seeds durable missions under a temp Paths, runs the model-free
``mission_tick``, and pins the two properties that matter for unattended runs:
the brake *tightens* (a stuck/blocked mission is parked with a durable reason even
though no agent session called ``decide``), and it never dispatches or un-parks.
"""

from __future__ import annotations

import pytest

from alpha.config.paths import Paths
from alpha.runtime.missions import MissionManager, MissionStack


def _plan_stack(*, complete: bool = False) -> MissionStack:
    from alpha.runtime.missions import MilestonePlan

    plan = MilestonePlan.create("objective", [{"id": "m1", "title": "A", "acceptance": "a", "validation": "cmd"}])
    stack = MissionStack(plan=plan)
    return stack


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    import alpha.config.paths as paths_mod

    paths = Paths(str(tmp_path))
    monkeypatch.setattr(paths_mod, "get_paths", lambda: paths)
    mgr = MissionManager(paths)

    # A healthy, in-flight mission -> continue.
    mgr.save("u1", "t_continue", _plan_stack())
    # No progress across enough cycles -> park(no_progress), not already blocked.
    stuck = _plan_stack()
    for i in range(6):
        stuck = stuck.tick(progressed=False, signature=f"noop-{i}")
    mgr.save("u1", "t_no_progress", stuck)
    # Repeated identical failure signature -> park(repeated signature).
    repeat = _plan_stack()
    for _ in range(3):
        repeat = repeat.tick(progressed=False, signature="m1:same")
    mgr.save("u2", "t_repeat", repeat)
    # A fully verified mission -> done, left untouched.
    done = _plan_stack().with_plan(_plan_stack().plan.verify("m1", passed=True, evidence="e"))
    mgr.save("u2", "t_done", done)
    # An operator already blocked a mission -> park, but NOT newly re-parked.
    mgr.save("u1", "t_blocked", _plan_stack().block("waiting on a credential"))
    # A corrupt mission file -> fail-open, skipped.
    corrupt = mgr._path("u2", "t_corrupt")
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{broken", encoding="utf-8")
    return Paths(str(tmp_path)), mgr


def test_scan_active_is_bounded_and_skips_corrupt(tmp_path, seeded) -> None:
    _paths, mgr = seeded
    scopes = mgr.scan_active(max_scopes=50)
    owners_threads = {(owner, thread) for owner, thread, _stack in scopes}
    assert ("u1", "t_continue") in owners_threads
    assert ("u2", "t_corrupt") not in owners_threads  # corrupt -> inactive -> skipped
    # hard cap honours the bound
    assert len(mgr.scan_active(max_scopes=1)) == 1


def test_mission_tick_parks_stuck_but_never_dispatches_or_unparks(tmp_path, monkeypatch, seeded) -> None:
    from app.gateway.autonomy.loops import mission_tick

    _paths, mgr = seeded
    summary = mission_tick()

    assert summary["scanned"] >= 5
    assert summary["continue"] >= 1  # t_continue
    assert summary["done"] >= 1  # t_done
    assert summary["park"] >= 2  # no_progress + repeat + blocked

    # The brake TIGHTENED: no-progress and repeated missions are now durably blocked.
    parked_threads = {entry["thread"] for entry in summary["parked"]}
    assert "t_no_progress" in parked_threads and "t_repeat" in parked_threads
    assert mgr.load("u1", "t_no_progress").blocked.strip()  # reason persisted
    assert "no measured progress" in mgr.load("u1", "t_no_progress").blocked

    # An operator's block reason is never overwritten.
    assert mgr.load("u1", "t_blocked").blocked == "waiting on a credential"

    # A done mission is never mutated, and nothing is dispatched (no run ids here).
    assert summary.get("dispatched", 0) == 0
    assert mgr.load("u2", "t_done").plan.complete
