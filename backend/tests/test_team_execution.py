"""Execution and reconciliation for a team of specialists.

Three claims are under test, and each is the kind this repository has been
burned by before:

1. A specialist that FAILS is recorded as failed, with the evidence, and never
   as a success.
2. Execution status stays separate from acceptance status, and duplicate voters
   or unverified acceptance evidence cannot produce a clean approval.
3. An unresolved panel says ``unresolved``.

Everything here runs through the real ``AsyncSwarmRunner`` and the real
``SwarmAggregator``. No lifecycle is stubbed: the coordinator owns the plan,
the scheduler owns the leases, and the aggregator owns the terminal decision.
The only substitution is the *worker backend*, which is the provider-facing
seam the swarm already exposes for exactly this purpose.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.scheduler import SwarmScheduler
from alpha.swarm.team import SPECIALIST_WORKER_TYPE, SpecialistProfile, assign_specialists, register_roster_provider, unregister_roster_provider
from alpha.swarm.worker import SpecialistSubagentWorker

RUNTIME = 0.05
LEASE = 5.0


# ---------------------------------------------------------------------------
# The subagent executor seam
# ---------------------------------------------------------------------------
# `tests/conftest.py` replaces `sys.modules["alpha.subagents.executor"]` with a
# MagicMock to break an import cycle, so the real `SubagentExecutor`,
# `SubagentResult` and `SubagentStatus` are NOT importable anywhere in this
# suite. That is a real limit on what these tests can prove, and it is why the
# worker is tested by substituting the executor's module-level symbols and
# driving the result through them, rather than by pretending to exercise the
# real delegation stack. The real stack is out of reach of the test suite, not
# merely out of scope of these tests.
# ---------------------------------------------------------------------------

COMPLETED = "status.completed"
FAILED = "status.failed"
TIMED_OUT = "status.timed_out"
CANCELLED = "status.cancelled"


def _stub_executor(monkeypatch, result):
    """Substitute the two module-level symbols the worker resolves at call time.

    ``SubagentStatus`` is replaced with a plain namespace of sentinels rather
    than poked at through the conftest's MagicMock, so the worker's
    ``status is not SubagentStatus.COMPLETED`` identity comparison is a real
    comparison between two known objects instead of an auto-created mock
    attribute that would match almost anything.
    """

    import alpha.subagents.executor as executor_mod

    calls: list[dict] = []

    class SimpleExecutor:
        def execute(self, task, result_holder=None):
            return result

    def factory(**kwargs):
        calls.append(kwargs)
        return SimpleExecutor()

    monkeypatch.setattr(executor_mod, "SubagentStatus", SimpleNamespace(COMPLETED=COMPLETED, FAILED=FAILED, TIMED_OUT=TIMED_OUT, CANCELLED=CANCELLED))
    monkeypatch.setattr(executor_mod, "SubagentExecutor", factory)
    return calls


def _fake_result(status, *, result=None, error=None, records=None, receipts=None, stop_reason=None):
    return SimpleNamespace(
        status=status,
        result=result,
        error=error,
        stop_reason=stop_reason,
        token_usage_records=records or [],
        tool_receipts=receipts,
        bash_executions=[],
    )


@pytest.fixture(autouse=True)
def _no_global_roster():
    unregister_roster_provider()
    yield
    unregister_roster_provider()


@pytest.fixture
def roster():
    return [SpecialistProfile(name="coder", capabilities=["code_generation", "python", "sql"], agent_type="deep-architect")]


class _ExplodingWorker:
    """A specialist whose provider call fails, the way a real one does."""

    def __init__(self, name: str):
        self.name = name

    def set_context(self, context):
        self.context = context

    def execute_task(self, task, plan):
        raise RuntimeError(f"specialist {self.name!r} execution failed: provider returned HTTP 500")


class _MalformedWorker:
    """A backend that returns the wrong type, which the runner must reject."""

    def set_context(self, context):
        self.context = context

    def execute_task(self, task, plan):
        return "I did the thing"


def _coordinator_with(plan: SwarmPlan, tmp_path) -> SwarmCoordinator:
    """Register *plan* on a coordinator so the runner can find it.

    The plan was already built by a coordinator (composition happens in
    ``create_swarm``); the runner resolves it through ``get_swarm``, so the same
    plan object is re-registered rather than being decomposed a second time.
    """

    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    coordinator._swarms[plan.swarm_id] = plan
    return coordinator


def _team_plan(roster, tmp_path, *, requires_consensus: bool = False) -> SwarmPlan:
    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = coordinator.create_swarm(goal="Implement the sql migration and the python service", owner_id="alice", requires_consensus=requires_consensus)
    assert plan.metrics["team"]["assigned"] > 0, "composition produced no specialists, so this test proves nothing"
    return plan


# ---------------------------------------------------------------------------
# 1. A failing specialist is a failure, and it carries its evidence
# ---------------------------------------------------------------------------


def test_a_specialist_that_raises_is_recorded_failed_with_the_reason(roster, tmp_path, monkeypatch):
    plan = _team_plan(roster, tmp_path)
    runner = AsyncSwarmRunner(_coordinator_with(plan, tmp_path), poll_interval=RUNTIME, lease_seconds=LEASE)
    monkeypatch.setattr(runner, "_build_worker", lambda node: _ExplodingWorker(node.assigned_worker or "unknown"))

    result = asyncio.run(runner.run_swarm_async(plan.swarm_id))

    assert result["status"] == "failed", f"a plan whose every specialist failed must not read clean: {result}"
    failed = [task for task in plan.tasks.values() if task.state == TaskNodeState.FAILED]
    assert failed, f"no task was marked failed: {[(t.task_id, str(t.state), t.error_message) for t in plan.tasks.values()]}"

    # Only the node the provider actually failed on carries the provider error.
    # Its dependents are failed by the scheduler with an honest, *different*
    # reason, and conflating the two would attribute a 500 to a node that
    # never called a provider.
    provider_failures = [task for task in failed if "provider returned HTTP 500" in (task.error_message or "")]
    assert provider_failures, f"no task carries the provider error: {[(t.task_id, t.error_message) for t in failed]}"
    for task in failed:
        if task not in provider_failures:
            assert "unrunnable" in (task.error_message or ""), f"a downstream node failed for an unnamed reason: {task.task_id} {task.error_message}"
        assert task.result_summary is None, f"a failed task must not carry a summary: {task.task_id}"


def test_a_backend_returning_the_wrong_shape_is_a_failure(roster, tmp_path, monkeypatch):
    plan = _team_plan(roster, tmp_path)
    runner = AsyncSwarmRunner(_coordinator_with(plan, tmp_path), poll_interval=RUNTIME, lease_seconds=LEASE)
    monkeypatch.setattr(runner, "_build_worker", lambda node: _MalformedWorker())

    result = asyncio.run(runner.run_swarm_async(plan.swarm_id))

    assert result["status"] != "completed"
    assert any(task.state == TaskNodeState.FAILED for task in plan.tasks.values())


def test_a_failed_specialist_run_reaches_the_incident_ledger(roster, tmp_path, monkeypatch):
    """The failure must be auditable after the fact, not only on the task node."""

    from alpha.swarm.incidents import get_swarm_incident_manager

    plan = _team_plan(roster, tmp_path)
    runner = AsyncSwarmRunner(_coordinator_with(plan, tmp_path), poll_interval=RUNTIME, lease_seconds=LEASE)
    monkeypatch.setattr(runner, "_build_worker", lambda node: _ExplodingWorker(node.assigned_worker or "unknown"))
    asyncio.run(runner.run_swarm_async(plan.swarm_id))

    incidents = get_swarm_incident_manager().get_incidents(plan.swarm_id)
    assert incidents, "a run whose every specialist failed recorded no incident"
    # The provider's text is on ``error_message``; ``reason`` is the recovery
    # verdict ("no eligible succession fallback"), and reading the wrong field
    # is how a test passes without proving anything.
    assert any("provider returned HTTP 500" in (incident.error_message or "") for incident in incidents), [incident.to_dict() for incident in incidents]
    assert any(incident.worker_type in {"specialist", "permanent_bot"} for incident in incidents)


# ---------------------------------------------------------------------------
# 2. The specialist worker itself: it must not manufacture a success
# ---------------------------------------------------------------------------


def _plan() -> SwarmPlan:
    return SwarmPlan(swarm_id="swm-t", goal="Ship it", mode=SwarmMode.PARALLEL)


def test_the_specialist_worker_refuses_to_run_without_a_tool_pool():
    worker = SpecialistSubagentWorker("coder", tool_pool=[], tool_pool_error="specialist tool pool unavailable: MCP timeout")
    with pytest.raises(RuntimeError, match="MCP timeout"):
        worker.execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())


def test_the_specialist_worker_refuses_an_empty_tool_pool_even_without_a_recorded_error():
    worker = SpecialistSubagentWorker("coder", tool_pool=[])
    with pytest.raises(RuntimeError, match="no tool pool was assembled"):
        worker.execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())


def test_the_specialist_worker_raises_on_a_non_completed_status(monkeypatch):
    """Teeth: the pre-team worker returned status='success' unconditionally."""

    _stub_executor(monkeypatch, _fake_result(FAILED, result=None, error="model refused", stop_reason="token_capped"))
    with pytest.raises(RuntimeError) as excinfo:
        SpecialistSubagentWorker("coder", tool_pool=[object()]).execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())
    text = str(excinfo.value)
    assert "model refused" in text
    assert "token_capped" in text, "a capped run must name the cap, not just say it failed"


def test_the_specialist_worker_names_a_timeout_separately_from_a_failure(monkeypatch):
    _stub_executor(monkeypatch, _fake_result(TIMED_OUT, result=None, error="Execution timed out after 1800 seconds"))
    with pytest.raises(RuntimeError) as excinfo:
        SpecialistSubagentWorker("coder", tool_pool=[object()]).execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())
    assert "Execution timed out" in str(excinfo.value)


def test_the_specialist_worker_raises_on_an_empty_completed_result(monkeypatch):
    _stub_executor(monkeypatch, _fake_result(COMPLETED, result="   "))
    with pytest.raises(RuntimeError, match="empty response"):
        SpecialistSubagentWorker("coder", tool_pool=[object()]).execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())


def test_the_specialist_worker_refuses_an_unknown_agent_type():
    """A specialist that silently runs as a generalist is the exact claim this
    worker exists to stop making."""

    worker = SpecialistSubagentWorker("coder", agent_type="deep-aeronautics", tool_pool=[object()])
    with pytest.raises(RuntimeError, match="unknown agent_type"):
        worker.execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())


def test_the_specialist_worker_reports_child_measured_usage_and_its_absence(monkeypatch):
    reported = _fake_result(
        COMPLETED,
        result="done",
        records=[{"input_tokens": 10, "output_tokens": 4}, {"input_tokens": 1, "output_tokens": 1}],
        receipts=[{"tool": "read_file"}, {"tool": "bash"}],
    )
    _stub_executor(monkeypatch, reported)
    outcome = SpecialistSubagentWorker("coder", tool_pool=[object()]).execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())
    assert outcome["summary"] == "done"
    assert outcome["usage"] == {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16}
    assert outcome["usage_source"] == "subagent_reported"
    assert any(item.get("source") == "tool_receipts" and item["count"] == 2 for item in outcome["evidence"])

    _stub_executor(monkeypatch, _fake_result(COMPLETED, result="done", records=[], receipts=None))
    outcome = SpecialistSubagentWorker("coder", tool_pool=[object()]).execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())
    assert outcome["usage_source"] == "unreported_by_provider", "an absent usage report must not read as a measured zero"
    assert outcome["usage"]["total_tokens"] == 0


def test_a_specialist_receives_the_shared_pool_and_its_declared_model(monkeypatch):
    """The pool is shared, so the executor is handed the whole pool and left to
    filter it -- and the specialist's declared model must travel with it."""

    calls = _stub_executor(monkeypatch, _fake_result(COMPLETED, result="ok"))
    pool = [object(), object(), object()]
    SpecialistSubagentWorker("coder", model="some-model", tool_pool=pool).execute_task(SwarmTaskNode(task_id="t1", objective="x"), _plan())
    assert len(calls) == 1
    assert len(calls[0]["tools"]) == 3, "the executor needs the whole pool; it filters per allowlist itself"
    assert calls[0]["config"].name == "team-coder"
    assert calls[0]["config"].model == "some-model"


