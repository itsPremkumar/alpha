"""Real model workers for the Mixture-of-Agents round.

The orchestrator itself is transport-agnostic: it takes a ``worker_fn`` and knows
nothing about models. The tool previously supplied a **stub** worker built from an
f-string that returned the same sentence for every candidate without calling any
model, so the tool produced a "Synthesized MoA Consensus (3 models)" header over
three copies of one invented string. This module is the real implementation of
that seam: it resolves each requested name through the ordinary model factory, so
an unconfigured name fails loudly instead of appearing to have been consulted.

Every failure mode here is surfaced as a ``MoACandidate`` error rather than an
exception, because ``execute_moa_round`` already converts a raising worker into a
failed candidate and the honest consensus is the one that names what failed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alpha.models.moa.orchestrator import MoACandidate

#: Instruction shared by every candidate so the perspectives stay comparable.
#: Each model is asked for an independent opinion rather than a vote on a
#: pre-decided answer, which is what makes the aggregate worth more than one call.
_CANDIDATE_INSTRUCTION = (
    "Give your independent assessment of the question below. "
    "State your reasoning and your conclusion, including any case where you "
    "disagree with the most obvious answer. You are one of several models being "
    "consulted in parallel; do not try to agree with the others."
)

_MAX_PROMPT_CHARS = 8_000


def build_model_worker(
    *,
    app_config: Any | None = None,
    thinking_enabled: bool = False,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> Callable[[str, str], str]:
    """Return a real ``worker_fn(model_name, prompt)`` for ``execute_moa_round``.

    The factory is imported lazily inside the closure so this module stays
    importable in a bare test environment (``alpha.models.factory`` pulls in the
    whole provider stack), and so a deployment that never configures a model does
    not pay for the import.

    Args:
        app_config: Optional operator config; ``None`` resolves the ambient config.
        thinking_enabled: Whether candidates should reason with thinking enabled.
        on_error: Optional observer invoked as ``on_error(model_name, exc)`` so a
            caller can log the real cause before the worker re-raises it.
    """

    def worker(model_name: str, prompt: str) -> str:
        from alpha.models.factory import create_chat_model

        bounded = prompt[:_MAX_PROMPT_CHARS]
        messages = [
            {"role": "system", "content": _CANDIDATE_INSTRUCTION},
            {"role": "user", "content": bounded},
        ]
        try:
            # Standalone one-shot calls keep the provider's own retries: nothing
            # above this tool orchestrates a retry, and the orchestrator's
            # per-candidate error handling is the only other layer.
            model = create_chat_model(
                name=model_name,
                thinking_enabled=thinking_enabled,
                app_config=app_config,
                retries_orchestrated=False,
            )
            response = model.invoke(messages)
        except BaseException as exc:  # noqa: BLE001 - re-raised; the observer must see every failure
            if on_error is not None:
                on_error(model_name, exc)
            raise
        text = getattr(response, "content", response)
        if not isinstance(text, str):
            text = str(text)
        if not text.strip():
            # An empty completion is not an answer. Returning "" would reach the
            # consensus as a blank perspective attributed to a model.
            raise RuntimeError(f"model {model_name!r} returned an empty response")
        return text

    return worker


def failed_candidate(model_name: str, reason: str) -> MoACandidate:
    """Build the candidate record used when a model cannot be consulted at all.

    A name that does not resolve in ``config.yaml`` must not be reported as a
    perspective. This keeps the failure in the result set, where the default
    aggregator can state it, instead of dropping the model silently.
    """
    return MoACandidate(model_name=model_name, response="", error=reason, success=False)
