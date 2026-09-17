"""Built-in avo_lineage tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.avo import AVOEngine

# Global AVO engine instance
_GLOBAL_AVO_ENGINE = AVOEngine()


@tool("run_avo_variation", parse_docstring=True)
def run_avo_variation(
    hypothesis: str,
    modification: str,
    correctness: bool,
    performance_score: float = 0.0,
    quality_score: float = 0.0,
) -> str:
    """Run an Autonomous Value Optimization (AVO) variation step.

    Applies the strict matches-or-improves commit policy against the lineage tree.
    Regressions below the parent score are rejected. Stagnation is monitored.

    Args:
        hypothesis: The testable hypothesis behind this modification.
        modification: Description of the code, prompt, or configuration change.
        correctness: Hard binary gate — whether unit tests / acceptance criteria pass.
        performance_score: Measured performance metric (0.0 to 1.0).
        quality_score: Measured code / artifact quality metric (0.0 to 1.0).
    """
    res = _GLOBAL_AVO_ENGINE.run_iteration(
        hypothesis=hypothesis,
        modification=modification,
        evaluate_fn=lambda: {
            "correctness": correctness,
            "performance": performance_score,
            "quality": quality_score,
        },
    )

    return json.dumps(res, indent=2)
