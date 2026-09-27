"""Reasoning-effort ladder: resolution, clamping, and provider wire translation.

The tests are organized around the failure modes rather than around the happy
path, because every one of them is a silent wrong answer:

* a rung the provider will reject (a 400 the user cannot explain),
* a rung the request was silently *raised* to, which spends more money than the
  user agreed to,
* a `thinking: disabled` body paired with a high effort, which Claude answers
  with a 400,
* a ladder the config declared being ignored in favour of a hardcoded table,
* a UI that offers a rung the factory will not send.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha.config.model_config import ModelConfig
from alpha.config.reasoning_effort import (
    CANONICAL_EFFORTS,
    EFFORT_LABELS,
    EFFORT_STYLES,
    canonical_order,
    clamp_effort,
    effort_rank,
    is_effort,
    normalize_effort,
)
from alpha.models.effort_translation import (
    EFFORT_STYLE_ANTHROPIC,
    EFFORT_STYLE_BEDROCK,
    EFFORT_STYLE_GOOGLE,
    EFFORT_STYLE_INERT,
    EFFORT_STYLE_OPENAI,
    EFFORT_STYLE_OPENROUTER,
    EFFORT_STYLE_VLLM,
    apply_effort,
    detect_effort_style,
    effective_effort_support,
    model_effort_support,
    resolve_effort,
    supports_effort_control,
)


def model(**overrides) -> ModelConfig:
    base = {
        "name": "probe",
        "model": "probe",
        "use": "langchain_openai:ChatOpenAI",
        "supports_thinking": True,
    }
    return ModelConfig(**{**base, **overrides})


# ---------------------------------------------------------------------------
# The canonical ladder
# ---------------------------------------------------------------------------


def test_canonical_ladder_is_ordered_weakest_to_strongest():
    assert CANONICAL_EFFORTS == ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    ranks = [effort_rank(level) for level in CANONICAL_EFFORTS]
    assert ranks == sorted(ranks)
    assert effort_rank("nonsense") == -1, "an unknown rung must sort below every real one, never as a clamp target"


def test_every_rung_has_a_display_label():
    assert set(EFFORT_LABELS) == set(CANONICAL_EFFORTS)


def test_every_style_is_named_for_the_operator():
    assert set(EFFORT_STYLES) >= {"auto", "openai", "openrouter", "anthropic", "google", "bedrock", "vllm", "inert"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("x-high", "xhigh"),
        ("x_high", "xhigh"),
        ("XHigh", "xhigh"),
        ("EXTRA_HIGH", "xhigh"),
        ("off", "none"),
        ("OFF", "none"),
        ("disabled", "none"),
        ("min", "minimal"),
        ("ultra", "max"),
        ("ultrathink", "max"),
        ("maximum", "max"),
        ("adaptive", "high"),
        ("  High  ", "high"),
    ],
)
def test_normalize_accepts_the_spellings_providers_and_agents_actually_use(raw, expected):
    assert normalize_effort(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "default", "DEFAULT", "auto", "provider", "inherit"])
def test_normalize_maps_every_unset_spelling_to_none_not_to_a_rung(raw):
    # The trap this pins: if "default" resolved to a level, a run that asked for
    # the provider's default would be pinned to that level forever.
    assert normalize_effort(raw) is None


@pytest.mark.parametrize("raw", ["bogus", "gpt-5", "veryhigh", 42, True, False, [], {}])
def test_normalize_refuses_a_value_that_names_no_rung(raw):
    assert normalize_effort(raw) is None


def test_is_effort_distinguishes_unset_from_unrecognized():
    assert is_effort("high") is True
    assert is_effort("x-high") is True
    assert is_effort("default") is False
    assert is_effort("bogus") is False


def test_canonical_order_dedupes_reorders_and_drops_unknowns():
    assert canonical_order(["max", "low", "high", "high", "nope"]) == ["low", "high", "max"]
    assert canonical_order([]) == []
    assert canonical_order(None) == []
    assert canonical_order("high") == ["high"]


# ---------------------------------------------------------------------------
# Clamping — the rule every agent harness converged on
# ---------------------------------------------------------------------------


def test_clamp_picks_the_strongest_supported_rung_at_or_below_the_request():
    ladder = ["low", "medium", "high", "xhigh", "max"]
    assert clamp_effort("high", ladder) == "high"
    assert clamp_effort("xhigh", ladder) == "xhigh"
    assert clamp_effort("max", ladder) == "max"


def test_clamp_lowers_a_request_above_the_ceiling_instead_of_rejecting_it():
    assert clamp_effort("max", ["low", "medium", "high", "xhigh"]) == "xhigh"
    assert clamp_effort("xhigh", ["low", "medium", "high"]) == "high"


def test_clamp_raises_a_request_below_every_supported_rung_to_the_floor():
    # The one place raising is correct: the alternative is a 400, not a cheaper
    # answer. `none` is handled before this function is reached (see the
    # thinking-disable routing below), so a raise here is only ever about a
    # sub-floor reasoning level.
    assert clamp_effort("none", ["low", "medium", "high"]) == "low"
    assert clamp_effort("minimal", ["medium", "high"]) == "medium"


def test_clamp_never_returns_more_effort_than_requested_above_the_floor():
    ladder = ["minimal", "low", "medium", "high", "xhigh", "max"]
    for request in ("minimal", "low", "medium", "high", "xhigh", "max"):
        clamped = clamp_effort(request, ladder)
        assert effort_rank(clamped) <= effort_rank(request), f"{request} clamped to {clamped} increased effort"


def test_clamp_without_a_ladder_is_none_not_a_guess():
    assert clamp_effort("high", []) is None
    assert clamp_effort("high", None) is None


# ---------------------------------------------------------------------------
# ModelConfig: the declared ladder is the contract
# ---------------------------------------------------------------------------


def test_declared_ladder_is_normalized_ordered_and_deduped():
    entry = model(reasoning_efforts=["max", "low", "x-high", "low", "high"])
    assert entry.reasoning_efforts == ["low", "high", "xhigh", "max"]


def test_a_misspelled_rung_fails_at_config_load_rather_than_shrinking_the_ladder():
    with pytest.raises(ValidationError, match="unknown reasoning effort"):
        model(reasoning_efforts=["low", "megahigh"])


def test_an_empty_declared_ladder_is_an_error_not_a_silent_no_op():
    with pytest.raises(ValidationError):
        model(reasoning_efforts=["nonsense"])


def test_default_effort_must_belong_to_the_declared_ladder():
    with pytest.raises(ValidationError, match="not in reasoning_efforts"):
        model(reasoning_efforts=["low", "medium"], default_reasoning_effort="high")


def test_default_effort_without_a_ladder_is_allowed():
    # The entry pins a rung but does not claim to know the ceiling; the provider
    # still decides which of its rungs that is. Valid, and clamped downstream.
    entry = model(default_reasoning_effort="x-high")
    assert entry.default_reasoning_effort == "xhigh"


def test_an_unknown_default_effort_fails_at_config_load():
    with pytest.raises(ValidationError, match="default_reasoning_effort"):
        model(default_reasoning_effort="turbo")


def test_an_unknown_wire_style_names_the_valid_ones():
    with pytest.raises(ValidationError, match="unknown reasoning_effort_style"):
        model(reasoning_effort_style="telepathy")


def test_a_declared_ladder_implies_effort_support():
    entry = model(reasoning_efforts=["low", "high"])
    assert entry.supports_reasoning_effort is False, "the boolean stays untouched; the ladder is what grants support"
    assert supports_effort_control(entry) is True


def test_an_entry_with_neither_declares_nothing_and_says_so():
    entry = model()
    assert effective_effort_support(entry) == []
    assert supports_effort_control(entry) is False


def test_effective_support_is_the_normalized_ladder_not_the_raw_one():
    assert effective_effort_support(model(reasoning_efforts=["MAX", "low"])) == ["low", "max"]


def test_model_effort_support_returns_the_pair_the_translator_expects():
    ladder, default = model_effort_support(model(reasoning_efforts=["low", "high"], default_reasoning_effort="high"))
    assert (ladder, default) == (["low", "high"], "high")
    assert model_effort_support(model()) == ([], None)


# ---------------------------------------------------------------------------
# Style detection
# ---------------------------------------------------------------------------


def test_an_openai_compatible_client_is_detected_as_openai():
    from langchain_openai import ChatOpenAI

    assert detect_effort_style(ChatOpenAI) == EFFORT_STYLE_OPENAI


def test_an_anthropic_client_is_detected_and_uses_adaptive_effort():
    anthropic = pytest.importorskip("langchain_anthropic")
    assert detect_effort_style(anthropic.ChatAnthropic) == EFFORT_STYLE_ANTHROPIC


def test_a_google_client_is_detected():
    google = pytest.importorskip("langchain_google_genai")
    assert detect_effort_style(google.ChatGoogleGenerativeAI) == EFFORT_STYLE_GOOGLE


def test_alpha_adapters_inherit_the_style_of_the_family_they_serve():
    # CodexChatModel deliberately does NOT subclass ChatOpenAI (it implements the
    # Responses API itself), so it is covered by the module table rather than by
    # the issubclass fallback. Both routes must land on the OpenAI shape.
    from langchain_openai import ChatOpenAI

    from alpha.models.openai_codex_provider import CodexChatModel
    from alpha.models.patched_openai import PatchedChatOpenAI

    assert detect_effort_style(CodexChatModel) == EFFORT_STYLE_OPENAI
    assert detect_effort_style(PatchedChatOpenAI) == EFFORT_STYLE_OPENAI
    assert issubclass(PatchedChatOpenAI, ChatOpenAI)


def test_a_vllm_adapter_is_detected_as_vllm():
    from alpha.models.vllm_provider import VllmChatModel

    assert detect_effort_style(VllmChatModel) == EFFORT_STYLE_VLLM


def test_a_class_with_no_effort_knob_is_inert():
    from langchain.chat_models.base import BaseChatModel

    class CustomChat(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "custom"

    assert detect_effort_style(CustomChat) == EFFORT_STYLE_INERT


def test_a_non_class_is_inert_rather_than_raising():
    assert detect_effort_style(None) == EFFORT_STYLE_INERT
    assert detect_effort_style("langchain_openai:ChatOpenAI") == EFFORT_STYLE_INERT


def test_an_explicitly_declared_style_beats_detection():
    from langchain_openai import ChatOpenAI

    # A gateway whose effort knob does not match its SDK package cannot be
    # detected from the class, which is exactly why the override exists.
    assert detect_effort_style(ChatOpenAI, EFFORT_STYLE_OPENROUTER) == EFFORT_STYLE_OPENROUTER


def test_auto_means_detect_not_explicitly_openai():
    from langchain_openai import ChatOpenAI

    assert detect_effort_style(ChatOpenAI, "auto") == EFFORT_STYLE_OPENAI


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_request_the_model_declares_is_sent_verbatim():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "medium", "high", "xhigh", "max"])
    assert resolve_effort(entry, "xhigh", model_class=ChatOpenAI) == ("xhigh", EFFORT_STYLE_OPENAI)


def test_a_request_above_the_ceiling_is_clamped_and_never_rejected():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "medium", "high", "xhigh"])
    assert resolve_effort(entry, "max", model_class=ChatOpenAI)[0] == "xhigh"


def test_a_request_above_the_style_ceiling_is_clamped_even_with_no_declared_ladder():
    from langchain_openai import ChatOpenAI

    # `max` is Anthropic's rung. Sending it to an OpenAI-compatible endpoint that
    # has never heard of it is a 400, so the style table caps it.
    entry = model(supports_reasoning_effort=True)
    assert resolve_effort(entry, "max", model_class=ChatOpenAI) == ("xhigh", EFFORT_STYLE_OPENAI)


def test_the_declared_ladder_takes_precedence_over_the_style_ceiling():
    from langchain_openai import ChatOpenAI

    # An operator serving a DeepSeek-V4-style endpoint that really does have a
    # `max` rung can say so, and the style table does not second-guess them.
    entry = model(reasoning_efforts=["low", "medium", "high", "max"], supports_reasoning_effort=True)
    assert resolve_effort(entry, "max", model_class=ChatOpenAI)[0] == "max"


def test_an_unrecognized_request_is_ignored_and_falls_back_to_the_entry_default(caplog):
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "medium", "high"], default_reasoning_effort="medium")
    with caplog.at_level("WARNING"):
        level, _ = resolve_effort(entry, "turbo", model_class=ChatOpenAI)
    assert level == "medium"
    assert any("unrecognized reasoning effort" in record.getMessage() for record in caplog.records)


def test_an_unrecognized_request_on_an_entry_with_no_default_sends_nothing(caplog):
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "high"])
    with caplog.at_level("WARNING"):
        level, _ = resolve_effort(entry, "turbo", model_class=ChatOpenAI)
    assert level is None, "silently substituting a rung would be the dishonest version of this feature"


def test_no_request_uses_the_entry_default():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "medium", "high"], default_reasoning_effort="high")
    assert resolve_effort(entry, None, model_class=ChatOpenAI)[0] == "high"


def test_no_request_and_no_default_sends_nothing_so_the_provider_decides():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "medium", "high"])
    assert resolve_effort(entry, None, model_class=ChatOpenAI)[0] is None


def test_the_entry_default_does_not_apply_to_a_run_that_asked_for_thinking_off():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "medium", "high"], default_reasoning_effort="high")
    assert resolve_effort(entry, None, model_class=ChatOpenAI, thinking_enabled=False)[0] is None


def test_an_entry_without_effort_support_resolves_to_nothing_and_does_not_probe_the_class():
    from langchain_openai import ChatOpenAI

    assert resolve_effort(model(), "high", model_class=ChatOpenAI) == (None, EFFORT_STYLE_INERT)


def test_an_entry_with_support_but_an_inert_class_sends_nothing():
    from langchain.chat_models.base import BaseChatModel

    class CustomChat(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "custom"

    entry = model(reasoning_efforts=["low", "high"])
    assert resolve_effort(entry, "high", model_class=CustomChat) == (None, EFFORT_STYLE_INERT)


def test_off_is_never_clamped_up_to_the_ladder_floor():
    from langchain_openai import ChatOpenAI

    # Clamping chooses the rung at or below a request. Raising "do not reason
    # at all" to the floor would answer the opposite of what was asked.
    entry = model(reasoning_efforts=["low", "medium", "high"])
    assert resolve_effort(entry, "none", model_class=ChatOpenAI)[0] == "none"
    assert resolve_effort(entry, "off", model_class=ChatOpenAI)[0] == "none"


# ---------------------------------------------------------------------------
# Wire translation
# ---------------------------------------------------------------------------


def test_openai_writes_a_top_level_reasoning_effort():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "high"])
    settings: dict = {}
    apply_effort(ChatOpenAI, entry, settings, "high", style=EFFORT_STYLE_OPENAI)
    assert settings == {"reasoning_effort": "high"}


def test_openai_router_nests_the_effort_under_extra_body():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "high"], reasoning_effort_style=EFFORT_STYLE_OPENROUTER)
    settings: dict = {}
    apply_effort(ChatOpenAI, entry, settings, "high", style=EFFORT_STYLE_OPENROUTER)
    assert settings == {"extra_body": {"reasoning": {"effort": "high"}}}


def test_openrouter_merges_into_an_existing_extra_body_without_dropping_it():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "high"], reasoning_effort_style=EFFORT_STYLE_OPENROUTER)
    settings: dict = {"extra_body": {"thinking": {"type": "enabled"}, "reasoning": {"exclude": "x"}}}
    apply_effort(ChatOpenAI, entry, settings, "high", style=EFFORT_STYLE_OPENROUTER)
    assert settings["extra_body"]["thinking"] == {"type": "enabled"}, "the operator's own body keys must survive"
    assert settings["extra_body"]["reasoning"] == {"exclude": "x", "effort": "high"}


def test_an_openai_client_that_declares_the_field_gets_the_constructor_kwarg():
    from langchain_openai import ChatOpenAI

    class ForeignOpenAI(ChatOpenAI):
        # A third-party compatible client that hides the declared field.
        reasoning_effort: str | None = None
        model_config = {"extra": "allow"}

    entry = model(reasoning_efforts=["low", "high"])
    settings: dict = {}
    apply_effort(ForeignOpenAI, entry, settings, "high", style=EFFORT_STYLE_OPENAI)
    # The field is still declared, so the constructor kwarg is correct.
    assert settings == {"reasoning_effort": "high"}


def test_anthropic_uses_the_effort_field_the_client_lowers_into_output_config():
    anthropic = pytest.importorskip("langchain_anthropic")
    entry = model(use="langchain_anthropic:ChatAnthropic", reasoning_efforts=["low", "medium", "high", "xhigh", "max"], max_tokens=32000)
    settings: dict = {}
    apply_effort(anthropic.ChatAnthropic, entry, settings, "xhigh", style=EFFORT_STYLE_ANTHROPIC)
    assert settings == {"effort": "xhigh"}, "langchain-anthropic lowers `effort` into output_config.effort itself"


def test_anthropic_never_asks_for_more_rungs_than_it_has():
    anthropic = pytest.importorskip("langchain_anthropic")
    entry = model(use="langchain_anthropic:ChatAnthropic", supports_reasoning_effort=True, max_tokens=32000)
    for requested, expected in (("minimal", "low"), ("none", "none"), ("low", "low"), ("max", "max")):
        level, style = resolve_effort(entry, requested, model_class=anthropic.ChatAnthropic)
        assert level == expected, requested


def test_anthropic_warns_when_a_high_rung_is_paired_with_a_small_output_cap(caplog):
    anthropic = pytest.importorskip("langchain_anthropic")
    entry = model(use="langchain_anthropic:ChatAnthropic", supports_reasoning_effort=True, max_tokens=2048)
    with caplog.at_level("WARNING"):
        apply_effort(anthropic.ChatAnthropic, entry, {}, "high", style=EFFORT_STYLE_ANTHROPIC)
    assert any("max_tokens" in record.getMessage() for record in caplog.records), "silently truncating reasoning is worse than a warning"


def test_anthropic_does_not_warn_when_the_output_cap_can_hold_the_thinking(caplog):
    anthropic = pytest.importorskip("langchain_anthropic")
    entry = model(use="langchain_anthropic:ChatAnthropic", supports_reasoning_effort=True, max_tokens=32000)
    with caplog.at_level("WARNING"):
        apply_effort(anthropic.ChatAnthropic, entry, {}, "high", style=EFFORT_STYLE_ANTHROPIC)
    assert not [record for record in caplog.records if "max_tokens" in record.getMessage()]


def test_anthropic_without_an_effort_knob_falls_back_to_a_thinking_budget():
    anthropic = pytest.importorskip("langchain_anthropic")
    entry = model(use="langchain_anthropic:ChatAnthropic", supports_reasoning_effort=True, max_tokens=32000)
    settings: dict = {}
    apply_effort(anthropic.ChatAnthropic, entry, settings, "high", style="anthropic_budget")
    assert settings["thinking"] == {"type": "enabled", "budget_tokens": 16000}


def test_anthropic_budget_preserves_an_operator_supplied_thinking_block():
    anthropic = pytest.importorskip("langchain_anthropic")
    entry = model(use="langchain_anthropic:ChatAnthropic", supports_reasoning_effort=True, max_tokens=32000)
    settings: dict = {"thinking": {"display": "summarized"}}
    apply_effort(anthropic.ChatAnthropic, entry, settings, "medium", style="anthropic_budget")
    assert settings["thinking"] == {"display": "summarized", "type": "enabled", "budget_tokens": 8000}


def test_google_writes_thinking_level():
    google = pytest.importorskip("langchain_google_genai")
    entry = model(use="langchain_google_genai:ChatGoogleGenerativeAI", reasoning_efforts=["minimal", "low", "medium", "high"])
    settings: dict = {}
    apply_effort(google.ChatGoogleGenerativeAI, entry, settings, "medium", style=EFFORT_STYLE_GOOGLE)
    assert settings == {"thinking_level": "medium"}


def test_google_expresses_off_as_a_zero_budget_not_as_a_level():
    google = pytest.importorskip("langchain_google_genai")
    entry = model(use="langchain_google_genai:ChatGoogleGenerativeAI", reasoning_efforts=["none", "low", "high"])
    settings: dict = {}
    apply_effort(google.ChatGoogleGenerativeAI, entry, settings, "none", style=EFFORT_STYLE_GOOGLE)
    assert settings == {"thinking_budget": 0}, "Gemini has no 'off' thinking level; a zero budget is the only representation"


def test_google_caps_max_at_high_because_its_level_literal_stops_there():
    google = pytest.importorskip("langchain_google_genai")
    entry = model(use="langchain_google_genai:ChatGoogleGenerativeAI", supports_reasoning_effort=True)
    assert resolve_effort(entry, "max", model_class=google.ChatGoogleGenerativeAI)[0] == "high"
    assert resolve_effort(entry, "xhigh", model_class=google.ChatGoogleGenerativeAI)[0] == "high"


def test_bedrock_writes_reasoning_config():
    entry = model(reasoning_efforts=["low", "medium", "high"])
    settings: dict = {}
    apply_effort(None, entry, settings, "high", style=EFFORT_STYLE_BEDROCK)
    assert settings == {"reasoningConfig": {"type": "enabled", "maxReasoningEffort": "high"}}


def test_vllm_enables_the_chat_template_toggle_and_carries_the_value():
    from alpha.models.vllm_provider import VllmChatModel

    entry = model(use="alpha.models.vllm_provider:VllmChatModel", reasoning_efforts=["low", "medium", "high"])
    settings: dict = {}
    apply_effort(VllmChatModel, entry, settings, "high", style=EFFORT_STYLE_VLLM)
    assert settings["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert settings["reasoning_effort"] == "high"


def test_an_inert_style_writes_nothing_so_the_operator_block_owns_the_wire():
    entry = model(reasoning_efforts=["low", "high"])
    settings: dict = {"when": "untouched"}
    apply_effort(None, entry, settings, "high", style=EFFORT_STYLE_INERT)
    assert settings == {"when": "untouched"}


def test_applying_nothing_leaves_the_settings_untouched():
    from langchain_openai import ChatOpenAI

    entry = model(reasoning_efforts=["low", "high"])
    settings: dict = {"max_tokens": 1024}
    apply_effort(ChatOpenAI, entry, settings, None, style=EFFORT_STYLE_OPENAI)
    assert settings == {"max_tokens": 1024}