# ---------------------------------------------------------------------------
# 3. Reconciliation: execution vs acceptance, and the consensus invariants
# ---------------------------------------------------------------------------


def _specialist_task_plan(**task_kwargs) -> SwarmPlan:
    plan = SwarmPlan(swarm_id="swm-t", goal="Ship it", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(
        task_id="t1",
        objective="Implement the sql migration",
        capability_tags=["sql"],
        worker_type=SPECIALIST_WORKER_TYPE,
        assigned_worker="coder",
        **task_kwargs,
    )
    return plan


def test_a_completed_task_whose_acceptance_fails_is_not_reported_as_accepted():
    plan = _specialist_task_plan(state=TaskNodeState.COMPLETED, result_summary="Implemented it and wrote a changelog.")
    plan.tasks["t1"].acceptance_criteria = [{"type": "file", "path": "migration.sql", "check": "exists"}]
    SwarmAggregator.apply_acceptance_verification(plan.tasks["t1"], [{"source": "test"}])

    result = SwarmAggregator.aggregate(plan)
    assert result["acceptance_failed_tasks"] == 1
    assert result["execution_status"] == "partial_success", "an acceptance failure must not read as a clean run"
    assert result["team"]["acceptance_status"] == "not_fully_accepted"
    # A *failed* acceptance is a known-bad verdict, not an unknown, so it must
    # not be filed under "unknown" -- but it must be visible as itself.
    assert result["team"]["members"][0]["acceptance_failed"] == 1
    assert result["team"]["members"][0]["tasks"][0]["acceptance"] == "acceptance_failed"
    assert "acceptance_unverified" not in {item["kind"] for item in result["team"]["unknown"]}


def test_duplicate_voters_cannot_produce_a_clean_approval():
    """Preserved, not reimplemented: this is SwarmAggregator's own rule, and
    the team report must not launder it."""

    plan = SwarmPlan(swarm_id="swm-t", goal="Ship it", mode=SwarmMode.PARALLEL, requires_consensus=True)
    plan.tasks["t1"] = SwarmTaskNode(
        task_id="t1",
        objective="a",
        worker_type=SPECIALIST_WORKER_TYPE,
        assigned_worker="coder",
        state=TaskNodeState.COMPLETED,
        result_summary="Verified: the change is safe and ready.",
        evidence=[
            {"source": "specialist:coder", "vote": "approve", "voter": "coder"},
            {"source": "specialist:coder", "vote": "approve", "voter": "coder"},
        ],
    )
    result = SwarmAggregator.aggregate(plan)
    consensus = result["consensus"]
    assert consensus is not None
    assert not consensus.get("approved"), f"a duplicate voter panel must not be approved: {consensus}"
    assert result["execution_status"] != "completed"
    assert any(item["kind"] == "consensus_not_approved" for item in result["team"]["unknown"])


def test_a_panel_with_no_evidence_is_not_approved():
    plan = SwarmPlan(swarm_id="swm-t", goal="Ship it", mode=SwarmMode.PARALLEL, requires_consensus=True)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="a", worker_type=SPECIALIST_WORKER_TYPE, assigned_worker="coder", state=TaskNodeState.COMPLETED, result_summary="Looks fine to me.")
    result = SwarmAggregator.aggregate(plan)
    assert not result["consensus"].get("approved")
    assert result["execution_status"] != "completed"


