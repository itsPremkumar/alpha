import pytest

from agent_workspace.lineage.artifact_lineage import (
    ArtifactLineageGraph,
    ConfidenceClass,
)


def test_artifact_registration_and_provenance_tracing():
    graph = ArtifactLineageGraph()

    # 1. Register raw source data
    source_art = graph.register_artifact(
        name="customer_feedback.csv",
        content="id,feedback\n1,fast response\n2,billing issue\n",
        creator="crawler_agent",
        mime_type="text/csv",
        confidence=ConfidenceClass.VERIFIED,
    )
    assert source_art.sha256_hash != ""

    # 2. Register intermediate analysis
    analysis_art = graph.register_artifact(
        name="sentiment_summary.json",
        content='{"positive": 1, "negative": 1}',
        creator="analyst_agent",
        mime_type="application/json",
        confidence=ConfidenceClass.PROVISIONAL,
    )

    # Record derivation: source -> analysis
    graph.record_derivation(
        source_id=source_art.artifact_id,
        target_id=analysis_art.artifact_id,
        derivation_action="sentiment_extraction",
        agent_id="analyst_agent",
    )

    # 3. Register final deliverable
    report_art = graph.register_artifact(
        name="executive_summary.pdf",
        content="Executive Report: 50% positive sentiment.",
        creator="writer_agent",
        mime_type="application/pdf",
        confidence=ConfidenceClass.HUMAN_APPROVED,
    )

    # Record derivation: analysis -> report
    graph.record_derivation(
        source_id=analysis_art.artifact_id,
        target_id=report_art.artifact_id,
        derivation_action="pdf_generation",
        agent_id="writer_agent",
    )

    # Trace upstream provenance of the report
    upstream = graph.get_upstream_provenance(report_art.artifact_id)
    assert upstream["total_ancestors"] == 2
    assert len(upstream["root_sources"]) == 1
    assert upstream["root_sources"][0]["name"] == "customer_feedback.csv"
    assert len(upstream["derivation_steps"]) == 2

    # Trace downstream impact of customer_feedback.csv
    downstream = graph.get_downstream_impact(source_art.artifact_id)
    assert downstream["impact_count"] == 2
    affected_names = [a["name"] for a in downstream["affected_artifacts"]]
    assert "sentiment_summary.json" in affected_names
    assert "executive_summary.pdf" in affected_names

    # Integrity verification
    integrity = graph.verify_provenance_integrity(report_art.artifact_id)
    assert integrity["valid"] is True
    assert integrity["confidence"] == "human_approved"


def test_artifact_lineage_cycle_rejection():
    graph = ArtifactLineageGraph()
    a1 = graph.register_artifact(name="doc_a.txt", content="A")
    a2 = graph.register_artifact(name="doc_b.txt", content="B")

    graph.record_derivation(source_id=a1.artifact_id, target_id=a2.artifact_id, derivation_action="transform")

    # Creating cycle a2 -> a1 should be rejected
    with pytest.raises(ValueError) as exc:
        graph.record_derivation(source_id=a2.artifact_id, target_id=a1.artifact_id, derivation_action="invalid_cycle")
    assert "would create a cycle" in str(exc.value)
