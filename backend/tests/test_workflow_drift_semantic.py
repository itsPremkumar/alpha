"""Semantic drift detection: character n-gram Jaccard similarity.

These tests pin the ``measure_semantic_similarity`` function that complements
the lexical term-overlap path. It is deterministic and model-free — an operator
can recompute every number by hand.
"""

from __future__ import annotations

from alpha.workflow.drift import measure_semantic_similarity


def test_identical_texts_score_one():
    text = "the quick brown fox jumps over the lazy dog"
    assert measure_semantic_similarity(text, text) == 1.0


def test_completely_different_texts_score_zero():
    text_a = "aaaa bbbb cccc"
    text_b = "xxxx yyyy zzzz"
    assert measure_semantic_similarity(text_a, text_b) == 0.0


def test_morphological_variants_score_nonzero():
    """'running' and 'run' share character n-grams despite different tokens."""
    goal = "run the test suite"
    output = "running the tests now"
    score = measure_semantic_similarity(output, goal)
    assert score > 0.0
    assert score < 1.0


def test_empty_text_scores_zero():
    assert measure_semantic_similarity("", "some goal") == 0.0
    assert measure_semantic_similarity("some output", "") == 0.0


def test_short_text_scores_zero():
    """Text shorter than NGRAM_SIZE has no n-grams to compare."""
    assert measure_semantic_similarity("ab", "some goal") == 0.0


def test_case_insensitive():
    goal = "Run The Test"
    output = "run the test"
    assert measure_semantic_similarity(output, goal) == 1.0


def test_whitespace_normalized():
    goal = "run   the   test"
    output = "run the test"
    assert measure_semantic_similarity(output, goal) == 1.0


def test_partial_overlap_scores_between_zero_and_one():
    goal = "implement the feature and verify it works"
    output = "implement the feature but skip verification"
    score = measure_semantic_similarity(output, goal)
    assert 0.0 < score < 1.0


def test_paraphrase_scores_higher_than_unrelated():
    goal = "run the test suite"
    paraphrase = "running the tests now"
    unrelated = "update the documentation page"
    paraphrase_score = measure_semantic_similarity(paraphrase, goal)
    unrelated_score = measure_semantic_similarity(unrelated, goal)
    assert paraphrase_score > unrelated_score
