"""Tests for alpha.graph.adapter — the seams to existing Alpha subsystems.

The specialist-team tests are the important ones here. Alpha already
coordinates multiple agents: ``alpha.swarm`` owns the DAG, leases, budgets and
aggregator, and ``alpha.swarm.team.assign_specialists`` is the capability
filter with recorded rejection reasons. ``TEAM_RUNTIME.md`` states the rule the
graph plane follows -- "a team is not a new lifecycle, a new plan type, or a new
run record" -- so ``SpecialistTeamAdapter`` **projects onto** that system
instead of building a second agent registry (which is what the multi-agent plan
would otherwise have specified).

These tests therefore pin two things: that the projection is faithful to the
existing capability filter, and that it never invents coverage the swarm
runtime does not have.
"""

from __future__ import annotations

import pytest

from alpha.graph.adapter import SpecialistTeamAdapter
from alpha.graph.ledger import TaskLedger, TaskLedgerConfig
from alpha.graph.models import CapabilityRequirement, Goal, GoalMode, TaskNode
from alpha.swarm.team import (
    SpecialistProfile,
    register_roster_provider,
    unregister_roster_provider,
)


@pytest.fixture()
def ledger(tmp_path) -> TaskLedger:
    return TaskLedger(TaskLedgerConfig(database_path=tmp_path / "adapter.db"))


@pytest.fixture()
def goal(ledger: TaskLedger) -> Goal:
    goal = Goal.create(
        run_id="run-1",
        user_request="Fix the API bug",
        normalized_objective="Reproduce, fix, and verify the API failure",
        mode=GoalMode.EXECUTE,
    )
    ledger.create_goal(goal)
    return goal


@pytest.fixture()
def roster():
    """A real specialist roster, as the ``specialists:`` config section feeds it."""
    profiles = [
        SpecialistProfile(
            name="qa-engineer",
            role="tester",
            capabilities=["shell", "python", "repository-read"],
            agent_type="deep-test-synthesizer",
        ),
        SpecialistProfile(
            name="doc-writer",
            role="writer",
            capabilities=["documentation"],
            agent_type="general-purpose",
        ),
    ]
    register_roster_provider(lambda: profiles, source="test-roster")
    yield profiles
    unregister_roster_provider()


def _task(goal: Goal, *, title: str, description: str, capabilities: list[str] | None = None) -> TaskNode:
    return TaskNode.create(
        goal_id=goal.goal_id,
        title=title,
        description=description,
        required_capabilities=[CapabilityRequirement(capability=capability) for capability in (capabilities or [])],
    )


