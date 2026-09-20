"""Tests for AST Safety Invariant Checker and Ephemeral Canary Sandboxing."""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from alpha.safety.canary_sandbox import (
    ASTSafetyInvariantChecker,
    EphemeralCanarySandbox,
)


def test_ast_safety_checker_banned_calls():
    checker = ASTSafetyInvariantChecker()

    # 1. Clean code -> Passed
    clean_code = """
def add(x: int, y: int) -> int:
    return x + y
"""
    res_clean = checker.check_source_code(clean_code)
    assert res_clean.passed is True
    assert len(res_clean.violations) == 0

    # 2. Forbidden dynamic eval/exec -> Rejected
    eval_code = "eval('__import__(\"os\")')"
    res_eval = checker.check_source_code(eval_code)
    assert res_eval.passed is False
    assert any(v.rule_name == "BANNED_DYNAMIC_EXECUTION" for v in res_eval.violations)

    # 3. Forbidden os.system call -> Rejected
    os_sys_code = """
import os
def dangerous():
    os.system("rm -rf /")
"""
    res_os = checker.check_source_code(os_sys_code)
    assert res_os.passed is False
    assert any("os.system" in v.message for v in res_os.violations)

    # 4. Forbidden module import -> Rejected
    ctypes_code = "import ctypes\nctypes.CDLL('libc.so')"
    res_ctypes = checker.check_source_code(ctypes_code)
    assert res_ctypes.passed is False
    assert any(v.rule_name == "BANNED_MODULE_IMPORT" for v in res_ctypes.violations)


def test_ast_safety_immutable_files_protection():
    checker = ASTSafetyInvariantChecker()
    res = checker.check_source_code("x = 1", file_path="backend/core/security.py")
    assert res.passed is False
    assert any(v.rule_name == "IMMUTABLE_FILE_VIOLATION" for v in res.violations)


def test_canary_sandbox_execution_and_rollback():
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_root = Path(tmp_dir)
        sandbox = EphemeralCanarySandbox(workspace_root=workspace_root)

        # 1. Invariant violation caught before execution
        bad_files = {"evil.py": "eval('2+2')"}
        res_bad = sandbox.run_canary_test(bad_files, test_command="python -m unittest")
        assert res_bad.success is False
        assert res_bad.rolled_back is True
        assert len(res_bad.safety_violations) > 0

        # 2. Safe execution passing in canary
        safe_files = {
            "calc.py": "def add(a, b): return a + b\n",
            "test_calc.py": "from calc import add\nassert add(1, 2) == 3\n",
        }
        res_good = sandbox.run_canary_test(
            safe_files,
            test_command="python test_calc.py",
        )
        assert res_good.success is True
        assert res_good.exit_code == 0
        assert res_good.rolled_back is False

        # 3. Failing test executes rollback cleanly
        failing_files = {
            "calc.py": "def add(a, b): return 0\n",
            "test_calc.py": "from calc import add\nassert add(1, 2) == 3\n",
        }
        res_fail = sandbox.run_canary_test(
            failing_files,
            test_command="python test_calc.py",
        )
        assert res_fail.success is False
        assert res_fail.rolled_back is True
        # Verify workspace remains completely untouched
        assert not (workspace_root / "calc.py").exists()
