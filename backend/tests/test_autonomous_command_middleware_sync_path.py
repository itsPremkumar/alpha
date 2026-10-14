"""Regression tests for the autonomous-command middleware's sync/async parity.

Defect: ``AutonomousCommandMiddleware`` overrode only ``awrap_model_call``.
``AgentMiddleware.wrap_model_call``'s base implementation raises
``NotImplementedError`` in that case, so every *synchronous* agent run
(``invoke()`` / ``stream()``) carrying the middleware failed outright instead of
running. These tests pin both seams and pin that they produce the same directive
messages, so the two cannot drift apart again.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from alpha.agents.middlewares.autonomous_command_middleware import AutonomousCommandMiddleware

DIRECTIVE_MARKER = "autonomous_command_lifecycle_directive"


class _FakeRequest:
    """Minimal stand-in for ``ModelRequest``: only messages + override()."""

    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages

    def override(self, **kwargs: Any) -> _FakeRequest:
        return _FakeRequest(kwargs.get("messages", self.messages))


def _directive_count(messages: list[Any]) -> int:
    return sum(1 for m in messages if isinstance(m, SystemMessage) and DIRECTIVE_MARKER in str(m.content))


def _sync_handler(store: list[Any]):
    def handler(request):
        store.append(request.messages)
        return AIMessage(content="ok")

    return handler


def _async_handler(store: list[Any]):
    async def handler(request):
        store.append(request.messages)
        return AIMessage(content="ok")

    return handler


def test_sync_seam_runs_and_injects_the_lifecycle_directive():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest([HumanMessage(content="Build a complete multi-tier platform from scratch overnight")])

    result = middleware.wrap_model_call(request, _sync_handler(seen))

    assert isinstance(result, AIMessage)
    assert seen, "the handler must be called on the sync path"
    assert _directive_count(seen[0]) == 1, "the sync path must inject the same directive the async path does"


def test_async_seam_runs_and_injects_the_lifecycle_directive():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest([HumanMessage(content="Build a complete multi-tier platform from scratch overnight")])

    result = asyncio.run(middleware.awrap_model_call(request, _async_handler(seen)))

    assert isinstance(result, AIMessage)
    assert _directive_count(seen[0]) == 1


def test_both_seams_produce_identical_directive_messages(monkeypatch):
    """The detection logic is shared, so the two seams cannot diverge."""
    from alpha.commands.autonomous_engine import AutonomousDetectionResult, LifecyclePhase

    detection = AutonomousDetectionResult(
        matched=True,
        command="/self-heal",
        phase=LifecyclePhase.SELF_HEAL,
        confidence=0.9,
        reason="pinned for the parity assertion",
        rule_id="probe",
        autonomous_directives=["Run the real repair pass."],
    )
    monkeypatch.setattr(
        "alpha.agents.middlewares.autonomous_command_middleware.autonomous_command_engine.identify_and_trigger",
        lambda *a, **k: detection,
    )

    sync_middleware = AutonomousCommandMiddleware()
    async_middleware = AutonomousCommandMiddleware()
    request = _FakeRequest([HumanMessage(content="something")])

    sync_seen: list[Any] = []
    sync_middleware.wrap_model_call(request, _sync_handler(sync_seen))
    async_seen: list[Any] = []
    asyncio.run(async_middleware.awrap_model_call(request, _async_handler(async_seen)))

    assert [m.content for m in sync_seen[0]] == [m.content for m in async_seen[0]]


def test_sync_seam_triggers_self_heal_only_for_structured_recoverable_tool_error():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [
            HumanMessage(content="carry on"),
            ToolMessage(
                content="Traceback: boom",
                tool_call_id="1",
                name="read_file",
                additional_kwargs={
                    "alpha_tool_meta": {
                        "status": "error",
                        "error_type": "permission",
                        "recoverable_by_model": True,
                        "recommended_next_action": "try_alternative",
                    }
                },
            ),
        ]
    )

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert _directive_count(seen[0]) == 1
    assert "SELF_HEAL" in str(seen[0][-1].content)
    assert "Traceback: boom" not in str(seen[0][-1].content)


def test_tool_output_that_mentions_error_does_not_trigger_self_heal():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [
            HumanMessage(content="carry on"),
            ToolMessage(
                content="The review could not be completed because of permission errors.",
                tool_call_id="1",
                name="task",
                additional_kwargs={
                    "alpha_tool_meta": {
                        "status": "success",
                        "recoverable_by_model": False,
                    }
                },
            ),
        ]
    )

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert _directive_count(seen[0]) == 0


def test_nonrecoverable_structured_tool_error_does_not_trigger_self_heal():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [
            HumanMessage(content="carry on"),
            ToolMessage(
                content="Error: credentials unavailable",
                tool_call_id="1",
                name="provider_call",
                additional_kwargs={
                    "alpha_tool_meta": {
                        "status": "error",
                        "error_type": "auth",
                        "recoverable_by_model": False,
                        "recommended_next_action": "stop",
                    }
                },
            ),
        ]
    )

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert _directive_count(seen[0]) == 0


def test_sync_seam_triggers_verification_after_a_successful_code_edit():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [
            HumanMessage(content="carry on"),
            AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {"file_path": "src/main.py"}, "id": "1", "type": "tool_call"}],
            ),
            ToolMessage(content="wrote 3 files", tool_call_id="1", name="write_file"),
        ]
    )

    result = middleware.wrap_model_call(request, _sync_handler(seen))

    assert isinstance(result, AIMessage)
    assert _directive_count(seen[0]) == 1
    assert "VERIFICATION" in str(seen[0][-1].content)


def test_sync_seam_does_not_request_code_verification_for_a_text_artifact():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [
            HumanMessage(content="Create a text artifact"),
            AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {"file_path": "notes/smoke.txt"}, "id": "1", "type": "tool_call"}],
            ),
            ToolMessage(content="wrote text file", tool_call_id="1", name="write_file"),
        ]
    )

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert _directive_count(seen[0]) == 0


def test_natural_language_command_is_dispatched_once_per_user_message(monkeypatch):
    from alpha.agents.middlewares import autonomous_command_middleware as middleware_module
    from alpha.commands.autonomous_engine import AutonomousDetectionResult, LifecyclePhase

    calls: list[str] = []
    detection = AutonomousDetectionResult(
        matched=True,
        command="/verify tests",
        phase=LifecyclePhase.VERIFICATION,
        confidence=0.9,
        reason="explicit verification request",
        rule_id="test",
        autonomous_directives=["Run the requested check."],
    )

    def detect(prompt, **_kwargs):
        calls.append(prompt)
        return detection

    monkeypatch.setattr(middleware_module.autonomous_command_engine, "identify_and_trigger", detect)
    middleware = AutonomousCommandMiddleware()
    request = _FakeRequest([HumanMessage(content="Please run pytest.", id="user-turn-1")])
    first: list[Any] = []
    second: list[Any] = []

    middleware.wrap_model_call(request, _sync_handler(first))
    middleware.wrap_model_call(request, _sync_handler(second))

    assert calls == ["Please run pytest."]
    assert _directive_count(first[0]) == 1
    assert _directive_count(second[0]) == 0

    next_turn: list[Any] = []
    next_request = _FakeRequest([HumanMessage(content="Please run pytest.", id="user-turn-2")])
    middleware.wrap_model_call(next_request, _sync_handler(next_turn))
    assert calls == ["Please run pytest.", "Please run pytest."]
    assert _directive_count(next_turn[0]) == 1


def test_seams_do_not_mutate_the_caller_request():
    middleware = AutonomousCommandMiddleware()
    original = [HumanMessage(content="Build a complete multi-tier platform from scratch overnight")]
    request = _FakeRequest(original)
    seen: list[Any] = []

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert request.messages == original, "the caller's message list must be left alone"
    assert seen[0] is not original


def test_a_quiet_turn_is_forwarded_unchanged():
    middleware = AutonomousCommandMiddleware()
    original = [HumanMessage(content="hello")]
    request = _FakeRequest(original)
    seen: list[Any] = []

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert _directive_count(seen[0]) == 0
    assert len(seen[0]) == 1


def test_sync_seam_does_not_raise_notimplementederror():
    """The regression itself: the base class raised this on every sync run."""
    middleware = AutonomousCommandMiddleware()
    request = _FakeRequest([HumanMessage(content="hello")])
    seen: list[Any] = []

    result = middleware.wrap_model_call(request, _sync_handler(seen))

    assert isinstance(result, AIMessage)
    assert seen