class TestSpecialistTeamAdapter:
    def test_projection_maps_task_fields(self, ledger: TaskLedger, goal: Goal) -> None:
        """The SwarmPlan projection carries the graph-plane task's own data."""
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Reproduce it", description="Run the request", capabilities=["shell"])
        ledger.create_task(task)

        plan = adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id))

        assert plan.goal == goal.normalized_objective
        assert task.task_id in plan.tasks
        projected = plan.tasks[task.task_id]
        assert projected.objective == "Run the request"
        assert projected.capability_tags == ["shell"]
        # A capability the task declared is strict, not a keyword guess.
        assert projected.capability_source == "declared"
        assert list(projected.dependencies) == []

    def test_declared_capability_is_strict_against_the_real_filter(self, ledger: TaskLedger, goal: Goal, roster) -> None:
        """A declared requirement is enforced by the existing hard filter."""
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Reproduce it", description="Run the request", capabilities=["shell"])
        ledger.create_task(task)

        assignment = adapter.assign(adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id)))

        assert [decision.get("specialist") for decision in assignment.decisions] == ["qa-engineer"]

    def test_uncovered_capability_is_a_named_gap_not_a_substitution(self, ledger: TaskLedger, goal: Goal, roster) -> None:
        """No generalist substitution: an unassignable task names what is missing."""
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Open the dashboard", description="Look at it", capabilities=["browser_navigation"])
        ledger.create_task(task)

        assignment = adapter.assign(adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id)))

        assert assignment.decisions == []
        gaps = adapter.capability_gaps(assignment)
        assert len(gaps) == 1
        assert "browser_navigation" in gaps[0]["required"]
        # The gap says why, not just that it failed.
        assert gaps[0].get("reason")

    def test_no_roster_is_disclosed_not_impersonated(self, ledger: TaskLedger, goal: Goal) -> None:
        """With no roster registered, the adapter says so rather than claiming a team."""
        unregister_roster_provider()
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Anything", description="Do it", capabilities=["shell"])
        ledger.create_task(task)

        assignment = adapter.assign(adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id)))

        assert assignment.roster_registered is False
        assert assignment.roster_provenance.get("reason")
        assert adapter.capability_gaps(assignment)

    def test_assignment_is_written_back_to_the_ledger(self, ledger: TaskLedger, goal: Goal, roster) -> None:
        """The ledger stays authoritative; the swarm's decision is copied into it."""
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Reproduce it", description="Run the request", capabilities=["shell"])
        ledger.create_task(task)
        assignment = adapter.assign(adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id)))

        updated = adapter.apply_assignments(ledger, goal.goal_id, assignment)

        assert [t.assigned_worker for t in updated] == ["qa-engineer"]
        reread = ledger.get_task(task.task_id)
        assert reread is not None and reread.assigned_worker == "qa-engineer"

    def test_inferred_requirement_is_a_hint_not_a_hard_conjunction(self, ledger: TaskLedger, goal: Goal, roster) -> None:
        """``capability_source`` must survive the projection.

        The swarm team module records that inferred tags were once written into
        the declaration field, so a four-tag keyword hint read as a hard
        conjunction and zero of five nodes could be assigned. This test pins the
        distinction through the adapter.
        """
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Check the failing endpoint", description="Audit the api endpoint for a security issue, run pytest, and check the test coverage")
        ledger.create_task(task)

        plan = adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id))
        projected = plan.tasks[task.task_id]

        assert projected.capability_source == "inferred"
        assert projected.capability_tags, "a directive with recognisable keywords should infer something"
        # Inferred means at least one tag must be covered -- qa-engineer covers
        # 'repository-read'/'shell', so it is assignable.
        assignment = adapter.assign(plan)
        assert assignment.decisions

    def test_projection_does_not_touch_swarm_state(self, ledger: TaskLedger, goal: Goal, roster) -> None:
        """This adapter answers 'who can take this', it is not a second scheduler."""
        adapter = SpecialistTeamAdapter()
        task = _task(goal, title="Reproduce it", description="Run the request", capabilities=["shell"])
        ledger.create_task(task)
        plan = adapter.build_plan(goal, ledger.list_tasks(goal_id=goal.goal_id))

        adapter.assign(plan)

        # No task state, no acceptance, no verification was written.
        assert projected_state_is_untouched(plan)
        # And the graph-plane task is still PENDING: assignment is ownership,
        # not execution.
        reread = ledger.get_task(task.task_id)
        assert reread is not None and reread.assigned_worker == ""
        assert reread.status.value == "pending"


def projected_state_is_untouched(plan) -> bool:
    """Every swarm task node must still be PENDING with no acceptance verdict."""
    from alpha.swarm.models import TaskNodeState

    return all(node.state is TaskNodeState.PENDING and node.acceptance_status == "not_required" and not node.verification and node.lease_id is None for node in plan.tasks.values())


class TestRunManagerAdapterProjection:
    def test_sync_task_is_a_projection_not_a_pass(self, ledger: TaskLedger, goal: Goal) -> None:
        """D5: ``sync_task`` used to be a bare ``pass``."""
        from alpha.graph.adapter import RunManagerAdapter

        recorded: list[tuple] = []

        class FakeRunManager:
            def get(self, run_id):
                return object()

            def set_status(self, run_id, status):
                recorded.append((run_id, status))

            def update_metadata(self, task_id, payload):
                recorded.append((task_id, payload))

        adapter = RunManagerAdapter(FakeRunManager())
        task = _task(goal, title="Reproduce it", description="Run the request")
        ledger.create_task(task)

        adapter.sync_task(ledger.get_task(task.task_id))

        assert recorded, "sync_task must project the task onto the run record"

    def test_sync_goal_maps_status(self, goal: Goal) -> None:
        from alpha.graph.adapter import RunManagerAdapter

        seen: list[tuple] = []

        class FakeRunManager:
            def get(self, run_id):
                return object()

            def set_status(self, run_id, status):
                seen.append((run_id, status))

        RunManagerAdapter(FakeRunManager()).sync_goal(goal)
        assert seen == [(goal.run_id, "running")]

    def test_sync_task_survives_a_run_manager_without_metadata(self, goal: Goal) -> None:
        """A projection failure is not a run failure."""
        from alpha.graph.adapter import RunManagerAdapter

        class BareRunManager:
            def get(self, run_id):
                return object()

        adapter = RunManagerAdapter(BareRunManager())
        task = _task(goal, title="Reproduce it", description="Run the request")
        adapter.sync_task(task)  # must not raise


class TestLeadAgentAdapterHonesty:
    def test_unbound_worker_never_reports_success(self, goal: Goal) -> None:
        """D4: the adapter returned ``succeeded`` for a task it never ran."""
        import asyncio

        from alpha.graph.adapter import LeadAgentAdapter

        adapter = LeadAgentAdapter(agent=object())
        task = _task(goal, title="Do research", description="Research the topic")
        result = asyncio.run(adapter.execute_task(task))

        assert result["outcome"] != "succeeded"
        assert result["reason"]
