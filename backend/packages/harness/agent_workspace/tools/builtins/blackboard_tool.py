"""Built-in blackboard tools inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.blackboard import (
    BlackboardEngine,
)

# Global blackboard instance for active session working memory
_GLOBAL_BLACKBOARD = BlackboardEngine()


@tool("blackboard_record_evidence", parse_docstring=True)
def blackboard_record_evidence(
    plane_id: str,
    evidence_type: str,
    content_json: str,
    confidence: float = 1.0,
) -> str:
    """Record an evidence item into the 20-Plane Shared Blackboard.

    Args:
        plane_id: The originating cognitive plane (e.g. 'plane_4_deep_research', 'plane_13_multi_round_verification').
        evidence_type: The type of evidence (e.g. 'fact_verified', 'test_oracle_pass', 'ast_audit').
        content_json: JSON string containing the structured evidence payload.
        confidence: Confidence score from 0.0 to 1.0.
    """
    try:
        content = json.loads(content_json) if content_json else {}
    except Exception:
        content = {"raw": content_json}

    item = _GLOBAL_BLACKBOARD.record_evidence(
        plane_id=plane_id,
        evidence_type=evidence_type,
        content=content,
        confidence=confidence,
    )

    return json.dumps({
        "status": "recorded",
        "evidence_id": item.evidence_id,
        "plane_id": item.plane_id,
        "evidence_type": item.evidence_type,
        "confidence": item.confidence,
    }, indent=2)


@tool("blackboard_query", parse_docstring=True)
def blackboard_query(
    plane_id: str = "",
    min_confidence: float = 0.0,
) -> str:
    """Query verified evidence and active states from the 20-Plane Shared Blackboard.

    Args:
        plane_id: Optional filter for a specific cognitive plane ID.
        min_confidence: Minimum confidence threshold (0.0 to 1.0).
    """
    pid = plane_id.strip() if plane_id else None
    evidence = _GLOBAL_BLACKBOARD.query_evidence(plane_id=pid, min_confidence=min_confidence)

    return json.dumps({
        "session_id": _GLOBAL_BLACKBOARD.session_id,
        "current_phase": _GLOBAL_BLACKBOARD.current_phase.value,
        "total_evidence_count": len(evidence),
        "evidence": [e.to_dict() for e in evidence],
    }, indent=2)
