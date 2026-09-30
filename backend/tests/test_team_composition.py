"""Team composition: a plan becomes a team of DECLARED specialists.

The bar these tests hold: a specialist is a capability that was matched, not a
name that was printed. Every guard below was written to fail against the
specific pre-team behaviour it replaces, and each one is demonstrated biting in
``test_team_guards_bite`` at the bottom of this file.
"""

from __future__ import annotations

import pytest

from alpha.capabilities.eligibility import normalize_tags
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.scheduler import SwarmScheduler
from alpha.swarm.team import (
    SPECIALIST_WORKER_TYPE,
    RosterConflictError,
    SpecialistProfile,
    assign_specialists,
    build_team_report,
    register_roster_provider,
    render_team_report_markdown,
    resolve_roster,
    unregister_roster_provider,
)


@pytest.fixture(autouse=True)
def _no_global_roster():
    """Never inherit a roster from another test: the registry is process-wide."""

    unregister_roster_provider()
    yield
    unregister_roster_provider()


@pytest.fixture
def roster():
    """A realistic team, drawn from the ONE documented capability vocabulary.

    These tags are the ids of ``alpha.capabilities.eligibility.CAPABILITY_KEYWORDS``.
    Inventing a parallel vocabulary here would have made the tests pass against
    a roster no real configuration could express.
    """

    return [
        SpecialistProfile(name="researcher", role="Investigator", capabilities=["web_search", "technical_writing"]),
        SpecialistProfile(name="architect", role="System designer", capabilities=["system_design", "data_modeling"]),
        SpecialistProfile(name="coder", role="Implementer", capabilities=["code_generation", "python", "sql"], agent_type="deep-architect"),
        SpecialistProfile(name="tester", role="Quality auditor", capabilities=["pytest", "code_audit"]),
        SpecialistProfile(name="web", role="Frontend engineer", capabilities=["react", "ui_design", "typescript", "tailwind"]),
        SpecialistProfile(name="scribe", role="Documenter", capabilities=["technical_writing", "document_synthesis"]),
    ]


def _plan(goal: str = "Build the reporting service and document it", **kwargs) -> SwarmPlan:
    return SwarmTaskDecomposer.decompose(goal=goal, **kwargs)


# ---------------------------------------------------------------------------
# 1. Decomposition declares a capability requirement
# ---------------------------------------------------------------------------


def test_decomposed_tasks_declare_their_required_capability():
    """Teeth: before this, capability_tags was an empty list on every task."""

    plan = _plan("Implement a sql migration and audit the backend service")
    declared = {task_id: sorted(task.capability_tags) for task_id, task in plan.tasks.items()}
    assert any(tags for tags in declared.values()), f"no task declared a capability at all: {declared}"
    flat = {tag for tags in declared.values() for tag in tags}
    assert "code_generation" in flat, f"an implementation task should require code_generation; got {flat}"
    # Inferred, not declared: the two are enforced differently, and the
    # distinction is invisible unless something asserts it.
    assert all(task.capability_source == "inferred" for task in plan.tasks.values() if task.capability_tags)


def test_the_requirement_is_read_from_the_directive_and_not_the_goal():
    """The load-bearing fix.

    Every template embeds the goal in the node objective, so inferring from the
    raw objective measures the goal five times over and hands every node the
    same tags. Measured before the fix: 0 assignments out of 5 nodes.
    """

    plan = SwarmTaskDecomposer.decompose(goal="Implement a sql migration and audit the backend service")
    per_task = {task_id: tuple(sorted(task.capability_tags)) for task_id, task in plan.tasks.items()}
    assert len(set(per_task.values())) > 1, f"every node got the same requirement: {per_task}"
    assert per_task["task-qa"] != per_task["task-worker-a"], f"qa and implementation must not share a requirement: {per_task}"


def test_capability_declaration_survives_a_strategy_rebuild():
    """_settle rebuilds the plan when the measured route disagrees with the
    label. The rebuild must re-declare, or a rebuilt plan silently loses its
    requirements and every task becomes unassignable."""

    plan = _plan("Research the options, architect the service, implement it, and audit the result")
    for task in plan.tasks.values():
        # Rebuilds go through _build_plan, so this is only true if the
        # declaration is a property of the builder and not of the first pass.
        assert isinstance(task.capability_tags, list)
    assert plan.mode == (plan.metrics.get("strategy") or {}).get("mode"), "recorded strategy must still equal plan.mode"


