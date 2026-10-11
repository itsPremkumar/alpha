"""Tests for alpha.graph.runtime — the top-level LangGraph execution graph."""

from __future__ import annotations

import pytest

from alpha.graph.ledger import TaskLedger, TaskLedgerConfig
from alpha.graph.models import TaskStatus
from alpha.graph.runtime import (
    GraphRuntimeConfig,
    build_graph,
    build_graph_runtime,
    build_initial_state,
)


class TestBuildGraph:
    def test_build_graph_compiles(self) -> None:
        graph = build_graph()
        assert graph is not None

    def test_build_graph_with_ledger(self, tmp_path) -> None:
        ledger = TaskLedger(TaskLedgerConfig(database_path=tmp_path / "test.db"))
        graph = build_graph(ledger=ledger)
        assert graph is not None

    def test_build_graph_runtime(self, tmp_path) -> None:
        runtime = build_graph_runtime(config=GraphRuntimeConfig(max_concurrency=2))
        assert runtime is not None
        assert runtime.graph is not None
        assert runtime.ledger is not None


class TestGraphRuntimeConfig:
    def test_default_config(self) -> None:
        config = GraphRuntimeConfig()
        assert config.max_concurrency == 2
        assert config.enable_checkpoints is True

    def test_zero_concurrency_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_concurrency"):
            GraphRuntimeConfig(max_concurrency=0)

    def test_negative_concurrency_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_concurrency"):
            GraphRuntimeConfig(max_concurrency=-1)


class TestBuildInitialState:
    def test_all_fields_filled(self) -> None:
        state = build_initial_state(user_request="Test", run_id="run-1")
        assert state["run_id"] == "run-1"
        assert state["goal_id"] == ""
        assert state["user_request"] == "Test"
        assert state["plan_tasks"] == []
        assert state["task_ids"] == []
        assert state["model_calls_used"] == 0

    def test_plan_tasks_default_to_empty_list(self) -> None:
        state = build_initial_state(user_request="Test", run_id="run-1")
        # A missing list must be ``[]``, never ``None``: a node iterating it
        # would raise ``TypeError`` naming no cause.
        assert state["plan_tasks"] == []
        assert state["verify_checks"] == []
        assert state["artifact_request"] == {}


class TestGraphRuntime:
    def test_runtime_has_graph_and_ledger(self, tmp_path) -> None:
        runtime = build_graph_runtime(config=GraphRuntimeConfig())
        assert runtime.graph is not None
        assert runtime.ledger is not None

    def test_run_end_to_end(self, tmp_path) -> None:
        """A full run: normalize -> plan -> validate -> dispatch -> capture
        -> verify -> final_verify -> report.

        The caller supplies the plan, the hashed artifact, and the measured
        verdicts, so the run completes on real evidence rather than on a worker
        saying "done". The final report must name the verified count rather than
        implying the goal-level prose criteria were checked -- those are not
        machine-evaluable here, so the goal is ``partial``, and the report says
        so.
        """
        runtime = build_graph_runtime(config=GraphRuntimeConfig(max_concurrency=2))
        state = build_initial_state(
            user_request="Fix the API bug",
            run_id="run-1",
            plan_tasks=[
                {
                    "title": "Inspect the API",
                    "acceptance_checks": ["endpoint responds"],
                    "task_type": "code",
                }
            ],
            artifact_request={
                "uri": "/tmp/api-inspection.txt",
                "content_hash": "a" * 64,
                "artifact_type": "test_report",
            },
            verify_checks=[{"check": "endpoint responds", "verdict": True, "evidence": "exit 0"}],
        )
        final = runtime.invoke(state, config={"configurable": {"thread_id": "thread-1"}})

        assert final["goal_id"]
        assert final["final_result"]
        assert "0 failed" in final["final_result"]

        # The artifact and the attempt are actually durable, not just claimed.
        artifacts = runtime.ledger.list_artifacts(goal_id=final["goal_id"])
        assert len(artifacts) == 1
        tasks = runtime.ledger.list_tasks(goal_id=final["goal_id"])
        assert len(tasks) == 1
        assert tasks[0].status is TaskStatus.SUCCEEDED
        assert tasks[0].attempt_count == 1

    def test_run_never_succeeds_without_evidence(self, tmp_path) -> None:
        """The false-success guard, end to end.

        The same plan with no artifact and no measured verdicts must not reach
        ``succeeded``. ``capture_artifact`` refuses an artifact-less capture, so
        the run never claims work it cannot point at.
        """
        runtime = build_graph_runtime(config=GraphRuntimeConfig(max_concurrency=2))
        state = build_initial_state(
            user_request="Fix the API bug",
            run_id="run-1",
            plan_tasks=[{"title": "Inspect the API", "acceptance_checks": ["endpoint responds"]}],
        )
        with pytest.raises(Exception):
            runtime.invoke(state, config={"configurable": {"thread_id": "thread-1"}})
