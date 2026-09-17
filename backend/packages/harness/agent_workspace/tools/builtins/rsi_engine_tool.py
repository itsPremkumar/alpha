"""Built-in run_rsi_cycle tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.rsi.engine import RSIEngine

_GLOBAL_RSI_ENGINE = RSIEngine()


@tool("run_rsi_cycle", parse_docstring=True)
def run_rsi_cycle(
    bottleneck: str,
    target_component: str = "compaction",
) -> str:
    """Execute an autonomous Recursive Self-Improvement (RSI) cycle: Bottleneck -> Hypothesis -> Candidate -> A/B Test -> Holdout -> Promote.

    Analyzes agent performance bottlenecks, generates an optimization hypothesis, synthesizes
    candidate parameter adjustments, benchmarks them in an A/B sandbox, and promotes them if holdout tests improve.

    Args:
        bottleneck: Description of the performance bottleneck or failure mode observed.
        target_component: Component to optimize ('compaction', 'tool_router', 'context_pruner').
    """
    engine = _GLOBAL_RSI_ENGINE
    result = engine.run_rsi_cycle(bottleneck=bottleneck, target_component=target_component)

    return json.dumps(result.to_dict(), indent=2)
