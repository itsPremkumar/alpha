import pytest

from alpha.research.engine import (
    ContradictionFinding,
    DeepResearchEngine,
    DeepResearchReport,
    EvidenceSource,
    ResearchGap,
)
from alpha.research.five_pass import (
    FivePassSearchCompiler,
    FivePassSearchPlan,
    SearchPassType,
)


def test_engine_plan_generation():
    engine = DeepResearchEngine()
    topic = "Next-Generation Solid State Battery Commercialization"
    plan = engine.generate_plan(topic, depth=4)

    assert isinstance(plan, FivePassSearchPlan)
    assert plan.original_question == topic
    assert len(plan.lanes) == 5

    pass_types = {lane.pass_type for lane in plan.lanes}
    assert pass_types == {
        SearchPassType.DISCOVERY,
        SearchPassType.SPECIFIC_EVIDENCE,
        SearchPassType.ADVERSARIAL_CONTRADICTION,
        SearchPassType.FACT_VERIFICATION,
        SearchPassType.STRATEGIC_SYNTHESIS,
    }


def test_evidence_source_citation():
    source = EvidenceSource(
        source_id="S1",
        title="MIT Solid State Energy Review",
        url="https://mit.edu/energy/solid-state",
        pass_type=SearchPassType.SPECIFIC_EVIDENCE,
        snippet="Ceramic electrolyte achieves 450 Wh/kg in 2026 lab tests.",
        extracted_facts=["Ceramic electrolyte achieves 450 Wh/kg."],
        metrics={"energy_density": "450 Wh/kg", "year": "2026"},
        confidence=0.95,
    )

    citation = source.to_citation()
    assert "[S1]" in citation
    assert "MIT Solid State Energy Review" in citation
    assert "https://mit.edu/energy/solid-state" in citation


def test_contradiction_detection():
    engine = DeepResearchEngine()
    s1 = EvidenceSource(
        source_id="S1",
        title="Vendor Claim",
        url="https://vendor.com",
        pass_type=SearchPassType.DISCOVERY,
        snippet="Commercial mass production is scaling rapidly with 99% yield.",
        confidence=0.9,
    )
    s2 = EvidenceSource(
        source_id="S2",
        title="Independent Lab Audit",
        url="https://audit-lab.org",
        pass_type=SearchPassType.ADVERSARIAL_CONTRADICTION,
        snippet="Dendrite formation causes high failure rate and low yield in mass production.",
        confidence=0.92,
    )

    contradictions = engine.detect_contradictions([s1, s2])
    assert len(contradictions) >= 1
    assert any("failure rate" in c.adversarial_evidence.lower() or "dendrite" in c.adversarial_evidence.lower() for c in contradictions)


def test_gap_detection_and_resolution():
    engine = DeepResearchEngine()
    gaps = engine.identify_gaps("Solid-State Batteries", [
        EvidenceSource(
            source_id="S1",
            title="Lab Test",
            url="https://lab.org",
            pass_type=SearchPassType.SPECIFIC_EVIDENCE,
            snippet="Testing in pouch cells completed.",
            confidence=0.8,
        )
    ])

    assert len(gaps) >= 1
    # Check resolution mechanism
    resolved = engine.resolve_gap(gaps[0])
    assert resolved.resolved is True
    assert len(resolved.resolution_notes) > 0


@pytest.mark.asyncio
async def test_full_autonomous_research_execution():
    engine = DeepResearchEngine()
    topic = "Autonomous Edge Computing with RISC-V Processors"
    report = await engine.conduct_research(topic, depth=3, max_sources=8, include_adversarial=True)

    assert isinstance(report, DeepResearchReport)
    assert report.topic == topic
    assert report.depth == 3
    assert len(report.sources) >= 4
    assert len(report.key_findings) >= 3
    assert "[S1]" in report.markdown_report
    assert "## Executive Summary" in report.markdown_report
    assert "## Verified Sources & Evidence Matrix" in report.markdown_report
    assert report.overall_confidence > 0.5
