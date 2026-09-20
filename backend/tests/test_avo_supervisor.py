
from alpha.avo.supervisor import AVOSupervisor


def test_supervisor_exhaustion_stall_detection():
    sup = AVOSupervisor(max_no_improve=3)

    stagnated, dir1, _ = sup.observe_step(improved=False, signature="sig_1")
    assert stagnated is False
    assert dir1 is None

    stagnated, dir2, _ = sup.observe_step(improved=False, signature="sig_2")
    assert stagnated is False
    assert dir2 is None

    # 3rd non-improvement triggers STAGNATION_PIVOT directive
    stagnated, dir3, diag = sup.observe_step(improved=False, signature="sig_3", backtrack_candidate="v_stable")
    assert stagnated is True
    assert dir3 is not None
    assert dir3.directive_type == "STAGNATION_PIVOT"
    assert len(dir3.recommended_directions) > 0
    assert dir3.suggested_backtrack_target == "v_stable"
    assert "STAGNATION_DETECTED" in diag


def test_supervisor_oscillation_cycle_detection():
    sup = AVOSupervisor(max_no_improve=10, cycle_window_size=6)

    # Simulate A -> B -> A -> B oscillation cycle
    sup.observe_step(improved=False, signature="edit_unroll_loop")
    sup.observe_step(improved=False, signature="edit_reroll_loop")
    sup.observe_step(improved=False, signature="edit_unroll_loop")
    stagnated, directive, diag = sup.observe_step(
        improved=False,
        signature="edit_reroll_loop",
        backtrack_candidate="v_init",
    )

    assert stagnated is True
    assert directive is not None
    assert directive.directive_type == "OSCILLATION_BREAK"
    assert "2-cycle" in directive.reason
    assert "edit_unroll_loop" in directive.taboo_patterns
    assert "edit_reroll_loop" in directive.taboo_patterns
    assert "CYCLE_DETECTED" in diag
