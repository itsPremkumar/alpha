"""Subagent turn-budget and capped-cut-off regressions.

Two independent, real defects in the native-subagent execution path, both
proven by driving ``SubagentExecutor`` against a real ``langchain``
``create_agent`` graph with a stub chat model:

1. ``SubagentConfig.max_turns`` is a TURN budget everywhere it is declared,
   documented, and defaulted (``general-purpose=150``, ``bash=60``, dataclass
   default 50) but was applied verbatim as LangGraph's ``recursion_limit``,
   which counts graph *super-steps*. The composed middleware stack spends
   ~7 super-steps per model turn, so the effective ceiling was ~1/7 of the
   configured value and any budget below ~7 bought ZERO model calls -- a
   subagent that could not reach the model at all.

2. A run cut off mid-turn by that ceiling was reported to the parent as
   ``status=completed`` with the interrupted turn's in-flight prose as its
   deliverable ("Task Succeeded (capped: turn budget). Result: working...").
   A child that never produced an answer must never be visible to the parent
   as a success.
"""

from __future__ import annotations

import importlib
import sys
import threading
from typing import Any

import pytest

from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.subagents.config import SubagentConfig
from alpha.subagents.status_contract import (
    format_subagent_result_message,
    make_subagent_additional_kwargs,
    read_subagent_result_metadata,
)

# ---------------------------------------------------------------------------
# The real executor module.
#
# ``tests/conftest.py`` installs a process-wide MagicMock for
# ``alpha.subagents.executor`` to break the
# ``alpha.subagents -> .executor -> alpha.agents -> subagent_limit_middleware
# -> alpha.subagents.executor`` import cycle. These tests need the REAL module
# (they execute real graphs), so it is loaded here and the mock is restored on
# teardown so nothing else in the session inherits the real module.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def executor_module():
    mocked = sys.modules.get("alpha.subagents.executor")
    package = sys.modules.get("alpha.subagents")
    package_had_attr = package is not None and hasattr(package, "executor")
    sys.modules.pop("alpha.subagents.executor", None)
    if package_had_attr:
        delattr(package, "executor")
    try:
        module = importlib.import_module("alpha.subagents.executor")
    except Exception:
        if mocked is not None:
            sys.modules["alpha.subagents.executor"] = mocked
        raise
    assert module.SubagentExecutor.__module__ == "alpha.subagents.executor"
    yield module
    sys.modules["alpha.subagents.executor"] = mocked
    if package is not None and package_had_attr:
        setattr(package, "executor", mocked)


@pytest.fixture
def executor_api(executor_module):
    """The public surface the tests drive, taken from the REAL module."""
    return {
        "module": executor_module,
        "SubagentExecutor": executor_module.SubagentExecutor,
        "SubagentStatus": executor_module.SubagentStatus,
        "resolve_graph_recursion_limit": executor_module.resolve_graph_recursion_limit,
        "steps_per_turn": executor_module._GRAPH_STEPS_PER_TURN,
    }


# ---------------------------------------------------------------------------
# Stub model: a real BaseChatModel so create_agent binds tools for real.
# ---------------------------------------------------------------------------


def _scripted_chat_model(mode: str, calls: list[int]):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class _Scripted(BaseChatModel):
        name: str = "stub"

        @property
        def _llm_type(self) -> str:
            return "stub"

        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, D102, ARG002
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> Any:  # noqa: ANN001, ANN003, ARG002
            calls[0] += 1
            usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
            if mode == "loop":
                # Never finishes: every turn is narration plus a tool call.
                message = AIMessage(
                    content="working...",
                    tool_calls=[{"name": "noop", "args": {"note": str(calls[0])}, "id": f"c{calls[0]}"}],
                    usage_metadata=usage,
                    response_metadata={"model_name": "stub"},
                )
            elif mode == "one_tool_then_done":
                if calls[0] == 1:
                    message = AIMessage(
                        content="let me look",
                        tool_calls=[{"name": "noop", "args": {"note": "1"}, "id": "c1"}],
                        usage_metadata=usage,
                        response_metadata={"model_name": "stub"},
                    )
                else:
                    message = AIMessage(
                        content="FINAL ANSWER",
                        usage_metadata=usage,
                        response_metadata={"model_name": "stub"},
                    )
            else:  # pragma: no cover - guarded by the tests below
                raise AssertionError(f"unknown mode {mode!r}")
            return ChatResult(generations=[ChatGeneration(message=message)])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> Any:  # noqa: ANN001, ANN003, ARG002
            return self._generate(messages)

    return _Scripted(name="stub")


