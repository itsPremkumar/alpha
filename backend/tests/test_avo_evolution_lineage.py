
from agent_workspace.avo import (
    AVOEngine,
    AVOLineage,
    AVOSupervisor,
    VersionRecord,
)


def test_lineage_matches_or_improves_policy():
    lineage = AVOLineage()

    # V1: Baseline (passes correctness, score = 0.5*1.0 + 0.3*0.4 + 0.2*0.5 = 0.72)
    v1 = VersionRecord(
        hypothesis="Initial baseline",
        modification="Add parser",
        correctness=True,
        performance_score=0.4,
        quality_score=0.5,
    )
    assert lineage.commit_candidate(v1) is True
    assert lineage.head_id == v1.version_id
    assert v1.composite_score == 0.72

    # V2: Failed correctness -> MUST be discarded
    v2 = VersionRecord(
        parent_id=v1.version_id,
        hypothesis="Break tests",
        modification="Typo bug",
        correctness=False,
        performance_score=0.9,
    )
    assert lineage.commit_candidate(v2) is False

    # V3: Regressed performance below parent -> MUST be rejected
    v3 = VersionRecord(
        parent_id=v1.version_id,
        hypothesis="Sub-optimal loop",
        modification="Slow traversal",
        correctness=True,
        performance_score=0.1,  # lower than parent's 0.4
        quality_score=0.1,
    )
    assert lineage.commit_candidate(v3) is False

    # V4: Improved performance -> MUST be accepted and promoted as head
    v4 = VersionRecord(
        parent_id=v1.version_id,
        hypothesis="Use hash map lookup",
        modification="Replace loop with O(1) dict",
        correctness=True,
        performance_score=0.9,
        quality_score=0.8,
    )
    assert lineage.commit_candidate(v4) is True
    assert lineage.head_id == v4.version_id
    assert v4.composite_score > v1.composite_score


def test_avo_supervisor_stagnation_detection():
    supervisor = AVOSupervisor(max_no_improve=3)

    stagnated, _ = supervisor.observe(improved=True)
    assert stagnated is False
    assert supervisor.consecutive_stagnation == 0

    stagnated, _ = supervisor.observe(improved=False)
    assert stagnated is False
    assert supervisor.consecutive_stagnation == 1

    stagnated, _ = supervisor.observe(improved=False)
    assert stagnated is False
    assert supervisor.consecutive_stagnation == 2

    # 3rd non-improvement triggers stagnation intervention
    stagnated, diag = supervisor.observe(improved=False)
    assert stagnated is True
    assert "STAGNATION_DETECTED" in diag


def test_avo_engine_closed_loop():
    engine = AVOEngine()

    res1 = engine.run_iteration(
        hypothesis="Initial commit",
        modification="v1 init",
        evaluate_fn=lambda: {"correctness": True, "performance": 0.5, "quality": 0.5},
    )
    assert res1["committed"] is True
    assert res1["current_head"] == res1["version_id"]

    res2 = engine.run_iteration(
        hypothesis="Higher performance commit",
        modification="v2 optimized",
        evaluate_fn=lambda: {"correctness": True, "performance": 0.8, "quality": 0.8},
    )
    assert res2["committed"] is True
    assert res2["current_head"] == res2["version_id"]
