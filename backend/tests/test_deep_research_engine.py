import pytest

from alpha.research.engine import (
    DeepResearchEngine,
    DeepResearchReport,
    EvidenceSource,
)
from alpha.research.five_pass import (
    FivePassSearchCompiler,
    FivePassSearchPlan,
    SearchPassType,
)


def _offline_engine() -> DeepResearchEngine:
    """Engine bound to the explicitly-passed offline providers.

    The engine's default is now the live web (honest backends); tests must
    opt in to the deterministic mock providers by name.
    """
    return DeepResearchEngine(
        search_fn=DeepResearchEngine.mock_search,
        fetch_fn=DeepResearchEngine.mock_fetch,
    )


def test_five_pass_discovery_uses_runtime_year(monkeypatch):
    monkeypatch.setattr(
        "alpha.research.five_pass.compiler.time.localtime",
        lambda: type("FakeTime", (), {"tm_year": 2099})(),
    )
    plan = FivePassSearchCompiler.compile("current systems")
    assert plan.lanes[0].query.endswith("2099")


def test_engine_plan_generation():
    engine = _offline_engine()
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
    engine = _offline_engine()
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
    assert "does not establish a logical contradiction" in contradictions[0].nuance_explanation


def test_gap_detection_and_resolution():
    engine = _offline_engine()
    gaps = engine.identify_gaps(
        "Solid-State Batteries",
        [
            EvidenceSource(
                source_id="S1",
                title="Lab Test",
                url="https://lab.org",
                pass_type=SearchPassType.SPECIFIC_EVIDENCE,
                snippet="Testing in pouch cells completed.",
                confidence=0.8,
            )
        ],
    )

    assert len(gaps) >= 1
    # Check resolution mechanism
    resolved = engine.resolve_gap(gaps[0])
    assert resolved.resolved is True
    assert len(resolved.resolution_notes) > 0


@pytest.mark.asyncio
async def test_empty_search_returns_no_evidence_without_fabrication() -> None:
    async def empty_search(query: str, max_results: int = 5) -> list[dict[str, object]]:
        return []

    async def should_not_fetch(url: str) -> str:
        raise AssertionError("No source URL should be fetched when discovery is empty")

    engine = DeepResearchEngine(search_fn=empty_search, fetch_fn=should_not_fetch)
    report = await engine.run_research("an unavailable subject", depth=2, max_sources=5)

    assert report.status == "no_evidence"
    assert report.sources == []
    assert report.citations == []
    assert report.core_findings == []
    assert "no verifiable sources" in report.executive_summary.lower()
    assert "example.org" not in report.markdown_content
    assert "accelerating maturity" not in report.markdown_content
    assert "strict Alpha citation" not in report.markdown_content


@pytest.mark.asyncio
async def test_full_autonomous_research_execution():
    engine = _offline_engine()
    topic = "Autonomous Edge Computing with RISC-V Processors"
    report = await engine.conduct_research(topic, depth=3, max_sources=8, include_adversarial=True)

    assert isinstance(report, DeepResearchReport)
    assert report.topic == topic
    assert report.depth == 3
    assert len(report.sources) >= 4
    assert len({source.source_id for source in report.sources}) == len(report.sources)
    assert len(report.key_findings) >= 3
    assert "[S1]" in report.markdown_report
    assert "## Executive Summary" in report.markdown_report
    assert "## Gathered Sources & Evidence Matrix" in report.markdown_report
    # overall_confidence is derived from the real per-source confidences
    # (it used to be a hardcoded 0.92 no matter what was gathered).
    expected = sum(s.confidence for s in report.sources) / len(report.sources)
    assert report.overall_confidence == pytest.approx(expected, abs=1e-4)
