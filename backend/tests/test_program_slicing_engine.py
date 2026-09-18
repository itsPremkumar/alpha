"""Unit tests for AST Dynamic Program Slicing and Blast-Radius Engine."""

import pytest

from agent_workspace.coding.program_slicing_engine import (
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
