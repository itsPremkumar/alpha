
from alpha.avo import (
    AgenticVariationLoop,
    AVOEngine,
    AVOLineage,
    AVOSupervisor,
    DomainKnowledgeBase,
    EvaluationVector,
    VersionRecord,
)


def test_agentic_variation_loop_multi_trial_repair():
    lineage = AVOLineage()
    # Baseline seed
    v0 = VersionRecord(
        hypothesis="Initial seed kernel",
        modification="Basic naive kernel",
        correctness=True,
        vector=EvaluationVector(metrics={"throughput": 500.0}, correctness=True),
        performance_score=500.0,
    )
    lineage.commit_candidate(v0)
    assert lineage.head_id == v0.version_id

    kb = DomainKnowledgeBase()
    sup = AVOSupervisor()
    loop = AgenticVariationLoop(max_internal_trials=3)

    # Edit function simulates:
    # Trial 1: Buggy syntax / failed correctness
    # Trial 2: Auto-repaired, valid and higher throughput
    def mock_edit(code: str, ctx: dict):
        trial = ctx["trial"]
        if trial == 1:
            return code + "\n// buggy syntax", "Attempted unrolling but broke syntax"
        else:
            return code + "\n// clean unroll", "Repaired syntax and optimized inner loop"

    def mock_eval(candidate_code: str) -> EvaluationVector:
        if "buggy syntax" in candidate_code:
            return EvaluationVector(metrics={"throughput": 0.0}, correctness=False)
        else:
            return EvaluationVector(metrics={"throughput": 750.0}, correctness=True)

    result = loop.run_variation_step(
        base_hypothesis="Optimize inner loop with loop unrolling",
        edit_fn=mock_edit,
        evaluate_fn=mock_eval,
        lineage=lineage,
        knowledge_base=kb,
        supervisor=sup,
    )

    assert result["success"] is True
    assert result["trials_conducted"] == 2  # Trial 1 failed and was repaired in Trial 2
    assert result["committed_version_id"] is not None
    assert lineage.head_id == result["committed_version_id"]
    
    head = lineage.get_head()
    assert head is not None
    assert head.vector.effective_metric("throughput") == 750.0

    # Lineage only has 2 committed versions (v0 and repaired v1)
    assert len(lineage.versions) == 2
    # Failed trial 1 is archived in trajectory memory
    assert len(lineage.rejected_attempts) == 1
    assert lineage.rejected_attempts[0].rejection_reason == "CORRECTNESS_FAILURE"


def test_avo_engine_agentic_variation_workflow():
    engine = AVOEngine()

    # Step 1: Initial baseline
    res1 = engine.run_iteration(
        hypothesis="Initial baseline",
        modification="Init code",
        evaluate_fn=lambda: {
            "correctness": True,
            "metrics": {"seq_4k": 800.0, "seq_8k": 850.0},
        },
    )
    assert res1["committed"] is True
    assert res1["current_head"] == res1["version_id"]

    # Step 2: Agentic variation with vector scoring
    def edit_fn(code: str, ctx: dict):
        return code + "\n// branchless", "Added branchless accumulator rescaling"

    def eval_fn(code: str) -> EvaluationVector:
        return EvaluationVector(
            metrics={"seq_4k": 880.0, "seq_8k": 930.0},
            correctness=True,
        )

    res2 = engine.run_agentic_variation(
        base_hypothesis="Branchless accumulator rescaling to remove warp divergence",
        edit_fn=edit_fn,
        evaluate_fn=eval_fn,
    )

    assert res2["success"] is True
    assert res2["committed_version_id"] is not None
    assert engine.lineage.head_id == res2["committed_version_id"]

    # Check Pareto frontier
    frontier = engine.lineage.get_pareto_frontier()
    assert len(frontier) >= 1

    # Check engine stats
    stats = engine.stats()
    assert stats["iteration_count"] == 2
    assert stats["lineage"]["total_committed"] == 2
    assert stats["knowledge_base"]["patterns"] >= 4
