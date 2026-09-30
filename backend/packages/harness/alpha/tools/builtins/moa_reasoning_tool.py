"""Built-in Mixture-of-Agents tool for multi-perspective consensus reasoning."""

from __future__ import annotations

import json
import logging

from langchain.tools import tool

from alpha.models.moa.orchestrator import get_moa_orchestrator
from alpha.models.moa.workers import build_model_worker

logger = logging.getLogger(__name__)


@tool("moa_multi_model_reasoning", parse_docstring=True)
def moa_multi_model_reasoning(
    prompt: str,
    models_csv: str = "claude-3-7-sonnet,deepseek-r1,gpt-4o",
) -> str:
    """Consult several models in parallel and synthesize their independent perspectives.

    Dispatches the question to every named model simultaneously, redacts PII and
    secrets from both the prompt and each reply, then aggregates the answers. Each
    model answers on its own, so the aggregate can surface disagreement rather than
    just one voice repeated.

    Every named model must exist in `config.yaml` -> `models[]`. A name that does not
    resolve is reported as a failed perspective with the reason; it is never replaced
    by invented text. The response states how many models actually answered, so check
    that number before treating the result as a multi-model consensus — a round where
    every model failed says so instead of returning a synthesis.

    Args:
        prompt: Complex architectural question, code review query, or design trade-off to evaluate.
        models_csv: Comma-separated list of candidate models to consult (default: 'claude-3-7-sonnet,deepseek-r1,gpt-4o').
    """
    candidate_list = [m.strip() for m in models_csv.split(",") if m.strip()]

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
        return (
            "MoA round produced no model answers. "
            f"{len(failed)} of {len(candidate_list)} requested models failed. "
            f"Details: {detail}. "
            "Check that each name exists in config.yaml -> models[] and that its provider is reachable."
        )

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
        parts.append(
            "Not consulted: "
            + "; ".join(f"`{c.model_name}` ({c.error})" for c in failed)
            + ". Treat the consensus above as incomplete."
        )
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
            "candidates": [
                {"model": c.model_name, "success": c.success, "error": c.error, "duration_ms": c.duration_ms}
                for c in result.candidates
            ],
            "total_duration_ms": result.total_duration_ms,
        },
        indent=2,
    )
