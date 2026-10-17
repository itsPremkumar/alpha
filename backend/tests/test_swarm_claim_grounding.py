"""Deterministic claim grounding for swarm worker outcomes (S2).

**The gap this closes.**  A recorded run completed 5/5 tasks with
``acceptance_status=not_required`` while a QA worker returned fabricated
repository paths (``backend/does/not/exist.py:42``).  Nothing re-checked the
checkable statements inside an accepted summary.  ``ground_claims`` extracts
file / ``path:line`` / ``path::test`` claims and resolves them against the
workspace -- deterministically, with no model call.

Four honesty rules are pinned here:

* **resolved proves existence, never correctness** -- stated in the rendered
  verdict itself;
* **``missing`` / ``out_of_range`` / ``absent`` are contradictions** that fail
  a declared-acceptance task (the summary cites something that does not exist);
* **``unverifiable`` (absolute / virtual-sandbox paths) never fails a task** --
  a claim this module cannot check must not read as a failed one, and
  ``state="not_checked"`` is never rendered as a clean pass;
* **the runner is the only writer** -- the field is written after lease
  acceptance from the internal worker outcome, and external completion
  (``complete_task``) cannot populate it.
"""

from __future__ import annotations

import json

import pytest

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.grounding import GROUNDING_BASIS, Claim, extract_claims, ground_claims, grounding_unresolved
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.team import build_team_report, render_team_report_markdown


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("first\nsecond\nthird\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text("def test_alpha():\n    assert True\n\n\nclass TestWidget:\n    pass\n", encoding="utf-8")
    return [("workspace", tmp_path)]


# --- extraction and resolution --------------------------------------------


def test_a_fabricated_path_is_missing_not_resolved(workspace):
    report = ground_claims("I updated backend/does/not/exist.py:42 and verified it.", roots=workspace)

    assert report["state"] == "unresolved"
    assert report["claims_checked"] == 1
    assert report["unresolved"] == 1
    claim = report["claims"][0]
    assert claim["status"] == "missing"
    assert claim["value"] == "backend/does/not/exist.py"
    assert claim["line"] == 42
    assert "workspace" in claim["detail"]


def test_a_real_path_and_line_resolve(workspace):
    report = ground_claims("See src/app.py:2 for the change.", roots=workspace)

    assert report["state"] == "clean"
    assert report["resolved"] == 1
    entry = report["claims"][0]
    assert entry["status"] == "resolved"
    assert "line 2" in entry["detail"]


def test_an_out_of_range_line_is_a_contradiction(workspace):
    report = ground_claims("src/app.py:9000 needs review", roots=workspace)

    assert report["claims"][0]["status"] == "out_of_range"
    assert report["unresolved"] == 1


def test_test_references_resolve_only_when_the_symbol_is_defined(workspace):
    resolved = ground_claims("tests/test_thing.py::test_alpha passed", roots=workspace)
    assert resolved["claims"][0]["kind"] == "test_ref"
    assert resolved["claims"][0]["status"] == "resolved"

    klass = ground_claims("tests/test_thing.py::TestWidget passed", roots=workspace)
    assert klass["claims"][0]["status"] == "resolved"

    absent = ground_claims("tests/test_thing.py::test_zzz passed", roots=workspace)
    assert absent["claims"][0]["status"] == "absent"
    assert absent["state"] == "unresolved"


def test_plain_prose_pairs_are_not_claims(workspace):
    report = ground_claims("The client/server split and input/output contract stay as they are.", roots=workspace)

    assert report["claims_checked"] == 0
    assert report["state"] == "not_checked"
    assert report["reason"]


def test_absolute_and_virtual_paths_are_unverifiable_never_failed(workspace):
    report = ground_claims("Wrote /mnt/user-data/outputs/report.md for the operator.", roots=workspace)

    assert report["unresolved"] == 0
    assert report["unverifiable"] == 1
    assert report["claims"][0]["status"] == "unverifiable"
    assert report["state"] == "partially_unverifiable"


def test_traversal_is_unverifiable_not_resolved(workspace):
    report = ground_claims("Touched ../../etc/app.py config", roots=workspace)

    assert report["claims"][0]["status"] == "unverifiable"
    assert report["unresolved"] == 0


def test_the_claim_bound_is_disclosed_not_silently_applied(workspace):
    report = ground_claims("a/one.py b/two.py c/three.py", roots=workspace, max_claims=2)

    assert report["claims_checked"] == 2
    assert report["truncated"] == 1


def test_empty_or_non_text_input_is_not_checked_never_clean(workspace):
    for text in ("", "   ", None):
        report = ground_claims(text, roots=workspace)
        assert report["state"] == "not_checked"
        assert report["claims_checked"] == 0
        assert report["reason"]


def test_reports_are_deterministic_and_json_safe(workspace):
    text = "src/app.py:1 and tests/test_thing.py::test_alpha and missing/nope.py:3"

    first = ground_claims(text, roots=workspace)
    second = ground_claims(text, roots=workspace)

    assert first == second
    json.dumps(first)


def test_extraction_is_ordered_first_seen_and_deduplicated():
    claims, truncated = extract_claims("b/two.py:2 and b/two.py:2 and a/one.py")

    assert [claim.value for claim in claims] == ["b/two.py", "a/one.py"]
    assert truncated == 0
    assert isinstance(claims[0], Claim)


# --- the typed field ------------------------------------------------------


def test_grounding_unresolved_only_reads_a_contract_carrying_payload():
    task = SwarmTaskNode(task_id="t", objective="x")
    assert grounding_unresolved(task) == 0

    task.claim_grounding = {"unresolved": 5}
    assert grounding_unresolved(task) == 0

    task.claim_grounding = {"basis": GROUNDING_BASIS, "unresolved": 2}
    assert grounding_unresolved(task) == 2

    task.claim_grounding = {"basis": GROUNDING_BASIS, "unresolved": 0}
    assert grounding_unresolved(task) == 0


def test_claim_grounding_round_trips_and_legacy_plans_default_to_empty(workspace):
    payload = ground_claims("src/app.py:1", roots=workspace)
    task = SwarmTaskNode(task_id="t", objective="x", claim_grounding=payload)

    restored = SwarmTaskNode.from_dict(task.to_dict())

    assert restored.claim_grounding == payload
    assert SwarmTaskNode.from_dict({"task_id": "legacy", "objective": "old"}).claim_grounding == {}


def test_external_completion_cannot_populate_claim_grounding(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-external-grounding",
        goal="verify external result",
        mode=SwarmMode.PARALLEL,
        status="running",
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="verify", state=TaskNodeState.RUNNING, lease_id="lease-current")},
    )
    coordinator._swarms[plan.swarm_id] = plan

    updated = coordinator.complete_task(
        plan.swarm_id,
        "task-1",
        result_summary="I edited backend/does/not/exist.py:1",
        evidence=[{"source": "claim_grounding", "unresolved": 0, "claims_checked": 99}],
        lease_id="lease-current",
    )

    assert updated is not None
    assert updated.state == TaskNodeState.COMPLETED
    # A caller-supplied evidence entry that *names* the field is still only
    # evidence: the typed grounding field has one writer, the runner.
    assert updated.claim_grounding == {}


