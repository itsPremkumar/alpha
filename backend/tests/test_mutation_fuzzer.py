"""Tests for Mutation Testing and Invariant Fuzzing Engine."""

from alpha.testing.mutation_fuzzer import (
    MutationTestingEngine,
    PropertyInvariantFuzzer,
    MutantType,
)


def test_mutation_testing_mutant_generation_and_kill_score():
    engine = MutationTestingEngine()
    sample_code = """
def is_valid_range(x):
    if x < 10:
        return True
    return False
"""
    mutants = engine.generate_mutants(sample_code)
    assert len(mutants) >= 1
    assert any(m.mutant_type == MutantType.COMPARISON_INVERSION for m in mutants)

    # Test runner that fails when mutant is introduced (i.e. kills mutant)
    def strict_runner(code: str) -> bool:
        # Fails if '<' was mutated to '<='
        return "<=" not in code

    report = engine.run_mutation_audit(sample_code, strict_runner)
    assert report.total_mutants >= 1
    assert report.killed_mutants >= 1
    assert report.kill_score > 0.0


def test_zero_mutants_yields_null_kill_score_with_disclosure():
    """No mutation sites -> no tests run -> kill_score must be None, never 1.0."""
    engine = MutationTestingEngine()
    report = engine.run_mutation_audit("value = 1\n", lambda code: True)
    assert report.total_mutants == 0
    assert report.kill_score is None
    assert report.disclosure is not None
    assert "no mutants" in report.disclosure


def test_all_runner_errors_yield_null_kill_score_with_disclosure():
    """If not a single test run executed, kill_score must be None, never 1.0."""
    engine = MutationTestingEngine()
    sample_code = "def f(x):\n    if x < 10:\n        return True\n    return False\n"

    def crashing_runner(code: str) -> bool:
        raise RuntimeError("test harness crashed")

    report = engine.run_mutation_audit(sample_code, crashing_runner)
    assert report.total_mutants >= 1
    assert report.killed_mutants == 0
    assert report.survived_mutants == 0
    assert report.kill_score is None
    assert report.disclosure is not None
    assert "tests not executed" in report.disclosure


def test_partial_runner_errors_are_disclosed_and_excluded():
    """Errored runs are excluded from the score and the exclusion is disclosed."""
    engine = MutationTestingEngine()
    sample_code = "def f(x):\n    if x < 10:\n        return True\n    return False\n"

    def flaky_runner(code: str) -> bool:
        if "<=" in code:
            raise RuntimeError("flaky infra")
        return True  # other mutants pass -> survive

    report = engine.run_mutation_audit(sample_code, flaky_runner)
    executed = report.killed_mutants + report.survived_mutants
    assert executed >= 1
    assert report.kill_score == round(report.killed_mutants / executed, 3)
    assert report.disclosure is not None
    assert "excluded from kill_score" in report.disclosure


def test_property_invariant_fuzzing():
    # Mathematical invariant: abs(x) >= 0 for all integers
    def target_abs(x: int) -> int:
        return abs(x)

    def invariant_non_negative(val: int, res: int) -> bool:
        return res >= 0

    import random
    report = PropertyInvariantFuzzer.fuzz_invariants(
        fn=target_abs,
        invariant_check=invariant_non_negative,
        input_generator=lambda: random.randint(-1000, 1000),
        trials=40,
    )
    assert report["total_trials"] == 40
    assert report["passed_trials"] == 40
    assert report["success_rate"] == 1.0
