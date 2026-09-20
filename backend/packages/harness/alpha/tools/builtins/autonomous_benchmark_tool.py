"""Built-in Autonomous Evaluation Benchmark Harness Tool.

Exposes ``run_autonomous_benchmark_eval`` to the agent.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.evaluation.autonomous_benchmark_harness import (
    AutonomousBenchmarkHarness,
    compute_pass_at_k,
    problem_from_spec,
)


def _build_harness(output_dir: str, keep_workspace: bool) -> AutonomousBenchmarkHarness:
    """Construct a harness instance for a tool invocation."""
    return AutonomousBenchmarkHarness(
        output_dir=output_dir or None,
        keep_workspace=keep_workspace,
    )


@tool("run_autonomous_benchmark_eval", parse_docstring=True)
def run_autonomous_benchmark_eval(
    action: str = "run",
    problem_specs_json: str = "[]",
    attempts: int = 1,
    k_values: str = "1,3,5",
    output_dir: str = "",
    report_name: str = "benchmark",
    keep_workspace: bool = False,
    verify_baseline: bool = True,
    total_attempts: int = 0,
    successful_attempts: int = 0,
    k: int = 1,
) -> dict[str, Any]:
    """Evaluate agent code patches against coding benchmarks and compute pass@k.

    Each problem is executed in an isolated git workspace: the repository is
    checked out (or materialised from an inline file overlay), the candidate
    patch and evaluation tests are injected, the test runner executes under a
    hard timeout, and verbose runner output is parsed into per-test outcomes
    for fail-to-pass and pass-to-pass verdicts. Results are scored with pass@k,
    repair efficiency, token economy and patch diff minimization, and optional
    Markdown and JSON reports are written.

    Args:
        action: ``run`` executes the supplied problems, ``metrics`` computes
            pass@k directly from ``total_attempts`` and ``successful_attempts``.
        problem_specs_json: JSON list of problem specifications. Each spec
            accepts ``problem_id``, ``name``, ``kind`` (``swe_bench``,
            ``human_eval``, ``custom``), ``repo_path``, ``base_commit``,
            ``files`` (path -> content), ``patch`` (unified diff),
            ``patch_files`` (path -> content), ``test_files``, ``test_patch``,
            ``fail_to_pass``, ``pass_to_pass``, ``test_paths``,
            ``test_command``, ``timeout_sec``, ``reference_patch_lines`` and
            ``tokens_used``.
        attempts: Attempts per problem (``n`` in the pass@k estimator).
        k_values: Comma-separated k values to report.
        output_dir: Directory for the Markdown and JSON report artifacts.
        report_name: Basename used for the report artifacts.
        keep_workspace: Keep isolated scratch workspaces for post-mortem.
        verify_baseline: Run the tests once before patching to record the
            genuine fail-to-pass baseline (doubles runner invocations).
        total_attempts: Total attempts used by the ``metrics`` action.
        successful_attempts: Successful attempts used by the ``metrics`` action.
        k: k used by the ``metrics`` action.

    Returns:
        dict: ``{"success": bool, "aggregate": dict, "problems": list,
        "artifacts": dict}``. On failure ``success`` is False and ``error``
        describes the reason.
    """
    try:
        normalized_action = (action or "run").strip().lower()

        if normalized_action == "metrics":
            score = compute_pass_at_k(total_attempts, successful_attempts, k)
            return {
                "success": True,
                "action": "metrics",
                "total_attempts": total_attempts,
                "successful_attempts": successful_attempts,
                "k": k,
                f"pass@{k}": round(score, 6),
            }

        if normalized_action != "run":
            return {
                "success": False,
                "error": f"unknown action '{action}'",
                "problems": [],
                "artifacts": {},
            }

        try:
            specs = json.loads(problem_specs_json) if problem_specs_json else []
        except (ValueError, TypeError) as exc:
            return {
                "success": False,
                "error": f"problem_specs_json is not valid JSON: {exc}",
                "problems": [],
                "artifacts": {},
            }

        if isinstance(specs, dict):
            specs = [specs]
        if not isinstance(specs, list):
            return {
                "success": False,
                "error": "problem_specs_json must decode to a list of problem specs",
                "problems": [],
                "artifacts": {},
            }
        if not specs:
            return {
                "success": False,
                "error": "at least one problem spec is required for action='run'",
                "problems": [],
                "artifacts": {},
            }

        problems = [problem_from_spec(spec) for spec in specs if isinstance(spec, dict)]
        parsed_k = [int(value) for value in str(k_values).split(",") if str(value).strip().isdigit()]
        if not parsed_k:
            parsed_k = [1]

        harness = _build_harness(output_dir, keep_workspace)
        report = harness.run_suite(
            problems,
            attempts=max(int(attempts), 1),
            k_values=parsed_k,
            verify_baseline=verify_baseline,
        )
        artifacts = harness.write_reports(report, name=report_name or "benchmark")
        report["artifacts"] = artifacts
        report["action"] = "run"
        return report
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "problems": [],
            "artifacts": {},
        }
