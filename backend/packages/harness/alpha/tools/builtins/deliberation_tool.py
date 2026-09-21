"""Built-in Deliberation Tool: Multi-LLM Council, Debate & Verification.

Enables agents to consult multiple independent AI models before finalizing
decisions, code refactors, security audits, or architectural designs.
Enforces blind peer reviews, self-vote exclusion, and truth-seeking verification.
"""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.deliberation.engine import get_master_deliberation_engine
from alpha.deliberation.models import DeliberationStrategy
from alpha.deliberation.router import DeliberationRouter


@tool("deliberate", parse_docstring=True)
def deliberation_tool(
    action: Literal["deliberate", "evaluate", "council", "debate", "ensemble"] = "deliberate",
    prompt: str = "",
    max_rounds: int = 3,
    code_test_command: str = "",
) -> str:
    """Consult multiple independent AI models via anonymous council or debate before deciding.

    Args:
        action: Deliberation operation to perform:
            - 'deliberate': Automatically evaluates prompt and runs the optimal strategy (Single, Ensemble, Council, Debate).
            - 'evaluate': Pre-flight check returning difficulty, risk, recommended strategy, and worthwhile assessment.
            - 'council': Forces the 3-Stage Anonymous Peer Review Council (blind generation -> rubric review -> chairman).
            - 'debate': Forces Multi-Round Sparse Debate between competing models with an independent judge.
            - 'ensemble': Forces parallel candidate generation with consensus aggregation.
        prompt: The high-stakes question, design decision, or code problem to deliberate.
        max_rounds: Maximum debate rounds if debate is selected (default 3).
        code_test_command: Optional deterministic shell test command to verify claims against reality.
    """
    if not prompt.strip():
        return "Error: 'prompt' parameter is required for deliberation."

    engine = get_master_deliberation_engine()

    # ACTION: EVALUATE (Pre-flight classification)
    if action == "evaluate":
        eval_res = DeliberationRouter.classify_smart(prompt)
        return (
            f"### 🔍 Deliberation Pre-Flight Evaluation\n"
            f"- **Difficulty**: `{eval_res.difficulty.value}`\n"
            f"- **Risk Tier**: `{eval_res.risk.value}`\n"
            f"- **Recommended Strategy**: `{eval_res.strategy.value}`\n"
            f"- **Worthwhile Deliberation**: `{'YES' if eval_res.worthwhile else 'NO (Single Model Sufficient)'}`\n"
            f"- **Proposed Roster**: `{', '.join(eval_res.roster_models)}`\n"
            f"- **Rationale**: {eval_res.rationale}\n"
        )

    # Determine strategy override
    if action == "council":
        strategy = DeliberationStrategy.COUNCIL
    elif action == "debate":
        strategy = DeliberationStrategy.DEBATE
    elif action == "ensemble":
        strategy = DeliberationStrategy.ENSEMBLE
    else:
        strategy = DeliberationStrategy.AUTO

    result = engine.deliberate(
        prompt=prompt.strip(),
        strategy=strategy,
        max_rounds=max_rounds,
        code_test_command=code_test_command if code_test_command.strip() else None,
    )

    dissent_section = f"\n\n**Minority Dissent / Critical Reservations**:\n> {result.minority_dissent}" if result.minority_dissent else ""

    return (
        f"### 🏛️ Multi-LLM Deliberation Result [{result.strategy_used.value.upper()}]\n"
        f"- **Deliberation ID**: `{result.deliberation_id}`\n"
        f"- **Confidence Score**: `{result.confidence_score * 100:.1f}%` ({result.confidence_level.value})\n"
        f"- **Consensus Level**: `{result.consensus_percentage:.1f}%`\n"
        f"- **Verification Status**: `{result.verification_status}`\n"
        f"- **Duration**: `{result.duration_seconds}s`\n\n"
        f"{result.final_answer}"
        f"{dissent_section}\n\n"
        f"**Verdict Rationale**: {result.verdict_rationale}"
    )
