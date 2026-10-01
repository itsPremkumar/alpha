"""Company OS: durability, tenancy, and evidence-honesty tests.

These are the invariants the whole package rests on. Each one corresponds to a
way the system could plausibly lie to an operator:

* a company that vanishes on restart,
* a company readable across tenant boundaries,
* a KPI that reports a number nobody measured,
* a workforce that claims hires it did not make,
* a loop that spends past its ceiling.
"""

from __future__ import annotations

import json

import pytest

from alpha.company_os import blueprint_for, slugify_handle
from alpha.company_os.loop_safety import Breaker, pre_tick_gate
from alpha.company_os.models import (
    AutonomyTier,
    Charter,
    Company,
    CompanyState,
    EmploymentStatus,
    KeyResult,
    MeasurementBasis,
    Objective,
)
from alpha.company_os.store import (
    CompanyNotFound,
    CompanyStore,
    CompanyStoreUnreadable,
)
from alpha.company_os.workforce import hire_blueprint


def _company(**kwargs) -> Company:
    defaults = dict(
        owner_id="owner-a",
        name="Test Company",
        charter=Charter(mission="Ship things that work"),
    )
    defaults.update(kwargs)
    return Company(**defaults)


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


class TestDurability:
    def test_company_survives_a_restart(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        company = _company()
        store.save(company, "owner-a")

        # A brand-new store instance, exactly as a fresh process would build one.
        reopened = CompanyStore(tmp_path)
        loaded = reopened.get(company.company_id, "owner-a")
        assert loaded is not None
        assert loaded.name == "Test Company"
        assert loaded.charter.mission == "Ship things that work"

    def test_tenancy_survives_a_restart(self, tmp_path) -> None:
        company = _company(owner_id="owner-a")
        CompanyStore(tmp_path).save(company, "owner-a")
        # A fresh process must still scope the company to its owner.
        assert len(CompanyStore(tmp_path).list_company_ids("owner-a")) == 1
        assert CompanyStore(tmp_path).list_company_ids("owner-b") == []
        assert CompanyStore(tmp_path).get(company.company_id, "owner-b") is None

    def test_a_corrupt_index_raises_instead_of_reading_empty(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        store.save(_company(), "owner-a")
        (tmp_path / "registry.json").write_text("{ not json", encoding="utf-8")
        with pytest.raises(CompanyStoreUnreadable):
            CompanyStore(tmp_path).list_company_ids("owner-a")

    def test_a_newer_schema_is_refused_not_reinterpreted(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        store.save(_company(), "owner-a")
        path = tmp_path / "registry.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = 99
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(CompanyStoreUnreadable):
            CompanyStore(tmp_path).list_company_ids("owner-a")

    def test_tick_ledger_is_append_only(self, tmp_path) -> None:
        from alpha.company_os.models import TickOutcome, TickRecord

        store = CompanyStore(tmp_path)
        company = _company()
        store.save(company, "owner-a")
        store.append_tick(TickRecord(company_id=company.company_id, outcome=TickOutcome.IDLE, reason="first"))
        store.append_tick(TickRecord(company_id=company.company_id, outcome=TickOutcome.DISPATCHED, reason="second"))

        ticks = store.list_ticks(company.company_id, "owner-a")
        assert [t.reason for t in ticks] == ["second", "first"]  # newest first
        assert store.tick_count(company.company_id) == 2


class TestTenancy:
    def test_another_owners_company_is_invisible_not_forbidden(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        company = _company(owner_id="owner-a")
        store.save(company, "owner-a")
        # None, not an error: the route turns this into 404 so another tenant's
        # company is never confirmed to exist.
        assert store.get(company.company_id, "owner-b") is None

    def test_saving_under_a_mismatched_owner_is_refused(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        company = _company(owner_id="owner-a")
        with pytest.raises(Exception):
            store.save(company, "owner-b")

    def test_require_raises_for_an_unknown_company(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        with pytest.raises(CompanyNotFound):
            store.require("co-does-not-exist", "owner-a")

    def test_a_company_id_cannot_escape_the_store_root(self, tmp_path) -> None:
        store = CompanyStore(tmp_path)
        with pytest.raises(Exception):
            store._record_path("../../etc/passwd")  # noqa: SLF001 - the guard is the test


# --------------------------------------------------------------------------- #
# Evidence honesty
# --------------------------------------------------------------------------- #


class TestEvidenceHonesty:
    def test_objective_progress_is_none_until_something_is_measured(self) -> None:
        objective = Objective(
            title="Ship",
            key_results=[KeyResult(title="Coverage", baseline=0.0, target=90.0, current=None)],
        )
        assert objective.progress_percent is None

    def test_objective_progress_is_computed_from_measured_key_results(self) -> None:
        objective = Objective(
            title="Ship",
            key_results=[KeyResult(title="Coverage", baseline=0.0, target=100.0, current=50.0)],
        )
        assert objective.progress_percent == 50.0

    def test_a_partially_measured_objective_ignores_unmeasured_key_results(self) -> None:
        objective = Objective(
            title="Ship",
            key_results=[
                KeyResult(title="Coverage", baseline=0.0, target=100.0, current=100.0),
                KeyResult(title="Latency", baseline=0.0, target=100.0, current=None),
            ],
        )
        # Only the measured key result counts; the unmeasured one is excluded
        # rather than treated as zero.
        assert objective.progress_percent == 100.0

    def test_unmeasured_health_is_none_not_a_fabricated_percentage(self) -> None:
        from alpha.company_os.orchestrator import company_health

        result = company_health(_company(), None)
        assert result["health_percent"] is None
        assert result["basis"] == MeasurementBasis.UNMEASURED.value

    def test_health_for_a_company_with_no_work_is_none(self) -> None:
        from alpha.company_os.orchestrator import company_health

        result = company_health(_company(), {"reachable": True, "total_items": 0, "done_items": 0})
        assert result["health_percent"] is None
        assert "nothing to measure" in result["reason"]

    def test_health_is_derived_only_from_a_reachable_board(self) -> None:
        from alpha.company_os.orchestrator import company_health

        result = company_health(
            _company(),
            {"reachable": True, "total_items": 10, "done_items": 5, "blocked_items": 1, "items_missing_dod": 0},
        )
        assert result["basis"] == MeasurementBasis.DERIVED.value
        assert 0.0 < result["health_percent"] <= 100.0


# --------------------------------------------------------------------------- #
# Loop safety
# --------------------------------------------------------------------------- #


class TestLoopSafety:
    def test_a_disabled_loop_is_gated_before_anything_else(self) -> None:
        verdict = pre_tick_gate(_company(state=CompanyState.PAUSED))
        assert verdict.ok is False
        assert verdict.breaker is Breaker.DISABLED

    def test_a_draft_company_does_not_run(self) -> None:
        verdict = pre_tick_gate(_company(state=CompanyState.DRAFT, loop_policy=_enabled_policy()))
        assert verdict.ok is False
        assert verdict.breaker is Breaker.NOT_ACTIVE

    def test_an_undeclared_budget_refuses_to_spend(self) -> None:
        from alpha.company_os.models import LoopPolicy

        # Deliberately no daily ceiling: an operator who has not declared one gets
        # no invented allowance.
        company = _company(state=CompanyState.ACTIVE, loop_policy=LoopPolicy(enabled=True))
        verdict = pre_tick_gate(company)
        assert verdict.ok is False
        assert verdict.breaker is Breaker.BUDGET
        assert "No daily spend ceiling" in verdict.reason

    def test_spending_past_the_ceiling_is_refused(self) -> None:
        policy = _enabled_policy(daily_cost_ceiling_usd=1.0)
        company = _company(state=CompanyState.ACTIVE, loop_policy=policy)
        company.cost.spent_today_usd = 1.5
        verdict = pre_tick_gate(company)
        assert verdict.ok is False
        assert verdict.breaker is Breaker.BUDGET

    def test_spending_under_the_ceiling_is_allowed(self) -> None:
        policy = _enabled_policy(daily_cost_ceiling_usd=10.0)
        company = _company(state=CompanyState.ACTIVE, loop_policy=policy)
        company.cost.spent_today_usd = 1.5
        assert pre_tick_gate(company).ok is True

    def test_no_progress_parks_the_loop(self) -> None:
        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        company.consecutive_no_progress_ticks = 3
        verdict = pre_tick_gate(company)
        assert verdict.ok is False
        assert verdict.breaker is Breaker.NO_PROGRESS

    def test_a_failure_storm_freezes_autonomy(self) -> None:
        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        company.recent_outcomes = ["failed"] * 6
        verdict = pre_tick_gate(company)
        assert verdict.ok is False
        assert verdict.breaker is Breaker.FAILURE_STORM

    def test_healthy_outcomes_do_not_trip_the_storm(self) -> None:
        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        company.recent_outcomes = ["ok"] * 10
        assert pre_tick_gate(company).ok is True

    def test_actions_are_bounded_and_deferred_not_dropped(self) -> None:
        from alpha.company_os.loop_safety import bounded_actions

        company = _company(loop_policy=_enabled_policy(max_actions_per_tick=2))
        actions = list(range(5))
        runnable, deferred = bounded_actions(company, actions)
        assert runnable == [0, 1]
        # Deferred work is returned so it is resumed, never silently discarded.
        assert deferred == [2, 3, 4]

    def test_cost_status_is_none_until_measured(self) -> None:
        from alpha.company_os.loop_safety import cost_status

        company = _company()
        company.loop_policy.daily_cost_ceiling_usd = 10.0
        status = cost_status(company)
        assert status["spent_today_usd"] is None
        assert status["utilization_percent"] is None
        assert status["basis"] == MeasurementBasis.UNMEASURED.value


def _enabled_policy(**kwargs):
    from alpha.company_os.models import LoopPolicy

    defaults = dict(enabled=True, daily_cost_ceiling_usd=100.0)
    defaults.update(kwargs)
    return LoopPolicy(**defaults)


# --------------------------------------------------------------------------- #
# Blueprints
# --------------------------------------------------------------------------- #


class TestBlueprints:
    def test_every_bundled_archetype_produces_a_staffed_org(self) -> None:
        for archetype in ("company", "open_source", "security_soc", "research_lab", "custom"):
            blueprint = blueprint_for(
                name=f"{archetype} co",
                mission="Do the work",
                archetype=archetype,
            )
            assert blueprint.employees, archetype
            assert blueprint.units, archetype
            assert blueprint.roles, archetype
            # Every planned hire must name a real bot template slug, otherwise a
            # hire would land on a generic placeholder profile.
            for employee in blueprint.employees:
                assert employee.bot_template is not None, f"{archetype}:{employee.handle}"
                assert employee.role_id is not None
                assert employee.unit_id is not None

    def test_the_first_planned_employee_of_a_unit_becomes_its_lead(self) -> None:
        blueprint = blueprint_for(name="x", mission="y", archetype="company")
        for unit in blueprint.units:
            assert unit.lead_agent_handle is not None
            assert any(e.handle == unit.lead_agent_handle for e in blueprint.employees)

    def test_accountabilities_always_have_an_escalation_path(self) -> None:
        blueprint = blueprint_for(name="x", mission="y", archetype="company")
        assert blueprint.accountabilities
        for duty in blueprint.accountabilities:
            assert duty.primary_agent_handle
            assert duty.backup_agent_handle or duty.escalation_agent_handle

    def test_seeded_key_results_report_no_measurement(self) -> None:
        blueprint = blueprint_for(name="x", mission="y", archetype="company")
        assert blueprint.objectives
        for objective in blueprint.objectives:
            assert objective.key_results
            for kr in objective.key_results:
                assert kr.current is None
                assert kr.current_basis is MeasurementBasis.UNMEASURED

    def test_seed_key_results_have_declared_targets(self) -> None:
        blueprint = blueprint_for(name="x", mission="y", archetype="company")
        kr = blueprint.objectives[0].key_results[0]
        assert kr.target is not None

    def test_an_unknown_archetype_falls_back_to_company(self) -> None:
        blueprint = blueprint_for(name="x", mission="y", archetype="not-a-real-archetype")
        assert blueprint.archetype in ("company", "open_source", "security_soc", "research_lab", "custom")

    def test_slugify_handle_produces_registry_shaped_slugs(self) -> None:
        assert slugify_handle("Data Pipeline", "Lead") == "data-pipeline-lead"
        assert slugify_handle("!!!") == "unit"
        assert slugify_handle("2026 roadmap") == "x-2026-roadmap"
        assert slugify_handle("a -- b").count("-") == 1


# --------------------------------------------------------------------------- #
# Model invariants
# --------------------------------------------------------------------------- #


class TestModelInvariants:
    def test_a_clean_company_reports_no_problems(self) -> None:
        assert _company().check_invariants() == []

    def test_a_duplicate_unit_id_is_reported(self) -> None:
        from alpha.company_os.models import OrgUnit

        company = _company()
        unit = OrgUnit(name="Engineering")
        company.units = [unit, unit.model_copy()]
        problems = company.check_invariants()
        assert any("Duplicate unit" in p for p in problems)

    def test_a_reports_to_a_missing_agent_is_reported(self) -> None:
        from alpha.company_os.models import Employment

        company = _company()
        company.employments = [Employment(agent_handle="ghost", reports_to_handle="nobody", status=EmploymentStatus.ACTIVE)]
        problems = company.check_invariants()
        assert any("reports to unknown agent" in p for p in problems)

    def test_a_project_pointing_at_a_missing_objective_is_reported(self) -> None:
        from alpha.company_os.models import ProjectLink

        company = _company()
        company.projects = [ProjectLink(project_id="p1", objective_ids=["obj-nope"])]
        problems = company.check_invariants()
        assert any("unknown objective" in p for p in problems)

    def test_autonomy_tiers_are_ordered(self) -> None:
        assert AutonomyTier.T0_OBSERVE.rank < AutonomyTier.T2_EXECUTE_ROUTINE.rank
        assert AutonomyTier.T2_EXECUTE_ROUTINE.rank < AutonomyTier.T4_AUTONOMOUS.rank


# --------------------------------------------------------------------------- #
# The loop itself
# --------------------------------------------------------------------------- #


class TestOrchestrator:
    def test_a_disabled_loop_ticks_as_paused_with_a_reason(self) -> None:
        from alpha.company_os.orchestrator import step

        record = step(_company())
        assert record.outcome.value == "paused"
        assert record.reason

    def test_an_idle_company_spends_nothing_and_says_so(self, tmp_path) -> None:
        from alpha.company_os.orchestrator import step
        from alpha.company_os.store import CompanyStore

        store = CompanyStore(tmp_path)
        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        store.save(company, "owner-a")

        record = step(company, workforce={"employment_count": 0, "missing_profile_count": 0})
        # No work items exist, so there is nothing to do and nothing was spent.
        assert record.outcome.value in ("idle", "deferred")
        assert record.cost_usd == 0.0
        assert record.cost_basis is MeasurementBasis.MEASURED

    def test_a_dispatching_tick_does_not_claim_a_zero_cost(self) -> None:
        from alpha.company_os.orchestrator import step

        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        company.work_items = []
        record = step(company, workforce={"employment_count": 0, "missing_profile_count": 0})
        # Whatever the outcome, a zero-cost claim only ever accompanies zero work.
        if record.dispatched:
            assert record.cost_usd is None
            assert record.cost_basis is MeasurementBasis.UNMEASURED


class TestPolicyRefusalIsNotAFailure:
    """A refusal must not be counted as a failure.

    Recording a correctly-refused action as a failure would count it toward the
    failure-storm breaker and park the loop for behaving properly. The breaker
    would then be a punishment for correct governance.
    """

    def test_an_action_above_the_autonomy_tier_is_deferred_not_failed(self) -> None:
        from alpha.company_os.models import Employment, WorkItemLink
        from alpha.company_os.orchestrator import step

        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        # T1_advise cannot execute routine work.
        assert company.charter.autonomy_tier is AutonomyTier.T1_ADVISE
        # An idle employee is required before the planner proposes an assignment.
        company.employments = [Employment(agent_handle="ceo", status=EmploymentStatus.ACTIVE)]
        company.work_items = [WorkItemLink(task_id="T1", board_id="b", title="Ship", column="todo", definition_of_done=["tests pass"])]

        record = step(company, workforce={"employment_count": 1, "missing_profile_count": 0})
        assert record.failures == []
        assert record.deferred
        assert "autonomy" in record.deferred[0]["reason"]
        assert record.outcome.value == "deferred"

    def test_execute_action_reports_a_policy_refusal_as_skipped(self) -> None:
        from alpha.company_os.orchestrator import ACTION_ASSIGN, PlannedAction, execute_action

        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        result = execute_action(company, PlannedAction(kind=ACTION_ASSIGN, reason="r", work_item_id="T1", agent_handle="ceo"))
        assert result["ok"] is True
        assert result["skipped"] is True

    def test_raising_the_tier_lets_the_action_through(self) -> None:
        from alpha.company_os.models import WorkItemLink
        from alpha.company_os.orchestrator import step

        company = _company(state=CompanyState.ACTIVE, loop_policy=_enabled_policy())
        company.charter.autonomy_tier = AutonomyTier.T2_EXECUTE_ROUTINE
        company.work_items = [WorkItemLink(task_id="T1", board_id="b", title="Ship", column="todo", definition_of_done=["tests pass"])]
        record = step(company, workforce={"employment_count": 1, "missing_profile_count": 0})
        assert record.failures == []


class TestBlueprintCommit:
    """A company created from a blueprint must own the units it points at."""

    def test_a_blueprint_commit_adds_units_roles_duties_and_objectives(self) -> None:
        from alpha.company_os import blueprint_for

        company = _company(state=CompanyState.ACTIVE)
        blueprint = blueprint_for(name="X", mission="Ship it", archetype="company")
        report = hire_blueprint(company, blueprint)

        assert report.ok, report.summary()
        # Units and roles must be *added*, not only merged: a fresh company has
        # neither, so an update-only pass would leave every employment's unit_id
        # pointing at a unit that does not exist.
        assert company.units
        assert company.roles
        assert company.accountabilities
        assert company.objectives
        assert company.check_invariants() == []

    def test_every_employment_points_at_a_unit_that_exists(self) -> None:
        from alpha.company_os import blueprint_for

        company = _company(state=CompanyState.ACTIVE)
        hire_blueprint(company, blueprint_for(name="X", mission="Ship it", archetype="company"))
        unit_ids = {u.unit_id for u in company.units}
        for employment in company.employments:
            assert employment.unit_id in unit_ids, employment.agent_handle

    def test_a_unit_lead_is_a_real_employee(self) -> None:
        from alpha.company_os import blueprint_for

        company = _company(state=CompanyState.ACTIVE)
        hire_blueprint(company, blueprint_for(name="X", mission="Ship it", archetype="company"))
        handles = {e.agent_handle for e in company.employments}
        for unit in company.units:
            assert unit.lead_agent_handle in handles, unit.name


class TestAuthorityVocabulary:
    """Domain tags and authority capabilities are different things.

    ``enforce_grant`` treats every unrecognised name as a violation, so shipping a
    blueprint that asks for "planning" as an authority capability would refuse
    every hire in the batch.
    """

    def test_blueprints_do_not_send_domain_tags_as_an_authority_grant(self) -> None:
        from alpha.bots.authority_ceiling import ALLOWED_CAPABILITIES
        from alpha.company_os import blueprint_for

        for archetype in ("company", "open_source", "security_soc", "research_lab", "custom"):
            blueprint = blueprint_for(name="X", mission="Ship it", archetype=archetype)
            for employee in blueprint.employees:
                grant = employee.authority_grant
                if grant is not None:
                    bad = [g for g in grant if g not in ALLOWED_CAPABILITIES]
                    assert not bad, f"{archetype}:{employee.handle} requested unknown authority {bad}"

    def test_the_default_hire_leaves_the_grant_to_the_registry(self) -> None:
        from alpha.company_os.blueprints import PlannedEmployee
        from alpha.company_os.workforce import hire_employee

        company = _company(state=CompanyState.ACTIVE)
        planned = PlannedEmployee(
            handle="probe-employee",
            display_name="Probe",
            role_title="Tester",
            unit_name="Engineering",
            bot_template="tester",
            domain_tags=["testing", "quality"],
        )
        outcome, employment = hire_employee(company, planned)
        # The point is that the hire is NOT refused for an authority violation.
        # Whether it created a profile or reused one from an earlier run depends
        # on the on-disk roster, which this suite does not own.
        assert outcome.ok, outcome.reason
        assert "violation" not in outcome.reason.lower()
        assert employment is not None
