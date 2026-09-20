"""Tests for Automated Program Repair (APR), Ochiai SBFL, and Test Assertion Immutability."""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from alpha.selfrepair.sbfl import OchiaiFaultLocalizer
from alpha.selfrepair.assertion_guard import TestAssertionImmutabilityGuard
from alpha.selfrepair.surgical_apr import SurgicalProgramRepairEngine
from alpha.tools.builtins.code_agentic_core import run_surgical_program_repair


def test_ochiai_sbfl_formula_computation():
    localizer = OchiaiFaultLocalizer()
    # If a statement was executed in 4 failing tests and 0 passing tests, with 4 total failing tests:
    # Ochiai = 4 / sqrt(4 * (4 + 0)) = 4 / 4 = 1.0 (Highest possible suspiciousness)
    score = localizer.calculate_ochiai_score(failing_hits=4, passing_hits=0, total_failing=4)
    assert pytest.approx(score, 0.001) == 1.0

    # If executed in 0 failing tests:
    score_zero = localizer.calculate_ochiai_score(failing_hits=0, passing_hits=10, total_failing=4)
    assert score_zero == 0.0

    # Intermediate suspiciousness
    score_mid = localizer.calculate_ochiai_score(failing_hits=2, passing_hits=2, total_failing=4)
    # 2 / sqrt(4 * 4) = 2 / 4 = 0.5
    assert pytest.approx(score_mid, 0.001) == 0.5


def test_ochiai_traceback_parsing():
    sample_traceback = """
Traceback (most recent call last):
  File "C:\\Users\\User\\project\\src\\calculator.py", line 42, in divide
    return a / b
ZeroDivisionError: division by zero
"""
    localizer = OchiaiFaultLocalizer()
    locs = localizer.parse_traceback_output(sample_traceback)

    assert len(locs) >= 1
    top_loc = locs[0]
    assert "calculator.py" in top_loc.file_path
    assert top_loc.line_number == 42
    assert top_loc.function_name == "divide"
    assert top_loc.error_type == "ZeroDivisionError"
    assert top_loc.score > 0.0


def test_test_assertion_immutability_guard():
    guard = TestAssertionImmutabilityGuard()

    # 1. Non-test files are unaffected
    res_prod = guard.validate_patch("src/service.py", "assert True", "pass")
    assert res_prod.allowed is True

    # 2. Candidate patch modifies production logic without touching test assertions -> Allowed
    orig_test = """
def test_calc():
    result = add(2, 3)
    assert result == 5
    assert result > 0
"""
    # Adding a new assertion is allowed
    patched_test_add = """
def test_calc():
    result = add(2, 3)
    assert result == 5
    assert result > 0
    assert result < 10
"""
    res_add = guard.validate_patch("tests/test_calc.py", orig_test, patched_test_add)
    assert res_add.allowed is True

    # 3. Weakening or deleting an existing test assertion -> Strictly REJECTED!
    patched_test_delete = """
def test_calc():
    result = add(2, 3)
    # assert result == 5 deleted!
    assert result > 0
"""
    res_del = guard.validate_patch("tests/test_calc.py", orig_test, patched_test_delete)
    assert res_del.allowed is False
    assert "Test assertion immutability violated" in res_del.reason
    assert "result == 5" in res_del.deleted_assertions[0]

    # 4. Modifying assertion expression -> Strictly REJECTED!
    patched_test_tamper = """
def test_calc():
    result = add(2, 3)
    assert result == 999  # tampered
    assert result > 0
"""
    res_tamper = guard.validate_patch("tests/test_calc.py", orig_test, patched_test_tamper)
    assert res_tamper.allowed is False


def test_surgical_apr_zero_division_repair():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        buggy_file = tmp_path / "math_ops.py"
        buggy_file.write_text(
            "def calculate_ratio(num, denom):\n    return num / denom\n",
            encoding="utf-8",
        )

        traceback_output = f"""
Traceback (most recent call last):
  File "{buggy_file}", line 2, in calculate_ratio
    return num / denom
ZeroDivisionError: division by zero
"""
        engine = SurgicalProgramRepairEngine(workspace_root=tmp_path)
        repair_res = engine.attempt_surgical_repair(traceback_output)

        assert repair_res.success is True
        assert repair_res.applied_patch is not None
        assert "ZeroDivision" in repair_res.applied_patch.description or "zero-division" in repair_res.applied_patch.description

        # Verify fixed file content
        fixed_content = buggy_file.read_text(encoding="utf-8")
        assert "denom != 0" in fixed_content or "if denom == 0" in fixed_content


def test_assertion_immutability_duplicate_deletion():
    guard = TestAssertionImmutabilityGuard()
    orig_code = """
def test_duplicates():
    assert check_state() == True
    do_something()
    assert check_state() == True
"""
    # Attacker tries to remove one duplicate and add dummy "assert True" so count matches
    tampered_code = """
def test_duplicates():
    assert check_state() == True
    do_something()
    assert True
"""
    res = guard.validate_patch("tests/test_dup.py", orig_code, tampered_code)
    assert res.allowed is False
    assert "Test assertion immutability violated" in res.reason


def test_surgical_apr_dict_lookup_double_quotes():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        buggy_file = tmp_path / "config_parser.py"
        buggy_file.write_text(
            'def get_setting(settings):\n    return settings["database_url"]\n',
            encoding="utf-8",
        )
        traceback_output = f"""
Traceback (most recent call last):
  File "{buggy_file}", line 2, in get_setting
    return settings["database_url"]
KeyError: 'database_url'
"""
        engine = SurgicalProgramRepairEngine(workspace_root=tmp_path)
        res = engine.attempt_surgical_repair(traceback_output)

        assert res.success is True
        assert res.applied_patch is not None
        fixed_code = buggy_file.read_text(encoding="utf-8")
        assert 'settings.get("database_url")' in fixed_code


def test_surgical_apr_assignment_zero_division():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        buggy_file = tmp_path / "stats.py"
        buggy_file.write_text(
            "def compute_stat(val, count):\n    rate = val / count\n    return rate\n",
            encoding="utf-8",
        )
        traceback_output = f"""
Traceback (most recent call last):
  File "{buggy_file}", line 2, in compute_stat
    rate = val / count
ZeroDivisionError: division by zero
"""
        engine = SurgicalProgramRepairEngine(workspace_root=tmp_path)
        res = engine.attempt_surgical_repair(traceback_output)

        assert res.success is True
        fixed_code = buggy_file.read_text(encoding="utf-8")
        assert "count != 0" in fixed_code


def test_run_surgical_program_repair_tool():
    import json
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        buggy_file = tmp_path / "math_fn.py"
        buggy_file.write_text(
            "def divide(a, b):\n    return a / b\n",
            encoding="utf-8",
        )
        traceback_output = f"""
Traceback (most recent call last):
  File "{buggy_file}", line 2, in divide
    return a / b
ZeroDivisionError: division by zero
"""
        raw_res = run_surgical_program_repair.invoke({
            "test_output": traceback_output,
            "root_path": str(tmp_path),
            "total_passing_tests": 1,
        })
        res_data = json.loads(raw_res)
        assert res_data["success"] is True
        assert res_data["applied_patch"] is not None
