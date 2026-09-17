"""Built-in Theory of Mind (ToM) Intent Consultant tool inspired by OpenHands."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.reasoning.tom.consultant import TheoryOfMindConsultant


@tool("tom_consult", parse_docstring=True)
def tom_consult(
    task_description: str,
    proposed_plan: str = "",
    workspace_context: str = "",
    context_metadata_json: str = "",
) -> str:
    """Consult the Theory of Mind cognitive model to infer unstated user expectations, invariants, and pitfalls.

    Before executing high-impact, destructive, or ambiguous modifications, this tool models the human
    developer's mental model, identifying implicit constraints, risk tolerance, and failure modes.

    Args:
        task_description: The user prompt or task goal to model.
        proposed_plan: Optional description of the agent's proposed plan of action.
        workspace_context: Optional workspace layout snippet for grounding (improves calibration).
        context_metadata_json: Optional JSON object with repo_map/file_list/prior_tasks hints.
    """
    import json as _json

    metadata = None
    if context_metadata_json:
        try:
            metadata = _json.loads(context_metadata_json)
        except Exception:
            metadata = None
    consultant = TheoryOfMindConsultant()
    hypothesis = consultant.consult(
        task_description=task_description,
        workspace_context=workspace_context or None,
        context_metadata=metadata,
    )

    return json.dumps(
        {
            "stated_goal": hypothesis.stated_goal,
            "inferred_intent": hypothesis.inferred_intent,
            "risk_tolerance": hypothesis.risk_tolerance.value,
            "top_priorities": [p.value for p in hypothesis.top_priorities],
            "unstated_expectations": hypothesis.unstated_expectations,
            "pitfalls_to_avoid": hypothesis.pitfalls_to_avoid,
            "recommended_constraints": hypothesis.recommended_constraints,
            "confidence_score": hypothesis.confidence_score,
            "summary_markdown": hypothesis.to_markdown(),
        },
        indent=2,
    )