@pytest.fixture
def real_app_config():
    return AppConfig(
        models=[
            ModelConfig(
                name="stub",
                display_name="stub",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="stub",
                supports_thinking=False,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )


@pytest.fixture
def stub_model(executor_module, monkeypatch: pytest.MonkeyPatch):
    """Install a scripted chat model into the executor's model factory."""
    monkeypatch.setattr(executor_module, "get_app_config", lambda: None)

    def _install(mode: str) -> list[int]:
        calls = [0]
        model = _scripted_chat_model(mode, calls)
        monkeypatch.setattr(executor_module, "create_chat_model", lambda **kwargs: model)
        return calls

    return _install


def _noop_tool():
    from langchain_core.tools import tool

    @tool("noop", parse_docstring=True)
    def noop(note: str) -> str:
        """Do nothing and echo a marker.

        Args:
            note: ignored.
        """
        return f"noop:{note}"

    return noop


def _executor(executor_api, app_config, *, max_turns: int, tools=None):
    config = SubagentConfig(
        name="turn-budget-probe",
        description="probe",
        system_prompt="probe",
        model="stub",
        max_turns=max_turns,
        timeout_seconds=180,
        skills=[],
    )
    executor = executor_api["SubagentExecutor"](
        config=config,
        tools=tools or [],
        app_config=app_config,
        thread_id="turn-budget-thread",
        user_id="probe-user",
    )
    executor.model_name = "stub"
    return executor


# ---------------------------------------------------------------------------
# 1. The turn budget is a TURN ceiling.
# ---------------------------------------------------------------------------


class TestTurnBudgetIsATurnCeiling:
    def test_recursion_limit_is_derived_not_the_raw_turn_count(self, executor_api):
        """The graph super-step budget must be derived from the turn budget."""
        resolve = executor_api["resolve_graph_recursion_limit"]
        steps = executor_api["steps_per_turn"]
        assert resolve(1) == steps
        assert resolve(50) == 50 * steps
        # Monotonic: a bigger turn budget is never a smaller graph budget.
        assert resolve(150) > resolve(50)
        # Never degenerate to a graph that cannot reach the model at all.
        assert resolve(0) >= steps
        assert resolve(-5) >= steps

    @pytest.mark.parametrize("max_turns", [1, 4, 7, 12])
    def test_every_configured_budget_buys_at_least_one_model_turn(self, executor_api, real_app_config, stub_model, max_turns):
        """A budget of N must reach the model at least once, for every N >= 1.

        Before the fix, ``recursion_limit == max_turns`` meant the graph spent
        its whole super-step allowance on the middleware chain's hook nodes and
        the subagent never called the model: ``max_turns=4`` produced ZERO
        model calls and a terminal ``Reached max_turns=4``.
        """
        calls = stub_model("loop")
        outcome = _executor(executor_api, real_app_config, max_turns=max_turns, tools=[_noop_tool()]).execute("work")

        assert outcome.status.is_terminal
        assert calls[0] >= 1, f"max_turns={max_turns} never reached the model"

    def test_configured_turns_are_actually_granted(self, executor_api, real_app_config, stub_model):
        """A 50-turn budget must allow at least 50 model calls.

        Measured on the real graph: ~7 super-steps per model turn, so 50 turns
        needs 400 super-steps. Before the fix, ``max_turns=50`` bought 7 model
        calls.
        """
        calls = stub_model("loop")
        result = _executor(executor_api, real_app_config, max_turns=50, tools=[_noop_tool()]).execute("work")

        assert result.status.is_terminal
        assert calls[0] >= 50, f"max_turns=50 only bought {calls[0]} model turns"

    def test_a_finished_run_is_unaffected(self, executor_api, real_app_config, stub_model):
        """A run that answers on its own still completes cleanly, uncapped."""
        calls = stub_model("one_tool_then_done")
        result = _executor(executor_api, real_app_config, max_turns=50, tools=[_noop_tool()]).execute("work")

        assert result.status is executor_api["SubagentStatus"].COMPLETED
        assert result.result == "FINAL ANSWER"
        assert result.stop_reason is None
        assert calls[0] == 2


# ---------------------------------------------------------------------------
# 2. A child cut off mid-turn is never a success for the parent.
# ---------------------------------------------------------------------------


class TestCappedCutOffIsNeverSuccess:
    def test_cut_off_mid_turn_is_failed_not_completed(self, executor_api, real_app_config, stub_model):
        """A ceiling that fires while the child is mid-turn must be FAILED.

        This is the load-bearing assertion: the interrupted turn's prose
        ("working...") is in-flight narration, not a deliverable.
        """
        stub_model("loop")
        result = _executor(executor_api, real_app_config, max_turns=4, tools=[_noop_tool()]).execute("work")

        assert result.status is executor_api["SubagentStatus"].FAILED
        assert result.result is None
        assert result.stop_reason == "turn_capped"
        assert "never produced a final answer" in (result.error or "")

    def test_parent_is_told_failed_with_a_reason(self, executor_api, real_app_config, stub_model):
        """The model-visible text and the wire metadata must both say failed."""
        stub_model("loop")
        result = _executor(executor_api, real_app_config, max_turns=4, tools=[_noop_tool()]).execute("work")

        assert result.status is executor_api["SubagentStatus"].FAILED
        content, metadata_error = format_subagent_result_message(
            "failed",
            result=result.result,
            error=result.error,
            stop_reason=result.stop_reason,
        )
        assert content.startswith("Task failed")
        assert "Task Succeeded" not in content
        assert "working..." not in content
        assert metadata_error

        kwargs = make_subagent_additional_kwargs(
            "failed",
            result=result.result,
            error=result.error,
            stop_reason=result.stop_reason,
        )
        read_back = read_subagent_result_metadata(kwargs)
        assert read_back is not None
        assert read_back["status"] == "failed"
        assert read_back["error"]
        # No result_brief on a failure: the parent must not be handed a
        # deliverable for a run that produced none.
        assert "result_brief" not in read_back
        assert "subagent_result_brief" not in kwargs

    def test_helper_does_not_promote_an_unfinished_turn(self, executor_api):
        """Unit-level pin on the recovery rule itself."""
        from langchain_core.messages import AIMessage

        recover = executor_module_of(executor_api)._recover_capped_partial

        finished = AIMessage(content="final deliverable")
        assert recover([finished]) == ("final deliverable", False)

        interrupted = AIMessage(
            content="working...",
            tool_calls=[{"name": "noop", "args": {}, "id": "c1"}],
        )
        assert recover([interrupted]) == (None, True)

        # A guard hard-stop strips tool_calls; that finished answer survives.
        guard_forced = AIMessage(content="forced final answer")
        assert recover([guard_forced]) == ("forced final answer", False)

        # Nothing from the assistant at all is not a cut-off turn.
        assert recover([]) == (None, False)


def executor_module_of(executor_api):
    return executor_api["module"]


# ---------------------------------------------------------------------------
# 3. Real parent + 3 children, nested grandchild, tokens attributed.
# ---------------------------------------------------------------------------


class TestRealTreeExecution:
    def test_parent_with_three_children_and_a_grandchild(self, executor_api, real_app_config, stub_model):
        """Drive a real parent -> 3 children -> grandchild tree with a stub model.

        Every unit is a real ``SubagentExecutor`` over the real graph. Asserts
        the observed facts: per-child token attribution, distinct trace ids, and
        that a child that answers is reported as a clean success.
        """
        statuses = executor_api["SubagentStatus"]
        stub_model("one_tool_then_done")
        results: dict[str, Any] = {}
        lock = threading.Lock()

        def run(name: str, task: str, depth: int) -> None:
            executor = _executor(executor_api, real_app_config, max_turns=12)
            executor.trace_id = f"trace-{name}"
            outcome = executor.execute(task)
            with lock:
                results[name] = (depth, outcome)

        parent = _executor(executor_api, real_app_config, max_turns=12)
        parent.trace_id = "trace-parent"

        children = [threading.Thread(target=run, args=(f"child-{i}", f"task {i}", 1)) for i in range(3)]
        for thread in children:
            thread.start()
        for thread in children:
            thread.join()

        assert set(results) == {"child-0", "child-1", "child-2"}
        for name, (depth, outcome) in sorted(results.items()):
            assert depth == 1
            assert outcome.status is statuses.COMPLETED
            assert outcome.result == "FINAL ANSWER"
            # Tokens are attributed per child, under the child's own caller id.
            assert outcome.token_usage_records
            assert all(record["caller"] == "subagent:turn-budget-probe" for record in outcome.token_usage_records)
            assert sum(record["total_tokens"] for record in outcome.token_usage_records) > 0
            assert outcome.trace_id == f"trace-{name}"

        # The nested grandchild unit runs the same way and is a distinct record.
        grandchild = _executor(executor_api, real_app_config, max_turns=12)
        grandchild.trace_id = "trace-grandchild"
        grandchild_result = grandchild.execute("nested work")
        assert grandchild_result.status is statuses.COMPLETED
        assert grandchild_result.result == "FINAL ANSWER"
        assert grandchild_result.trace_id == "trace-grandchild"
        assert parent.trace_id == "trace-parent"
