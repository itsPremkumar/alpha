"""Reasoning support for the keyless free-LLM router (``ChatFreeLLM``).

``alpha-free`` is the shipped ``default_model``, so its declared capabilities
are what every keyless install advertises in the model picker. It used to
declare ``supports_thinking: false`` and ``supports_reasoning_effort: false``,
which was a statement about the *client* rather than the model: the router
threw ``reasoning_content`` away on the way in, and had no way to send
``reasoning_effort`` on the way out. A reasoning gateway therefore looked
non-reasoning, and an operator could not reach the effort rungs the endpoint
actually serves.

These tests pin the fixed behaviour at all three layers:

* ``providers.py`` parses a provider's reasoning field (honestly: absent stays
  absent, and a Responses-API ``reasoning`` *object* is never stringified).
* ``catalog.py`` carries it through and can send a rung.
* ``chat_model.py`` forwards the rung and surfaces the reasoning under the
  ``reasoning_content`` key every other reasoning provider in this package uses,
  and is classified as ``openai``-shaped rather than ``inert`` so the declared
  ladder is reachable instead of clamped away.

Network is never touched: ``providers.request`` is the single HTTP seam and is
stubbed, exactly as ``test_free_llm_router.py`` does.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.config.model_config import ModelConfig
from alpha.models.effort_translation import (
    apply_effort,
    detect_effort_style,
    effective_effort_support,
    model_effort_support,
    resolve_effort,
)
from alpha.models.free_router import providers as provider_layer
from alpha.models.free_router.catalog import FreeLLMRouter
from alpha.models.free_router.chat_model import ChatFreeLLM
from alpha.models.free_router.providers import _extract_openai_chat

# --------------------------------------------------------------------------
# providers.py - parsing the provider's reasoning field
# --------------------------------------------------------------------------


def _payload(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


class TestReasoningParsing:
    def test_reasoning_content_is_captured(self) -> None:
        result = _extract_openai_chat(_payload({"content": "42", "reasoning_content": "adding 40 and 2"}))
        assert result.text == "42"
        assert result.reasoning == "adding 40 and 2"

    def test_absent_reasoning_stays_none(self) -> None:
        """Absent is not "empty" - a non-reasoning gateway sends no field."""
        result = _extract_openai_chat(_payload({"content": "hi"}))
        assert result.reasoning is None

    def test_blank_reasoning_is_treated_as_absent(self) -> None:
        result = _extract_openai_chat(_payload({"content": "hi", "reasoning_content": "   "}))
        assert result.reasoning is None

    def test_string_reasoning_alias_is_accepted(self) -> None:
        result = _extract_openai_chat(_payload({"content": "hi", "reasoning": "thinking"}))
        assert result.reasoning == "thinking"

    def test_responses_api_reasoning_object_is_not_stringified(self) -> None:
        """The Responses API sends ``reasoning`` as an object.

        Stringifying it would put a JSON blob into the transcript as if the
        model had written prose, so an object must be ignored outright.
        """
        result = _extract_openai_chat(_payload({"content": "hi", "reasoning": {"type": "reasoning", "id": "r1"}}))
        assert result.reasoning is None

    def test_reasoning_content_wins_over_alias(self) -> None:
        result = _extract_openai_chat(_payload({"content": "hi", "reasoning_content": "primary", "reasoning": "alias"}))
        assert result.reasoning == "primary"

    def test_reasoning_survives_a_tool_call_turn(self) -> None:
        """A reasoning turn that only calls a tool has no content but is valid."""
        payload = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": "need the tool",
                        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                    }
                }
            ]
        }
        result = _extract_openai_chat(payload)
        assert result.reasoning == "need the tool"
        assert result.tool_calls[0]["function"]["name"] == "f"


# --------------------------------------------------------------------------
# catalog.py - carrying reasoning and sending a rung
# --------------------------------------------------------------------------


#: Pin to the one gateway the shipped `alpha-free` entry names. Gateway specs
#: come from `config.yaml -> free_gateways` at import time, so the test drives a
#: real provider spec and stubs only the HTTP seam beneath it - which is exactly
#: the seam `test_free_llm_router.py` stubs.
TARGET_MODEL = "free:opencode-zen:space-bunny-free"


def _stub_router(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reasoning: str | None,
    captured: dict[str, Any],
) -> FreeLLMRouter:
    """A real router whose every HTTP call is stubbed; records chat bodies."""
    import httpx

    def handler(method: str, url: str, *, headers=None, params=None, json_body=None, timeout=None) -> httpx.Response:
        if "chat/completions" in url or url.endswith("/openai") or "generate/text" in url:
            captured["body"] = json_body
            return httpx.Response(200, json=_payload({"content": "answer", "reasoning_content": reasoning}))
        return httpx.Response(
            200,
            json={"data": [{"id": "space-bunny-free", "object": "model", "owned_by": "stub", "pricing": {}}]},
        )

    monkeypatch.setattr(provider_layer, "request", handler)
    return FreeLLMRouter(ttl=300.0, cache_path=tmp_path / "catalog.json", clock=lambda: 1_000_000.0)


class TestCatalogReasoning:
    def test_reasoning_reaches_the_caller(self, tmp_path, monkeypatch) -> None:
        captured: dict[str, Any] = {}
        router = _stub_router(tmp_path, monkeypatch, reasoning="step by step", captured=captured)
        result = router.chat([{"role": "user", "content": "hi"}], model=TARGET_MODEL)
        assert result.reasoning == "step by step"

    def test_absent_reasoning_reaches_the_caller_as_none(self, tmp_path, monkeypatch) -> None:
        router = _stub_router(tmp_path, monkeypatch, reasoning=None, captured={})
        assert router.chat([{"role": "user", "content": "hi"}], model=TARGET_MODEL).reasoning is None

    def test_reasoning_effort_is_sent_as_a_top_level_key(self, tmp_path, monkeypatch) -> None:
        captured: dict[str, Any] = {}
        router = _stub_router(tmp_path, monkeypatch, reasoning=None, captured=captured)
        router.chat([{"role": "user", "content": "hi"}], model=TARGET_MODEL, reasoning_effort="high")
        assert captured["body"]["reasoning_effort"] == "high"

    def test_effort_is_omitted_when_no_rung_is_requested(self, tmp_path, monkeypatch) -> None:
        """Omitting the key leaves the gateway's own default in place."""
        captured: dict[str, Any] = {}
        router = _stub_router(tmp_path, monkeypatch, reasoning=None, captured=captured)
        router.chat([{"role": "user", "content": "hi"}], model=TARGET_MODEL)
        assert "reasoning_effort" not in captured["body"]

    def test_effort_does_not_mutate_a_caller_owned_extra_body(self, tmp_path, monkeypatch) -> None:
        captured: dict[str, Any] = {}
        router = _stub_router(tmp_path, monkeypatch, reasoning=None, captured=captured)
        caller_extra: dict[str, Any] = {"temperature": 0.2}
        router.chat([{"role": "user", "content": "hi"}], model=TARGET_MODEL, extra=caller_extra, reasoning_effort="low")
        assert "reasoning_effort" not in caller_extra
        assert captured["body"]["temperature"] == 0.2
        assert captured["body"]["reasoning_effort"] == "low"


