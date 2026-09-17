"""Built-in reproduce_and_verify tool inspired by Devin and SWE-agent."""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain.tools import tool

from agent_workspace.reproduction.engine import ReproductionEngine


@tool("reproduce_and_verify", parse_docstring=True)
def reproduce_and_verify(
    phase: str,
    issue_description: str = "",
    test_body: str = "",
    script_path: str | None = None,
    regression_command: str | None = None,
    workspace_dir: str | None = None,
) -> str:
    """Execute test-driven autonomous bug reproduction and verification.

    Phase 'prepare': Synthesizes reproduction script and verifies that it FAILS before code modification.
    Phase 'verify': Runs reproduction script after code fix to ensure it PASSES (exit 0) and verifies adjacent regression tests.

    Args:
        phase: Either 'prepare' (pre-fix reproduction) or 'verify' (post-fix confirmation).
        issue_description: Detailed description of the bug or expected behavior (for 'prepare').
        test_body: Python code block executing the test logic and asserting expected state (for 'prepare').
        script_path: Path to existing reproduction script (required for 'verify').
        regression_command: Optional shell command to run regression test suite (e.g. 'pytest tests/').
        workspace_dir: Target directory where code resides (defaults to current working directory).
    """
    ws_dir = Path(workspace_dir) if workspace_dir else Path(os.getcwd())
    engine = ReproductionEngine()

    if phase.lower() == "prepare":
        report = engine.prepare_reproduction(
            issue_description=issue_description,
            test_body=test_body,
            workspace_dir=ws_dir,
        )
        return json.dumps({
            "status": report.status.value,
            "reproduction_script": report.reproduction_script_path,
            "bug_reproduced": report.pre_fix_result.passed if report.pre_fix_result else False,
            "exit_code": report.pre_fix_result.exit_code if report.pre_fix_result else None,
            "diagnostics": report.diagnostics,
            "pre_fix_output": report.pre_fix_result.stderr or report.pre_fix_result.stdout if report.pre_fix_result else "",
        }, indent=2)

    elif phase.lower() == "verify":
        if not script_path:
            return json.dumps({"error": "script_path parameter is required for 'verify' phase."})

        report = engine.verify_solution(
            reproduction_script_path=Path(script_path),
            workspace_dir=ws_dir,
            regression_command=regression_command,
        )
        return json.dumps({
            "status": report.status.value,
            "is_verified": report.is_verified,
            "reproduction_passed": report.post_fix_result.passed if report.post_fix_result else False,
            "regression_passed": report.regression_result.passed if report.regression_result else True,
            "diagnostics": report.diagnostics,
        }, indent=2)

    return json.dumps({"error": f"Invalid phase '{phase}'. Must be 'prepare' or 'verify'."})
