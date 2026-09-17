"""Tests for Test-Driven Autonomous Reproduction Engine."""

import json
from pathlib import Path

from agent_workspace.reproduction.engine import ReproductionEngine
from agent_workspace.reproduction.gates import (
    PostFixVerificationGate,
    PreFixFailureGate,
)
from agent_workspace.reproduction.models import ReproductionStatus
from agent_workspace.reproduction.synthesizer import ReproductionSynthesizer
from agent_workspace.tools.builtins.reproduction_tool import reproduce_and_verify


def test_reproduction_synthesizer():
    synth = ReproductionSynthesizer()
    script = synth.synthesize(
        issue_description="Divide by zero in calc",
        test_body="assert 1 == 1",
    )
    assert "Standalone Autonomous Bug Reproduction Script" in script.code
    assert "Divide by zero in calc" in script.code
    assert "sys.exit(0)" in script.code
    assert "sys.exit(1)" in script.code


def test_pre_and_post_gates(tmp_path):
    pre_gate = PreFixFailureGate()
    post_gate = PostFixVerificationGate()

    # 1. Create a script that fails (assert False)
    failing_script = tmp_path / "fail_test.py"
    failing_script.write_text("import sys\nsys.exit(1)\n")

    # Pre-fix check should pass because the bug DOES reproduce
    pre_res = pre_gate.evaluate(failing_script, tmp_path)
    assert pre_res.passed
    assert pre_res.exit_code == 1

    # Post-fix check on failing script should FAIL
    post_res = post_gate.evaluate(failing_script, tmp_path)
    assert not post_res.passed

    # 2. Create a script that passes (sys.exit 0)
    passing_script = tmp_path / "pass_test.py"
    passing_script.write_text("import sys\nsys.exit(0)\n")

    # Pre-fix check on passing script should FAIL (bug did not reproduce)
    pre_res_pass = pre_gate.evaluate(passing_script, tmp_path)
    assert not pre_res_pass.passed
    assert "passed with exit code 0" in pre_res_pass.message

    # Post-fix check on passing script should PASS
    post_res_pass = post_gate.evaluate(passing_script, tmp_path)
    assert post_res_pass.passed


def test_reproduction_engine_lifecycle(tmp_path):
    # Mock module with a bug
    module_file = tmp_path / "mod.py"
    module_file.write_text("def solve(): return False\n")

    engine = ReproductionEngine()

    # Step 1: Prepare reproduction (test asserts solve() is True)
    test_code = f"import sys\nsys.path.insert(0, r'{tmp_path}')\nfrom mod import solve\nassert solve() is True\n"
    prep_report = engine.prepare_reproduction(
        issue_description="solve() returns False instead of True",
        test_body=test_code,
        workspace_dir=tmp_path,
    )
    assert prep_report.status == ReproductionStatus.PRE_FIX_FAILED
    assert prep_report.reproduction_script_path is not None

    # Step 2: Fix the bug in mod.py
    module_file.write_text("def solve(): return True\n")

    # Step 3: Verify solution
    verify_report = engine.verify_solution(
        reproduction_script_path=Path(prep_report.reproduction_script_path),
        workspace_dir=tmp_path,
        pre_fix_result=prep_report.pre_fix_result,
    )
    assert verify_report.is_verified
    assert verify_report.status == ReproductionStatus.VERIFIED_SOLVED


def test_reproduce_and_verify_tool(tmp_path):
    # Test tool 'prepare' phase with an assertion failure
    res_prep_str = reproduce_and_verify.invoke({
        "phase": "prepare",
        "issue_description": "String uppercase failure",
        "test_body": "assert 'hello'.upper() == 'WORLD'",
        "workspace_dir": str(tmp_path),
    })
    prep_data = json.loads(res_prep_str)
    assert prep_data["bug_reproduced"] is True
    assert prep_data["status"] == "pre_fix_failed"
    script_path = prep_data["reproduction_script"]

    # Now rewrite test script to pass and test 'verify' phase
    Path(script_path).write_text("import sys\nsys.exit(0)\n")

    res_ver_str = reproduce_and_verify.invoke({
        "phase": "verify",
        "script_path": script_path,
        "workspace_dir": str(tmp_path),
    })
    ver_data = json.loads(res_ver_str)
    assert ver_data["is_verified"] is True
    assert ver_data["reproduction_passed"] is True
