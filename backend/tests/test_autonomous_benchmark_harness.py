"""Tests for the Autonomous SWE-Bench Style Evaluation Benchmark Harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_workspace.evaluation.autonomous_benchmark_harness import (
    DEFAULT_REFERENCE_PATCH_LINES,
    AutonomousBenchmarkHarness,
    BenchmarkProblem,
    ProblemKind,
    RunOutcome,
    compute_diff_minimization,
    compute_pass_at_k,
    compute_repair_efficiency,
    compute_token_economy,
    count_diff_lines,
    parse_test_log,
    problem_from_spec,
    summarize_log_counts,
)
from agent_workspace.tools.builtins.autonomous_benchmark_tool import (
    run_autonomous_benchmark_eval,
)

BASE_MODULE = "def bump(x):\n    return x\n"
FIXED_MODULE = "def bump(x):\n    return x + 1\n"
BROKEN_MODULE = "def bump(x):\n    return x - 1\n"
TEST_MODULE = "from calc import bump\n\n\ndef test_bump():\n    assert bump(1) == 2\n"


def _problem(**overrides) -> BenchmarkProblem:
    """Return a synthetic problem that a one-line patch can resolve."""
    defaults = dict(
        problem_id="demo-001",
        name="fix increment",
        kind=ProblemKind.CUSTOM,
        files={"calc.py": BASE_MODULE},
        patch_files={"calc.py": FIXED_MODULE},
        test_files={"test_calc.py": TEST_MODULE},
        fail_to_pass=["test_bump"],
        tokens_used=1000,
        reference_patch_lines=10,
        timeout_sec=120.0,
    )
    defaults.update(overrides)
    return BenchmarkProblem(**defaults)


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------


def test_parse_test_log_reads_pytest_verbose_output():
    log = "test_calc.py::test_bump PASSED                    [100%]\n"
    assert parse_test_log(log) == {"test_calc.py::test_bump": "PASSED"}


def test_parse_test_log_reads_multiple_outcomes():
    log = (
        "test_a.py::test_one PASSED [50%]\n"
        "test_a.py::test_two FAILED [100%]\n"
        "test_a.py::test_three ERROR [100%]\n"
    )
    outcomes = parse_test_log(log)
    assert outcomes["test_a.py::test_one"] == "PASSED"
    assert outcomes["test_a.py::test_two"] == "FAILED"
    assert outcomes["test_a.py::test_three"] == "ERROR"


def test_parse_test_log_reads_unittest_output():
    log = "test_bump (tests.TestCalc) ... ok\n"
    assert parse_test_log(log) == {"tests.TestCalc.test_bump": "PASSED"}


def test_parse_test_log_ignores_noise():
    assert parse_test_log("collected 3 items\n\nnothing here\n") == {}


def test_summarize_log_counts_extracts_passed_and_failed():
    log = "==== 2 passed, 1 failed, 3 skipped in 0.10s ===="
    assert summarize_log_counts(log) == (2, 1)


def test_summarize_log_counts_handles_errors():
    assert summarize_log_counts("1 error in 0.01s") == (0, 1)


def test_count_diff_lines_ignores_headers():
    diff = "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-old\n+new\n"
    assert count_diff_lines(diff) == 2
    assert count_diff_lines("") == 0


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def test_pass_at_k_zero_when_no_successes():
    assert compute_pass_at_k(5, 0, 1) == 0.0


def test_pass_at_k_estimator_matches_closed_form():
    # 1 - C(3, 1) / C(5, 1) = 1 - 3/5 = 0.4
    assert compute_pass_at_k(5, 2, 1) == pytest.approx(0.4)


def test_pass_at_k_degenerates_when_k_at_least_n():
    assert compute_pass_at_k(3, 1, 3) == 1.0
    assert compute_pass_at_k(2, 1, 5) == 1.0


def test_pass_at_k_requires_positive_inputs():
    assert compute_pass_at_k(0, 1, 1) == 0.0
    assert compute_pass_at_k(3, 1, 0) == 0.0


def test_repair_efficiency_is_normalized():
    assert compute_repair_efficiency(2, 4, 1) == pytest.approx(0.5)
    assert compute_repair_efficiency(4, 4, 1) == pytest.approx(1.0)


def test_repair_efficiency_without_targets_runs_clean():
    assert compute_repair_efficiency(0, 0, 2) == 1.0


def test_token_economy_measures_resolutions_per_thousand_tokens():
    assert compute_token_economy(2, 2000) == pytest.approx(1.0)
    assert compute_token_economy(1, 0) == pytest.approx(1.0)


def test_diff_minimization_favours_smaller_patches():
    small = compute_diff_minimization(5, 100)
    large = compute_diff_minimization(200, 100)
    assert small > large
    assert compute_diff_minimization(100, 100) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Problem specification
# ---------------------------------------------------------------------------


def test_problem_from_spec_maps_kinds():
    problem = problem_from_spec({"problem_id": "x", "kind": "swe_bench"})
    assert problem.kind is ProblemKind.SWE_BENCH
    assert problem_from_spec({"kind": "humaneval"}).kind is ProblemKind.HUMAN_EVAL
    assert problem_from_spec({}).kind is ProblemKind.CUSTOM


def test_problem_from_spec_defaults():
    problem = problem_from_spec({"id": "abc"})
    assert problem.problem_id == "abc"
    assert problem.test_command == "pytest"
    assert problem.reference_patch_lines == DEFAULT_REFERENCE_PATCH_LINES


def test_problem_serialization_is_json_safe():
    payload = json.dumps(_problem().to_dict())
    assert "demo-001" in payload


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def test_run_problem_resolves_correct_patch():
    harness = AutonomousBenchmarkHarness()
    result = harness.run_problem(_problem(), capture_before=False)
    assert result.error == ""
    assert result.resolved is True
    assert result.outcome is RunOutcome.RESOLVED
    assert result.fail_to_pass_results == {"test_bump": True}
    assert result.tests_passed >= 1


def test_run_problem_marks_wrong_patch_unresolved():
    harness = AutonomousBenchmarkHarness()
    problem = _problem(problem_id="demo-002", patch_files={"calc.py": BROKEN_MODULE})
    result = harness.run_problem(problem, capture_before=False)
    assert result.resolved is False
    assert result.outcome is RunOutcome.UNRESOLVED
    assert result.fail_to_pass_results == {"test_bump": False}


def test_run_problem_records_metrics():
    harness = AutonomousBenchmarkHarness()
    result = harness.run_problem(_problem(), capture_before=False)
    assert result.patch_lines > 0
    assert 0.0 < result.diff_minimization_score <= 1.0
    assert result.repair_efficiency > 0.0
    assert result.token_economy > 0.0
    assert result.duration_sec > 0.0


def test_run_problem_reports_unappliable_patch():
    harness = AutonomousBenchmarkHarness()
    problem = _problem(
        problem_id="demo-003",
        patch="--- a/missing.py\n+++ b/missing.py\n@@ -1 +1 @@\n-a\n+b\n",
        patch_files={},
    )
    result = harness.run_problem(problem, capture_before=False)
    assert result.resolved is False
    assert "patch failed to apply" in result.error


def test_run_problem_reports_missing_test_runner():
    harness = AutonomousBenchmarkHarness()
    problem = _problem(problem_id="demo-004", test_command="definitely-not-a-runner")
    result = harness.run_problem(problem, capture_before=False)
    assert result.resolved is False


def test_run_problem_checks_out_a_git_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "calc.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

    problem = _problem(
        problem_id="demo-005",
        repo_path=str(repo),
        files={},
        patch_files={},
        test_files={"test_calc.py": TEST_MODULE},
    )
    result = AutonomousBenchmarkHarness().run_problem(problem, capture_before=False)
    assert result.resolved is True


# ---------------------------------------------------------------------------
# Suite aggregation and reporting
# ---------------------------------------------------------------------------


def test_suite_aggregates_resolve_rate():
    harness = AutonomousBenchmarkHarness()
    report = harness.run_suite([_problem()], attempts=1, k_values=[1], verify_baseline=False)
    assert report["success"] is True
    assert report["aggregate"]["problem_count"] == 1
    assert report["aggregate"]["resolved_count"] == 1
    assert report["aggregate"]["resolve_rate"] == pytest.approx(1.0)
    assert report["aggregate"]["pass_at_k"]["pass@1"] == pytest.approx(1.0)


def test_suite_reports_unresolved_problems():
    harness = AutonomousBenchmarkHarness()
    problem = _problem(problem_id="demo-006", patch_files={"calc.py": BROKEN_MODULE})
    report = harness.run_suite([problem], attempts=1, k_values=[1], verify_baseline=False)
    assert report["aggregate"]["resolved_count"] == 0
    assert report["aggregate"]["resolve_rate"] == pytest.approx(0.0)


def test_reports_are_written_to_disk(tmp_path):
    harness = AutonomousBenchmarkHarness(output_dir=tmp_path)
    report = harness.run_suite([_problem()], attempts=1, k_values=[1], verify_baseline=False)
    artifacts = harness.write_reports(report, name="report")
    assert artifacts["json"].endswith("report.json")
    assert artifacts["markdown"].endswith("report.md")
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["aggregate"]["problem_count"] == 1
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "# Autonomous Benchmark Report" in markdown
    assert "demo-001" in markdown


def test_markdown_render_handles_empty_report():
    markdown = AutonomousBenchmarkHarness.render_markdown({"aggregate": {}, "problems": []})
    assert "Autonomous Benchmark Report" in markdown


def test_workspace_is_cleaned_up_by_default(tmp_path):
    harness = AutonomousBenchmarkHarness(workdir_root=tmp_path)
    harness.run_problem(_problem(problem_id="demo-007"), capture_before=False)
    assert list(tmp_path.iterdir()) == []


def test_workspace_can_be_retained(tmp_path):
    harness = AutonomousBenchmarkHarness(workdir_root=tmp_path, keep_workspace=True)
    harness.run_problem(_problem(problem_id="demo-008"), capture_before=False)
    assert any(path.name.startswith("bench-demo-008") for path in tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_metrics_action():
    result = run_autonomous_benchmark_eval.invoke(
        {"action": "metrics", "total_attempts": 5, "successful_attempts": 2, "k": 1}
    )
    assert result["success"] is True
    assert result["pass@1"] == pytest.approx(0.4)


def test_tool_run_action(tmp_path):
    spec = {
        "problem_id": "tool-001",
        "files": {"calc.py": BASE_MODULE},
        "patch_files": {"calc.py": FIXED_MODULE},
        "test_files": {"test_calc.py": TEST_MODULE},
        "fail_to_pass": ["test_bump"],
        "timeout_sec": 120,
    }
    result = run_autonomous_benchmark_eval.invoke(
        {
            "action": "run",
            "problem_specs_json": json.dumps([spec]),
            "attempts": 1,
            "k_values": "1",
            "output_dir": str(tmp_path),
            "verify_baseline": False,
        }
    )
    assert result["success"] is True
    assert result["aggregate"]["resolved_count"] == 1
    assert result["artifacts"]["json"].endswith("benchmark.json")


def test_tool_rejects_invalid_json():
    result = run_autonomous_benchmark_eval.invoke(
        {"action": "run", "problem_specs_json": "{not json"}
    )
    assert result["success"] is False
    assert "not valid JSON" in result["error"]


def test_tool_requires_at_least_one_problem():
    result = run_autonomous_benchmark_eval.invoke({"action": "run", "problem_specs_json": "[]"})
    assert result["success"] is False


def test_tool_rejects_unknown_action():
    result = run_autonomous_benchmark_eval.invoke({"action": "explode"})
    assert result["success"] is False


def test_pytest_command_uses_current_interpreter():
    harness = AutonomousBenchmarkHarness()
    command = harness._build_command(_problem(), Path("."))
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "pytest"]
    assert "test_calc.py" in command


def test_python_command_targets_test_files():
    harness = AutonomousBenchmarkHarness()
    command = harness._build_command(_problem(test_command="python"), Path("."))
    assert command[0] == sys.executable
    assert command[1] == "test_calc.py"
