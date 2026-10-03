"""ReasoningBankContextMiddleware unit tests.

Pins the contract: every model call gets at most one fresh, bounded
reasoning-bank recall as a hidden HumanMessage appended to the request;
state is never mutated; a stranded marker is swept before injection; and
non-string/tool-result content never becomes the query.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from alpha.reasoning_bank.bank import get_reasoning_bank


class _Runtime:
    def __init__(self, context: dict | None = None) -> None:
        self.context = context or {}


class _Request:
    def __init__(self, messages: list) -> None:
        self.messages = list(messages)

    def override(self, **changes: Any) -> _Request:
        clone = _Request([])
        for k, v in {**self.__dict__, **changes}.items():
            setattr(clone, k, v)
        return clone


def _assistant() -> AIMessage:
    return AIMessage(content="done")


def _handler(messages: list) -> Any:
    def _inner(request: Any) -> Any:
        return _assistant()

    return _inner


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    import alpha.reasoning_bank.bank as bank_mod

    monkeypatch.setattr(bank_mod, "_bank", None, raising=False)
    monkeypatch.setattr(bank_mod, "_bank_path", None, raising=False)
    yield


def _middleware():
    from alpha.agents.middlewares.reasoning_bank_context_middleware import ReasoningBankContextMiddleware

    return ReasoningBankContextMiddleware()


class TestInject:
    def test_appends_a_hidden_recall_to_the_request(self):
        get_reasoning_bank().record("code", "flaky tests", "rerun with -p no:cacheprovider", verdict="success", evidence_ref="r1")
        messages = [HumanMessage(content="the tests keep flaking in CI", id="u1")]
        request = _Request(messages)
        seen: dict = {}

        def handler(req: Any) -> Any:
            seen["messages"] = req.messages
            return _assistant()

        middleware = _middleware()
        middleware.wrap_model_call(request, handler)
        out = seen["messages"]
        assert len(out) == 2
        injected = out[-1]
        assert isinstance(injected, HumanMessage)
        assert "rerun with -p no:cacheprovider" in injected.content
        assert "<reasoning_bank>" in injected.content
        assert injected.additional_kwargs["hide_from_ui"] is True
        assert injected.additional_kwargs["reasoning_bank_reminder"] is True
        assert injected.id.startswith("__reasoning_bank__")
        # The model never sees this as system-role.
        assert not isinstance(injected, SystemMessage)
        # State is untouched: only the request was overridden.
        assert request.messages == messages

    def test_no_injection_when_bank_is_empty(self):
        messages = [HumanMessage(content="hello there", id="u1")]
        request = _Request(messages)
        seen: dict = {}

        def handler(req: Any) -> Any:
            seen["messages"] = req.messages
            return _assistant()

        middleware = _middleware()
        middleware.wrap_model_call(request, handler)
        assert seen["messages"] == messages

    def test_a_stranded_hidden_block_is_swept_not_duplicated(self):
        get_reasoning_bank().record("code", "flaky tests", "rerun with -p no:cacheprovider", verdict="success", evidence_ref="r1")
        stale = HumanMessage(
            content="<reasoning_bank>\n- **code** old\n</reasoning_bank>",
            id="__reasoning_bank__u1",
            additional_kwargs={"hide_from_ui": True, "reasoning_bank_reminder": True},
        )
        messages = [HumanMessage(content="the tests keep flaking in CI", id="u1"), stale]
        request = _Request(messages)
        seen: dict = {}

        def handler(req: Any) -> Any:
            seen["messages"] = req.messages
            return _assistant()

        middleware = _middleware()
        middleware.wrap_model_call(request, handler)
        out = seen["messages"]
        bank_messages = [m for m in out if isinstance(m, HumanMessage) and m.additional_kwargs.get("reasoning_bank_reminder")]
        assert len(bank_messages) == 1
        assert "old" not in bank_messages[0].content

    def test_non_text_user_content_yields_no_injection(self):
        get_reasoning_bank().record("code", "flaky tests", "rerun", verdict="success", evidence_ref="r1")
        parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]
        request = _Request([HumanMessage(content=parts, id="u1")])
        seen: dict = {}

        def handler(req: Any) -> Any:
            seen["messages"] = req.messages
            return _assistant()

        middleware = _middleware()
        middleware.wrap_model_call(request, handler)
        assert len(seen["messages"]) == 1

    def test_disabled_via_config(self):
        get_reasoning_bank().record("code", "flaky tests", "rerun", verdict="success", evidence_ref="r1")
        from alpha.agents.middlewares.reasoning_bank_context_middleware import ReasoningBankContextMiddleware

        class _Config:
            reasoning_bank = {"enabled": False}

        middleware = ReasoningBankContextMiddleware(app_config=_Config())
        messages = [HumanMessage(content="the tests keep flaking in CI", id="u1")]
        request = _Request(messages)
        seen: dict = {}

        def handler(req: Any) -> Any:
            seen["messages"] = req.messages
            return _assistant()

        middleware.wrap_model_call(request, handler)
        assert seen["messages"] == messages

    def test_async_path_matches_sync(self):
        import asyncio

        get_reasoning_bank().record("code", "flaky tests", "rerun", verdict="success", evidence_ref="r1")
        messages = [HumanMessage(content="the tests keep flaking in CI", id="u1")]
        request = _Request(messages)
        seen: dict = {}

        async def handler(req: Any) -> Any:
            seen["messages"] = req.messages
            return _assistant()

        middleware = _middleware()
        asyncio.run(middleware.awrap_model_call(request, handler))
        assert len(seen["messages"]) == 2
