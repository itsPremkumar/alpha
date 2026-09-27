"""The provider-neutral reasoning-effort ladder Alpha speaks.

Effort is the field the whole field converged on. Claude Code has ``/effort``,
Codex has ``model_reasoning_effort``, OpenCode has ``/variants``, Inspect has
``--reasoning-effort`` — and every one of them accepts a *named rung* rather
than a raw token budget. But no two of them spell the rungs the same way, and
each provider's API wants the value in its own place:

    OpenAI / OpenRouter / vLLM   reasoning_effort            (or reasoning.effort)
    Anthropic                    output_config.effort
    Google                       thinkingConfig.thinking_level
    Bedrock                      reasoningConfig.max_reasoning_effort

So Alpha keeps **one canonical ladder**, ordered weakest → strongest, and a
separate translation layer per provider. This module owns the ladder, the
aliases, the ordering, and the clamp. It is deliberately dependency-free and
lives under ``config/`` so :mod:`alpha.config.model_config` can validate a
declared ladder without importing ``alpha.models`` (which would cycle through
the factory).

The rung set is the union of what the major providers document:

=============  ===============================================================
``none``       Do not reason. Anthropic ``thinking: disabled`` / OpenAI
               ``reasoning_effort: "none"`` / Gemini ``thinkingBudget: 0``.
``minimal``    Barely any reasoning. GPT-5 tier, Gemini 3 ``minimal``.
``low``        Fast path — well-scoped tasks, short answers.
``medium``     The everyday default for most agentic coding work.
``high``       Complex debugging, multi-file refactors, architecture.
``xhigh``      Long-running agentic/coding work; more than ``high``, less
               than ``max`` in cost (Anthropic Opus 4.7+, GPT-5.1 Codex max+).
``max``        The most reasoning a model will spend in one pass.
=============  ===============================================================

``default``/``auto`` is deliberately **not** a rung. They mean "send nothing and
let the provider's own default apply", which is :data:`None` here. Conflating
them with a rung is how a user ends up pinned to ``low`` forever because the
UI showed a checkmark next to "Default".
"""

from __future__ import annotations

#: Canonical ladder, weakest → strongest. Order is the contract: it is what
#: ``clamp_effort`` ranks by and what the UI renders top-to-bottom.
CANONICAL_EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: Display label per rung. The server ships these so the client never invents
#: or re-orders the vocabulary; the UI may add a hint but must not rename a rung.
EFFORT_LABELS: dict[str, str] = {
    "none": "Off",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra High",
    "max": "Max",
}

#: Accepted spellings that mean "no explicit request", not a rung.
UNSET_ALIASES: frozenset[str] = frozenset({"", "default", "auto", "provider", "inherit", "none_set"})

#: Accepted synonyms for an existing rung. Providers and agents disagree on
#: spelling, and a user typing ``x-high`` or ``ultra`` in a config file or a
#: channel's ``run_context`` means a rung that already exists, so normalize
#: rather than reject.
EFFORT_ALIASES: dict[str, str] = {
    "off": "none",
    "disabled": "none",
    "no": "none",
    "min": "minimal",
    "x-high": "xhigh",
    "x_high": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "very-high": "xhigh",
    "very_high": "xhigh",
    "extreme": "max",
    "maximum": "max",
    "ultra": "max",
    "ultrathink": "max",
    "adaptive": "high",
}

_ORDER: dict[str, int] = {level: index for index, level in enumerate(CANONICAL_EFFORTS)}

# --------------------------------------------------------------------------
# Wire styles
# --------------------------------------------------------------------------
#
# A rung is one idea; the place it travels is a per-provider detail. The style
# *names* are configuration vocabulary (an operator writes them into
# ``models[].reasoning_effort_style``) so they live here, beside the ladder and
# free of any provider import. The behavior behind each name lives in
# :mod:`alpha.models.effort_translation`.

