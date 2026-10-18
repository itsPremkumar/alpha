"""The rubric acceptance judge (bot-mode plan phase 3.3).

**The gap this closes.**  ``SwarmAggregator.aggregate`` used to answer "how
good is this deliverable?" with a 40% keyword-overlap number written into
``plan.quality_score`` — a figure that read as a measurement but was lexical
similarity between the report and the goal text.  Phase 3.3 replaces it with a
rubric judge over the goal plus per-task evidence (``alpha.swarm.judge``) and
makes the honest failure modes first-class:

* ``quality_score`` is **judge-measured only** — ``None`` when no judge ran,
  never a reassuring 0.0 and never the keyword gate's number;
* **judge failure means ``unverified``, never a pass** — empty reply,
  non-JSON, unknown verdict, ``passed`` with no measurable score, an out-of-
  range score, a raising ``invoke``, or no ``invoke`` at all;
* ``completed`` requires the judge to have passed when the plan's
  ``acceptance_mode`` asked for one, and the guard can only demote;
* the deterministic per-task gate keeps its own authority: a judge that passes
  cannot lift tasks whose declared acceptance never passed.
"""

from __future__ import annotations

import json

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.judge import (
    ACCEPTANCE_MODES,
    DEFAULT_ACCEPTANCE_MODE,
    JUDGE_BASIS,
    VERDICT_PASSED,
    VERDICT_UNVERIFIED,
    build_judge_prompt,
    judge_required,
    normalize_acceptance_mode,
    parse_judge_response,
    run_acceptance_judge,
)
from alpha.swarm.memory import SwarmMemoryManager
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState

# --- helpers ---------------------------------------------------------------


def _completed_task(task_id: str = "t1", **overrides) -> SwarmTaskNode:
    fields = {
        "task_id": task_id,
        "objective": "Run the suite and report",
        "state": TaskNodeState.COMPLETED,
        "result_summary": "All 12 tests pass.",
        "evidence": [{"source": "pytest", "detail": "12 passed"}],
    }
    fields.update(overrides)
    return SwarmTaskNode(**fields)


def _plan(**overrides) -> SwarmPlan:
    fields = {
        "swarm_id": "swm-judge",
        "goal": "Ship the release with passing tests",
        "mode": SwarmMode.PARALLEL,
        "tasks": {"t1": _completed_task()},
    }
    fields.update(overrides)
    return SwarmPlan(**fields)


def _invoke(text: str, errors: dict | None = None):
    """A scripted model seam: records its prompts and returns ``text``."""

    def invoke(system: str, user: str) -> str:
        invoke.calls.append({"system": system, "user": user})
        if errors is not None and errors.get("raise"):
            raise RuntimeError("model endpoint unavailable")
        return text

    invoke.calls = []
    return invoke


def _valid(score: float = 0.92, verdict: str = VERDICT_PASSED) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "score": score,
            "reasons": ["all tasks completed with evidence"],
            "evidence_refs": ["t1"],
        }
    )


# --- vocabulary and cost gate ----------------------------------------------


def test_the_default_acceptance_mode_is_the_free_one():
    assert ACCEPTANCE_MODES == ("evidence", "judge", "both")
    assert DEFAULT_ACCEPTANCE_MODE == "evidence"
    assert normalize_acceptance_mode("judge") == "judge"
    # An unknown spelling resolves to the free default: a typo may never
    # become a silent opt-in that starts spending tokens.
    for spelling in ("banana", "", None, 7):
        assert normalize_acceptance_mode(spelling) == "evidence"
    assert judge_required("evidence") is False
    assert judge_required("judge") is True
    assert judge_required("both") is True
    assert judge_required("banana") is False


# --- prompt construction ----------------------------------------------------


def test_the_prompt_is_bounded_and_discloses_its_truncation():
    plan = _plan(goal="G" * 5_000, tasks={f"t{i}": _completed_task(f"t{i}", objective=f"objective {i}") for i in range(40)})
    prompt = build_judge_prompt(plan)

    goal_line = next(line for line in prompt["user"].splitlines() if line.startswith("Goal:"))
    assert len(goal_line) < 2_100, "the goal slice must be clipped, not passed through whole"
    assert "omitted at the 32-task bound" in prompt["user"], "truncation is disclosed, never silent"
    assert "objective 31" in prompt["user"]
    assert "objective 39" not in prompt["user"]
    assert "Recorded tasks and evidence" in prompt["user"]


def test_the_prompt_rubric_names_every_verdict_and_the_json_contract():
    prompt = build_judge_prompt(_plan())
    for verdict in ("passed", "unverified", "failed"):
        assert verdict in prompt["system"]
    assert "ONLY one JSON object" in prompt["system"]
    assert "you have no tools" in prompt["system"]


