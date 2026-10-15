"""Behavioural tests for loop detection in a real agent graph.

Each test drives ``langchain.agents.create_agent`` with a scripted model and
the production :class:`LoopDetectionMiddleware`, then asserts on observable
run outcomes: what the model was shown, whether the run terminated with a
final answer, and whether the executor-facing stop reason was recorded.
"""

from __future__ import annotations

import pytest
from _helpers import final_turn, injected_warnings, scripted_model, tool_call, turn_with_calls
from langchain.agents import create_agent
from langchain_core.tools import tool as as_tool

from alpha.agents.middlewares.loop_detection_middleware import (
    _HARD_STOP_MSG,
    LoopDetectionMiddleware,
)


@as_tool
def bash(command: str) -> str:
    """Run a shell command."""
    return f"ran: {command}"


@as_tool
def read_file(path: str) -> str:
    """Read a file from disk."""
    return f"content of {path}"


def _run(model, middleware, *, prompt="inspect the repo", thread="t-loop", run="r-loop", limit=25):
    graph = create_agent(model=model, tools=[bash, read_file], middleware=[middleware])
    return graph.invoke(
        {"messages": [("user", prompt)]},
        context={"thread_id": thread, "run_id": run},
        config={"recursion_limit": limit},
    )


class TestIdenticalCallLoop:
    def test_synthesis_retry_cannot_start_another_tool_loop(self):
        middleware = LoopDetectionMiddleware(
            warn_threshold=99,
            hard_limit=2,
            tool_freq_warn=100,
            tool_freq_hard_limit=200,
        )
        model = scripted_model(
            turn_with_calls(tool_call("bash", {"command": "ls"}, "first")),
            turn_with_calls(tool_call("bash", {"command": "ls"}, "second")),
            turn_with_calls(tool_call("bash", {"command": "ls"}, "synthesis-tool-call")),
        )

        result = _run(model, middleware, run="r-no-second-retry")

        assert len(model.seen_requests) == 3
        assert model.bound_tool_counts[-1] == 0
        assert result["messages"][-1].tool_calls == []
        assert "FORCED STOP" in result["messages"][-1].content
        assert middleware.consume_stop_reason("r-no-second-retry") == "loop_capped"

    def test_repeated_identical_call_warns_then_forces_final_answer(self):
        """3 identical `ls` calls: warn at the 2nd, hard stop at the 3rd.

        The behavioural contract is that the run *ends* with a usable answer
        instead of burning the recursion limit, and that the model was told
        it was looping before it was stopped.
        """
        middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = scripted_model(
            *[turn_with_calls(tool_call("bash", {"command": "ls"}, f"call-{i}")) for i in range(3)],
            final_turn("I kept repeating myself; here is what I found."),
        )
        result = _run(model, middleware)

        assert result["messages"][-1].content == "I kept repeating myself; here is what I found."
        assert any(_HARD_STOP_MSG.split(".")[0] in str(message.content) for message in result["messages"])
        assert 0 in model.bound_tool_counts, f"the synthesis retry must not receive tools: {model.bound_tool_counts}"
        assert sum(any(getattr(message, "name", None) == "loop_detection_finalization" for message in request) for request in model.seen_requests) == 1
        # A warning reached the model at some request, and none leaked into
        # the persisted state (they are request-only, per the module design).
        warned = [injected_warnings(r) for r in model.seen_requests]
        assert any(len(w) == 1 for w in warned)
        from langchain_core.messages import HumanMessage

        assert not [m for m in result["messages"] if isinstance(m, HumanMessage) and m.name == "loop_warning"]

    def test_hard_stop_records_loop_capped_stop_reason_for_the_executor(self):
        """A capped run must not look like a clean completion to the caller."""
        middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = scripted_model(
            *[turn_with_calls(tool_call("bash", {"command": "pwd"}, f"c{i}")) for i in range(3)],
            final_turn("stopping"),
        )
        _run(model, middleware, run="r-capped")

        assert middleware.consume_stop_reason("r-capped") == "loop_capped"
        # Consuming is a pop: a second read must not report the same cap twice.
        assert middleware.consume_stop_reason("r-capped") is None

    def test_distinct_calls_never_trip_the_loop_guard(self):
        """Varying arguments is normal iteration, not a loop."""
        middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = scripted_model(
            *[turn_with_calls(tool_call("bash", {"command": f"step-{i}"}, f"d{i}")) for i in range(3)],
            final_turn("three distinct steps done"),
        )
        result = _run(model, middleware, run="r-distinct")

        assert result["messages"][-1].content == "three distinct steps done"
        assert not any(injected_warnings(r) for r in model.seen_requests)
        assert middleware.consume_stop_reason("r-distinct") is None