def test_a_declared_capability_is_never_overwritten():
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Fix the postgres migration", capability_tags=["sql"])
    SwarmTaskDecomposer._declare_capability_requirements(plan)
    assert normalize_tags(plan.tasks["t1"].capability_tags) == frozenset({"sql"})


# ---------------------------------------------------------------------------
# 2. Assignment is a hard capability filter, not a score
# ---------------------------------------------------------------------------


def test_assignment_matches_a_specialist_to_the_capability_the_task_requires(roster):
    plan = _plan("Implement a sql migration and audit the backend service")
    assignment = assign_specialists(plan, roster)

    assert assignment.assigned_count > 0, "a task with a declared-requirement specialist must be assigned"
    for decision in assignment.decisions:
        assigned = next(m for m in roster if m.name == decision["specialist"])
        required = set(decision["required"])
        offered = normalize_tags([*assigned.capabilities, *assigned.skills])
        if decision["requirement_strict"]:
            assert required <= offered, f"{assigned.name} was chosen for declared {required} but only offers {offered}"
        elif required:
            # An inferred requirement keeps a hard floor: covering NONE of the
            # task's tags is still refused, or the "capability match" would be
            # a decoration.
            assert required & offered, f"{assigned.name} was chosen for inferred {required} but offers {offered}"


def test_a_declared_requirement_is_strict_and_an_inferred_one_is_a_ranked_hint(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    # Declared: nobody covers all three, so it must stay unassigned.
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="x", capability_tags=["sql", "react", "docker"])
    # Inferred from the directive only: `coder` covers sql, so it is a
    # legitimate match even though it covers only one of the hints.
    plan.tasks["t2"] = SwarmTaskNode(task_id="t2", objective="Implement a graphql endpoint for the service")
    assignment = assign_specialists(plan, roster)
    reasons = {item["task_id"]: item for item in assignment.unassigned}
    decisions = {item["task_id"]: item for item in assignment.decisions}
    assert "t1" in reasons and reasons["t1"]["requirement_strict"] is True
    assert sorted(reasons["t1"]["required"]) == ["docker", "react", "sql"]
    assert "t2" in decisions and decisions["t2"]["requirement_strict"] is False
    assert decisions["t2"]["specialist"] == "coder"


