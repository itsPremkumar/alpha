"""Wiring tests for the two previously-orphaned middlewares.

* MetacognitiveMiddleware — observe-only; after_model must record tool outcomes,
  never mutate state, and never raise.
* ContinualHarnessMiddleware — injects harness context as a hidden system
  reminder; fail-open on any state/disk error; resolves the local state from the
  thread workspace when available.
* Lead-chain wiring — agent.py must reference both middlewares inside the
  autonomy-gated blocks (guards the manifest's declared wiring point).
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from alpha.agents.middlewares.continual_harness_middleware import (
    CONTINUAL_HARNESS_REMINDER_KEY,
    ContinualHarnessMiddleware,
)
from alpha.agents.middlewares.metacognitive_middleware import MetacognitiveMiddleware


def _tool_message(name: str, content: str, status: str) -> ToolMessage:
    """Build a post-validated ToolMessage (pydantic validates status on assignment)."""
    message = ToolMessage(content=content, tool_call_id=f"call-{name}")
    object.__setattr__(message, "name", name)
    object.__setattr__(message, "status", status)
    return message


class TestMetacognitiveMiddleware:
    def test_records_tool_outcomes_from_state(self) -> None:
        middleware = MetacognitiveMiddleware()
        state = {
            "messages": [
                AIMessage(content="doing work"),
                _tool_message("bash", "ok output", "success"),
                _tool_message("write_file", "Error: disk full", "error"),
            ]
        }
        result = middleware.after_model(state, runtime=None)
        assert result is None  # observe-only: never mutates
        assert {"tool": "bash", "success": True} in middleware._history
        assert {"tool": "write_file", "success": False} in middleware._history

    def test_heuristic_success_without_status_field(self) -> None:
        middleware = MetacognitiveMiddleware()
        outcome = middleware._tool_outcomes([_tool_message("grep_tool", "plain results", "")])
        assert outcome == [("grep_tool", True)]

    def test_never_raises_on_bad_state(self) -> None:
        middleware = MetacognitiveMiddleware()
        assert middleware.after_model(None, runtime=None) is None
        assert middleware.after_model({}, runtime=None) is None
        assert middleware.after_model({"messages": [object()]}, runtime=None) is None

    def test_assessment_warns_on_repeated_failures(self, caplog) -> None:
        import logging

        middleware = MetacognitiveMiddleware(confidence=0.99, window=3)
        for _ in range(6):
            middleware.record_tool_result("bash", success=False)
        with caplog.at_level(logging.WARNING):
            middleware.record_tool_result("bash", success=False)
        assert any("Metacognitive signal" in record.message for record in caplog.records)


class TestContinualHarnessMiddleware:
    def test_no_entries_means_no_injection(self) -> None:
        middleware = ContinualHarnessMiddleware(
            local_state=None,
            global_state=None,
        )
        # Default states point at non-existent files in the test environment.
        result = middleware.before_agent(state={}, runtime=SimpleNamespace(context={}))
        assert result is None

    def test_local_state_resolves_from_workspace_context(self, tmp_path) -> None:
        from alpha.harness.continual.state import HarnessState

        local = HarnessState(file_path=tmp_path / "harness.json", scope="local")
        local.add_entry(
            kind="prompt",
            title="Rule",
            content="Always run tests",
        )
        assert local.entries["prompt"], "fixture entry must land in the prompt bucket"
        middleware = ContinualHarnessMiddleware(local_state=local)
        runtime = SimpleNamespace(context={"workspace_path": str(tmp_path / "ws")})
        result = middleware.before_agent(state={}, runtime=runtime)
        # The explicit local_state is used as-is and produces the reminder.
        assert result is not None
        message = result["messages"][0]
        assert "Always run tests" in message.content
        assert message.additional_kwargs[CONTINUAL_HARNESS_REMINDER_KEY] is True
        assert message.additional_kwargs["hide_from_ui"] is True

    def test_fail_open_on_broken_state(self) -> None:
        class BrokenState:
            def format_for_prompt(self) -> str:
                raise RuntimeError("disk exploded")

        middleware = ContinualHarnessMiddleware(local_state=BrokenState())  # type: ignore[arg-type]
        result = middleware.before_agent(state={}, runtime=SimpleNamespace(context={}))
        assert result is None  # degraded to "no reminder", never raised


def test_lead_chain_references_both_middlewares() -> None:
    from pathlib import Path

    agent_py = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "harness"
        / "alpha"
        / "agents"
        / "lead_agent"
        / "agent.py"
    )
    source = agent_py.read_text(encoding="utf-8")
    assert "ContinualHarnessMiddleware()" in source, "continual_harness middleware is not wired into the lead chain"
    assert "MetacognitiveMiddleware(" in source, "metacognitive middleware is not wired into the lead chain"
    assert "autonomy.continual_harness.enabled" in source
    assert "autonomy.metacognition.enabled" in source
