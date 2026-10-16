"""Tests for TrajectoryRecorderMiddleware.

Verifies that the middleware records StepRecord entries via TrajectoryStore
when agent nodes execute.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from alpha.agents.middlewares.trajectory_middleware import (
    TrajectoryRecorderMiddleware,
    _bounded_tool_input,
)
from alpha.trajectory.models import StepRecord
from alpha.trajectory.store import TrajectoryStore


@pytest.fixture
def mock_store():
    """A mock TrajectoryStore that records calls."""
    store = MagicMock(spec=TrajectoryStore)
    store.record_step.return_value = StepRecord(
        step_id="step_test",
        goal_id="goal_1",
        step_index=0,
    )
    return store


@pytest.fixture
def middleware(mock_store):
    return TrajectoryRecorderMiddleware(store=mock_store)


class TestTrajectoryRecorderMiddleware:
    """Tests for TrajectoryRecorderMiddleware."""

    def test_records_step_on_after_agent(self, middleware, mock_store):
        """after_agent records a StepRecord via the store."""
        state = {
            "messages": [
                AIMessage(content="I will use bash", tool_calls=[{"name": "bash", "id": "call_1", "args": {"command": "ls"}}]),
                ToolMessage(content="file1.py", tool_call_id="call_1", name="bash"),
            ]
        }
        runtime = SimpleNamespace(context={"thread_id": "thread_123"})

        middleware.after_agent(state, runtime)

        mock_store.record_step.assert_called_once()
        call_kwargs = mock_store.record_step.call_args
        # positional: goal_id, step_index
        assert call_kwargs[0][0] == "thread_123"
        assert call_kwargs[0][1] == 0

    def test_records_step_with_tool_info(self, middleware, mock_store):
        """The recorded step includes tool name and input."""
        state = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "write_file", "id": "call_2", "args": {"path": "test.py"}}]),
                ToolMessage(content="done", tool_call_id="call_2", name="write_file"),
            ]
        }
        runtime = SimpleNamespace(context={"thread_id": "thread_456"})

        middleware.after_agent(state, runtime)

        mock_store.record_step.assert_called_once()
        call_kwargs = mock_store.record_step.call_args
        assert call_kwargs[1]["tool_name"] == "write_file"
        assert call_kwargs[1]["tool_input"] == {"path": "test.py"}

    def test_increments_step_index(self, middleware, mock_store):
        """Each call to after_agent increments the step index."""
        state = {"messages": [AIMessage(content="step 1")]}
        runtime = SimpleNamespace(context={"thread_id": "t1"})

        middleware.after_agent(state, runtime)
        middleware.after_agent(state, runtime)
        middleware.after_agent(state, runtime)

        assert mock_store.record_step.call_count == 3
        indices = [c[0][1] for c in mock_store.record_step.call_args_list]
        assert indices == [0, 1, 2]

    def test_handles_empty_messages(self, middleware, mock_store):
        """Empty messages list still records a step."""
        state = {"messages": []}
        runtime = SimpleNamespace(context={"thread_id": "t2"})

        middleware.after_agent(state, runtime)

        mock_store.record_step.assert_called_once()

    def test_handles_no_runtime_context(self, middleware, mock_store):
        """Missing runtime context uses 'unknown' as goal_id."""
        state = {"messages": [AIMessage(content="hello")]}
        runtime = SimpleNamespace(context=None)

        middleware.after_agent(state, runtime)

        mock_store.record_step.assert_called_once()
        call_kwargs = mock_store.record_step.call_args
        assert call_kwargs[0][0] == "unknown"

    def test_aafter_agent_records_step(self, middleware, mock_store):
        """The async variant also records a step."""
        state = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "call_a1", "args": {"command": "pwd"}}]),
            ]
        }
        runtime = SimpleNamespace(context={"thread_id": "thread_async"})

        asyncio.run(middleware.aafter_agent(state, runtime))

        mock_store.record_step.assert_called_once()
        call_kwargs = mock_store.record_step.call_args
        assert call_kwargs[0][0] == "thread_async"

    def test_returns_none_from_after_agent(self, middleware):
        """after_agent returns None (no state update)."""
        state = {"messages": [AIMessage(content="hi")]}
        runtime = SimpleNamespace(context={"thread_id": "t"})

        result = middleware.after_agent(state, runtime)
        assert result is None

    def test_returns_none_from_aafter_agent(self, middleware):
        """aafter_agent returns None (no state update)."""
        state = {"messages": [AIMessage(content="hi")]}
        runtime = SimpleNamespace(context={"thread_id": "t"})

        result = asyncio.run(middleware.aafter_agent(state, runtime))
        assert result is None

    def test_multiple_tool_calls_records_last(self, middleware, mock_store):
        """When multiple tool calls exist, the last one is recorded."""
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "bash", "id": "c1", "args": {"command": "ls"}},
                        {"name": "write_file", "id": "c2", "args": {"path": "f.py"}},
                    ],
                ),
                ToolMessage(content="ok", tool_call_id="c2", name="write_file"),
            ]
        }
        runtime = SimpleNamespace(context={"thread_id": "t"})

        middleware.after_agent(state, runtime)

        call_kwargs = mock_store.record_step.call_args
        assert call_kwargs[1]["tool_name"] == "write_file"

    def test_no_tool_calls_records_empty_tool(self, middleware, mock_store):
        """When no tool calls exist, tool_name is empty."""
        state = {"messages": [AIMessage(content="just thinking")]}
        runtime = SimpleNamespace(context={"thread_id": "t"})

        middleware.after_agent(state, runtime)

        call_kwargs = mock_store.record_step.call_args
        assert call_kwargs[1]["tool_name"] == ""

    def test_redacts_credential_fields_and_inline_tokens(self, middleware, mock_store):
        state = {
            "messages": [
                AIMessage(
                    content="Using OPENAI_API_KEY=top-secret and Bearer abcdefghijklmnop",
                    tool_calls=[
                        {
                            "name": "bash",
                            "id": "call_secret",
                            "args": {"command": "echo sk-1234567890abcdefghijkl", "api_key": "field-secret"},
                        }
                    ],
                ),
                ToolMessage(content="Authorization: Bearer qwertyuiopasdfgh", tool_call_id="call_secret", name="bash"),
            ]
        }

        middleware.after_agent(state, SimpleNamespace(context={"thread_id": "private-thread"}))

        kwargs = mock_store.record_step.call_args.kwargs
        persisted = f"{kwargs['thought']} {kwargs['tool_input']} {kwargs['tool_output']}"
        for secret in ("top-secret", "abcdefghijklmnop", "1234567890abcdefghijkl", "field-secret", "qwertyuiopasdfgh"):
            assert secret not in persisted
        assert "[REDACTED]" in persisted

    def test_bounds_large_model_and_tool_payloads(self, middleware, mock_store):
        state = {
            "messages": [
                AIMessage(
                    content="x" * 100_000,
                    tool_calls=[{"name": "write_file", "id": "call_big", "args": {"content": "y" * 100_000}}],
                ),
                ToolMessage(content="z" * 100_000, tool_call_id="call_big", name="write_file"),
            ]
        }

        middleware.after_agent(state, SimpleNamespace(context={"thread_id": "bounded-thread"}))

        kwargs = mock_store.record_step.call_args.kwargs
        assert len(kwargs["thought"]) <= 4_020
        assert len(kwargs["tool_output"]) <= 4_020
        assert len(str(kwargs["tool_input"]["content"])) <= 1_040
        import json

        assert len(json.dumps(_bounded_tool_input({"payload": "x" * 100_000})).encode("utf-8")) <= 8_192

    def test_nonserializable_tool_input_is_disclosed_in_debug_log(self, caplog):
        import logging

        with caplog.at_level(logging.DEBUG, logger="alpha.agents.middlewares.trajectory_middleware"):
            projected = _bounded_tool_input({"large_integer": 10**5_000})

        assert projected["_omitted"].startswith("tool arguments exceeded")
        assert "serialization failed" in caplog.text

    def test_lead_builder_wires_the_initialized_trajectory_store(self, monkeypatch, mock_store):
        from alpha.agents.middlewares import tool_error_handling_middleware as builder

        received = {}

        def capture_runtime_middlewares(**kwargs):
            received.update(kwargs)
            return []

        monkeypatch.setattr(builder, "_build_runtime_middlewares", capture_runtime_middlewares)
        monkeypatch.setattr("alpha.trajectory.store.get_trajectory_store", lambda: mock_store)

        builder.build_lead_runtime_middlewares(app_config=MagicMock())

        assert received["trajectory_store"] is mock_store
