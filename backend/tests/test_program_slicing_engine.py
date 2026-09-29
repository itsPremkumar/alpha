"""Unit tests for AST Dynamic Program Slicing and Blast-Radius Engine."""

import pytest

from alpha.coding.program_slicing_engine import (
    ProgramSlicingEngine,
    compute_program_slice,
)


SAMPLE_CODE = """def process_data(value):
    unrelated_flag = False
    base_factor = 10
    multiplier = 2
    if value > 0:
        intermediate = value * multiplier
        final_result = intermediate + base_factor
    else:
        final_result = base_factor
    unrelated_counter = 42
    assert final_result > 0
    return final_result
"""


def test_pdg_construction():
    engine = ProgramSlicingEngine()
    pdg = engine.build_pdg(SAMPLE_CODE)

    assert len(pdg.statements) > 5
    # Line 2: unrelated_flag = False
    assert 2 in pdg.statements
    assert "unrelated_flag" in pdg.statements[2].defined_vars

    # Line 3: base_factor = 10
    assert 3 in pdg.statements
    assert "base_factor" in pdg.statements[3].defined_vars

    # Line 6: intermediate = value * multiplier
    assert 6 in pdg.statements
    assert "multiplier" in pdg.statements[6].used_vars
    assert "intermediate" in pdg.statements[6].defined_vars


def test_backward_slice_isolates_causal_chain():
    engine = ProgramSlicingEngine()

    # Target line 11: assert final_result > 0
    res = engine.backward_slice(SAMPLE_CODE, target_line=11, target_variable="final_result")

    assert res["slicing_mode"] == "backward"
    assert res["target_line"] == 11
    slice_lines = res["slice_lines"]

    # Causal statements influencing final_result:
    # Both branch definitions (line 7 in if, line 9 in else) must be present in slice
    assert 11 in slice_lines
    assert 7 in slice_lines and 9 in slice_lines
    assert 3 in slice_lines  # base_factor
    assert 5 in slice_lines  # if condition

    # Unrelated lines must NOT be in the slice
    assert 2 not in slice_lines  # unrelated_flag
    assert 10 not in slice_lines  # unrelated_counter


def test_forward_slice_blast_radius():
    engine = ProgramSlicingEngine()

    # Planned edit at line 4: multiplier = 2
    res = engine.forward_slice(SAMPLE_CODE, target_line=4, target_variable="multiplier")

    assert res["slicing_mode"] == "forward"
    impacted = res["impacted_lines"]

    # Multiplier influences line 6 (intermediate), which influences line 7 (final_result),
    # which influences line 11 (assert) and line 12 (return)
    assert 4 in impacted
    assert 6 in impacted
    assert 7 in impacted

    # Unrelated statements must not be impacted
    assert 2 not in impacted  # unrelated_flag
    assert 10 not in impacted  # unrelated_counter

    assert res["blast_radius_risk"] in ("Low", "Medium", "High", "Critical")


def test_empty_and_syntax_error_handling():
    engine = ProgramSlicingEngine()

    # Empty code
    empty_res = engine.backward_slice("", target_line=1)
    assert empty_res["slicing_mode"] == "backward"
    assert empty_res["slice_lines"] == []

    # Syntax error
    broken_code = "def broken(:\n    pass\n"
    broken_res = engine.backward_slice(broken_code, target_line=1)
    assert broken_res["slicing_mode"] == "backward"


def test_compute_program_slice_tool(tmp_path):
    # Test backward slice tool invocation
    res_b = compute_program_slice.invoke({
        "source_code": SAMPLE_CODE,
        "slicing_mode": "backward",
        "target_line": 11,
        "target_variable": "final_result",
    })
    assert res_b["success"] is True
    assert "slice_lines" in res_b["data"]

    # Test forward slice tool invocation via temporary file
    test_file = tmp_path / "target.py"
    test_file.write_text(SAMPLE_CODE, encoding="utf-8")

    res_f = compute_program_slice.invoke({
        "file_path": str(test_file),
        "slicing_mode": "forward",
        "target_line": 3,
    })
    assert res_f["success"] is True
    assert "impacted_lines" in res_f["data"]
    assert "blast_radius_risk" in res_f["data"]


