"""ReproductionEngine: Orchestrates test-driven bug reproduction, verification, and regression prevention."""

from __future__ import annotations

import logging
from pathlib import Path

from alpha.reproduction.gates import (
    PostFixVerificationGate,
    PreFixFailureGate,
    RegressionSafetyGuard,
)
from alpha.reproduction.models import (
    GateResult,
    ReproductionReport,
    ReproductionStatus,
)
from alpha.reproduction.synthesizer import ReproductionSynthesizer

logger = logging.getLogger(__name__)


class ReproductionEngine:
    """End-to-end Test-Driven Autonomous Reproduction lifecycle coordinator."""

    def __init__(
        self,
        python_executable: str | None = None,
    ):
        self.python_executable = python_executable
        self.synthesizer = ReproductionSynthesizer()
        self.pre_fix_gate = PreFixFailureGate()
        self.post_fix_gate = PostFixVerificationGate()
        self.regression_guard = RegressionSafetyGuard()

    def prepare_reproduction(
        self,
        issue_description: str,
        test_body: str,
        workspace_dir: Path,
        script_name: str = "reproduce_issue.py",
        timeout: int = 30,
    ) -> ReproductionReport:
        """Step 1: Synthesize reproduction script and verify that it fails prior to code fix."""
        script = self.synthesizer.synthesize(
            issue_description=issue_description,
            test_body=test_body,
            script_name=script_name,
        )
        script_path = self.synthesizer.write_script(script, workspace_dir)

        pre_res = self.pre_fix_gate.evaluate(
            script_path=script_path,
            workspace_dir=workspace_dir,
            python_executable=self.python_executable,
            timeout=timeout,
        )

        if not pre_res.passed:
            return ReproductionReport(
                status=ReproductionStatus.PRE_FIX_PASSED,
                pre_fix_result=pre_res,
                reproduction_script_path=str(script_path),
                diagnostics="Error: Reproduction script did NOT reproduce the issue. It passed without any code changes.",
            )

        return ReproductionReport(
            status=ReproductionStatus.PRE_FIX_FAILED,
            pre_fix_result=pre_res,
            reproduction_script_path=str(script_path),
            diagnostics="Success: Bug successfully reproduced before fix.",
        )

    def verify_solution(
        self,
        reproduction_script_path: Path,
        workspace_dir: Path,
        pre_fix_result: GateResult | None = None,
        regression_command: str | None = None,
        timeout: int = 30,
    ) -> ReproductionReport:
        """Step 2: Verify that reproduction script passes cleanly and no regressions occurred."""
        post_res = self.post_fix_gate.evaluate(
            script_path=reproduction_script_path,
            workspace_dir=workspace_dir,
            python_executable=self.python_executable,
            timeout=timeout,
        )

        if not post_res.passed:
            return ReproductionReport(
                status=ReproductionStatus.POST_FIX_FAILED,
                pre_fix_result=pre_fix_result,
                post_fix_result=post_res,
                reproduction_script_path=str(reproduction_script_path),
                diagnostics="Failure: Reproduction script still fails after fix.",
            )

        # Optional Step 3: Run adjacent regression tests
        reg_res: GateResult | None = None
        if regression_command:
            reg_res = self.regression_guard.evaluate(
                test_command=regression_command,
                workspace_dir=workspace_dir,
                timeout=timeout * 2,
            )
            if not reg_res.passed:
                return ReproductionReport(
                    status=ReproductionStatus.REGRESSION_DETECTED,
                    pre_fix_result=pre_fix_result,
                    post_fix_result=post_res,
                    regression_result=reg_res,
                    reproduction_script_path=str(reproduction_script_path),
                    diagnostics=f"Failure: Regression detected in adjacent tests: {reg_res.message}",
                )

        return ReproductionReport(
            status=ReproductionStatus.VERIFIED_SOLVED,
            pre_fix_result=pre_fix_result,
            post_fix_result=post_res,
            regression_result=reg_res,
            reproduction_script_path=str(reproduction_script_path),
            diagnostics="Success: Bug confirmed fixed and all regression checks passed!",
        )
