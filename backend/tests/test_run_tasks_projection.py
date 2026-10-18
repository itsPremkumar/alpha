"""The run-scoped task projection (Bot Mode 3.5): one route, every engine.

Pinned here:

1. ``GET /api/tasks?run_id=`` projects graph-plane tasks and swarm tasks
   through the SAME ``alpha.tasks`` adapters the capability catalog declares —
   so the graph plane and Team Ops consume one vocabulary rather than each
   re-mapping engine statuses client-side.
2. A failed engine read reports the reason in ``engine_errors``; it never
   reads as a silent empty list, which would render "this run has no tasks"
   over a Gateway that merely could not answer.
3. An unknown run is a real answer (zero from both engines, no errors) —
   distinct from a failed read.
4. Goal-level acceptance criteria travel with each projected task: the
   adapter reads them from the goal when the node declares none.
"""

from __future__ import annotations

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import alpha.graph.ledger as graph_ledger
import alpha.swarm.coordinator as swarm_coordinator
from alpha.graph.models import Goal, GoalMode, TaskNode
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from app.gateway.routers import tasks as tasks_router


class _FakeLedger:
    """The two reads the route performs, over in-memory rows.

    Real ``Goal``/``TaskNode`` rows flow through the REAL adapters; only the
    store is fake, so what is under test is the projection path itself.
    """

    def __init__(self, goals=(), tasks_by_goal=None):
        self._goals = list(goals)
        self._tasks_by_goal = tasks_by_goal or {}

    def list_goals(self, *, run_id=None, status=None):
        return [g for g in self._goals if run_id is None or g.run_id == run_id]

    def list_tasks(self, *, goal_id=None, status=None):
        return list(self._tasks_by_goal.get(goal_id, []))


class _FakeCoordinator:
    """The one read the route performs: plans correlated to a run."""

    def __init__(self, plans=()):
        self._plans = list(plans)

    def plans_for_run(self, run_id):
        return [p for p in self._plans if p.run_id == run_id]


def _goal(goal_id="g1", run_id="run-1", criteria=None):
    return Goal(
        goal_id=goal_id,
        run_id=run_id,
        user_request="Ship the release notes",
        normalized_objective="ship the release notes",
        mode=GoalMode.ANSWER,
        acceptance_criteria=list(criteria or []),
    )


def _client(monkeypatch, ledger=None, coordinator=None):
    monkeypatch.setattr(graph_ledger, "TaskLedger", lambda: ledger or _FakeLedger())
    monkeypatch.setattr(
        swarm_coordinator,
        "get_swarm_coordinator",
        lambda: coordinator or _FakeCoordinator(),
    )
    app = make_authed_test_app()
    app.include_router(tasks_router.router)
    return TestClient(app)


def test_both_engines_arrive_in_one_contract_vocabulary(monkeypatch):
    goal = _goal(criteria=["notes mention every changed module"])
    node = TaskNode.create(goal_id="g1", title="Draft the notes")
    plan = SwarmPlan(
        swarm_id="s1",
        goal="Ship the release notes",
        mode=next(iter(SwarmMode.__members__.values())),
        run_id="run-1",
        tasks={"t1": SwarmTaskNode(task_id="t1", objective="Draft section A")},
    )
    client = _client(
        monkeypatch,
        ledger=_FakeLedger(goals=[goal], tasks_by_goal={"g1": [node]}),
        coordinator=_FakeCoordinator(plans=[plan]),
    )

    body = client.get("/api/tasks", params={"run_id": "run-1"}).json()

    assert body["count"] == 2
    assert body["engines"] == {"graph": 1, "swarm": 1}
    assert body["engine_errors"] == {}
    by_engine = {row["engine"]: row for row in body["tasks"]}
    assert set(by_engine) == {"graph", "swarm"}
    graph_row = by_engine["graph"]
    assert graph_row["task_id"] == node.task_id
    assert graph_row["native_status"] == "pending"
    assert graph_row["status"] == "pending"
    assert graph_row["acceptance_criteria"] == ["notes mention every changed module"]
    swarm_row = by_engine["swarm"]
    assert swarm_row["task_id"] == "t1"
    assert swarm_row["native_status"] == "pending"
    assert all(row["run_id"] == "run-1" for row in body["tasks"])


def test_a_failing_engine_reports_a_reason_not_an_empty_list(monkeypatch):
    class _BrokenLedger:
        def list_goals(self, *, run_id=None, status=None):
            raise OSError("ledger volume is gone")

    plan = SwarmPlan(
        swarm_id="s1",
        goal="g",
        mode=next(iter(SwarmMode.__members__.values())),
        run_id="run-1",
        tasks={"t1": SwarmTaskNode(task_id="t1", objective="x")},
    )
    client = _client(monkeypatch, ledger=_BrokenLedger(), coordinator=_FakeCoordinator(plans=[plan]))

    body = client.get("/api/tasks", params={"run_id": "run-1"}).json()

    assert "graph" not in body["engines"], "a failed read contributes no count"
    assert body["engine_errors"]["graph"].startswith("OSError:")
    assert "volume is gone" in body["engine_errors"]["graph"]
    assert body["engines"] == {"swarm": 1}
    assert body["count"] == 1


def test_an_unknown_run_is_zero_from_both_engines_without_errors(monkeypatch):
    client = _client(monkeypatch, ledger=_FakeLedger(), coordinator=_FakeCoordinator())

    body = client.get("/api/tasks", params={"run_id": "no-such-run"}).json()

    assert body["count"] == 0
    assert body["engines"] == {"graph": 0, "swarm": 0}
    assert body["engine_errors"] == {}, "an empty run is an answer, not a failure"
    assert body["tasks"] == []


def test_a_missing_run_id_is_refused(monkeypatch):
    client = _client(monkeypatch)

    assert client.get("/api/tasks").status_code == 422


def test_a_blank_run_id_is_refused(monkeypatch):
    client = _client(monkeypatch)

    assert client.get("/api/tasks", params={"run_id": "   "}).status_code == 422
