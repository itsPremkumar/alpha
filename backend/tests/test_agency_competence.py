
from agent_workspace.agency import (
    CompetenceTracker,
    CuriosityScorer,
    MotivationArbiter,
)


def test_wilson_competence_small_vs_large_sample():
    tracker = CompetenceTracker()

    # Case 1: Single attempt with success (1/1)
    # Empirical = 100%, but Wilson lower bound is conservative (~0.20)
    score_small = tracker.record_attempt("concurrency_locks", success=True)
    assert score_small < 0.50  # Conservative lower bound protects against overconfidence

    # Case 2: 20 attempts with 19 successes (19/20)
    # Empirical = 95%, Wilson lower bound should be high (~0.75+)
    for _ in range(19):
        tracker.record_attempt("concurrency_locks", success=True)
    tracker.record_attempt("concurrency_locks", success=False)

    score_large = tracker.get_competence("concurrency_locks")
    assert score_large > 0.70
    assert score_large > score_small


def test_curiosity_scorer_novelty_and_prediction_error():
    scorer = CuriosityScorer()

    situation = {"state": "database_deadlock", "retry_count": 0}

    # First encounter -> maximum novelty (1.0)
    score1 = scorer.score(situation, predicted_outcome=1.0, actual_outcome=0.0)
    assert score1.novelty == 1.0
    assert score1.prediction_error == 1.0
    assert score1.score == 1.0

    # Second encounter -> familiarity increases, novelty decreases
    score2 = scorer.score(situation, predicted_outcome=0.0, actual_outcome=0.0)
    assert score2.novelty < 1.0
    assert score2.prediction_error == 0.0
    assert score2.score < score1.score


def test_motivation_arbiter():
    arbiter = MotivationArbiter()

    # High user priority -> DIRECTED_EXECUTION
    mode1, val1 = arbiter.arbitrate(curiosity_score=0.4, competence_score=0.5, user_priority=1.0)
    assert mode1 == "DIRECTED_EXECUTION"
    assert val1 > 0.4

    # Low user priority, high curiosity -> AUTONOMOUS_EXPLORATION
    mode2, val2 = arbiter.arbitrate(curiosity_score=0.9, competence_score=0.3, user_priority=0.2)
    assert mode2 == "AUTONOMOUS_EXPLORATION"