def test_a_required_but_unevaluated_consensus_is_reported_as_unknown():
    plan = SwarmPlan(swarm_id="swm-t", goal="Ship it", mode=SwarmMode.PARALLEL, requires_consensus=True)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="a", worker_type=SPECIALIST_WORKER_TYPE, assigned_worker="coder", state=TaskNodeState.COMPLETED, result_summary="Done.")
    SwarmAggregator.aggregate(plan)
    team = SwarmAggregator.compose_team(plan)
    kinds = {item["kind"] for item in team["unknown"]}
    assert kinds & {"consensus_not_evaluated", "consensus_not_approved"}, f"a required consensus left no trace in the report: {kinds}"


def test_the_team_report_and_the_aggregate_result_agree_on_conflicts():
    """The report is composed from the same conflict list the aggregation used.
    A report that disagreed with the run it describes would be worse than none."""

    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="a", worker_type=SPECIALIST_WORKER_TYPE, assigned_worker="coder", state=TaskNodeState.COMPLETED, result_summary="The change is verified and ready.")
    plan.tasks["t2"] = SwarmTaskNode(task_id="t2", objective="b", worker_type=SPECIALIST_WORKER_TYPE, assigned_worker="scribe", state=TaskNodeState.COMPLETED, result_summary="The change failed: the test is broken.")
    plan.metrics["team"] = assign_specialists(plan, []).to_dict()
    result = SwarmAggregator.aggregate(plan)
    assert result["conflicts_detected"] == 1
    assert len(result["team"]["conflicts"]) == result["conflicts_detected"]
    assert result["execution_status"] == "partial_success"
    assert any(item["kind"] == "unreconciled_conflicts" for item in result["team"]["unknown"])


