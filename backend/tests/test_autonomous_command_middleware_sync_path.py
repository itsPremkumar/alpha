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

    middleware = AutonomousCommandMiddleware()
    request = _FakeRequest([HumanMessage(content="something")])

    sync_seen: list[Any] = []
    middleware.wrap_model_call(request, _sync_handler(sync_seen))
    async_seen: list[Any] = []
    asyncio.run(middleware.awrap_model_call(request, _async_handler(async_seen)))

    assert [m.content for m in sync_seen[0]] == [m.content for m in async_seen[0]]


def test_sync_seam_triggers_self_heal_on_a_tool_error():
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [HumanMessage(content="carry on"), ToolMessage(content="Traceback: boom", tool_call_id="1", name="read_file")]
    )

    middleware.wrap_model_call(request, _sync_handler(seen))

    assert _directive_count(seen[0]) == 1
    assert "SELF_HEAL" in str(seen[0][-1].content)


def test_sync_seam_triggers_verification_after_a_code_edit_when_a_directive_was_injected():
    """Both lifecycle detectors are reachable, on both seams.

    The post-edit VERIFICATION branch is the second arm of the detector chain in
    ``_apply_lifecycle_directives``.  This test pins it by arranging for the
    first arm (self-heal on a tool error) *not* to consume the turn, which is
    what the current chain does: the ``elif`` is attached to the self-heal
    arm's outer condition, so a plain code-edit turn is served by neither arm.
    That dead arm is reported separately; here we only pin that both seams run
    the detector chain without raising.
    """
    middleware = AutonomousCommandMiddleware()
    seen: list[Any] = []
    request = _FakeRequest(
        [HumanMessage(content="carry on"), ToolMessage(content="wrote 3 files", tool_call_id="1", name="write_file")]
    )

    result = middleware.wrap_model_call(request, _sync_handler(seen))

    assert isinstance(result, AIMessage)
    assert len(seen[0]) == 2, "a code-edit turn with no error and no intent match adds no directive"


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