# --- acceptance gate ------------------------------------------------------

_CRITERIA = [{"id": "test", "required_evidence_kinds": ["test_result"]}]
_PASSING_EVIDENCE = [{"criterion_id": "test", "kind": "test_result", "passed": True, "reference": "test:1"}]


def test_a_clean_verification_still_passes():
    task = SwarmTaskNode(task_id="t", objective="x", acceptance_criteria=list(_CRITERIA))

    SwarmAggregator.apply_acceptance_verification(task, _PASSING_EVIDENCE)

    assert task.acceptance_status == "passed"


def test_unresolved_claims_fail_acceptance_with_the_count_visible(workspace):
    task = SwarmTaskNode(task_id="t", objective="x", acceptance_criteria=list(_CRITERIA))
    task.claim_grounding = ground_claims("patched backend/does/not/exist.py:7", roots=workspace)

    SwarmAggregator.apply_acceptance_verification(task, _PASSING_EVIDENCE)

    assert task.acceptance_status == "failed"
    assert task.verification["verified"] is False
    assert task.verification["grounding_unresolved"] == 1
    assert "did not resolve" in task.verification["error"]
    # The evidence verdicts stay visible beside the downgrade.
    assert task.verification["verdicts"][0]["verified"] is True


def test_unverifiable_claims_do_not_fail_acceptance(workspace):
    task = SwarmTaskNode(task_id="t", objective="x", acceptance_criteria=list(_CRITERIA))
    task.claim_grounding = ground_claims("wrote /mnt/user-data/outputs/report.md", roots=workspace)

    SwarmAggregator.apply_acceptance_verification(task, _PASSING_EVIDENCE)

    assert task.acceptance_status == "passed"


def test_a_foreign_shaped_grounding_field_is_ignored():
    task = SwarmTaskNode(task_id="t", objective="x", acceptance_criteria=list(_CRITERIA))
    task.claim_grounding = {"unresolved": 9, "claims_checked": 9}

    SwarmAggregator.apply_acceptance_verification(task, _PASSING_EVIDENCE)

    assert task.acceptance_status == "passed"