def test_a_plan_with_no_executed_work_is_never_completed():
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="a", worker_type=SPECIALIST_WORKER_TYPE, assigned_worker="coder", state=TaskNodeState.PENDING)
    result = SwarmAggregator.aggregate(plan)
    assert result["execution_status"] != "completed"
    assert result["team"]["summary"]["completed"] == 0


def test_the_scheduler_still_validates_the_dag_before_a_team_run(roster, tmp_path, monkeypatch):
    """Composition must never make an unexecutable plan look executable."""

    plan = _team_plan(roster, tmp_path)
    plan.tasks["task-qa"].dependencies = ["task-does-not-exist"]
    runner = AsyncSwarmRunner(_coordinator_with(plan, tmp_path), poll_interval=RUNTIME, lease_seconds=LEASE)
    monkeypatch.setattr(runner, "_build_worker", lambda node: _MalformedWorker())
    result = asyncio.run(runner.run_swarm_async(plan.swarm_id))
    assert result["status"] == "failed"
    assert "invalid_swarm_plan" in (plan.terminal_reason or "")


def test_a_lease_is_released_when_a_specialist_fails(roster, tmp_path, monkeypatch):
    """A failed task must not keep a live lease, or the watchdog treats it as
    straggling work forever."""

    plan = _team_plan(roster, tmp_path)
    runner = AsyncSwarmRunner(_coordinator_with(plan, tmp_path), poll_interval=RUNTIME, lease_seconds=LEASE)
    monkeypatch.setattr(runner, "_build_worker", lambda node: _ExplodingWorker(node.assigned_worker or "unknown"))
    asyncio.run(runner.run_swarm_async(plan.swarm_id))
    for task in plan.tasks.values():
        if task.state == TaskNodeState.FAILED:
            assert task.lease_id is None
            assert task.lease_expires_at is None
            assert task.lease_owner is None


def test_the_report_is_available_from_the_coordinator_under_owner_scoping(roster, tmp_path):
    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = coordinator.create_swarm(goal="Implement the sql migration and the python service", owner_id="alice")

    visible = coordinator.team_report(plan.swarm_id, owner_id="alice")
    assert visible["summary"]["members"] > 0
    hidden = coordinator.team_report(plan.swarm_id, owner_id="mallory")
    assert hidden == {"status": "not_found", "swarm_id": plan.swarm_id}, "a report must not leak another owner's team"
    assert coordinator.team_report("swm-nope")["status"] == "not_found"


def test_the_scheduler_is_untouched_by_composition(roster):
    plan = SwarmPlan(swarm_id="swm-t", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="Implement the sql migration", capability_tags=["sql"])
    before = SwarmScheduler(plan).progress()
    assign_specialists(plan, roster)
    assert SwarmScheduler(plan).progress() == before
    SwarmScheduler(plan).validate_graph()