class TestToolFrequencyLayer:
    def test_many_distinct_reads_trip_the_frequency_warning(self):
        """Layer 2 catches the same *tool* on many different files.

        Hash-based detection is blind here (every path differs), so the
        per-tool frequency layer is what saves the run.
        """
        middleware = LoopDetectionMiddleware(
            warn_threshold=99,
            hard_limit=999,
            tool_freq_warn=3,
            tool_freq_hard_limit=999,
            window_size=10,
        )
        paths = ["a.py", "b.py", "c.py", "d.py"]
        model = scripted_model(
            *[turn_with_calls(tool_call("read_file", {"path": p}, f"f{i}")) for i, p in enumerate(paths)],
            final_turn("read everything"),
        )
        result = _run(model, middleware, run="r-freq")

        assert result["messages"][-1].content == "read everything"
        warned = [injected_warnings(r) for r in model.seen_requests]
        assert sum(len(w) for w in warned) == 1
        assert "read_file" in warned[next(i for i, w in enumerate(warned) if w)][0].content

    def test_frequency_hard_limit_strips_tool_calls(self):
        """Past the per-tool hard limit the run is forced to finish."""
        middleware = LoopDetectionMiddleware(
            warn_threshold=2,
            hard_limit=999,
            tool_freq_warn=2,
            tool_freq_hard_limit=3,
            window_size=10,
        )
        paths = ["a.py", "b.py", "c.py"]
        model = scripted_model(
            *[turn_with_calls(tool_call("read_file", {"path": p}, f"g{i}")) for i, p in enumerate(paths)],
            final_turn("cap reached"),
        )
        result = _run(model, middleware, run="r-freq-hard")

        assert result["messages"][-1].content == "cap reached"
        assert middleware.consume_stop_reason("r-freq-hard") == "loop_capped"


class TestRunScoping:
    def test_each_run_gets_a_fresh_loop_budget_on_one_graph(self):
        """A cached graph must not carry one run's loop history into the next."""
        middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = scripted_model(
            turn_with_calls(tool_call("bash", {"command": "ls"}, "run1")),
            final_turn("first answer"),
            turn_with_calls(tool_call("bash", {"command": "ls"}, "run2")),
            final_turn("second answer"),
        )
        graph = create_agent(model=model, tools=[bash, read_file], middleware=[middleware])
        shared = {"thread_id": "shared-thread"}

        first = graph.invoke({"messages": [("user", "go")]}, context={**shared, "run_id": "run-1"}, config={"recursion_limit": 10})
        second = graph.invoke({"messages": [("user", "go again")]}, context={**shared, "run_id": "run-2"}, config={"recursion_limit": 10})

        assert first["messages"][-1].content == "first answer"
        assert second["messages"][-1].content == "second answer"
        # One identical call per run is under the warn threshold of 2 — if the
        # budgets had leaked, the second run would have been warned immediately.
        assert not any(injected_warnings(r) for r in model.seen_requests)


class TestResetAndWarnings:
    def test_reset_clears_history_for_one_thread_only(self):
        """Reset is the operator's escape hatch and must stay thread-scoped."""
        middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = scripted_model(
            turn_with_calls(tool_call("bash", {"command": "ls"}, "h1")),
            final_turn("thread A done"),
        )
        _run(model, middleware, thread="thread-a", run="ra")

        assert middleware._history, "the run should have recorded loop history"
        middleware.reset("thread-a")
        assert middleware._history == {}
        assert middleware._stop_reason == {}

    def test_pending_warnings_are_dropped_not_carried_into_a_later_run(self):
        """A warning queued but never drained must not leak to the next run."""
        middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = scripted_model(
            turn_with_calls(tool_call("bash", {"command": "ls"}, "w1")),
            turn_with_calls(tool_call("bash", {"command": "ls"}, "w2")),
            final_turn("wrapped up"),
        )
        result = _run(model, middleware, run="r-transient")

        assert result["messages"][-1].content == "wrapped up"
        assert middleware._pending_warnings == {}


@pytest.mark.parametrize("threshold_pair", [(2, 3), (3, 5)])
def test_warn_threshold_configuration_is_honoured(threshold_pair):
    """Thresholds are constructor-driven; both documented pairs must behave."""
    warn, hard = threshold_pair
    middleware = LoopDetectionMiddleware(warn_threshold=warn, hard_limit=hard, tool_freq_warn=100, tool_freq_hard_limit=200)
    repeats = hard  # exactly enough to trip the hard limit
    model = scripted_model(
        *[turn_with_calls(tool_call("bash", {"command": "ls"}, f"p{i}")) for i in range(repeats)],
        final_turn("configured stop"),
    )
    result = _run(model, middleware, run=f"r-{warn}-{hard}")

    assert result["messages"][-1].content == "configured stop"
    assert middleware.consume_stop_reason(f"r-{warn}-{hard}") == "loop_capped"
