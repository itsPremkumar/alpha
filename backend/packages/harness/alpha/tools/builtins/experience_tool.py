"""Built-in consult_experience tool for querying and updating episodic experience memory."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.learning.experience.models import ExperienceKind, ExperienceRecord, OutcomeType
from alpha.learning.experience.retriever import ExperienceRetriever
from alpha.learning.experience.store import ExperienceStore

_KNOWN_TYPES = ("EPISODE", "FACT", "TIP")

# Process-wide store so a FACT/TIP recorded by one call can be queried by a
# later call in the same process (in-memory; no new on-disk location is
# introduced by the tool).
_STORE: ExperienceStore | None = None


def _get_store() -> ExperienceStore:
    global _STORE
    if _STORE is None:
        _STORE = ExperienceStore()
    return _STORE


@tool("consult_experience", parse_docstring=True)
def consult_experience(
    query: str,
    action: str = "query",
    outcome: str = "success",
    lessons: list[str] | None = None,
    pitfalls: list[str] | None = None,
    type: str = "",
    evidence: list[str] | None = None,
    limit: int = 3,
) -> str:
    """Consult or update the Episodic Experience Memory to retrieve past lessons or record new ones.

    Action 'query': Retrieves relevant past failure modes, lessons learned, and guidelines for the current task.
    Action 'record': Stores a newly discovered lesson or resolution pattern into the episodic memory.

    FACT/TIP bank: pass type='FACT' or type='TIP' to record/query TTSE-style facts
    (stable information learned from execution) and tips (reusable action strategies).
    FACT/TIP records require an evidence reference list (trace ids / observed
    outcomes); refusals are returned verbatim as
    {"status": "rejected", "stored": false, "reason": ...} and are never reported
    as recorded. Query results for FACT/TIP disclose the lexical heuristic used
    (score_method + reason per item).

    Args:
        query: The task goal or technical query to search for lessons on (or the FACT/TIP statement when recording).
        action: Either 'query' (default) or 'record'.
        outcome: Outcome of the task if recording ('success', 'failure', 'partial').
        lessons: List of lessons learned to record (for action 'record').
        pitfalls: List of pitfalls encountered to record (for action 'record').
        type: Optional experience type: 'FACT', 'TIP', or 'EPISODE' (empty keeps the legacy episode behaviour).
        evidence: Evidence reference list (trace ids / observed outcomes); required when recording a FACT or TIP.
        limit: Maximum number of query results to return (for action 'query').
    """
    store = _get_store()
    retriever = ExperienceRetriever(store=store)
    type_filter = (type or "").strip().upper()

    if type_filter and type_filter not in _KNOWN_TYPES:
        return json.dumps(
            {"error": f"Unknown type '{type}'. Must be one of: {', '.join(_KNOWN_TYPES)}."},
            indent=2,
        )

    if action.lower() == "query":
        if type_filter in ("FACT", "TIP"):
            items = retriever.relevant_for(query, top_k=limit, kinds=[type_filter])
            return json.dumps(
                {
                    "query": query,
                    "type": type_filter,
                    "match_count": len(items),
                    "score_method": "lexical_overlap_v1",
                    "items": items,
                    "note": ("FACT/TIP matches come from a disclosed lexical token-overlap heuristic (see each item's score_method and reason); no semantic model and no independent verification of the stored claims is implied."),
                },
                indent=2,
            )

        matches = retriever.retrieve(query, limit=limit)
        prompt_md = retriever.render_lessons_prompt(query, limit=limit)

        return json.dumps(
            {
                "query": query,
                "match_count": len(matches),
                "experiences": [m.to_dict() for m in matches],
                "markdown_advice": prompt_md,
            },
            indent=2,
        )

    elif action.lower() == "record":
        try:
            outcome_type = OutcomeType(outcome.lower())
        except ValueError:
            return json.dumps(
                {
                    "status": "rejected",
                    "stored": False,
                    "reason": f"Invalid outcome '{outcome}'. Must be 'success', 'failure', or 'partial'.",
                },
                indent=2,
            )

        kind = ExperienceKind(type_filter) if type_filter else ExperienceKind.EPISODE
        rec = ExperienceRecord(
            task_goal=query,
            outcome=outcome_type,
            lessons_learned=lessons or [],
            pitfalls_to_avoid=pitfalls or [],
            kind=kind,
            statement=query if kind in (ExperienceKind.FACT, ExperienceKind.TIP) else "",
            evidence=[str(ref) for ref in (evidence or []) if str(ref).strip()],
        )

        # FACT/TIP go through the evidence-gated intake; EPISODE keeps the
        # legacy path (secret/PII screening still applies; evidence optional —
        # its without-evidence behaviour is pinned by existing tests).
        result = store.add(rec) if kind is not ExperienceKind.EPISODE else store.record(rec)
        if not result.get("stored"):
            return json.dumps(
                {
                    "status": "rejected",
                    "stored": False,
                    "reason": result.get("reason", "store refused the record without a stated reason"),
                    "kind": kind.value,
                },
                indent=2,
            )

        payload = {
            "status": "recorded",
            "stored": True,
            "experience_id": rec.experience_id,
            "task_goal": rec.task_goal,
            "kind": kind.value,
            "confidence": rec.confidence,
            "confidence_basis": ("reuse_evidence (success_count/failure_count)" if (rec.success_count or rec.failure_count or rec.reuse_count) else "neutral_baseline_0.5 (no reuse evidence yet)"),
            "evidence": rec.evidence,
        }
        if kind is ExperienceKind.EPISODE and not rec.evidence:
            payload["note"] = "Legacy episode path: stored without evidence references (an evidence reference list is mandatory for FACT/TIP records)."
        return json.dumps(payload, indent=2)

    return json.dumps({"error": f"Invalid action '{action}'. Must be 'query' or 'record'."})
