"""Reproduction and regression verification gates."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from agent_workspace.reproduction.models import GateResult

logger = logging.getLogger(__name__)


class PreFixFailureGate:
    """Verifies that the reproduction script fails prior to applying any fix, proving the bug reproduces."""

    def evaluate(
        self,
        script_path: Path,
        workspace_dir: Path,
        python_executable: str | None = None,
        timeout: int = 30,
    ) -> GateResult:
        py_bin = python_executable or sys.executable
        start_t = time.time()

        try:
            res = subprocess.run(
                [py_bin, str(script_path)],
                cwd=str(workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            duration = time.time() - start_t

            # Pre-fix check: We EXPECT non-zero exit code (bug reproduced)
            if res.returncode != 0:
                return GateResult(
                    passed=True,
                    exit_code=res.returncode,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    message=f"Pre-fix check passed: Bug reproduced with exit code {res.returncode}.",
                    duration_seconds=round(duration, 2),
                )
            else:
                return GateResult(
                    passed=False,
                    exit_code=0,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    message="Pre-fix check failed: Reproduction script passed with exit code 0! Bug did not reproduce.",
                    duration_seconds=round(duration, 2),
                )
        except Exception as e:
            duration = time.time() - start_t
            return GateResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                message=f"Pre-fix execution error: {e}",
                duration_seconds=round(duration, 2),
            )


class PostFixVerificationGate:
    """Verifies that the reproduction script passes cleanly (exit 0) after fix is applied."""

    def evaluate(
        self,
        script_path: Path,
        workspace_dir: Path,
        python_executable: str | None = None,
        timeout: int = 30,
    ) -> GateResult:
        py_bin = python_executable or sys.executable
        start_t = time.time()

        try:
            res = subprocess.run(
                [py_bin, str(script_path)],
                cwd=str(workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            duration = time.time() - start_t

            # Post-fix check: We EXPECT exit code 0 (fix works)
            if res.returncode == 0:
                return GateResult(
                    passed=True,
                    exit_code=0,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    message="Post-fix verification passed: Reproduction script succeeded with exit code 0.",
                    duration_seconds=round(duration, 2),
                )
            else:
                return GateResult(
                    passed=False,
                    exit_code=res.returncode,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    message=f"Post-fix verification failed: Script exited with code {res.returncode}.",
                    duration_seconds=round(duration, 2),
                )
        except Exception as e:
            duration = time.time() - start_t
            return GateResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                message=f"Post-fix execution error: {e}",
                duration_seconds=round(duration, 2),
            )


class RegressionSafetyGuard:
    """Executes adjacent regression tests to confirm no secondary regressions were introduced."""

    def evaluate(
        self,
        test_command: str,
        workspace_dir: Path,
        timeout: int = 60,
    ) -> GateResult:
        start_t = time.time()
        try:
            res = subprocess.run(
                test_command,
                cwd=str(workspace_dir),
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            duration = time.time() - start_t

            if res.returncode == 0:
                return GateResult(
                    passed=True,
                    exit_code=0,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    message="Regression safety guard passed: All adjacent test suites green.",
                    duration_seconds=round(duration, 2),
                )
            else:
                return GateResult(
                    passed=False,
                    exit_code=res.returncode,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    message=f"Regression detected: Test command failed with exit code {res.returncode}.",
                    duration_seconds=round(duration, 2),
                )
        except Exception as e:
            duration = time.time() - start_t
            return GateResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                message=f"Regression test execution error: {e}",
                duration_seconds=round(duration, 2),
            )
