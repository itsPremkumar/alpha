"""Built-in Mixture-of-Agents tool for multi-perspective consensus reasoning."""

# NOTE: no ``from __future__ import annotations`` — under PEP 563 the ``runtime: Runtime``
# annotation would become a string and LangChain's injected-argument detection would
# never register it (see ``alpha.tools.types.Runtime``).

import json
import logging

from langchain.tools import tool

from alpha.models.moa.orchestrator import get_moa_orchestrator
from alpha.models.moa.workers import build_model_worker
from alpha.tools.types import Runtime

logger = logging.getLogger(__name__)

#: Legacy default used when neither the caller nor the bot profile names a panel.
_DEFAULT_CANDIDATES = "claude-3-7-sonnet,deepseek-r1,gpt-4o"


def _bot_panel_models(runtime: Runtime | None) -> list[str]:
    """The current bot profile's declared mixture panel, or ``[]``.

    The panel only applies when the run is bound to a roster bot
    (``runtime.context["bot_name"]``) and that bot's ``model_config.mixture``
    is enabled with references. Everything else — no runtime, no bot binding,
    an invalid/missing profile — degrades to ``[]`` so the caller falls back
    to the explicit ``models_csv`` or the legacy default; a tool call must
    never crash on roster state.
    """
    if runtime is None or not isinstance(getattr(runtime, "context", None), dict):
        return []
    bot_name = runtime.context.get("bot_name")
    if not isinstance(bot_name, str) or not bot_name.strip():
        return []
    try:
        from alpha.bots.model_config import validate_bot_model_config
        from alpha.bots.registry import get_bot_registry

        bot = get_bot_registry().get_bot(bot_name.strip())
        if bot is None or not getattr(bot, "model_config", None):
            return []
        cfg = validate_bot_model_config(bot.model_config, known_models=_known_models(), field_prefix=f"bots[{bot.name}].model_config")
    except Exception:  # noqa: BLE001 — a roster/panel problem must not fail the call
        logger.warning("Bot %r mixture panel could not be resolved; using the explicit model list.", bot_name, exc_info=True)
        return []
    if cfg.mixture is None or not cfg.mixture.enabled or not cfg.mixture.references:
        return []
    return list(cfg.mixture.references)


def _known_models() -> set[str]:
    """Declared ``models[]`` names. Empty set = cannot check (validation skips)."""
    try:
        from alpha.config import get_app_config

        return {m.name for m in get_app_config().models}
    except Exception:  # noqa: BLE001 — validation degrades to shape-only
        return set()


@tool("moa_multi_model_reasoning", parse_docstring=True)
def moa_multi_model_reasoning(
    runtime: Runtime,
    prompt: str,
    models_csv: str = "",
) -> str:
    """Consult several models in parallel and synthesize their independent perspectives.

    Dispatches the question to every named model simultaneously, redacts PII and
    secrets from both the prompt and each reply, then aggregates the answers. Each
    model answers on its own, so the aggregate can surface disagreement rather than
    just one voice repeated.

    Model selection order: the explicit `models_csv`, then the current bot
    profile's `model_config.mixture` panel when this run is bound to a bot,
    then a built-in default list. Every selected model must exist in
    `config.yaml` -> `models[]`. A name that does not resolve is reported as a
    failed perspective with the reason; it is never replaced by invented text.
    The response states how many models actually answered, so check that number
    before treating the result as a multi-model consensus — a round where every
    model failed says so instead of returning a synthesis.

    Args:
        prompt: Complex architectural question, code review query, or design trade-off to evaluate.
        models_csv: Comma-separated list of candidate models to consult (default: '' → bot panel → built-in list).
    """
    candidate_list = [m.strip() for m in models_csv.split(",") if m.strip()]
    if not candidate_list:
        candidate_list = _bot_panel_models(runtime) or [m.strip() for m in _DEFAULT_CANDIDATES.split(",") if m.strip()]

    def _log_failure(model_name: str, exc: BaseException) -> None:
        logger.warning("MoA candidate %s failed: %s", model_name, exc)

    worker_fn = build_model_worker(on_error=_log_failure)
    orchestrator = get_moa_orchestrator()

    result = orchestrator.execute_moa_round(
        prompt=prompt,
        candidate_models=candidate_list,
        worker_fn=worker_fn,
    )

    succeeded = [c for c in result.candidates if c.success]
    failed = [c for c in result.candidates if not c.success]
    if not succeeded:
        # Nothing was consulted. Returning a "consensus" here would be the exact
        # fabrication this tool used to perform.
        detail = "; ".join(f"{c.model_name}: {c.error}" for c in failed) or "no candidates resolved"
        return f"MoA round produced no model answers. {len(failed)} of {len(candidate_list)} requested models failed. Details: {detail}. Check that each name exists in config.yaml -> models[] and that its provider is reachable."

    header = f"## Synthesized MoA Consensus ({len(succeeded)}/{len(candidate_list)} models answered)"
    # The orchestrator's default aggregator emits its own heading counting only the
    # successful candidates. Replace it rather than printing two, because the tool's
    # count (`answered/requested`) is the one that reveals a partial round.
    body = result.consensus_response
    default_heading_prefix = "## Synthesized MoA Consensus ("
    if body.startswith(default_heading_prefix):
        body = body.split(":", 1)[1].lstrip("\n") if ":" in body else body
    parts = [header, body]
    if failed:
        parts.append("Not consulted: " + "; ".join(f"`{c.model_name}` ({c.error})" for c in failed) + ". Treat the consensus above as incomplete.")
    return "\n\n".join(parts)


def _moa_debug_payload(prompt: str, models_csv: str) -> str:  # pragma: no cover - operator aid
    """Structured record of a round, for operators diagnosing candidate failures.

    The tool returns human/model-readable text by design; this helper exists so a
    caller with the runtime can get the raw candidate list (including errors and
    durations) without re-implementing the round.
    """
    candidates = [m.strip() for m in models_csv.split(",") if m.strip()]
    result = get_moa_orchestrator().execute_moa_round(
        prompt=prompt,
        candidate_models=candidates,
        worker_fn=build_model_worker(),
    )
    return json.dumps(
        {
            "requested": candidates,
            "candidates": [{"model": c.model_name, "success": c.success, "error": c.error, "duration_ms": c.duration_ms} for c in result.candidates],
            "total_duration_ms": result.total_duration_ms,
        },
        indent=2,
    )