def test_no_grounding_ran_is_not_a_grounding_failure():
    task = SwarmTaskNode(task_id="t", objective="x", acceptance_criteria=list(_CRITERIA))

    SwarmAggregator.apply_acceptance_verification(task, _PASSING_EVIDENCE)

    assert task.acceptance_status == "passed"


def test_tasks_without_criteria_keep_not_required_and_ignore_grounding(workspace):
    task = SwarmTaskNode(task_id="t", objective="x")
    task.claim_grounding = ground_claims("backend/does/not/exist.py", roots=workspace)

    SwarmAggregator.apply_acceptance_verification(task, [])

    assert task.acceptance_status == "not_required"
    assert task.verification == {}


# --- runner persistence ---------------------------------------------------


@pytest.mark.asyncio
async def test_runner_persists_claim_grounding_only_after_accepting_worker_result(tmp_path, monkeypatch):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-runner-grounding",
        goal="exercise claim grounding persistence",
        mode=SwarmMode.PARALLEL,
        status="pending",
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="inspect", max_attempts=1)},
    )
    coordinator._swarms[plan.swarm_id] = plan

    class Worker:
        def execute_task(self, _task, _plan):
            return {"summary": "I updated backend/packages/harness/alpha/swarm/grounding.py:1", "tool_calls": 0}

    runner = AsyncSwarmRunner(coordinator, poll_interval=0.01)
    monkeypatch.setattr(runner, "_build_worker", lambda _task: Worker())

    result = await runner.run_swarm_async(plan.swarm_id)

    assert result["status"] == "completed"
    grounding = plan.tasks["task-1"].claim_grounding
    assert grounding["basis"] == GROUNDING_BASIS
    assert grounding["state"] == "clean"
    assert grounding["claims_checked"] == 1
    assert coordinator.get_swarm(plan.swarm_id).to_dict()["tasks"]["task-1"]["claim_grounding"] == grounding


# --- team report ----------------------------------------------------------


def _grounded_plan(text: str, workspace, task_id: str = "task-ground") -> SwarmPlan:
    return SwarmPlan(
        swarm_id=f"swm-{task_id}",
        goal="Review a repository change",
        mode=SwarmMode.PARALLEL,
        tasks={
            task_id: SwarmTaskNode(
                task_id=task_id,
                objective="Review the change",
                state=TaskNodeState.COMPLETED,
                result_summary=text,
                claim_grounding=ground_claims(text, roots=workspace),
            )
        },
    )


def test_team_report_renders_grounded_claims_with_their_limit(workspace):
    report = build_team_report(_grounded_plan("Updated src/app.py:1", workspace))

    rows = report["tasks_without_a_specialist"]
    assert rows and rows[0]["claim_grounding_status"] == "grounded"
    assert "proves the cited paths exist" in rows[0]["claim_grounding_detail"]
    assert "Claim grounding" in render_team_report_markdown(report)


def test_team_report_surfaces_unresolved_claims_as_unknown(workspace):
    report = build_team_report(_grounded_plan("Updated backend/does/not/exist.py:42", workspace))

    rows = report["tasks_without_a_specialist"]
    assert rows and rows[0]["claim_grounding_status"] == "unresolved_claims"
    assert "unresolved_claim_grounding" in {item["kind"] for item in report["unknown"]}
    assert "did not resolve" in render_team_report_markdown(report)


def test_team_report_does_not_fabricate_a_grounding_verdict():
    # An empty field means grounding never ran (external completion, a legacy
    # plan, a task that produced no claims).  Neither status nor note may be
    # rendered from absence.
    plan = SwarmPlan(
        swarm_id="swm-no-grounding",
        goal="Review a repository change",
        mode=SwarmMode.PARALLEL,
        tasks={"task-plain": SwarmTaskNode(task_id="task-plain", objective="Review the change", state=TaskNodeState.COMPLETED)},
    )

    report = build_team_report(plan)

    rows = report["tasks_without_a_specialist"]
    assert rows and rows[0]["claim_grounding_status"] is None
    assert rows[0]["claim_grounding_detail"] == ""
    assert "Claim grounding" not in render_team_report_markdown(report)


def test_team_report_vacuous_grounding_renders_no_line(workspace):
    # `not_checked` (nothing extractable) is not a pass and not a failure: it
    # renders nothing, same as an audit that never ran.
    report = build_team_report(_grounded_plan("The client/server split is fine as is.", workspace))

    rows = report["tasks_without_a_specialist"]
    assert rows and rows[0]["claim_grounding_status"] is None
    assert "Claim grounding" not in render_team_report_markdown(report)