# --- target_variable must locate its own statement -------------------------
#
# Found by running the tool, not by reading it. Asked to slice
# ``target_variable="main"`` with no usable ``target_line`` (the default is 1),
# the engine resolved the start line from ``target_line`` alone, so it sliced
# the *first* function in the file and still reported ``success: True``. The
# only three existing tests that pass ``target_variable`` all pass a matching
# ``target_line`` alongside it, so the by-name path was never exercised.


TWO_FUNCTION_CODE = """def helper():
    return 1


def main():
    return helper()
"""

# Line map for the fixture above, asserted so a reformat cannot silently move
# the target and make this file test the wrong thing:
#   1 `def helper():`   2 `    return 1`   3 (blank)   4 (blank)
#   5 `def main():`     6 `    return helper()`
HELPER_DEF_LINE = 1
MAIN_DEF_LINE = 5
MAIN_CALL_LINE = 6


def test_two_function_fixture_line_map_is_what_the_tests_assume():
    """Guard the line numbers the resolution assertions below depend on."""
    lines = TWO_FUNCTION_CODE.splitlines()
    assert lines[HELPER_DEF_LINE - 1] == "def helper():"
    assert lines[MAIN_DEF_LINE - 1] == "def main():"
    assert lines[MAIN_CALL_LINE - 1] == "    return helper()"


def test_backward_slice_by_variable_name_slices_that_variable():
    """A named variable with no line must resolve to its own definition."""
    engine = ProgramSlicingEngine()

    res = engine.backward_slice(TWO_FUNCTION_CODE, target_line=1, target_variable="main")

    # `main` is defined on line 5. The old code anchored to line 1 (`helper`).
    assert res["resolved_line"] == MAIN_DEF_LINE
    assert MAIN_DEF_LINE in res["slice_lines"]


def test_backward_slice_by_variable_name_does_not_slice_the_wrong_function():
    """The regression itself: the old code answered about `helper`."""
    engine = ProgramSlicingEngine()

    res = engine.backward_slice(TWO_FUNCTION_CODE, target_line=1, target_variable="main")

    assert res["resolved_line"] != HELPER_DEF_LINE
    assert "def helper():" not in res["slice_code"]


def test_compute_program_slice_does_not_report_success_for_a_wrong_slice():
    """The tool must not answer for `helper` when asked about `main`."""
    res = compute_program_slice.invoke({
        "source_code": TWO_FUNCTION_CODE,
        "slicing_mode": "backward",
        "target_variable": "main",
    })

    assert res["success"] is True
    assert "def helper():" not in res["data"]["slice_code"].split("def main():")[0]


def test_unknown_variable_is_refused_rather_than_substituted():
    """An absent name is disclosed; it is never answered with a nearby line."""
    engine = ProgramSlicingEngine()

    res = engine.backward_slice(TWO_FUNCTION_CODE, target_line=1, target_variable="does_not_exist")

    assert res["slice_lines"] == []
    assert "was not found" in res["summary"]


def test_unknown_variable_is_not_reported_as_success():
    """The caller must be able to distinguish 'no slice' from 'wrong slice'."""
    res = compute_program_slice.invoke({
        "source_code": TWO_FUNCTION_CODE,
        "slicing_mode": "backward",
        "target_variable": "does_not_exist",
    })

    assert res["success"] is False


def test_forward_slice_by_variable_name_resolves_the_variable():
    """Blast radius obeys the same resolution rule as the backward slice."""
    res = compute_program_slice.invoke({
        "source_code": TWO_FUNCTION_CODE,
        "slicing_mode": "forward",
        "target_variable": "helper",
    })

    assert res["success"] is True
    # `main` calls `helper` on line 6, so that call site is inside the radius.
    assert MAIN_CALL_LINE in res["data"]["impacted_lines"]
