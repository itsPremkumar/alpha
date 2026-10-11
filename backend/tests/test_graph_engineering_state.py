"""Tests for alpha.graph.state — the LangGraph state schema and reducers."""

from __future__ import annotations

import pytest

from alpha.graph.state import AlphaGraphState, GraphIntent, GraphStatus, append_dedupe, merge_graph_state


class TestAppendDedupe:
    def test_appends_new_items(self) -> None:
        assert append_dedupe(["a", "b"], ["c"]) == ["a", "b", "c"]

    def test_deduplicates(self) -> None:
        assert append_dedupe(["a", "b"], ["b", "c"]) == ["a", "b", "c"]

    def test_handles_none(self) -> None:
        assert append_dedupe(None, ["a"]) == ["a"]
        assert append_dedupe(["a"], None) == ["a"]
        assert append_dedupe(None, None) == []

    def test_preserves_order(self) -> None:
        assert append_dedupe(["b"], ["a", "c"]) == ["b", "a", "c"]


class TestMergeGraphState:
    def test_merge_adds_new_fields(self) -> None:
        current: AlphaGraphState = {"run_id": "run-1", "goal_id": "goal-1"}
        merged = merge_graph_state(current, {"status": GraphStatus.RUNNING})
        assert merged["status"] is GraphStatus.RUNNING
        assert merged["run_id"] == "run-1"

    def test_merge_applies_list_reducer(self) -> None:
        current: AlphaGraphState = {"task_ids": ["a", "b"]}
        merged = merge_graph_state(current, {"task_ids": ["b", "c"]})
        assert merged["task_ids"] == ["a", "b", "c"]

    def test_merge_applies_counter_reducer(self) -> None:
        current: AlphaGraphState = {"model_calls_used": 5}
        merged = merge_graph_state(current, {"model_calls_used": 3})
        assert merged["model_calls_used"] == 5  # max, not sum

    def test_merge_refuses_unknown_fields(self) -> None:
        current: AlphaGraphState = {"run_id": "run-1"}
        with pytest.raises(ValueError, match="unknown"):
            merge_graph_state(current, {"unknown_field": "value"})

    def test_merge_preserves_unmodified_fields(self) -> None:
        current: AlphaGraphState = {
            "run_id": "run-1",
            "goal_id": "goal-1",
            "status": GraphStatus.CREATED,
            "task_ids": ["a"],
        }
        merged = merge_graph_state(current, {"status": GraphStatus.RUNNING})
        assert merged["run_id"] == "run-1"
        assert merged["goal_id"] == "goal-1"
        assert merged["task_ids"] == ["a"]


class TestStateSchema:
    def test_state_is_typeddict(self) -> None:
        state: AlphaGraphState = {
            "run_id": "run-1",
            "goal_id": "goal-1",
            "graph_id": "graph-1",
            "thread_id": "thread-1",
            "intent": GraphIntent.EXECUTE,
            "status": GraphStatus.CREATED,
            "current_node": "",
            "next_node": "normalize_request",
            "task_ids": [],
            "ready_task_ids": [],
            "active_task_ids": [],
            "completed_task_ids": [],
            "blocked_task_ids": [],
            "model_calls_used": 0,
            "tool_calls_used": 0,
            "planning_iteration": 0,
            "verify_cycle": 0,
            "repair_attempts": 0,
            "retry_count": 0,
            "cancel_requested": False,
            "approval_request_id": None,
            "failure_summary": None,
            "final_result": None,
            "started_at": "",
            "deadline_at": None,
        }
        assert state["run_id"] == "run-1"
        assert state["status"] is GraphStatus.CREATED