def test_a_plan_with_no_tasks_says_so_instead_of_earning_a_prompt_vacuous_pass():
    prompt = build_judge_prompt(_plan(tasks={}))
    assert "no tasks executed" in prompt["user"]


# --- parsing: judge failure may never become a pass -------------------------


def test_a_valid_judgement_round_trips_with_its_measured_score():
    report = parse_judge_response(_valid(0.92), model="judge-mini")

    assert report["verdict"] == VERDICT_PASSED
    assert report["score"] == 0.92
    assert report["reasons"] == ["all tasks completed with evidence"]
    assert report["evidence_refs"] == ["t1"]
    assert report["model"] == "judge-mini"
    assert report["basis"] == JUDGE_BASIS
    assert report["error"] is None


def test_a_fenced_or_prose_wrapped_reply_still_parses():
    fenced = "```json\n" + _valid(0.8) + "\n```"
    prose = "Here is my judgement:\n" + _valid(0.7) + "\nThanks."
    assert parse_judge_response(fenced)["score"] == 0.8
    assert parse_judge_response(prose)["score"] == 0.7


def test_every_judge_defect_is_unverified_and_never_a_score():
    defective = {
        "empty reply": "",
        "whitespace reply": "   \n",
        "not json": "the suite looks fine to me",
        "array instead of object": "[1, 2, 3]",
        "unknown verdict": '{"verdict": "probably", "score": 0.5}',
        "missing verdict": '{"score": 0.5}',
        "passed with no score": '{"verdict": "passed"}',
        "passed with a bool score": '{"verdict": "passed", "score": true}',
        "passed with an out-of-range score": '{"verdict": "passed", "score": 1.7}',
        "passed with a negative score": '{"verdict": "passed", "score": -0.2}',
    }
    for label, text in defective.items():
        report = parse_judge_response(text, model="judge-mini")
        assert report["verdict"] == VERDICT_UNVERIFIED, label
        assert report["score"] is None, label
        assert report["reasons"], label
    # Out-of-range is refused rather than clamped: 1.7 must not become 1.0.
    assert parse_judge_response('{"verdict": "passed", "score": 1.7}')["score"] is None
    # The reason names the defect so the operator sees which rule fired.
    assert parse_judge_response("nope")["reasons"] == ["judge_malformed_output"]
    assert parse_judge_response("")["reasons"] == ["judge_empty_response"]


def test_a_judge_that_cannot_run_reports_why_rather_than_inventing_a_verdict():
    plan = _plan(acceptance_mode="judge")

    unconfigured = run_acceptance_judge(plan, invoke=None, model="judge-mini")
    assert unconfigured["verdict"] == VERDICT_UNVERIFIED
    assert unconfigured["score"] is None
    assert unconfigured["reasons"] == ["judge_not_configured"]

    raising = run_acceptance_judge(plan, invoke=_invoke(_valid(), errors={"raise": True}), model="judge-mini")
    assert raising["verdict"] == VERDICT_UNVERIFIED
    assert raising["score"] is None
    assert raising["reasons"] == ["judge_invocation_failed"]
    assert "RuntimeError" in (raising["error"] or "")


def test_the_report_label_comes_from_the_caller_never_from_the_model():
    # A model cannot attest to which model it is: the response's own ``model``
    # field is ignored and the caller's label is what the report carries.
    spoofed = json.dumps({"verdict": "passed", "score": 0.9, "model": "gpt-everything"})
    report = parse_judge_response(spoofed, model="judge-mini")
    assert report["model"] == "judge-mini"


def test_running_the_judge_passes_a_bounded_prompt_to_the_injected_seam():
    plan = _plan()
    invoke = _invoke(_valid(0.95))
    report = run_acceptance_judge(plan, invoke=invoke, model="judge-mini")

    assert report["verdict"] == VERDICT_PASSED
    assert len(invoke.calls) == 1
    assert "ONLY one JSON object" in invoke.calls[0]["system"]
    assert plan.goal in invoke.calls[0]["user"]


# --- aggregation: quality_score comes only from the judge -------------------


def test_evidence_mode_leaves_quality_score_unmeasured_but_still_discloses_the_keyword_gate():
    plan = _plan()
    result = SwarmAggregator.aggregate(plan)

    assert plan.quality_score is None, "no judge ran — the keyword gate must not fill this in"
    assert plan.judge_report == {}
    assert result["judge"] == {}
    assert plan.status == "completed", "an evidence-mode plan keeps its pre-3.3 status behaviour"
    # The advisory keyword gate still answers its own question in the report;
    # phase 3.3 removes its authority, not its existence.
    assert result["quality_gate"]["advisory"] is True
    assert result["quality_gate"]["method"] == "keyword_overlap"
    assert "judge-measured only" in result["quality_basis"]


