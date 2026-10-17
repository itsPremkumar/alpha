"""Pre-registration specification lint for swarm plans (S1).

**The gap this closes.**  The recorded run ``swm-91f4e0bd`` completed 5/5 tasks
with ``acceptance_status=not_required`` and a QA worker returned fabricated
repository paths: the acceptance overlay only executes when
``task.acceptance_criteria`` is set, so a plan could run and "complete" without
ever having been asked to prove anything.  ``lint_plan`` answers *before* the
first token: is this plan checkable?

What is pinned here:

* every finding code and severity, including the compat boundary that a task
  with **no** artifacts and no criteria warns rather than refuses;
* the refusal is pre-registration — no swarm record, no checkpoint, no event;
* the waiver is explicit and recorded, never implied: a refused plan can be
  admitted with ``acceptance_waiver=True`` and the decision stays readable on
  ``plan.metrics["spec_lint"]``;
* the tool surface is a ``lint`` **action** on the existing ``swarm`` tool
  (dry-run without a swarm id, live re-check with one), so ``BUILTIN_TOOLS``
  and the capability counts are untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

import alpha.swarm.coordinator as coord_mod
from alpha.swarm.coordinator import SwarmCoordinator, get_swarm_coordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from alpha.swarm.spec_lint import SPEC_LINT_BASIS, lint_plan
from alpha.tools.builtins.swarm_tool import swarm_tool


@pytest.fixture(autouse=True)
def _isolated_coordinator(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


def _task(task_id: str, objective: str = "Do the thing", **kwargs) -> SwarmTaskNode:
    return SwarmTaskNode(task_id=task_id, objective=objective, **kwargs)


def _plan(**tasks: SwarmTaskNode) -> SwarmPlan:
    return SwarmPlan(swarm_id="swm-lint", goal="g", mode=SwarmMode.PARALLEL, tasks=dict(tasks))


def _codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


# --- pure lint rules ------------------------------------------------------


def test_empty_objective_is_refused():
    report = lint_plan(_plan(t1=_task("t1", "")))

    assert report.refused is True
    assert "empty_objective" in _codes(report)
    message = report.refusal_message()
    assert message.startswith("swarm was refused before registration")
    assert "empty_objective" in message


def test_artifact_without_acceptance_is_refused_and_the_waiver_downgrades_it():
    plan = _plan(t1=_task("t1", "Write the report", output_artifacts=["reports/out.md"]))

    refused = lint_plan(plan)
    assert refused.refused is True
    assert "acceptance_missing" in _codes(refused)

    waived = lint_plan(plan, acceptance_waiver=True)
    assert waived.refused is False
    assert waived.count("refuse") == 0
    assert waived.acceptance_waiver is True
    assert "acceptance_waived" in _codes(waived)
    assert waived.to_dict()["acceptance_waiver"] is True


def test_a_task_without_criteria_warns_instead_of_refusing():
    # The compat boundary: blanket refusal here would refuse decomposed plans
    # and existing callers, so only an *artifact promise with no acceptance*
    # escalates.  The warn still states the consequence out loud.
    report = lint_plan(_plan(t1=_task("t1", "Summarize the findings")))

    assert report.refused is False
    finding = next(finding for finding in report.findings if finding.code == "acceptance_missing")
    assert finding.severity == "warn"
    assert "not_required" in finding.evidence


def test_vague_directive_warns_unless_something_checkable_is_declared():
    vague = lint_plan(_plan(t1=_task("t1", "Improve the codebase")))
    assert "vague_directive" in _codes(vague)

    with_artifact = lint_plan(_plan(t1=_task("t1", "Improve the codebase", output_artifacts=["reports/out.md"])))
    assert "vague_directive" not in _codes(with_artifact)

    with_criteria = lint_plan(_plan(t1=_task("t1", "Improve the codebase", acceptance_criteria=[{"id": "c1", "required_evidence_kinds": ["test_result"]}])))
    assert "vague_directive" not in _codes(with_criteria)


def test_duplicate_directives_warn_on_the_repeat_not_the_first():
    plan = _plan(
        t1=_task("t1", "Summarize the deployment risks"),
        t2=_task("t2", "Summarize  the   deployment risks"),
        t3=_task("t3", "Enumerate the rollback steps"),
    )

    report = lint_plan(plan)

    duplicates = [finding for finding in report.findings if finding.code == "duplicate_directive"]
    assert [finding.task_id for finding in duplicates] == ["t2"]
    assert "'t1'" in duplicates[0].evidence


def test_dangling_reference_warns_only_when_the_cited_path_resolves_nowhere():
    plan = _plan(
        t1=_task("t1", "Patch backend/does/not/exist.py:42 with the fix"),
        t2=_task("t2", "Read backend/packages/harness/alpha/swarm/grounding.py first"),
    )

    report = lint_plan(plan)

    danglings = [finding for finding in report.findings if finding.code == "dangling_reference"]
    assert [finding.task_id for finding in danglings] == ["t1"]
    assert "backend/does/not/exist.py" in danglings[0].evidence


def test_no_declared_write_scope_warns_only_for_declared_mutating_tags():
    declared = lint_plan(
        _plan(t1=_task("t1", "Run the migration script", capability_tags=["python_repl"], capability_source="declared")),
    )
    assert "no_declared_write_scope" in _codes(declared)

    inferred = lint_plan(
        _plan(t1=_task("t1", "Run the migration script", capability_tags=["python_repl"], capability_source="inferred")),
    )
    assert "no_declared_write_scope" not in _codes(inferred)

    scoped = lint_plan(
        _plan(t1=_task("t1", "Run the migration script", capability_tags=["python_repl"], capability_source="declared", worktree_path="worktrees/t1")),
    )
    assert "no_declared_write_scope" not in _codes(scoped)


def test_lint_is_pure_and_deterministic():
    plan = _plan(t1=_task("t1", "Summarize the findings"), t2=_task("t2", "Summarize the findings"))
    before = plan.to_dict()

    first = lint_plan(plan)
    second = lint_plan(plan)

    assert first.to_dict() == second.to_dict()
    assert plan.to_dict() == before
    assert first.to_dict()["basis"] == SPEC_LINT_BASIS


# --- admission: refusal happens before registration -----------------------


def _decompose_with_artifact(swarm_id: str = "refused-lint"):
    def decompose(**_kwargs: object) -> SwarmPlan:
        plan = SwarmPlan(swarm_id=swarm_id, goal="g", mode=SwarmMode.AUTO)
        plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Write the report", output_artifacts=["reports/out.md"])
        return plan

    return decompose


def test_an_uncheckable_specification_is_refused_before_registration():
    with TemporaryDirectory() as storage:
        coordinator = SwarmCoordinator(storage_dir=storage)
        with patch.object(SwarmTaskDecomposer, "decompose", side_effect=_decompose_with_artifact()):
            with pytest.raises(ValueError, match="refused before registration") as error:
                coordinator.create_swarm(goal="write the report")

        assert "acceptance_missing" in str(error.value)
        assert coordinator.list_swarms() == []
        assert list(Path(storage).glob("*.json")) == []


def test_the_waiver_admits_the_plan_and_records_the_decision():
    with TemporaryDirectory() as storage:
        coordinator = SwarmCoordinator(storage_dir=storage)
        with patch.object(SwarmTaskDecomposer, "decompose", side_effect=_decompose_with_artifact("accepted-waived")):
            created = coordinator.create_swarm(goal="write the report", acceptance_waiver=True)

        report = created.metrics["spec_lint"]
        assert report["acceptance_waiver"] is True
        assert report["refused"] is False
        assert "acceptance_waived" in {finding["code"] for finding in report["findings"]}
        assert coordinator.get_swarm("accepted-waived") is not None


def test_a_normal_admission_records_the_lint_report(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")

    created = coordinator.create_swarm(goal="Compare two deployment options for an internal tool")

    report = created.metrics["spec_lint"]
    assert report["basis"] == SPEC_LINT_BASIS
    assert report["refused"] is False
    assert isinstance(report["findings"], list)


# --- tool surface: the `lint` action --------------------------------------


def _spawn(goal: str = "Compare React vs Vue for an enterprise dashboard", **kwargs) -> str:
    response = swarm_tool.invoke({"action": "spawn", "goal": goal, **kwargs})
    assert "Autonomous Swarm Successfully Spawned" in response, response
    match = re.search(r"Swarm ID\*\*:\s*`([^`]+)`", response)
    assert match is not None, response
    return match.group(1)


def test_lint_dry_run_reports_findings_without_registering_anything():
    response = swarm_tool.invoke({"action": "lint", "goal": "Improve the deployment pipeline"})

    assert "Swarm Specification Lint" in response
    assert "(dry run)" in response
    assert "Verdict" in response
    assert get_swarm_coordinator().list_swarms() == []


def test_lint_requires_a_goal_or_a_registered_swarm():
    assert swarm_tool.invoke({"action": "lint"}) == "Error: 'goal' parameter is required for a dry-run lint (or pass an existing 'swarm_id')."


def test_lint_of_a_registered_swarm_reports_the_live_verdict():
    sid = _spawn()

    response = swarm_tool.invoke({"action": "lint", "swarm_id": sid})

    assert f"Swarm Specification Lint: `{sid}`" in response
    assert "(dry run)" not in response


def test_lint_of_an_unknown_swarm_fails_closed():
    response = swarm_tool.invoke({"action": "lint", "swarm_id": "swm-missing"})

    assert "not found or is not visible" in response


def test_spawn_records_the_waiver_flag_on_the_plan():
    default_sid = _spawn()
    default_plan = get_swarm_coordinator().get_swarm(default_sid)
    assert default_plan.metrics["spec_lint"]["acceptance_waiver"] is False

    waived_sid = _spawn(acceptance_waiver=True)
    waived_plan = get_swarm_coordinator().get_swarm(waived_sid)
    assert waived_plan.metrics["spec_lint"]["acceptance_waiver"] is True
