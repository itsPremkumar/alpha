"""Built-in Cognitive Memory Engine tool for autonomous agent cognition.

Enables agents to:
1. 'recall': Execute context-aware hybrid retrieval across all 6 cognitive memory tiers
2. 'store_belief': Record new epistemic beliefs/facts in the semantic knowledge graph
3. 'lookup_skill': Retrieve learned procedural skills, execution recipes, and guardrails
4. 'record_step': Log episodic action traces with execution outcomes
5. 'overview': Inspect memory density, active working items, and consolidation state
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from langchain.tools import tool

from alpha.memory.cognitive import (
    HybridRecallQuery,
    get_cognitive_memory_system,
)
from alpha.runtime.user_context import (
    _user_id_from_auth_user,
    _user_id_from_langgraph_auth,
    get_current_user,
    resolve_runtime_user_id,
)
from alpha.tools.types import Runtime


def _cognitive_runtime_owner(runtime: Runtime | None) -> str:
    context = getattr(runtime, "context", None)
    context_owner = context.get("user_id") if isinstance(context, Mapping) else None
    server_owner = _user_id_from_auth_user(getattr(getattr(runtime, "server_info", None), "user", None))
    if not server_owner and not _user_id_from_langgraph_auth() and not context_owner and get_current_user() is None:
        raise RuntimeError("Cognitive memory requires an owner")
    return resolve_runtime_user_id(runtime)


@tool("cognitive_memory_tool", parse_docstring=True)
def cognitive_memory_tool(
    action: str,
    runtime: Runtime,
    query: str = "",
    subject: str = "",
    predicate: str = "",
    object_val: str = "",
    confidence: float = 0.8,
    observation: str = "",
    outcome: str = "success",
    error_context: str | None = None,
    limit: int = 5,
) -> str:
    """Access, search, and update the agent's Multi-Tier Cognitive Memory System.

    Actions:
    - 'recall': Hybrid retrieval fusing BM25, semantic vector similarity, graph traversal, and temporal decay. Use 'query' and optional 'limit'.
    - 'store_belief': Crystallize a factual statement into the semantic graph with automated contradiction checking. Requires 'subject', 'predicate', and 'object_val'.
    - 'lookup_skill': Find reusable procedural playbooks and avoidance routines for a given task or error. Uses 'query'.
    - 'record_step': Record an episodic trace of an action and observation. Requires 'query' (as the action), 'observation', and 'outcome' ('success', 'failure', 'partial').
    - 'overview': Return stats and density metrics across all 6 cognitive memory tiers.

    Args:
        action: One of 'recall', 'store_belief', 'lookup_skill', 'record_step', 'overview'.
        query: Query string for recall, task context for skill lookup, or action description for step recording.
        subject: Subject entity for 'store_belief' (e.g. 'PostgreSQL', 'UserPreference').
        predicate: Predicate relationship for 'store_belief' (e.g. 'requires_port', 'prefers_theme').
        object_val: Object value for 'store_belief' (e.g. '5432', 'dark').
        confidence: Confidence score 0.0-1.0 for 'store_belief' (default: 0.8).
        observation: Observation or output text for 'record_step'.
        outcome: Outcome classification for 'record_step': 'success', 'failure', or 'partial'.
        error_context: Optional error message or traceback for 'record_step' on failure.
        limit: Maximum results to return for 'recall' or 'lookup_skill' (default: 5).
    """
    system = get_cognitive_memory_system(user_id=_cognitive_runtime_owner(runtime))
    with system.operation():
        return _execute_cognitive_action(system, action, query, subject, predicate, object_val, confidence, observation, outcome, error_context, limit)


def _execute_cognitive_action(system, action, query, subject, predicate, object_val, confidence, observation, outcome, error_context, limit):
    act = action.strip().lower()

    if act == "recall":
        if not query.strip():
            return json.dumps({"error": "Query string is required for recall action."})
        recall_query = HybridRecallQuery(query=query.strip(), limit=min(limit, 20))
        results = system.recall(recall_query)
        return json.dumps(
            {
                "query": query.strip(),
                "count": len(results),
                "results": [r.to_dict() for r in results],
            },
            indent=2,
        )

    elif act == "store_belief":
        if not subject.strip() or not predicate.strip() or not object_val.strip():
            return json.dumps({"error": "Subject, predicate, and object_val are required for store_belief."})
        node = system.semantic_graph.add_belief(
            subject=subject.strip(),
            predicate=predicate.strip(),
            object_val=object_val.strip(),
            confidence=max(0.0, min(1.0, confidence)),
            tags=["agent_inferred"],
        )
        system.save_to_disk()
        return json.dumps(
            {
                "status": "stored",
                "node_id": node.node_id,
                "statement": node.statement,
                "status_code": node.status.value,
                "confidence": node.confidence,
            },
            indent=2,
        )

    elif act == "lookup_skill":
        context = query.strip() or f"{subject} {predicate} {object_val}".strip()
        if not context:
            return json.dumps({"error": "Query or context is required for lookup_skill."})
        matches = system.procedural_mem.find_matching_skills(context, limit=min(limit, 10))
        return json.dumps(
            {
                "context": context,
                "count": len(matches),
                "skills": [
                    {
                        "name": s[0].name,
                        "description": s[0].description,
                        "match_score": round(s[1], 3),
                        "steps": s[0].steps,
                        "code_snippet": s[0].code_snippet,
                        "success_rate": round(s[0].success_rate, 3),
                    }
                    for s in matches
                ],
            },
            indent=2,
        )

    elif act == "record_step":
        action_text = query.strip()
        if not action_text or not observation.strip():
            return json.dumps({"error": "Query (action) and observation are required for record_step."})
        trace = system.episodic_mem.record_trace(
            action=action_text,
            observation=observation.strip(),
            outcome=outcome,
            error_context=error_context,
            tags=["runtime_turn"],
        )
        system.save_to_disk()
        return json.dumps(
            {
                "status": "recorded",
                "trace_id": trace.trace_id,
                "outcome": trace.outcome.value,
                "salience": trace.salience,
            },
            indent=2,
        )

    elif act == "overview":
        return json.dumps(system.overview(), indent=2)

    return json.dumps({"error": f"Unknown action '{action}'. Supported actions: recall, store_belief, lookup_skill, record_step, overview."})
