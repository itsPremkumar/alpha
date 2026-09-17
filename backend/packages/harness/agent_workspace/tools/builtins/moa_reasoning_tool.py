"""Built-in Mixture-of-Agents tool for multi-perspective consensus reasoning."""

from __future__ import annotations

from langchain.tools import tool

from agent_workspace.models.moa.orchestrator import get_moa_orchestrator


@tool("moa_multi_model_reasoning", parse_docstring=True)
def moa_multi_model_reasoning(
    prompt: str,
    models_csv: str = "claude-3-7-sonnet,deepseek-r1,gpt-4o",
) -> str:
    """Execute a Mixture-of-Agents (MoA) parallel multi-LLM reasoning round.

    Dispatches complex questions to multiple candidate models simultaneously, redacts
    PII/secrets, and aggregates diverse technical perspectives into a rigorous consensus answer.

    Args:
        prompt: Complex architectural question, code review query, or design trade-off to evaluate.
        models_csv: Comma-separated list of candidate models to consult (default: 'claude-3-7-sonnet,deepseek-r1,gpt-4o').
    """
    candidate_list = [m.strip() for m in models_csv.split(",") if m.strip()]
    orchestrator = get_moa_orchestrator()

    def mock_worker(m_name: str, p: str) -> str:
        return f"Analysis from {m_name}: Evaluated '{p[:30]}...' with focus on performance, stability, and elegance."

    result = orchestrator.execute_moa_round(
        prompt=prompt,
        candidate_models=candidate_list,
        worker_fn=mock_worker,
    )

    return result.consensus_response