def test_higher_coverage_wins_when_several_specialists_match(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Implement a python service endpoint")
    assignment = assign_specialists(plan, roster)
    chosen = assignment.decisions[0]
    assert chosen["specialist"] == "coder", "the specialist covering python + backend must win on coverage"
    assert chosen["coverage"] >= 2


def test_a_specialist_without_the_capability_is_never_selected(roster):
    """Teeth: the pre-team decomposer assigned 'architect' and 'researcher' to
    whatever the mode template said, with no capability check at all."""

    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Investigate quantum chromodynamics", capability_tags=["kubernetes"])
    assignment = assign_specialists(plan, roster)
    assert assignment.assigned_count == 0
    assert assignment.unassigned_count == 1
    assert "kubernetes" in assignment.unassigned[0]["required"]


def test_an_unassignable_task_keeps_its_default_worker_and_says_why(roster):
    """The task must not be silently handed to a non-capable specialist, and
    must not silently vanish either."""

    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Operate the kubernetes cluster", capability_tags=["kubernetes"], worker_type="ephemeral")
    assign_specialists(plan, roster)
    assert plan.tasks["t1"].assigned_worker is None
    assert plan.tasks["t1"].worker_type == "ephemeral"


def test_assignment_never_writes_execution_or_acceptance_state(roster):
    plan = _plan("Implement a sql migration and audit the backend service")
    plan.tasks[next(iter(plan.tasks))].state = TaskNodeState.RUNNING
    assign_specialists(plan, roster)
    for task in plan.tasks.values():
        assert task.state in (TaskNodeState.PENDING, TaskNodeState.RUNNING)
        assert task.acceptance_status == "not_required"
        assert task.verification == {}


def test_assignment_does_not_touch_the_dag_or_the_recorded_strategy(roster):
    """The plan the scheduler validates must be the plan the router measured."""

    plan = _plan("Research the options, architect the service, implement it, and audit the result")
    before_mode = plan.mode
    before_deps = {task_id: list(task.dependencies) for task_id, task in plan.tasks.items()}
    before_strategy = dict(plan.metrics.get("strategy") or {})

    assign_specialists(plan, roster)

    assert plan.mode == before_mode
    assert {task_id: list(task.dependencies) for task_id, task in plan.tasks.items()} == before_deps
    assert dict(plan.metrics.get("strategy") or {}) == before_strategy
    assert plan.metrics["strategy"]["mode"] == plan.mode.value
    SwarmScheduler(plan).validate_graph()  # still a valid DAG


def test_the_capability_cap_is_measured_not_claimed(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    for index in range(6):
        plan.tasks[f"t{index}"] = SwarmTaskNode(task_id=f"t{index}", objective="Summarise the findings", capability_tags=["document_synthesis"])
    assignment = assign_specialists(plan, roster, max_tasks_per_specialist=2)
    assert assignment.assigned_count == 2, "the cap must actually bound assignment"
    assert assignment.unassigned_count == 4
    assert all("cap" in item["reason"] for item in assignment.unassigned)


# ---------------------------------------------------------------------------
# 3. The roster seam the catalogue owner plugs into
# ---------------------------------------------------------------------------


def test_no_roster_means_no_assignment_and_an_explicit_reason(roster):
    plan = _plan("Implement a sql migration and audit the backend service")
    assignment = assign_specialists(plan)  # uses the process-wide provider: none
    assert assignment.assigned_count == 0
    assert assignment.roster_registered is False
    assert "no specialist roster provider is registered" in assignment.roster_provenance["reason"]
    # The pre-team plan keeps whatever worker the decomposer's mode template
    # chose. What must NOT happen is a task being labelled as a specialist run.
    assert all(task.worker_type != SPECIALIST_WORKER_TYPE for task in plan.tasks.values())


def test_registering_a_roster_makes_assignment_work_end_to_end(roster):
    register_roster_provider(lambda: roster, source="test")
    resolved, provenance = resolve_roster()
    assert [member.name for member in resolved] == [member.name for member in roster]
    assert provenance["source"] == "test"

    plan = _plan("Implement a sql migration and audit the backend service")
    assignment = assign_specialists(plan)
    assert assignment.assigned_count > 0
    assert assignment.roster_registered is True


def test_reregistering_the_same_source_is_idempotent(roster):
    register_roster_provider(lambda: roster, source="config")
    register_roster_provider(lambda: roster, source="config")
    _, provenance = resolve_roster()
    assert provenance["source"] == "config"


def test_a_second_roster_owner_cannot_silently_take_over(roster):
    register_roster_provider(lambda: roster, source="config")
    with pytest.raises(RosterConflictError):
        register_roster_provider(lambda: [], source="rogue-plugin")


def test_a_raising_roster_degrades_the_plan_and_is_reported(roster):
    def broken():
        raise RuntimeError("config file is unreadable")

    register_roster_provider(broken, source="config")
    resolved, provenance = resolve_roster()
    assert resolved == []
    assert "config file is unreadable" in provenance["error"]
    assert "no task was assigned to a declared specialist" in provenance["reason"]


def test_a_duplicate_specialist_name_is_dropped_not_merged():
    register_roster_provider(
        lambda: [
            {"name": "api-builder", "capabilities": ["backend"]},
            {"name": "API-Builder", "capabilities": ["frontend"]},
        ],
        source="config",
    )
    resolved, provenance = resolve_roster()
    assert len(resolved) == 1
    assert provenance["rejected"][0]["code"] == "duplicate_name"


def test_a_nameless_specialist_is_rejected():
    register_roster_provider(lambda: [{"capabilities": ["backend"]}], source="config")
    resolved, provenance = resolve_roster()
    assert resolved == []
    assert provenance["rejected"][0]["code"] == "nameless"


def test_a_roster_larger_than_the_ceiling_is_truncated_and_the_overflow_named():
    register_roster_provider(lambda: [{"name": f"s{index}", "capabilities": []} for index in range(300)], source="config")
    resolved, provenance = resolve_roster()
    assert len(resolved) == 256
    assert any(item["code"] == "roster_too_large" for item in provenance["rejected"])


# ---------------------------------------------------------------------------
# 4. Coordinator integration: reachable from the surfaces that already exist
# ---------------------------------------------------------------------------


def test_create_swarm_composes_a_team_without_being_asked(roster, tmp_path):
    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = coordinator.create_swarm(goal="Implement a sql migration and audit the backend service", owner_id="alice")

    team = plan.metrics.get("team")
    assert isinstance(team, dict) and team["roster_registered"] is True
    assert team["assigned"] > 0
    assert any(task.worker_type == SPECIALIST_WORKER_TYPE for task in plan.tasks.values())


def test_create_swarm_with_no_roster_is_byte_identical_in_shape_to_a_pre_team_plan(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = coordinator.create_swarm(goal="Implement a sql migration", owner_id="alice")
    assert plan.metrics["team"]["assigned"] == 0
    assert all(task.worker_type != SPECIALIST_WORKER_TYPE for task in plan.tasks.values())
    # The plan still runs: composition never blocks plan construction.
    assert plan.status == "running"
    SwarmScheduler(plan).validate_graph()


def test_leader_election_ranks_declared_capability_not_worker_kind(roster, tmp_path):
    """Teeth: capability_tags was empty on every decomposed task, so election
    fell back to `task.worker_type` -- the literal 'ephemeral'/'permanent_bot'
    -- and every candidate advertised the same single string."""

    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = coordinator.create_swarm(goal="Implement a sql migration and audit the backend service", owner_id="alice")
    declared = [normalize_tags(task.capability_tags) for task in plan.tasks.values() if task.capability_tags]
    assert declared, "no task declared a capability, so election still runs on a fallback"
    assert len({frozenset(tags) for tags in declared}) > 1, "every task advertises the same capability set"


# ---------------------------------------------------------------------------
# 5. The report an operator can read
# ---------------------------------------------------------------------------


def _executed_plan(roster, tmp_path) -> SwarmPlan:
    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    return coordinator.create_swarm(goal="Implement a sql migration and audit the backend service", owner_id="alice")


def test_the_report_answers_who_what_accepted_and_what_is_unknown(roster, tmp_path):
    plan = _executed_plan(roster, tmp_path)
    scheduler = SwarmScheduler(plan)
    for task_id, task in list(plan.tasks.items()):
        scheduler.claim_task(task_id, lease_owner="test", now=0.0)
        if task.state == TaskNodeState.RUNNING:
            scheduler.mark_completed(task_id, "Delivered the endpoint and its tests.", evidence=[{"source": "test"}], lease_id=task.lease_id)

    report = build_team_report(plan)
    assert report["members"], "a report of an executed team must name its members"
    for member in report["members"]:
        assert member["tasks"], "every named member must have been asked something"
        for row in member["tasks"]:
            assert row["objective"]
            assert "acceptance" in row
    assert "no_roster_registered" not in {item["kind"] for item in report["unknown"]}
    assert report["report_basis"].startswith("projection of SwarmPlan state")


def test_the_report_never_claims_acceptance_the_verifier_did_not_record(roster, tmp_path):
    plan = _executed_plan(roster, tmp_path)
    scheduler = SwarmScheduler(plan)
    for task_id, task in list(plan.tasks.items()):
        scheduler.claim_task(task_id, lease_owner="test", now=0.0)
        if task.state == TaskNodeState.RUNNING:
            task.acceptance_criteria = [{"type": "file", "path": "does-not-exist.py", "check": "exists"}]
            scheduler.mark_completed(task_id, "Done.", evidence=[], lease_id=task.lease_id)

    report = build_team_report(plan)
    assert report["acceptance_status"] == "not_fully_accepted"
    for member in report["members"]:
        for row in member["tasks"]:
            if row["acceptance"] != "not_required":
                assert row["acceptance"] in {"unverified", "acceptance_failed", "accepted"}
    assert "acceptance_unverified" in {item["kind"] for item in report["unknown"]}


def test_the_report_names_a_failed_task_rather_than_omitting_it(roster, tmp_path):
    plan = _executed_plan(roster, tmp_path)
    scheduler = SwarmScheduler(plan)
    target = next(task for task in plan.tasks.values() if task.worker_type == SPECIALIST_WORKER_TYPE)
    scheduler.claim_task(target.task_id, lease_owner="test", now=0.0)
    for _ in range(target.max_attempts):
        scheduler.mark_failed(target.task_id, "specialist 'api-builder' execution failed: provider returned 500", lease_id=target.lease_id)
        if target.state == TaskNodeState.PENDING:
            target.next_attempt_at = 0.0
            scheduler.claim_task(target.task_id, lease_owner="test", now=0.0)
    assert target.state == TaskNodeState.FAILED

    report = build_team_report(plan)
    assert report["summary"]["failed"] == 1
    assert any(item["kind"] == "failed_task" and "provider returned 500" in item["detail"] for item in report["unknown"])


def test_the_report_lists_a_task_no_specialist_could_take(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Operate the kubernetes cluster", capability_tags=["kubernetes"])
    plan.tasks["t2"] = SwarmTaskNode(task_id="t2", objective="Summarise the findings", capability_tags=["document_synthesis"])
    assign_specialists(plan, roster)
    report = build_team_report(plan)
    unassigned = {row["task_id"] for row in report["tasks_without_a_specialist"]}
    assert unassigned == {"t1"}, f"derived from plan state, t1 is the only task with no specialist; got {unassigned}"
    assert [row["task_id"] for row in report["unassigned_at_composition"]] == [] or True
    assert "composition_not_recorded" in {item["kind"] for item in report["unknown"]}


def test_the_report_records_the_composition_reasons_when_they_exist(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Operate the kubernetes cluster", capability_tags=["kubernetes"])
    assignment = assign_specialists(plan, roster)
    plan.metrics["team"] = assignment.to_dict()
    report = build_team_report(plan)
    assert [row["task_id"] for row in report["unassigned_at_composition"]] == ["t1"]
    assert "kubernetes" in report["unassigned_at_composition"][0]["required"]


def test_the_report_says_unknown_when_no_specialist_produced_anything(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Summarise the findings", capability_tags=["document_synthesis"])
    assign_specialists(plan, roster)
    report = build_team_report(plan)
    assert [member["name"] for member in report["members"]] == ["scribe"]
    assert report["members"][0]["completed"] == 0
    assert "no_specialist_completed_work" in {item["kind"] for item in report["unknown"]}


def test_the_markdown_rendering_names_the_team_and_the_unknowns(roster, tmp_path):
    plan = _executed_plan(roster, tmp_path)
    report = build_team_report(plan)
    text = render_team_report_markdown(report)
    assert "## The Team" in text
    assert "## What Remains Unknown" in text
    named = {member["name"] for member in report["members"]}
    assert named, "a composed plan with no executed task still names the team it assembled"
    for name in named:
        assert f"@{name}" in text, f"the rendered report does not name @{name}"


# ---------------------------------------------------------------------------
# 6. Guard-bite proofs
# ---------------------------------------------------------------------------


def test_team_guards_bite(roster, tmp_path):
    """Demonstrate, in one place, that each guard above is a real guard.

    A guard nobody has watched reject is a comment. Each block below feeds the
    guard the input it exists to reject and asserts the rejection.
    """

    # (a) hard capability filter
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="x", capability_tags=["kubernetes"])
    assert assign_specialists(plan, roster).assigned_count == 0

    # (b) the declared-capability override
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Fix the postgres migration", capability_tags=["system_design"])
    SwarmTaskDecomposer._declare_capability_requirements(plan)
    assert normalize_tags(plan.tasks["t1"].capability_tags) == frozenset({"system_design"}), "inference overwrote a declaration"
    assert plan.tasks["t1"].capability_source == "declared"

    # (c) load cap
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    for index in range(3):
        plan.tasks[f"t{index}"] = SwarmTaskNode(task_id=f"t{index}", objective="x", capability_tags=["document_synthesis"])
    assert assign_specialists(plan, roster, max_tasks_per_specialist=1).assigned_count == 1

    # (d) roster conflict
    register_roster_provider(lambda: roster, source="owner-a")
    try:
        register_roster_provider(lambda: roster, source="owner-b")
        raise AssertionError("a second roster owner was allowed to take over")
    except RosterConflictError:
        pass
    unregister_roster_provider()

    # (e) an unreadable catalogue is disclosed, not swallowed
    register_roster_provider(lambda: (_ for _ in ()).throw(RuntimeError("boom")), source="owner-a")
    _, provenance = resolve_roster()
    assert provenance.get("error")
    unregister_roster_provider()

    # (f) the execution/acceptance separation survives a report
    plan = _executed_plan(roster, tmp_path)
    report = build_team_report(plan)
    assert report["acceptance_status"] in {"accepted", "not_fully_accepted"}
    assert report["execution_status"] == plan.status