# --------------------------------------------------------------------------
# chat_model.py - the declared capabilities are reachable
# --------------------------------------------------------------------------


def _alpha_free_entry(**overrides: Any) -> ModelConfig:
    fields: dict[str, Any] = {
        "name": "alpha-free",
        "use": "alpha.models.free_router:ChatFreeLLM",
        "model": "free:opencode-zen:space-bunny-free",
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "reasoning_efforts": ["low", "medium", "high"],
        "reasoning_effort_style": "openai",
    }
    fields.update(overrides)
    return ModelConfig(**fields)


class TestFreeLLMEffortPlumbing:
    def test_reasoning_effort_is_a_real_constructor_field(self) -> None:
        """``apply_effort`` writes ``settings['reasoning_effort']`` only when the
        class declares the field; with ``extra_body`` instead, ``ChatFreeLLM``
        would drop it and the ladder would be inert config."""
        assert "reasoning_effort" in ChatFreeLLM.model_fields

    def test_style_resolves_to_openai_with_or_without_a_declaration(self) -> None:
        """The wire shape these gateways speak.

        Auto-detection already works: ``alpha.models.free_router`` is registered
        in ``effort_translation._MODULE_STYLES`` as ``openai``, so the module is
        matched even though ``ChatFreeLLM`` is not a ``BaseChatOpenAI``
        subclass. The shipped config still declares it, as documentation.
        """
        assert detect_effort_style(ChatFreeLLM, "openai") == "openai"
        assert detect_effort_style(ChatFreeLLM, None) == "openai"

    def test_declared_ladder_is_the_ceiling(self) -> None:
        entry = _alpha_free_entry()
        assert model_effort_support(entry) == (["low", "medium", "high"], None)
        assert effective_effort_support(entry) == ["low", "medium", "high"]

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            # Never raised above the declared ladder...
            ("xhigh", "high"),
            ("max", "high"),
            # ...and clamped up to its floor when the request is below it.
            ("minimal", "low"),
            ("off", "none"),
        ],
    )
    def test_resolution_clamps_without_raising_effort(self, requested: str, expected: str) -> None:
        level, style = resolve_effort(_alpha_free_entry(), requested, model_class=ChatFreeLLM)
        assert (level, style) == (expected, "openai")

    def test_no_request_sends_nothing(self) -> None:
        """Sending nothing is what preserves the gateway's own default."""
        level, _ = resolve_effort(_alpha_free_entry(), None, model_class=ChatFreeLLM)
        assert level is None

    def test_resolved_rung_lands_in_constructor_settings(self) -> None:
        entry = _alpha_free_entry()
        level, style = resolve_effort(entry, "high", model_class=ChatFreeLLM)
        settings = apply_effort(ChatFreeLLM, entry, {}, level, style=style)
        assert settings["reasoning_effort"] == "high"
        # The real field, not a diverted extra_body key.
        assert "extra_body" not in settings

    def test_reasoning_surfaces_under_the_shared_key(self) -> None:
        """One vocabulary: ``patched_mimo``/``patched_deepseek``/
        ``openai_codex_provider`` and ``restore_reasoning_content`` all read
        ``additional_kwargs['reasoning_content']``."""
        from alpha.models.free_router.catalog import FreeChatResult

        llm = ChatFreeLLM(model="free:opencode-zen:space-bunny-free")
        message = llm._result_to_message(
            FreeChatResult(
                text="answer",
                provider="opencode-zen",
                model_id="space-bunny-free",
                reasoning="thought it through",
            )
        )
        assert message.additional_kwargs["reasoning_content"] == "thought it through"
        assert message.content == "answer"

    def test_no_reasoning_key_when_the_gateway_sent_none(self) -> None:
        from alpha.models.free_router.catalog import FreeChatResult

        llm = ChatFreeLLM(model="free:opencode-zen:space-bunny-free")
        message = llm._result_to_message(FreeChatResult(text="answer", provider="opencode-zen", model_id="space-bunny-free"))
        assert "reasoning_content" not in message.additional_kwargs

    def test_reasoning_survives_the_single_chunk_stream(self) -> None:
        """The router emits one chunk carrying the whole message, so reasoning
        must reach the UI through that path too."""
        from alpha.models.free_router.catalog import FreeChatResult

        llm = ChatFreeLLM(model="free:opencode-zen:space-bunny-free")
        message = llm._result_to_message(FreeChatResult(text="answer", provider="opencode-zen", model_id="space-bunny-free", reasoning="r"))
        chunk = llm._to_chunk(message)
        assert chunk.additional_kwargs["reasoning_content"] == "r"


