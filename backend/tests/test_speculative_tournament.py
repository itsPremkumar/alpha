"""Tests for Speculative Synthesis and Tournament Bake-Off Engine."""

from alpha.synthesis.speculative_tournament import (
    SpeculativeSynthesisEngine,
    ParetoScorer,
    PatchStrategy,
)


def test_speculative_candidates_generation():
    engine = SpeculativeSynthesisEngine()
    original = """
def compute_metrics(val):
    return val * 100
"""
    candidates = engine.generate_speculative_candidates("metrics.py", original)
    assert len(candidates) == 3
    strategies = {c.strategy for c in candidates}
    assert PatchStrategy.SURGICAL_GUARD in strategies
    assert PatchStrategy.IDIOMATIC_REFACTOR in strategies
    assert PatchStrategy.ALGORITHMIC_REWRITE in strategies


def test_tournament_bakeoff_and_pareto_selection():
    engine = SpeculativeSynthesisEngine()
    original = "def solve(): return 42"
    candidates = engine.generate_speculative_candidates("solution.py", original)

    # Runner simulating tests pass on surgical and refactor, fails on rewrite
    def mock_runner(code: str):
        if "algorithmic" in code:
            return 0, 5
        return 5, 0

    bakeoff = engine.run_tournament_bakeoff(candidates, mock_runner)
    assert len(bakeoff) == 3

    winner = engine.select_winner(bakeoff)
    assert winner is not None
    assert winner.pass_rate == 1.0
    assert winner.pareto_score > 0.5
