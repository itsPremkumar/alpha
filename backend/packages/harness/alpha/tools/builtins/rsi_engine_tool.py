"""Built-in run_rsi_cycle tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.rsi.engine import RSIEngine

_GLOBAL_RSI_ENGINE = RSIEngine()


@tool("run_rsi_cycle", parse_docstring=True)
def run_rsi_cycle(
    bottleneck: str,
    target_component: str = "compaction",
) -> str:
    """PREVIEW an optimization candidate for an observed performance bottleneck. It never measures, never benchmarks, and never changes anything.

    This is a proposal generator, not an experiment. It runs Bottleneck -> Hypothesis ->
    Candidate and stops there. The A/B and holdout steps are NOT executed, so their scores
    are reported as null with the reason, and no runtime configuration is ever modified.

    What you get that is real: a diagnosis of the bottleneck, a written hypothesis, and the
    exact configuration delta that *would* be applied if it were approved.

    What you do NOT get: any score, any improvement verdict, any promotion. Do not report this
    tool's output as a measurement or as evidence that a change helped — it is a proposal
    awaiting evaluation, and `promoted` is always false.

    For an actual measured verdict, the candidate must be evaluated by the real hidden-suite
    harness and routed through the promotion gates, which accept only `evidence_kind="measured"`.

    Args:
        bottleneck: Description of the performance bottleneck or failure mode observed.
        target_component: Component to optimize ('compaction', 'tool_router', 'context_pruner').
    """
    engine = _GLOBAL_RSI_ENGINE
    result = engine.run_rsi_cycle(bottleneck=bottleneck, target_component=target_component)

    payload = result.to_dict()
    # Lead the payload with the honest headline. A model scanning this JSON must not be
    # able to reach `candidate` without first passing the fact that nothing was measured.
    return json.dumps(
        {
            "outcome": "PREVIEW_ONLY_NOT_MEASURED",
            "what_this_is": (
                "A proposed configuration change. No benchmark, no holdout suite, and no "
                "promotion were performed, and no runtime configuration was changed. Treat "
                "this as a hypothesis awaiting evaluation, never as a measured result."
            ),
            **payload,
        },
        indent=2,
    )