# --------------------------------------------------------------------------
# Shipped config
# --------------------------------------------------------------------------


class TestShippedConfig:
    def test_example_template_advertises_reasoning(self) -> None:
        """``config.example.yaml`` is the template every install copies, so the
        declared capability is what a keyless install shows in the picker."""
        from pathlib import Path

        import yaml

        example = Path(__file__).resolve().parents[2] / "config.example.yaml"
        data = yaml.safe_load(example.read_text(encoding="utf-8"))
        entry = next(m for m in data["models"] if m["name"] == "alpha-free")
        assert entry["supports_thinking"] is True
        assert entry["supports_reasoning_effort"] is True
        assert entry["reasoning_efforts"] == ["low", "medium", "high"]
        # Declared for documentation; auto-detection resolves the same shape.
        assert entry["reasoning_effort_style"] == "openai"
        # No guessed default rung; sending nothing preserves the gateway default.
        assert "default_reasoning_effort" not in entry

    def test_every_declared_rung_is_on_the_canonical_ladder(self) -> None:
        from pathlib import Path

        import yaml

        from alpha.config.reasoning_effort import CANONICAL_EFFORTS

        example = Path(__file__).resolve().parents[2] / "config.example.yaml"
        data = yaml.safe_load(example.read_text(encoding="utf-8"))
        entry = next(m for m in data["models"] if m["name"] == "alpha-free")
        assert set(entry["reasoning_efforts"]) <= set(CANONICAL_EFFORTS)
