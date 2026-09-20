
from alpha.avo.knowledge import DomainKnowledgeBase


def test_knowledge_base_seeding_and_query():
    kb = DomainKnowledgeBase()
    stats = kb.stats()
    assert stats["patterns"] >= 3
    assert stats["anti_patterns"] >= 1

    # Query by keyword "branchless"
    results = kb.query("branchless accumulator")
    assert len(results) > 0
    assert "Branchless Accumulator Rescaling" in results[0].title
    assert results[0].category == "pattern"

    # Query by keyword "divergence"
    div_results = kb.query("warp divergence")
    assert len(div_results) > 0
    assert any(e.category == "anti_pattern" for e in div_results)


def test_knowledge_base_negative_and_positive_recording():
    kb = DomainKnowledgeBase()

    # Record failed trial
    neg_entry = kb.record_negative_lesson(
        attempt_hypothesis="Increase thread block size to 1024",
        failure_reason="Register spill caused 40% memory latency stall",
        tags=["spill", "thread-block"],
    )
    assert neg_entry.category == "anti_pattern"
    assert "spill" in neg_entry.tags

    # Record breakthrough
    pos_entry = kb.record_positive_pattern(
        hypothesis="Stage SMEM loads asynchronously with TMA",
        modification_summary="Added asynchronous cp.async with barrier arrival",
        measured_gain="geomean=+12.4% TFLOPS",
        tags=["tma", "async-copy"],
    )
    assert pos_entry.category == "pattern"
    assert "tma" in pos_entry.tags

    # Querying "spill" should retrieve the recorded anti-pattern
    query_res = kb.query("spill")
    assert any("Register spill" in e.content for e in query_res)
