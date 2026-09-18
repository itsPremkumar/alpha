"""Tests for the bounded self-improvement delegation loop (ralph_loop)."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent_workspace.subagents.config import SubagentConfig

ralph_module = importlib.import_module("agent_workspace.tools.builtins.self_improvement_tool")


# NOTE: backend/tests/conftest.py pre-mocks ``agent_workspace.subagents.executor``
# with a MagicMock class (breaks the subagents->thread_state import cycle),
# so the ``SubagentStatus`` name is unusable in tests. Mirror what
# test_task_tool_core_logic.py does: a local fake with the same members, and
# patch it into the ralph module so identity comparisons hold.
class _FakeStatus:
    def __init__(self, value: str, is_terminal: bool = True) -> None:
        self.value = value
        self.is_terminal = is_terminal


class _FakeSubagentStatus:
    COMPLETED = _FakeStatus("completed")
    FAILED = _FakeStatus("failed")
    TIMED_OUT = _FakeStatus("timed_out")


SubagentStatus = _FakeSubagentStatus
# Point the ralph module at the fake for every test in this file (unit tests
# below never install the full mock set).
ralph_module.SubagentStatus = _FakeSubagentStatus


def _make_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context={"thread_id": "thread-1"},
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )


def _make_config() -> SubagentConfig:
    return SubagentConfig(
        name="general-purpose",
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
    )


def _make_result(status: SubagentStatus, *, result: str | None = "report text", error: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        result=result,
        error=error,
        stop_reason=None,
        tool_receipts=None,
        bash_executions=None,
    )


def _hold_verdict() -> dict:
    return {
        "all_hold": True,
        "leaves": [{"criterion": "file:out.txt exists", "family": "file", "checked": True, "holds": True, "detail": ""}],
    }


def _fail_verdict() -> dict:
    return {
        "all_hold": False,
        "leaves": [{"criterion": "file:out.txt exists", "family": "file", "checked": True, "holds": False, "detail": "missing"}],
    }


def _unchecked_verdict() -> dict:
    return {
        "all_hold": False,
        "leaves": [{"criterion": "be brilliant", "family": "undecidable", "checked": False, "holds": False, "detail": "not deterministically checkable"}],
    }


class _FakeExecutor:
    """Stand-in for SubagentExecutor recording prompts per construction."""

    instances: list[_FakeExecutor] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.prompts: list[str] = []
        type(self).instances.append(self)

    def execute_async(self, prompt: str, task_id: str | None = None) -> str:
        self.prompts.append(prompt)
        return f"exec-{len(type(self).instances)}-{len(self.prompts)}"


@pytest.fixture(autouse=True)
def _reset_fake_executor():
    _FakeExecutor.instances.clear()
    yield
    _FakeExecutor.instances.clear()


def _install_mocks(monkeypatch, *, results: list, verdicts: list | None = None, events: list | None = None) -> dict:
    """Patch every external boundary of the ralph tool. Results/verdicts are consumed per round."""
    recorded: dict[str, Any] = {"events": events if events is not None else []}
    result_iter = iter(results)
    verdict_iter = iter(verdicts if verdicts is not None else [])

    monkeypatch.setattr(ralph_module, "SubagentExecutor", _FakeExecutor)
    monkeypatch.setattr(ralph_module, "SubagentStatus", _FakeSubagentStatus)
    monkeypatch.setattr(ralph_module, "_get_runtime_app_config", lambda runtime: None)
    monkeypatch.setattr(ralph_module, "get_available_subagent_names", lambda *args, **kwargs: ["general-purpose"])
    monkeypatch.setattr(ralph_module, "get_subagent_config", lambda *args, **kwargs: _make_config())
    monkeypatch.setattr(ralph_module, "resolve_subagent_model_name", lambda *args, **kwargs: "fake-model")
    monkeypatch.setattr(ralph_module, "is_host_bash_allowed", lambda *args, **kwargs: True)

    async def _fake_run_assembly(func, **kwargs):
        return []

    monkeypatch.setattr(ralph_module, "run_assembly", _fake_run_assembly)
    monkeypatch.setattr(ralph_module, "resolve_runtime_user_id", lambda runtime: "user-1")
    monkeypatch.setattr(ralph_module, "normalize_authz_attributes", lambda value: {})
    monkeypatch.setattr(ralph_module, "resolve_run_extensions", lambda context: None)
    monkeypatch.setattr(ralph_module, "resolve_trace_id", lambda value: "trace-1")
    monkeypatch.setattr(ralph_module, "get_stream_writer", lambda: object())

    async def _fake_emit(event: dict, writer: Any = None) -> None:
        recorded["events"].append(event)

    monkeypatch.setattr(ralph_module, "aemit_custom_event", _fake_emit)
    monkeypatch.setattr(ralph_module, "verify_receipt_citations", lambda text, receipts: None)

    def _fake_get_result(execution_id: str):
        return next(result_iter)

    def _fake_check(criteria, **kwargs):
        # Sync: production invokes the checker via asyncio.to_thread.
        return next(verdict_iter)

    monkeypatch.setattr(ralph_module, "get_background_task_result", _fake_get_result)
    monkeypatch.setattr(ralph_module, "check_acceptance_criteria", _fake_check)
    recorded_cleanup: list[str] = []
    recorded["cleaned"] = recorded_cleanup
    monkeypatch.setattr(ralph_module, "cleanup_background_task", recorded_cleanup.append)
    return recorded


def _run_ralph(**kwargs) -> str | Command:
    kwargs.setdefault("runtime", _make_runtime())
    kwargs.setdefault("tool_call_id", "ralph-1")
    coroutine = getattr(ralph_module.ralph_loop_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return ralph_module.ralph_loop_tool.func(**kwargs)


def _message(result: str | Command) -> ToolMessage:
    assert isinstance(result, Command)
    messages = result.update["messages"]
    assert len(messages) == 1 and isinstance(messages[0], ToolMessage)
    return messages[0]


class TestRalphValidation:
    def test_empty_task_fails(self, monkeypatch):
        _install_mocks(monkeypatch, results=[])
        message = _message(_run_ralph(task="  ", completion_promise="file:out.txt exists"))
        assert "non-empty task" in message.content

    def test_empty_promise_fails(self, monkeypatch):
        _install_mocks(monkeypatch, results=[])
        message = _message(_run_ralph(task="do it", completion_promise=" "))
        assert "non-empty completion_promise" in message.content

    @pytest.mark.parametrize("bad_rounds", [0, 9, "3", None])
    def test_max_rounds_bounds_rejected(self, monkeypatch, bad_rounds):
        _install_mocks(monkeypatch, results=[])
        message = _message(_run_ralph(task="do it", completion_promise="file:out.txt exists", max_rounds=bad_rounds))
        assert "max_rounds" in message.content
        assert _FakeExecutor.instances == []

    def test_unknown_subagent_type_lists_available(self, monkeypatch):
        _install_mocks(monkeypatch, results=[])
        message = _message(_run_ralph(task="do it", completion_promise="file:out.txt exists", subagent_type="nope"))
        assert "Unknown subagent type 'nope'" in message.content
        assert "general-purpose" in message.content


class TestRalphLoop:
    def test_success_first_round(self, monkeypatch):
        recorded = _install_mocks(monkeypatch, results=[_make_result(SubagentStatus.COMPLETED)], verdicts=[_hold_verdict()])
        message = _message(_run_ralph(task="write it", completion_promise="file:out.txt exists"))
        assert "holds after 1 round(s)" in message.content
        assert "report text" in message.content
        assert len(_FakeExecutor.instances) == 1
        assert message.additional_kwargs["subagent_status"] == "completed"
        kinds = [event["type"] for event in recorded["events"]]
        assert kinds == ["ralph_started", "ralph_round_end", "ralph_completed"]

    def test_failed_round_retries_with_shortfall(self, monkeypatch):
        _install_mocks(
            monkeypatch,
            results=[_make_result(SubagentStatus.FAILED, result=None, error="boom"), _make_result(SubagentStatus.COMPLETED)],
            verdicts=[_hold_verdict()],
        )
        message = _message(_run_ralph(task="write it", completion_promise="file:out.txt exists", max_rounds=3))
        assert "holds after 2 round(s)" in message.content
        assert len(_FakeExecutor.instances) == 2
        second_prompt = _FakeExecutor.instances[1].prompts[0]
        assert "Previous attempt fell short" in second_prompt
        assert "boom" in second_prompt or "failed" in second_prompt

    def test_exhaustion_reports_gaps_honestly(self, monkeypatch):
        recorded = _install_mocks(
            monkeypatch,
            results=[_make_result(SubagentStatus.FAILED, result=None, error="nope"), _make_result(SubagentStatus.FAILED, result=None, error="still nope")],
        )
        message = _message(_run_ralph(task="write it", completion_promise="file:out.txt exists", max_rounds=2))
        assert message.additional_kwargs["subagent_status"] == "failed"
        assert "still unmet after 2 round(s)" in message.content
        kinds = [event["type"] for event in recorded["events"]]
        assert kinds[-1] == "ralph_exhausted"

    def test_failing_leaf_retries_and_unchecked_completes(self, monkeypatch):
        _install_mocks(
            monkeypatch,
            results=[_make_result(SubagentStatus.COMPLETED), _make_result(SubagentStatus.COMPLETED)],
            verdicts=[_fail_verdict(), _hold_verdict()],
        )
        message = _message(_run_ralph(task="write it", completion_promise="file:out.txt exists", max_rounds=3))
        assert "holds after 2 round(s)" in message.content
        assert "file:out.txt exists" in _FakeExecutor.instances[1].prompts[0]

    def test_unchecked_verdict_completes_on_clean_run(self, monkeypatch):
        _install_mocks(monkeypatch, results=[_make_result(SubagentStatus.COMPLETED)], verdicts=[_unchecked_verdict()])
        message = _message(_run_ralph(task="be brilliant", completion_promise="be brilliant"))
        assert "not deterministically checkable" in message.content
        assert len(_FakeExecutor.instances) == 1

    def test_vanished_round_is_retried(self, monkeypatch):
        _install_mocks(
            monkeypatch,
            results=[None, _make_result(SubagentStatus.COMPLETED)],
            verdicts=[_hold_verdict()],
        )
        message = _message(_run_ralph(task="write it", completion_promise="file:out.txt exists", max_rounds=2))
        assert "holds after 2 round(s)" in message.content

    def test_parent_cancel_requests_round_cancel_and_reraises(self, monkeypatch):
        def _raise(execution_id: str):
            raise asyncio.CancelledError()

        cancelled: list[str] = []
        _install_mocks(monkeypatch, results=[_make_result(SubagentStatus.COMPLETED)], verdicts=[_hold_verdict()])
        monkeypatch.setattr(ralph_module, "get_background_task_result", _raise)
        monkeypatch.setattr(ralph_module, "request_cancel_background_task", cancelled.append)
        with pytest.raises(asyncio.CancelledError):
            _run_ralph(task="write it", completion_promise="file:out.txt exists")
        assert len(cancelled) == 1


class TestRalphCompletionRule:
    def test_completed_with_hold(self):
        complete, _ = ralph_module._ralph_complete(SubagentStatus.COMPLETED, _hold_verdict())
        assert complete is True

    def test_failed_status_never_completes(self):
        complete, reason = ralph_module._ralph_complete(SubagentStatus.FAILED, _hold_verdict())
        assert complete is False
        assert "failed" in reason

    def test_missing_verdict_fails_open(self):
        complete, _ = ralph_module._ralph_complete(SubagentStatus.COMPLETED, None)
        assert complete is True

    def test_round_prompt_shortfall_only_after_round_one(self):
        first = ralph_module._round_prompt("do x", "file:o exists", 1, None)
        assert "Previous attempt" not in first
        second = ralph_module._round_prompt("do x", "file:o exists", 2, "missing file")
        assert "Previous attempt fell short" in second
        assert "missing file" in second


class TestRalphRegistration:
    def test_tool_registered_as_builtin(self):
        from agent_workspace.tools.builtins import ralph_loop_tool
        from agent_workspace.tools.tools import BUILTIN_TOOLS

        assert ralph_loop_tool.name == "ralph_loop"
        assert ralph_loop_tool in BUILTIN_TOOLS

    def test_subagents_deny_ralph_loop_by_default(self):
        import dataclasses

        field = next(f for f in dataclasses.fields(SubagentConfig) if f.name == "disallowed_tools")
        assert "ralph_loop" in field.default_factory()
        assert "task" in field.default_factory()