#: Detect the shape from the model class. The default.
EFFORT_STYLE_AUTO = "auto"
#: OpenAI Chat Completions / Responses: ``reasoning_effort``.
EFFORT_STYLE_OPENAI = "openai"
#: OpenRouter: ``extra_body.reasoning.effort``.
EFFORT_STYLE_OPENROUTER = "openrouter"
#: ``langchain-anthropic`` with adaptive ``effort``.
EFFORT_STYLE_ANTHROPIC = "anthropic"
#: ``langchain-anthropic`` without it: a thinking *budget* is the only knob.
EFFORT_STYLE_ANTHROPIC_BUDGET = "anthropic_budget"
#: ``langchain-google-genai``: ``thinking_level``.
EFFORT_STYLE_GOOGLE = "google"
#: Bedrock Converse: ``reasoningConfig``.
EFFORT_STYLE_BEDROCK = "bedrock"
#: vLLM chat-template reasoning toggles.
EFFORT_STYLE_VLLM = "vllm"
#: The model class exposes no effort knob; ``when_thinking_enabled`` owns the wire.
EFFORT_STYLE_INERT = "inert"

#: Every value ``models[].reasoning_effort_style`` may take.
EFFORT_STYLES: tuple[str, ...] = (
    EFFORT_STYLE_AUTO,
    EFFORT_STYLE_OPENAI,
    EFFORT_STYLE_OPENROUTER,
    EFFORT_STYLE_ANTHROPIC,
    EFFORT_STYLE_ANTHROPIC_BUDGET,
    EFFORT_STYLE_GOOGLE,
    EFFORT_STYLE_BEDROCK,
    EFFORT_STYLE_VLLM,
    EFFORT_STYLE_INERT,
)


def effort_rank(level: str | None) -> int:
    """Rank of *level* on the canonical ladder, or ``-1`` when unknown.

    ``-1`` deliberately sorts *below* ``none`` so an unrecognized value can
    never be picked as a clamp target.
    """
    return _ORDER.get(level or "", -1)


def is_effort(value: object) -> bool:
    """Whether *value* names a canonical rung (aliases accepted)."""
    return normalize_effort(value) is not None


def normalize_effort(value: object) -> str | None:
    """Resolve any accepted spelling to a canonical rung, or ``None``.

    ``None`` is returned for "no explicit request" (``None``, ``""``,
    ``"default"``) **and** for an unrecognized value. Callers that must not
    silently swallow a typo should compare
    ``is_effort(value) and not normalize_effort(value)`` first; see
    :func:`alpha.config.model_config.ModelConfig` validators, which reject a
    misspelled rung at config-load time rather than dropping it.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower().replace("_", "-")
    if text in UNSET_ALIASES:
        return None
    text = EFFORT_ALIASES.get(text, text)
    return text if text in _ORDER else None


def canonical_order(values: object) -> list[str]:
    """Dedupe *values* onto the canonical ladder, weakest → strongest.

    Accepts any iterable of spellings. Unrecognized entries are dropped, so
    this is safe to run on a config value, an API payload, or a discovery
    descriptor. Returns ``[]`` for an empty/absent input, which is how "the
    model never declared a ladder" is represented downstream.
    """
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    seen = {normalize_effort(value) for value in values}
    return sorted((level for level in seen if level), key=_ORDER.__getitem__)


def clamp_effort(requested: object, supported: object) -> str | None:
    """Resolve *requested* onto *supported* the way every agent harness does.

    The rule, adopted from OpenCode's variant mapping and Inspect AI's
    per-provider table: **the strongest supported rung at or below the request,
    and if the request is below every rung, the weakest supported rung.** Never
    return something the provider will reject with a 400, and never silently
    do *more* reasoning than the user asked for.

    ``supported`` empty (nothing declared) means "no ladder to resolve
    against", which is different from "a ladder that excludes this rung", so
    the result is ``None`` and the caller's own default applies.
    """
    level = normalize_effort(requested)
    if level is None:
        return None
    ladder = canonical_order(supported)
    if not ladder:
        return None
    target = _ORDER[level]
    for candidate in reversed(ladder):
        if _ORDER[candidate] <= target:
            return candidate
    return ladder[0]


__all__ = [
    "CANONICAL_EFFORTS",
    "EFFORT_ALIASES",
    "EFFORT_LABELS",
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
    "UNSET_ALIASES",
    "canonical_order",
    "clamp_effort",
    "effort_rank",
    "is_effort",
    "normalize_effort",
]
