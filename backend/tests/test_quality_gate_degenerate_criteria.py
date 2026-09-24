"""Honesty pins for the quality gate: degenerate criteria never auto-pass.

A criterion with no evaluable tokens (empty, punctuation-only, unparseable)
used to auto-pass via `or len(crit_tokens) == 0`, letting the gate report
"passed" while evaluating nothing. The 40% keyword-overlap threshold itself
is a disclosed design choice and stays.
"""

from alpha.bots.quality_gate import evaluate_quality_gate


def test_degenerate_criterion_never_passes():
    deliverable = "Successfully completed all requested work with comprehensive verification, tests, and documentation."
    res = evaluate_quality_gate(deliverable, ["!!!", ""])

    assert res["verdict"] == "rejected"

    degenerate = res["degenerate_criteria"]
    assert [d["criterion"] for d in degenerate] == ["!!!", ""]
    assert all(d["reason"] == "criterion has no evaluable tokens" for d in degenerate)

    criterion_checks = [c for c in res["checks"] if c["name"].startswith("Criterion:")]
    assert len(criterion_checks) == 2
    assert all(c["passed"] is False for c in criterion_checks)
    assert all(c["details"] == "criterion has no evaluable tokens" for c in criterion_checks)

    assert "Degenerate criteria" in res["feedback"]
    assert "criterion has no evaluable tokens" in res["feedback"]


def test_degenerate_flag_is_empty_for_normal_criteria():
    """Non-degenerate criteria keep the disclosed 40% overlap threshold."""
    res = evaluate_quality_gate(
        "Successfully implemented authentication endpoints with JWT verification and comprehensive unit tests.",
        ["Implemented authentication endpoints", "JWT verification"],
    )
    assert res["verdict"] == "passed"
    assert res["degenerate_criteria"] == []
    assert res["score"] >= 0.6


def test_mixed_criteria_surface_degenerates_even_when_gate_fails():
    res = evaluate_quality_gate("", ["Implement endpoints", None])
    # The empty deliverable already fails; the unparseable criterion must be
    # disclosed rather than silently skipped or auto-passed.
    assert res["verdict"] == "rejected"
    assert [d["criterion"] for d in res["degenerate_criteria"]] == ["None"]
    assert res["degenerate_criteria"][0]["reason"] == "criterion has no evaluable tokens"
