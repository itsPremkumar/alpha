"""Unit tests for Differential Invariant Synthesis and Regression Oracle."""

import pytest

from agent_workspace.testing.differential_invariant_fuzzer import (
    BoundaryValueGenerator,
    DifferentialInvariantFuzzer,
    run_differential_regression_oracle,
)


def test_boundary_generator():
    gen = BoundaryValueGenerator(seed=123)
    inputs = gen.generate_random_inputs(["x", "y"], {"x": "int", "y": "str"}, num_samples=15)
    assert len(inputs) == 15
    assert "x" in inputs[0] and "y" in inputs[0]
    assert isinstance(inputs[0]["x"], int)
    assert isinstance(inputs[0]["y"], str)


def test_differential_clean_parity():
    # Baseline and modified have identical correct behavior
    baseline = """def add_one(x):\n    return x + 1\n"""
    modified = """def add_one(x):\n    return 1 + x\n"""

    fuzzer = DifferentialInvariantFuzzer()
    res = fuzzer.evaluate_differential(
        baseline_code=baseline,
        modified_code=modified,
        entrypoint_function="add_one",
        input_schema={"x": "int"},
        num_trials=25,
    )

    assert res["success"] is True
    assert res["consistency_score"] == 1.0
    assert res["regressions_detected_count"] == 0


def test_differential_verifies_bug_fix():
    # Baseline has ZeroDivisionError bug when divisor is 0
    baseline = """def safe_div(num, den):\n    return num // den\n"""
    # Modified fixes the zero division bug
    modified = """def safe_div(num, den):\n    if den == 0:\n        return 0\n    return num // den\n"""

    fuzzer = DifferentialInvariantFuzzer()
    res = fuzzer.evaluate_differential(
        baseline_code=baseline,
        modified_code=modified,
        entrypoint_function="safe_div",
        input_schema={"num": "int", "den": "int"},
        num_trials=20,
        bug_inducing_inputs=[{"num": 10, "den": 0}],
    )

    assert res["success"] is True
    assert res["bug_fixes_verified_count"] >= 1
    # Unmutated non-zero inputs must remain consistent
    assert res["consistency_score"] == 1.0
    assert res["regressions_detected_count"] == 0


def test_differential_detects_regression():
    # Baseline works on all numbers
    baseline = """def absolute_val(x):\n    return abs(x)\n"""
    # Modified accidentally breaks for negative numbers
    modified = """def absolute_val(x):\n    if x < 0:\n        raise ValueError('Negative not supported')\n    return x\n"""

    fuzzer = DifferentialInvariantFuzzer()
    res = fuzzer.evaluate_differential(
        baseline_code=baseline,
        modified_code=modified,
        entrypoint_function="absolute_val",
        input_schema={"x": "int"},
        num_trials=30,
    )

    # Must detect regression because baseline succeeded on negative inputs but modified failed
    assert res["success"] is False
    assert res["regressions_detected_count"] > 0
    assert res["consistency_score"] < 1.0


def test_run_differential_regression_oracle_tool():
    baseline = """def double(x):\n    return x * 2\n"""
    modified = """def double(x):\n    return x + x\n"""

    res = run_differential_regression_oracle.invoke({
        "baseline_code": baseline,
        "modified_code": modified,
        "entrypoint_function": "double",
        "input_schema": {"x": "int"},
        "num_trials": 15,
    })

    assert isinstance(res, dict)
    assert res["success"] is True
    assert "consistency_score" in res["data"]
    assert res["data"]["consistency_score"] == 1.0
