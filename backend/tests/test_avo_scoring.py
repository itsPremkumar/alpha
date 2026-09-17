
from agent_workspace.avo.scoring import EvaluationVector


def test_evaluation_vector_correctness_gating():
    # Candidate with high throughput but correctness=False MUST have zero effective score
    vec_failed = EvaluationVector(
        metrics={"seq_4096": 1500.0, "seq_8192": 1600.0},
        correctness=False,
    )
    assert vec_failed.effective_metric("seq_4096") == 0.0
    assert vec_failed.effective_metric("seq_8192") == 0.0
    assert vec_failed.geometric_mean() == 0.0
    assert vec_failed.arithmetic_mean() == 0.0

    # Correct candidate maintains full metrics
    vec_ok = EvaluationVector(
        metrics={"seq_4096": 1500.0, "seq_8192": 1600.0},
        correctness=True,
    )
    assert vec_ok.effective_metric("seq_4096") == 1500.0
    assert vec_ok.effective_metric("seq_8192") == 1600.0
    assert vec_ok.geometric_mean() > 1540.0


def test_evaluation_vector_geometric_mean():
    vec = EvaluationVector(
        metrics={"c1": 100.0, "c2": 400.0},
        correctness=True,
    )
    # geomean(100, 400) = sqrt(40000) = 200.0
    assert vec.geometric_mean() == 200.0
    assert vec.arithmetic_mean() == 250.0


def test_evaluation_vector_pareto_dominance():
    base = EvaluationVector(
        metrics={"c1": 100.0, "c2": 200.0},
        correctness=True,
    )
    improved_both = EvaluationVector(
        metrics={"c1": 110.0, "c2": 220.0},
        correctness=True,
    )
    improved_one = EvaluationVector(
        metrics={"c1": 100.0, "c2": 210.0},
        correctness=True,
    )
    tradeoff = EvaluationVector(
        metrics={"c1": 90.0, "c2": 300.0},
        correctness=True,
    )
    failed = EvaluationVector(
        metrics={"c1": 999.0, "c2": 999.0},
        correctness=False,
    )

    assert improved_both.dominates(base) is True
    assert improved_one.dominates(base) is True
    assert base.dominates(improved_both) is False
    assert tradeoff.dominates(base) is False
    assert base.dominates(tradeoff) is False
    assert base.dominates(failed) is True
    assert failed.dominates(base) is False


def test_rhae_action_efficiency_score():
    vec = EvaluationVector(correctness=True)
    
    # 100% completion with equal or fewer actions than baseline -> 100.00 RHAE
    score_full = vec.rhae_score(
        total_levels=183,
        levels_solved=183,
        baseline_actions=7542,
        agent_actions=6624,  # 12% fewer actions, matching NVIDIA AVO ARC-AGI-3 run
    )
    assert score_full == 100.0

    # Partial solve: 50% levels solved
    score_half = vec.rhae_score(
        total_levels=100,
        levels_solved=50,
        baseline_actions=1000,
        agent_actions=1000,
    )
    assert score_half == 50.0

    # Solved all levels but took 2x more actions than baseline
    score_slow = vec.rhae_score(
        total_levels=100,
        levels_solved=100,
        baseline_actions=1000,
        agent_actions=2000,
    )
    assert score_slow == 50.0
