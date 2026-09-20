"""Built-in Artifact Lineage and Provenance LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.lineage.artifact_lineage import (
    ArtifactLineageGraph,
    ConfidenceClass,
)

_GLOBAL_LINEAGE = ArtifactLineageGraph()


@tool("trace_artifact_lineage", parse_docstring=True)
def trace_artifact_lineage(
    action: str,
    artifact_id: str = "",
    name: str = "",
    content: str = "",
    task_id: str = "",
    creator: str = "agent",
    mime_type: str = "text/plain",
    confidence_level: str = "unverified",
    source_id: str = "",
    target_id: str = "",
    derivation_action: str = "derived_from",
) -> str:
    """Manage artifact provenance, track causal derivation, and audit the ancestry graph of any output.

    Args:
        action: 'register_artifact', 'record_derivation', 'set_confidence', 'get_upstream', 'get_downstream', 'verify_integrity', 'render_ascii'.
        artifact_id: Target artifact ID for queries, confidence updates, or integrity verification.
        name: Name of the artifact being registered (e.g. 'summary_report.pdf', 'metrics.csv').
        content: Raw text content to compute SHA-256 fingerprint.
        task_id: Associated task ID.
        creator: Role or agent name that generated the artifact.
        mime_type: MIME type of artifact (e.g. 'text/markdown', 'application/json').
        confidence_level: Trust tier ('unverified', 'provisional', 'verified', 'human_approved').
        source_id: Upstream ancestor artifact ID when recording derivation.
        target_id: Downstream derived artifact ID when recording derivation.
        derivation_action: Description of causal transform (e.g. 'aggregated_statistics', 'synthesized_code').
    """
    try:
        if action == "register_artifact":
            conf = ConfidenceClass(confidence_level.lower())
            node = _GLOBAL_LINEAGE.register_artifact(
                name=name,
                content=content if content else None,
                task_id=task_id,
                creator=creator,
                mime_type=mime_type,
                confidence=conf,
                artifact_id=artifact_id or None,
            )
            return json.dumps({"status": "registered", "artifact": node.to_dict()}, indent=2)

        elif action == "record_derivation":
            edge = _GLOBAL_LINEAGE.record_derivation(
                source_id=source_id,
                target_id=target_id,
                derivation_action=derivation_action,
                agent_id=creator,
            )
            return json.dumps({"status": "derivation_recorded", "edge": edge.to_dict()}, indent=2)

        elif action == "set_confidence":
            conf = ConfidenceClass(confidence_level.lower())
            updated = _GLOBAL_LINEAGE.set_confidence(artifact_id, conf)
            return json.dumps({"status": "confidence_updated", "artifact": updated.to_dict()}, indent=2)

        elif action == "get_upstream":
            trace = _GLOBAL_LINEAGE.get_upstream_provenance(artifact_id)
            return json.dumps(trace, indent=2)

        elif action == "get_downstream":
            impact = _GLOBAL_LINEAGE.get_downstream_impact(artifact_id)
            return json.dumps(impact, indent=2)

        elif action == "verify_integrity":
            audit = _GLOBAL_LINEAGE.verify_provenance_integrity(artifact_id)
            return json.dumps(audit, indent=2)

        elif action == "render_ascii":
            return _GLOBAL_LINEAGE.render_lineage_ascii(artifact_id)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error tracing artifact lineage: {exc}"