def test_a_stale_judge_report_never_survives_a_re_aggregation_in_evidence_mode():
    plan = _plan()
    plan.judge_report = {"verdict": VERDICT_PASSED, "score": 0.9}
    plan.quality_score = 0.9

    SwarmAggregator.aggregate(plan)

    assert plan.judge_report == {}
    assert plan.quality_score is None


def test_a_judging_plan_records_the_measured_score_and_completes():
    plan = _plan(acceptance_mode="judge")
    result = SwarmAggregator.aggregate(plan, judge_invoke=_invoke(_valid(0.95)), judge_model="judge-mini")

    assert plan.quality_score == 0.95, "the score is the judge's measurement"
    assert plan.judge_report["verdict"] == VERDICT_PASSED
    assert plan.judge_report["model"] == "judge-mini"
    assert result["judge"]["score"] == 0.95
    assert plan.status == "completed"
    assert plan.terminal_reason is None


def test_a_judge_that_cannot_run_leaves_the_plan_unverified_not_completed():
    plan = _plan(acceptance_mode="judge")
    SwarmAggregator.aggregate(plan)  # no judge_invoke supplied

    assert plan.status == "partial_success"
    assert plan.terminal_reason == "judge_unverified"
    assert plan.quality_score is None
    assert plan.judge_report["reasons"] == ["judge_not_configured"]


def test_malformed_judge_output_demotes_a_plan_that_otherwise_completed():
    plan = _plan(acceptance_mode="both")
    SwarmAggregator.aggregate(plan, judge_invoke=_invoke("the release looks good to me"))

    assert plan.status == "partial_success"
    assert plan.terminal_reason == "judge_unverified"
    assert plan.quality_score is None
    assert plan.judge_report["verdict"] == VERDICT_UNVERIFIED


def test_an_explicit_judge_rejection_is_named_and_carries_its_measured_score():
    plan = _plan(acceptance_mode="judge")
    SwarmAggregator.aggregate(plan, judge_invoke=_invoke(_valid(0.2, verdict="failed")))

    assert plan.status == "partial_success"
    assert plan.terminal_reason == "judge_rejected"
    # Measured and honest: the judge did produce a score, it just rejected the
    # work.  Unmeasured would be ``None``; 0.2 is what was actually judged.
    assert plan.quality_score == 0.2


def test_a_passing_judge_cannot_lift_tasks_whose_acceptance_never_passed():
    task = _completed_task(acceptance_criteria=[{"kind": "test_ref", "value": "tests/test_release.py"}])
    plan = _plan(acceptance_mode="both", tasks={"t1": task})
    result = SwarmAggregator.aggregate(plan, judge_invoke=_invoke(_valid(0.97)))

    assert result["judge"]["verdict"] == VERDICT_PASSED, "the judge did pass"
    # ...but the deterministic gate still decides per-task acceptance: a judge
    # withholds status, it never grants it past the task facts.
    assert plan.status == "partial_success"
    assert plan.terminal_reason == "acceptance_unverified"


# --- persistence ------------------------------------------------------------


def test_judge_fields_survive_a_checkpoint_round_trip():
    plan = _plan(acceptance_mode="both")
    plan.judge_report = {"verdict": VERDICT_PASSED, "score": 0.9, "reasons": [], "evidence_refs": [], "model": "judge-mini"}

    restored = SwarmPlan.from_dict(plan.to_dict())
    assert restored.acceptance_mode == "both"
    assert restored.judge_report == plan.judge_report
    assert restored.quality_score is None

    # An older checkpoint predates the fields entirely: it reads as the free
    # default with no judgement recorded, never as a guess.
    legacy = plan.to_dict()
    legacy.pop("acceptance_mode")
    legacy.pop("judge_report")
    legacy.pop("quality_score")
    restored_legacy = SwarmPlan.from_dict(legacy)
    assert restored_legacy.acceptance_mode == "evidence"
    assert restored_legacy.judge_report == {}
    assert restored_legacy.quality_score is None

    # A malformed mode in a hand-edited checkpoint is the free default.
    typo = plan.to_dict()
    typo["acceptance_mode"] = "judgee"
    assert SwarmPlan.from_dict(typo).acceptance_mode == "evidence"


# --- promotion --------------------------------------------------------------


def test_organization_memory_promotion_never_uses_an_unmeasured_score():
    mem = SwarmMemoryManager()
    plan = _plan(final_result="Delivered release notes.")

    # No judge ran: unmeasured, so promotion must decline without writing
    # anything — and without logging a fabricated 0.0 quality figure.
    assert plan.quality_score is None
    assert mem.promote_to_org_memory("swm-unjudged", plan, min_quality=0.8) is False

    # A judge that did not pass blocks promotion even with a high score: an
    # unverified deliverable is not accepted work.
    plan.quality_score = 0.95
    plan.judge_report = {"verdict": VERDICT_UNVERIFIED, "score": 0.95}
    assert mem.promote_to_org_memory("swm-unverified", plan, min_quality=0.8) is False
