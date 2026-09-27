"""Translate a canonical reasoning-effort rung into each provider's wire shape.

The ladder itself lives in :mod:`alpha.config.reasoning_effort` (dependency-free
so config validation can use it). This module owns the half that has to know
about LangChain client classes, and it is deliberately the *only* place that
does.

Why translation is needed at all: the same rung is spelled four different ways
on the wire.

    OpenAI-compatible (ChatOpenAI, Codex, vLLM, MindIE, DeepSeek, MiMo, …)
        -> ``reasoning_effort`` (a top-level constructor field)
    OpenRouter-style gateways
        -> ``reasoning_effort`` nested in ``extra_body["reasoning"]["effort"]``
    Anthropic (langchain-anthropic >= 1.x)
        -> ``effort`` (which the client lowers into ``output_config.effort``)
    Anthropic before adaptive effort existed
        -> ``thinking.budget_tokens`` — there is no effort knob, only a budget
    Google (langchain-google-genai)
        -> ``thinking_level``, with ``thinking_budget=0`` for "off"
    Bedrock
        -> ``reasoningConfig.max_reasoning_effort`` (Nova) or ``output_config``

Each style therefore declares the rungs it can actually express
(:data:`_STYLE_LADDER`), and that table is the fallback used when a model entry
declares no ladder of its own. A declared ``models[].reasoning_efforts`` is
**authoritative** and is never second-guessed by the style table, because
declaring it is precisely the assertion that this endpoint serves those rungs.

Two rules must not be broken here:

1. **Never send a rung the resolved ladder cannot express, and never exceed what
   the operator declared.** A request above the ceiling is a ceiling hit, logged;
   a request below the floor is raised to the floor, logged. Silently
   substituting would be the dishonest version of this feature.
2. **Never disable thinking at a rung that forbids it.** Claude rejects
   ``thinking: disabled`` at ``xhigh``/``max`` with a 400, so the ``none`` rung
   is resolved by the caller *before* the disable path runs, not by writing a
   disable alongside a high effort here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from alpha.config.reasoning_effort import (
    CANONICAL_EFFORTS,
    EFFORT_STYLE_ANTHROPIC,
    EFFORT_STYLE_ANTHROPIC_BUDGET,
    EFFORT_STYLE_AUTO,
    EFFORT_STYLE_BEDROCK,
    EFFORT_STYLE_GOOGLE,
    EFFORT_STYLE_INERT,
    EFFORT_STYLE_OPENAI,
    EFFORT_STYLE_OPENROUTER,
    EFFORT_STYLE_VLLM,
    EFFORT_STYLES,
    canonical_order,
    clamp_effort,
    normalize_effort,
)

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.config.model_config import ModelConfig

logger = logging.getLogger(__name__)


#: Rungs each style can express. An empty tuple means the style sends nothing.
#:
#: ``openai`` deliberately excludes ``max``: that rung is Anthropic's, and
#: sending it to a provider that has never heard of it is a 400. Operators who
#: serve a DeepSeek-V4-style endpoint that *does* have ``max`` declare it in
#: ``models[].reasoning_efforts``, which takes precedence over this table.
_STYLE_LADDER: dict[str, tuple[str, ...]] = {
    EFFORT_STYLE_OPENAI: ("none", "minimal", "low", "medium", "high", "xhigh"),
    EFFORT_STYLE_OPENROUTER: ("none", "minimal", "low", "medium", "high", "xhigh"),
    # Anthropic has no "off" rung: thinking is either on or the request 400s at
    # xhigh/max, and the minimum documented effort is `low`.
    EFFORT_STYLE_ANTHROPIC: ("low", "medium", "high", "xhigh", "max"),
    EFFORT_STYLE_ANTHROPIC_BUDGET: ("low", "medium", "high", "xhigh", "max"),
    # Gemini's `thinking_level` Literal is minimal/low/medium/high; "off" is a
    # zero budget, not a level.
    EFFORT_STYLE_GOOGLE: ("none", "minimal", "low", "medium", "high"),
    EFFORT_STYLE_BEDROCK: ("low", "medium", "high"),
    EFFORT_STYLE_VLLM: ("low", "medium", "high"),
    EFFORT_STYLE_INERT: (),
}

#: Thinking budgets for a client that predates Anthropic's adaptive effort.
#: Mirrors the ladder every harness used before ``effort`` existed: 2k for a
#: quick look, 8k for normal work, 16k for hard work, and just under the
#: 32k thinking cap for the top two.
_ANTIHROPIC_BUDGET_TOKENS: dict[str, int] = {
    "low": 2_000,
    "medium": 8_000,
    "high": 16_000,
    "xhigh": 24_000,
    "max": 31_999,
}

#: Output cap below which a high Anthropic rung is known to truncate its own
#: reasoning. Anthropic documents a large ``max_tokens`` at high effort and
#: above because the cap is on thinking *plus* reply.
_ANTHROPIC_RECOMMENDED_MAX_TOKENS: int = 16_000

#: Module roots used for class detection. Duck-typing on field names alone
#: would false-positive: several unrelated clients expose a ``thinking`` dict.
_MODULE_STYLES: tuple[tuple[str, str], ...] = (
    ("langchain_anthropic", EFFORT_STYLE_ANTHROPIC),
    ("langchain_google_genai", EFFORT_STYLE_GOOGLE),
    ("langchain_google_vertexai", EFFORT_STYLE_GOOGLE),
    ("langchain_aws", EFFORT_STYLE_BEDROCK),
    ("alpha.models.vllm_provider", EFFORT_STYLE_VLLM),
    ("alpha.models.mindie_provider", EFFORT_STYLE_OPENAI),
    ("alpha.models.patched_deepseek", EFFORT_STYLE_OPENAI),
    ("alpha.models.patched_mimo", EFFORT_STYLE_OPENAI),
    ("alpha.models.patched_minimax", EFFORT_STYLE_OPENAI),
    ("alpha.models.patched_stepfun", EFFORT_STYLE_OPENAI),
    ("alpha.models.patched_openai", EFFORT_STYLE_OPENAI),
    ("alpha.models.openai_codex_provider", EFFORT_STYLE_OPENAI),
    ("alpha.models.free_router", EFFORT_STYLE_OPENAI),
)


def _is_subclass_of(model_class: object, base: type) -> bool:
    return isinstance(model_class, type) and issubclass(model_class, base)


def detect_effort_style(model_class: type | None, declared: str | None = None) -> str:
    """Resolve the wire style for *model_class*.

    ``declared`` (``models[].reasoning_effort_style``) wins when it is anything
    other than ``auto``/absent, because a gateway whose effort knob does not
    match its SDK package cannot be detected from the class alone. Otherwise the
    style comes from the module the class was defined in, and finally from
    ``issubclass(..., BaseChatOpenAI)`` so any OpenAI-compatible adapter
    inherits the OpenAI shape automatically.
    """
    if declared and declared != EFFORT_STYLE_AUTO:
        return declared
    if not isinstance(model_class, type):
        return EFFORT_STYLE_INERT

    module = getattr(model_class, "__module__", "") or ""
    for root, style in _MODULE_STYLES:
        if module == root or module.startswith(f"{root}."):
            if style == EFFORT_STYLE_ANTHROPIC and "effort" not in getattr(model_class, "model_fields", {}):
                # langchain-anthropic before adaptive effort: budgets only.
                return EFFORT_STYLE_ANTHROPIC_BUDGET
            return style

    from langchain_openai.chat_models.base import BaseChatOpenAI

    if _is_subclass_of(model_class, BaseChatOpenAI):
        return EFFORT_STYLE_OPENAI
    return EFFORT_STYLE_INERT


def model_effort_support(model_config: ModelConfig) -> tuple[list[str], str | None]:
    """Return ``(declared_ladder, declared_default)`` for a model entry.

    A declared ladder is the operator asserting the contract, so it also
    implies ``supports_reasoning_effort`` — a model that lists the rungs it
    serves must not need a second, redundant boolean turned on. The boolean
    still governs entries that declare no ladder (the pre-existing shape).
    """
    ladder = canonical_order(getattr(model_config, "reasoning_efforts", None))
    default = normalize_effort(getattr(model_config, "default_reasoning_effort", None))
    return ladder, default


def effective_effort_support(model_config: ModelConfig) -> list[str]:
    """The ladder ``GET /api/models`` advertises for a model entry.

    A model that declares nothing advertises nothing. That is the honest
    answer: the UI must then hide the picker rather than offer rungs the
    factory would silently clamp.
    """
    ladder, _ = model_effort_support(model_config)
    return ladder


def supports_effort_control(model_config: ModelConfig) -> bool:
    """Whether this entry accepts a reasoning-effort request at all."""
    ladder, _ = model_effort_support(model_config)
    return bool(getattr(model_config, "supports_reasoning_effort", False) or ladder)


def resolve_effort(
    model_config: ModelConfig,
    requested: object,
    *,
    model_class: type | None = None,
    thinking_enabled: bool = True,
) -> tuple[str | None, str | None]:
    """Resolve *requested* to the rung that will actually be sent.

    Returns ``(level, style)``. ``level`` is ``None`` when nothing should be
    sent, which happens when the entry has no effort control, when the client
    class exposes no effort knob, or when no request and no default exist. The
    caller is responsible for honouring ``level == "none"`` by taking the
    thinking-disable path *before* writing any effort — that ordering is what
    keeps Claude from receiving ``thinking: disabled`` next to ``xhigh``.
    """
    declared, default = model_effort_support(model_config)
    if not (getattr(model_config, "supports_reasoning_effort", False) or declared):
        return None, EFFORT_STYLE_INERT
    style = detect_effort_style(model_class, getattr(model_config, "reasoning_effort_style", None))
    if style == EFFORT_STYLE_INERT:
        return None, style
    if not _STYLE_LADDER.get(style):
        return None, style

    level = normalize_effort(requested)
    if level is None and requested is not None and str(requested).strip():
        # `normalize_effort` cannot tell "no request" from "a request that
        # names no rung", and answering those two identically is how a client
        # ends up at the default while the picker shows something else. Say so
        # out loud; the fallback is still the declared default.
        logger.warning(
            "Model '%s': ignoring unrecognized reasoning effort %r; expected one of %s.",
            getattr(model_config, "name", "?"),
            requested,
            ", ".join(CANONICAL_EFFORTS),
        )
    if level is None and thinking_enabled:
        # No explicit request: the entry's own default is the only thing left
        # that can change the answer, and it must not apply to a run that asked
        # for thinking off.
        level = default
    if level is None:
        return None, style

    if level == "none":
        # "Off" is deliberately never clamped. Clamping is for choosing the
        # rung at or below a request, and raising "do not reason at all" to the
        # ladder's floor would answer the opposite of what was asked. It is
        # returned as-is so the caller can route it to the thinking-disable
        # path, which is what actually stops reasoning.
        if declared and "none" not in declared:
            logger.info(
                "Model '%s': reasoning effort 'none' requested but the declared ladder %s has no off rung; the model's own thinking-disable path will govern instead.",
                getattr(model_config, "name", "?"),
                declared,
            )
        return "none", style

    # A declared ladder is authoritative and is NOT second-guessed by the style
    # table. That is the whole point of declaring it: an operator serving a
    # DeepSeek-V4-style endpoint that really does expose ``max`` says so, and a
    # style table that capped them at ``xhigh`` would be the silent substitution
    # this feature exists to avoid. The style ladder applies only when nothing
    # is declared, where it is the best available guess at what the wire can
    # carry.
    resolved = clamp_effort(level, declared) if declared else clamp_effort(level, _STYLE_LADDER[style])
    if resolved is None:
        return None, style
    if resolved != normalize_effort(requested) and normalize_effort(requested) is not None:
        logger.info(
            "Model '%s': reasoning effort %s resolved to %s for the %s wire format (declared: %s).",
            getattr(model_config, "name", "?"),
            normalize_effort(requested),
            resolved,
            style,
            declared or "undeclared",
        )
    return resolved, style


def _merge_extra_body(settings: dict[str, Any], payload: dict[str, Any]) -> None:
    """Deep-merge *payload* into ``settings["extra_body"]``.

    Effort is written under an existing ``extra_body`` for gateway-shaped
    models, and the operator may already have set ``extra_body.thinking`` or
    ``extra_body.chat_template_kwargs``. A shallow assignment would drop those,
    so this merges the two levels that actually occur in practice.
    """
    existing = settings.get("extra_body")
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in payload.items():
        current = merged.get(key)
        merged[key] = {**current, **value} if isinstance(current, dict) and isinstance(value, dict) else value
    settings["extra_body"] = merged


def _warn_missing_output_budget(model_class: type | None, level: str, settings: dict[str, Any], model_config: ModelConfig) -> None:
    """Warn when a high Anthropic rung is paired with a small ``max_tokens``.

    ``max_tokens`` is a hard cap on *total* output — thinking plus reply — so
    ``high``/``xhigh``/``max`` with a small cap silently truncates the
    reasoning rather than failing. Raising the cap is the operator's call, so
    this only says so once per build.

    The cap is read from the effective settings first (a caller or provider
    profile may have overridden the entry) and from the entry itself second, so
    the warning fires wherever the call is made.
    """
    cap = settings.get("max_tokens")
    if cap is None:
        cap = getattr(model_config, "max_tokens", None)
    if not isinstance(cap, (int, float)) or cap >= _ANTHROPIC_RECOMMENDED_MAX_TOKENS:
        return
    logger.warning(
        "Model '%s' (%s): reasoning effort '%s' with max_tokens=%s caps total output (thinking included) and will truncate reasoning; Anthropic recommends max_tokens of at least %s at high effort and above.",
        getattr(model_config, "name", "?"),
        getattr(model_class, "__name__", "?"),
        level,
        cap,
        _ANTHROPIC_RECOMMENDED_MAX_TOKENS,
    )


def apply_effort(
    model_class: type | None,
    model_config: ModelConfig,
    settings: dict[str, Any],
    level: str | None,
    *,
    style: str,
) -> dict[str, Any]:
    """Write the resolved rung into *settings* in *style*'s wire shape.

    *settings* is the effective constructor kwargs the factory is about to
    splat into the client, so the keys written here must be real fields of that
    client (or land inside ``extra_body``, which every LangChain provider client
    forwards verbatim). Returns *settings* for call-site convenience.
    """
    if level is None:
        return settings
    fields = getattr(model_class, "model_fields", {}) or {}

    if style == EFFORT_STYLE_OPENAI:
        if "reasoning_effort" in fields or model_class is None:
            settings["reasoning_effort"] = level
        else:
            # A third-party OpenAI-compatible client that does not declare the
            # field. ``extra_body`` is the only honest way to add a body key
            # without it being diverted and rejected.
            _merge_extra_body(settings, {"reasoning_effort": level})
    elif style == EFFORT_STYLE_OPENROUTER:
        _merge_extra_body(settings, {"reasoning": {"effort": level}})
    elif style == EFFORT_STYLE_ANTHROPIC:
        if "effort" in fields:
            # langchain-anthropic lowers this into output_config.effort itself.
            settings["effort"] = level
        elif "output_config" in fields:
            # Older client: the nested shape is the only one it forwards.
            _merge_existing(settings, "output_config", {"effort": level})
        else:
            # A class explicitly declared `anthropic` but carrying neither knob
            # is still Anthropic-shaped on the wire, so the budget ladder is
            # the only thing that can express the request.
            _apply_anthropic_budget(settings, level)
            return settings
        _warn_missing_output_budget(model_class, level, settings, model_config)
    elif style == EFFORT_STYLE_ANTHROPIC_BUDGET:
        _apply_anthropic_budget(settings, level)
    elif style == EFFORT_STYLE_GOOGLE:
        if level == "none":
            # Gemini expresses "off" as a zero budget, not as a level.
            if "thinking_budget" in fields:
                settings["thinking_budget"] = 0
            else:
                _merge_extra_body(settings, {"thinking_config": {"thinking_budget": 0}})
        elif "thinking_level" in fields:
            settings["thinking_level"] = level
        else:
            _merge_extra_body(settings, {"thinking_config": {"thinking_level": level}})
    elif style == EFFORT_STYLE_BEDROCK:
        _merge_existing(settings, "reasoningConfig", {"type": "enabled", "maxReasoningEffort": level})
    elif style == EFFORT_STYLE_VLLM:
        # vLLM exposes the toggle through chat-template kwargs; the value still
        # travels as reasoning_effort for parsers that understand it.
        _merge_extra_body(settings, {"chat_template_kwargs": {"enable_thinking": True}})
        if "reasoning_effort" in fields:
            settings["reasoning_effort"] = level
    # EFFORT_STYLE_INERT: the operator's when_thinking_enabled owns the wire.
    return settings


def _merge_existing(settings: dict[str, Any], key: str, payload: dict[str, Any]) -> None:
    current = settings.get(key)
    settings[key] = {**current, **payload} if isinstance(current, dict) else dict(payload)


def _apply_anthropic_budget(settings: dict[str, Any], level: str) -> None:
    """Express a rung as an extended-thinking budget.

    Used for Anthropic deployments with no adaptive ``effort`` knob. The
    operator's own ``thinking`` block is merged rather than replaced, so a
    configured ``display``/``type`` choice survives.
    """
    budget = _ANTIHROPIC_BUDGET_TOKENS.get(level)
    if budget is not None:
        _merge_existing(settings, "thinking", {"type": "enabled", "budget_tokens": budget})


__all__ = [
    "CANONICAL_EFFORTS",
    "EFFORT_STYLES",
    "EFFORT_STYLE_ANTHROPIC",
    "EFFORT_STYLE_ANTHROPIC_BUDGET",
    "EFFORT_STYLE_AUTO",
    "EFFORT_STYLE_BEDROCK",
    "EFFORT_STYLE_GOOGLE",
    "EFFORT_STYLE_INERT",
    "EFFORT_STYLE_OPENAI",
    "EFFORT_STYLE_OPENROUTER",
    "EFFORT_STYLE_VLLM",
    "apply_effort",
    "detect_effort_style",
    "effective_effort_support",
    "model_effort_support",
    "resolve_effort",
    "supports_effort_control",
]
