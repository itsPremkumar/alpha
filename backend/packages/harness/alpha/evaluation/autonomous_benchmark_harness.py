"""Autonomous SWE-Bench Style Evaluation Benchmark Harness.

A self-contained benchmarking engine that evaluates agent code patches against
standardized coding challenge specifications (SWE-bench style repository
problems, HumanEval style function synthesis problems, or arbitrary custom
problem specs).

Execution pipeline per problem
------------------------------
1. **Isolated checkout.** The repository is cloned into a scratch workspace and
   reset to the declared base commit (or materialised from an inline file
   overlay when no repository is supplied), so runs never touch the host tree.
2. **Patch injection.** The candidate patch (unified diff or file overlay) and
   the test patch are applied with ``git apply`` or direct writes.
3. **Test execution.** The configured test runner (pytest by default) executes
   the injected tests inside the scratch workspace under a hard timeout.
4. **Log parsing.** Verbose runner output is parsed into per-test outcomes,
   from which fail-to-pass and pass-to-pass verdicts are derived.
5. **Metric computation.** pass@k, repair efficiency, token consumption
   economy and patch diff minimization are computed and scored.
6. **Reporting.** Structured JSON and human readable Markdown artifacts are
   written to the output directory.

Exposed agent tool
------------------
``run_autonomous_benchmark_eval``.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

DEFAULT_TEST_COMMAND: str = "pytest"
DEFAULT_TIMEOUT_SEC: float = 300.0
DEFAULT_REFERENCE_PATCH_LINES: int = 100


class ProblemKind(str, Enum):
    """Supported benchmark problem families."""

    SWE_BENCH = "swe_bench"
    HUMAN_EVAL = "human_eval"
    CUSTOM = "custom"


class RunOutcome(str, Enum):
    """Coarse verdict for a single benchmark run."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ERROR = "error"


@dataclass
class BenchmarkProblem:
    """Declarative description of one benchmark problem.

    Attributes:
        problem_id: Stable identifier, e.g. ``swebench-001``.
        name: Human readable title.
        kind: Problem family used for reporting.
        repo_path: Optional local git repository to clone and check out.
        base_commit: Optional commit-ish to reset to before patching.
        files: Inline base file overlay (path -> content) used when no
            repository is supplied.
        patch: Candidate unified diff produced by the agent, or ``None`` when
            ``patch_files`` carries the change instead.
        patch_files: Inline file overlay describing the candidate patch.
        test_files: Inline test file overlay (path -> test source).
        test_patch: Optional unified diff adding the evaluation tests.
        fail_to_pass: Test node ids that must flip from failing to passing.
        pass_to_pass: Test node ids that must keep passing.
        test_paths: Test files or directories passed to the runner.
        test_command: Runner executable (``pytest`` or ``python``).
        timeout_sec: Hard timeout for the test subprocess.
        reference_patch_lines: Reference solution size used to score diff
            minimization.
        tokens_used: Tokens consumed by the agent while producing the patch.
        metadata: Arbitrary JSON-serializable annotations.
    """

    problem_id: str
    name: str = ""
    kind: ProblemKind = ProblemKind.CUSTOM
    repo_path: str = ""
    base_commit: str = ""
    files: dict[str, str] = field(default_factory=dict)
    patch: str = ""
    patch_files: dict[str, str] = field(default_factory=dict)
    test_files: dict[str, str] = field(default_factory=dict)
    test_patch: str = ""
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    test_paths: list[str] = field(default_factory=list)
    test_command: str = DEFAULT_TEST_COMMAND
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    reference_patch_lines: int = DEFAULT_REFERENCE_PATCH_LINES
    tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "name": self.name,
            "kind": self.kind.value,
            "repo_path": self.repo_path,
            "base_commit": self.base_commit,
            "file_count": len(self.files),
            "has_patch": bool(self.patch),
            "patch_file_count": len(self.patch_files),
            "test_file_count": len(self.test_files),
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "test_paths": list(self.test_paths),
            "test_command": self.test_command,
            "timeout_sec": self.timeout_sec,
            "reference_patch_lines": self.reference_patch_lines,
            "tokens_used": self.tokens_used,
            "metadata": dict(self.metadata),
        }


@dataclass
class BenchmarkRunResult:
    """Outcome of executing one problem once."""

    problem_id: str
    outcome: RunOutcome
    resolved: bool
    attempt: int
    fail_to_pass_results: dict[str, bool] = field(default_factory=dict)
    pass_to_pass_results: dict[str, bool] = field(default_factory=dict)
    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    duration_sec: float = 0.0
    patch_lines: int = 0
    tokens_used: int = 0
    repair_efficiency: float = 0.0
    token_economy: float = 0.0
    diff_minimization_score: float = 0.0
    error: str = ""
    log_excerpt: str = ""
    baseline_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "outcome": self.outcome.value,
            "resolved": self.resolved,
            "attempt": self.attempt,
            "fail_to_pass": self.fail_to_pass_results,
            "pass_to_pass": self.pass_to_pass_results,
            "tests_total": self.tests_total,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "duration_sec": round(self.duration_sec, 4),
            "patch_lines": self.patch_lines,
            "tokens_used": self.tokens_used,
            "repair_efficiency": round(self.repair_efficiency, 6),
            "token_economy": round(self.token_economy, 6),
            "diff_minimization_score": round(self.diff_minimization_score, 6),
            "error": self.error,
            "log_excerpt": self.log_excerpt,
            "baseline_failures": self.baseline_failures,
        }


@dataclass
class BenchmarkAggregate:
    """Cross-problem aggregate metrics for a benchmark suite run."""

    problem_count: int = 0
    resolved_count: int = 0
    attempt_count: int = 0
    pass_at_k: dict[str, float] = field(default_factory=dict)
    repair_efficiency: float = 0.0
    token_economy: float = 0.0
    mean_diff_minimization: float = 0.0
    mean_duration_sec: float = 0.0
    resolve_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_count": self.problem_count,
            "resolved_count": self.resolved_count,
            "attempt_count": self.attempt_count,
            "pass_at_k": {key: round(value, 6) for key, value in self.pass_at_k.items()},
            "repair_efficiency": round(self.repair_efficiency, 6),
            "token_economy": round(self.token_economy, 6),
            "mean_diff_minimization": round(self.mean_diff_minimization, 6),
            "mean_duration_sec": round(self.mean_duration_sec, 4),
            "resolve_rate": round(self.resolve_rate, 6),
        }


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def compute_pass_at_k(total_attempts: int, successful_attempts: int, k: int) -> float:
    """Compute the unbiased pass@k estimator.

    ``pass@k = 1 - C(n - c, k) / C(n, k)`` where ``n`` is the number of
    attempts, ``c`` the number of successful attempts and ``k`` the budget.
    When ``n < k`` the estimator is undefined and reported as ``0.0``; when
    ``k >= n`` it degenerates to "did any attempt succeed".
    """
    n = int(total_attempts)
    c = int(successful_attempts)
    k = int(k)
    if n <= 0 or k <= 0 or c <= 0:
        return 0.0
    if k >= n:
        return 1.0
    try:
        complement = math.comb(n - c, k)
        total = math.comb(n, k)
    except ValueError:
        return 0.0
    if total == 0:
        return 0.0
    return 1.0 - (complement / total)


def compute_repair_efficiency(
    fail_to_pass_resolved: int,
    fail_to_pass_total: int,
    attempts: int,
) -> float:
    """Fraction of required repairs delivered per attempt.

    Returns ``resolved / (total * attempts)`` clamped to ``[0, 1]``; a problem
    with no declared fail-to-pass tests scores ``1.0`` when it runs cleanly.
    """
    if fail_to_pass_total <= 0:
        return 1.0
    attempts = max(int(attempts), 1)
    return _clamp(float(fail_to_pass_resolved) / (float(fail_to_pass_total) * attempts))


def compute_token_economy(resolved: int, tokens_used: int) -> float:
    """Resolved repairs per thousand tokens consumed."""
    if tokens_used <= 0:
        return float(resolved)
    return float(resolved) / (float(tokens_used) / 1000.0)


def compute_diff_minimization(changed_lines: int, reference_lines: int) -> float:
    """Score how compact a patch is relative to a reference solution.

    ``score = reference / (reference + changed)`` which is monotonically
    decreasing in patch size, equals ``0.5`` when the patch matches the
    reference size, and approaches ``1.0`` for minimal patches.
    """
    reference = max(int(reference_lines), 1)
    changed = max(int(changed_lines), 0)
    return reference / (reference + changed)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into ``[low, high]``."""
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

_PYTEST_NODE_PATTERN = re.compile(
    r"^(?P<node>[^\s:]+\.py::[^\s]+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
)
_UNITTEST_NODE_PATTERN = re.compile(
    r"^(?P<node>[\w\[\]-]+)\s+\((?P<context>[\w\.]+)\)\s*\.\.\.\s*(?P<outcome>ok|FAIL|ERROR|skipped)"
)
_SUMMARY_PATTERN = re.compile(
    r"(?P<count>\d+)\s+(?P<unit>passed|failed|error|errors|skipped)", re.IGNORECASE
)


def parse_test_log(log: str) -> dict[str, str]:
    """Parse runner output into a mapping of test node id to outcome.

    Supports the verbose pytest format (``path::test PASSED``) and the classic
    unittest format (``test_name (module.Class) ... ok``).
    """
    outcomes: dict[str, str] = {}
    for line in (log or "").splitlines():
        line = line.strip()
        match = _PYTEST_NODE_PATTERN.match(line)
        if match:
            outcomes[match.group("node")] = match.group("outcome").upper()
            continue
        unittest_match = _UNITTEST_NODE_PATTERN.match(line)
        if unittest_match:
            node = f"{unittest_match.group('context')}.{unittest_match.group('node')}"
            raw = unittest_match.group("outcome").lower()
            outcomes[node] = {
                "ok": "PASSED",
                "fail": "FAILED",
                "error": "ERROR",
                "skipped": "SKIPPED",
            }.get(raw, raw.upper())
    return outcomes


def summarize_log_counts(log: str) -> tuple[int, int]:
    """Extract ``(passed, failed)`` counts from a runner summary line."""
    passed = 0
    failed = 0
    for match in _SUMMARY_PATTERN.finditer(log or ""):
        count = int(match.group("count"))
        unit = match.group("unit").lower()
        if unit == "passed":
            passed += count
        elif unit in {"failed", "error", "errors"}:
            failed += count
    return passed, failed


def count_diff_lines(diff_text: str) -> int:
    """Count added and removed lines in a unified diff."""
    if not diff_text:
        return 0
    return sum(
        1
        for line in diff_text.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def _match_node(node: str, expected: str) -> bool:
    """Return True when an observed test node satisfies an expectation."""
    if node == expected:
        return True
    if expected in node:
        return True
    node_tail = node.split("::")[-1]
    expected_tail = expected.split("::")[-1]
    return node_tail == expected_tail


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class AutonomousBenchmarkHarness:
    """Executes benchmark problems in isolated scratch workspaces."""

    def __init__(
        self,
        output_dir: Optional[str | Path] = None,
        workdir_root: Optional[str | Path] = None,
        keep_workspace: bool = False,
        default_timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ):
        self.output_dir = Path(output_dir) if output_dir else None
        self.workdir_root = Path(workdir_root) if workdir_root else None
        self.keep_workspace = bool(keep_workspace)
        self.default_timeout_sec = float(default_timeout_sec)
        self.history: list[BenchmarkRunResult] = []

    # -- workspace materialisation ----------------------------------------

    def _create_workspace(self, problem: BenchmarkProblem) -> Path:
        """Create an isolated git workspace for ``problem``."""
        root_parent = self.workdir_root or Path(tempfile.gettempdir())
        root_parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix=f"bench-{problem.problem_id}-", dir=str(root_parent)))

        if problem.repo_path and Path(problem.repo_path).exists():
            self._run_git(
                ["clone", "--quiet", str(Path(problem.repo_path).resolve()), "."],
                cwd=workspace,
            )
            if problem.base_commit:
                self._run_git(["checkout", "--quiet", problem.base_commit], cwd=workspace)
        elif problem.metadata.get("initialize_git", False):
            # Synthetic problems need a repository only when the caller wants
            # git semantics (for example to diff or commit the patch). Plain
            # file overlays skip repository creation entirely, which keeps
            # synthetic benchmark runs fast.
            self._run_git(["init", "--quiet"], cwd=workspace)
            self._configure_git_identity(workspace)
        return workspace

    @staticmethod
    def _configure_git_identity(workspace: Path) -> None:
        """Set a deterministic commit identity for synthetic repositories."""
        AutonomousBenchmarkHarness._run_git(
            ["config", "user.email", "benchmark@agent-workspace.local"], cwd=workspace
        )
        AutonomousBenchmarkHarness._run_git(
            ["config", "user.name", "Autonomous Benchmark Harness"], cwd=workspace
        )

    @staticmethod
    def _run_git(args: list[str], cwd: Path, timeout: float = 60.0) -> tuple[int, str]:
        """Run a git command, returning ``(returncode, combined_output)``."""
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, f"git {args[0]} failed: {exc}"
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")

    @staticmethod
    def _write_files(workspace: Path, files: dict[str, str]) -> None:
        """Write an inline file overlay into the workspace."""
        for relative_path, content in (files or {}).items():
            target = workspace / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _apply_patch(workspace: Path, patch_text: str) -> tuple[bool, str]:
        """Apply a unified diff, preferring ``git apply`` over ``patch``."""
        if not patch_text:
            return True, ""
        patch_file = workspace / ".benchmark_patch.diff"
        patch_file.write_text(patch_text, encoding="utf-8")
        code, output = AutonomousBenchmarkHarness._run_git(
            ["apply", "--whitespace=nowarn", str(patch_file)], cwd=workspace
        )
        if code == 0:
            return True, output
        code, output = AutonomousBenchmarkHarness._run_git(
            ["apply", "--3way", "--whitespace=nowarn", str(patch_file)], cwd=workspace
        )
        return code == 0, output

    # -- test execution ----------------------------------------------------

    def _build_command(self, problem: BenchmarkProblem, workspace: Path) -> list[str]:
        """Build the test runner command line for ``problem``."""
        command = (problem.test_command or DEFAULT_TEST_COMMAND).strip()
        targets = list(problem.test_paths) or list(problem.test_files.keys())
        if command in {"pytest", "py.test"}:
            return [
                sys.executable,
                "-m",
                "pytest",
                "-v",
                "-p",
                "no:cacheprovider",
                *targets,
            ]
        if command in {"python", "python3"}:
            return [sys.executable, *targets]
        parts = command.split()
        return [*parts, *targets]

    @staticmethod
    def _build_environment(problem: BenchmarkProblem, workspace: Path) -> dict[str, str]:
        """Build an isolated environment for the test subprocess.

        Third-party pytest plugins are disabled by default: benchmark runs must
        be reproducible and fast, and plugin autoloading is the dominant
        startup cost. Problems that genuinely require a plugin can opt out via
        ``metadata["pytest_disable_plugin_autoload"] = False``.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = str(workspace)
        if problem.metadata.get("pytest_disable_plugin_autoload", True):
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        else:
            env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
        env.pop("PYTEST_ADDOPTS", None)
        return env

    def _execute_tests(
        self, problem: BenchmarkProblem, workspace: Path
    ) -> tuple[str, int, float]:
        """Run the test command, returning ``(log, returncode, duration)``."""
        command = self._build_command(problem, workspace)
        started = time.time()
        try:
            completed = subprocess.run(
                command,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=float(problem.timeout_sec or self.default_timeout_sec),
                encoding="utf-8",
                errors="replace",
                env=self._build_environment(problem, workspace),
            )
            log = (completed.stdout or "") + (completed.stderr or "")
            return log, completed.returncode, time.time() - started
        except subprocess.TimeoutExpired as exc:
            log = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            return f"TIMEOUT after {problem.timeout_sec}s\n{log}", 124, time.time() - started
        except (OSError, ValueError) as exc:
            return f"RUNNER ERROR: {exc}", 127, time.time() - started

    # -- single run --------------------------------------------------------

    def run_problem(
        self,
        problem: BenchmarkProblem,
        attempt: int = 1,
        capture_before: bool = True,
    ) -> BenchmarkRunResult:
        """Execute one attempt at ``problem`` inside an isolated workspace.

        Args:
            problem: The benchmark problem specification.
            attempt: Attempt index (used by pass@k aggregation).
            capture_before: Run the tests before applying the candidate patch
                to establish the true fail-to-pass baseline.

        Returns:
            A :class:`BenchmarkRunResult` describing the attempt.
        """
        started = time.time()
        workspace: Optional[Path] = None
        try:
            workspace = self._create_workspace(problem)
            self._write_files(workspace, problem.files)

            baseline_failures: set[str] = set()
            if capture_before and problem.test_files:
                self._write_files(workspace, problem.test_files)
                baseline_log, _, _ = self._execute_tests(problem, workspace)
                baseline = parse_test_log(baseline_log)
                baseline_failures = {
                    node for node, outcome in baseline.items() if outcome != "PASSED"
                }

            if problem.test_patch:
                applied, output = self._apply_patch(workspace, problem.test_patch)
                if not applied:
                    return self._result(
                        problem,
                        attempt,
                        RunOutcome.ERROR,
                        error=f"test_patch failed to apply: {output[:400]}",
                        started=started,
                    )
            if problem.test_files:
                self._write_files(workspace, problem.test_files)

            patch_lines = count_diff_lines(problem.patch)
            if problem.patch:
                applied, output = self._apply_patch(workspace, problem.patch)
                if not applied:
                    return self._result(
                        problem,
                        attempt,
                        RunOutcome.ERROR,
                        error=f"candidate patch failed to apply: {output[:400]}",
                        started=started,
                        patch_lines=patch_lines,
                    )
            if problem.patch_files:
                self._write_files(workspace, problem.patch_files)
                patch_lines += sum(
                    max(len(content.splitlines()), 1)
                    for content in problem.patch_files.values()
                )

            log, returncode, duration = self._execute_tests(problem, workspace)
            outcomes = parse_test_log(log)
            passed_total, failed_total = summarize_log_counts(log)
            if not outcomes and returncode == 0:
                passed_total = max(passed_total, 1)

            fail_to_pass: dict[str, bool] = {}
            for expected in problem.fail_to_pass:
                matched = [node for node in outcomes if _match_node(node, expected)]
                if matched:
                    fail_to_pass[expected] = all(
                        outcomes[node] == "PASSED" for node in matched
                    )
                else:
                    fail_to_pass[expected] = False

            pass_to_pass: dict[str, bool] = {}
            for expected in problem.pass_to_pass:
                matched = [node for node in outcomes if _match_node(node, expected)]
                pass_to_pass[expected] = (
                    all(outcomes[node] == "PASSED" for node in matched) if matched else False
                )

            if not problem.fail_to_pass:
                resolved = returncode == 0 and failed_total == 0 and passed_total > 0
            else:
                resolved = all(fail_to_pass.values()) and all(pass_to_pass.values())

            resolved_count = sum(1 for value in fail_to_pass.values() if value)
            if not problem.fail_to_pass:
                resolved_count = 1 if resolved else 0

            repair_efficiency = compute_repair_efficiency(
                resolved_count, len(problem.fail_to_pass), 1
            )
            token_economy = compute_token_economy(resolved_count, problem.tokens_used)
            diff_minimization = compute_diff_minimization(
                patch_lines, problem.reference_patch_lines
            )

            return self._result(
                problem,
                attempt,
                RunOutcome.RESOLVED if resolved else RunOutcome.UNRESOLVED,
                fail_to_pass=fail_to_pass,
                pass_to_pass=pass_to_pass,
                tests_total=len(outcomes) or (passed_total + failed_total),
                tests_passed=passed_total,
                tests_failed=failed_total,
                duration=duration,
                patch_lines=patch_lines,
                repair_efficiency=repair_efficiency,
                token_economy=token_economy,
                diff_minimization=diff_minimization,
                log_excerpt=log[-2000:],
                started=started,
                baseline_failures=len(baseline_failures),
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            return self._result(
                problem,
                attempt,
                RunOutcome.ERROR,
                error=f"{type(exc).__name__}: {exc}",
                started=started,
            )
        finally:
            if workspace is not None and not self.keep_workspace:
                shutil.rmtree(workspace, ignore_errors=True)

    def _result(
        self,
        problem: BenchmarkProblem,
        attempt: int,
        outcome: RunOutcome,
        fail_to_pass: Optional[dict[str, bool]] = None,
        pass_to_pass: Optional[dict[str, bool]] = None,
        tests_total: int = 0,
        tests_passed: int = 0,
        tests_failed: int = 0,
        duration: float = 0.0,
        patch_lines: int = 0,
        repair_efficiency: float = 0.0,
        token_economy: float = 0.0,
        diff_minimization: float = 0.0,
        error: str = "",
        log_excerpt: str = "",
        started: Optional[float] = None,
        baseline_failures: int = 0,
    ) -> BenchmarkRunResult:
        """Construct and record a run result."""
        result = BenchmarkRunResult(
            problem_id=problem.problem_id,
            outcome=outcome,
            resolved=outcome is RunOutcome.RESOLVED,
            attempt=attempt,
            fail_to_pass_results=dict(fail_to_pass or {}),
            pass_to_pass_results=dict(pass_to_pass or {}),
            tests_total=tests_total,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            duration_sec=duration
            or (max(time.time() - started, 0.0) if started is not None else 0.0),
            patch_lines=patch_lines,
            tokens_used=problem.tokens_used,
            repair_efficiency=repair_efficiency,
            token_economy=token_economy,
            diff_minimization_score=diff_minimization,
            error=error,
            log_excerpt=log_excerpt,
        )
        result.baseline_failures = baseline_failures
        self.history.append(result)
        return result

    # -- suite -------------------------------------------------------------

    def run_suite(
        self,
        problems: list[BenchmarkProblem],
        attempts: int = 1,
        k_values: Optional[list[int]] = None,
        verify_baseline: bool = True,
    ) -> dict[str, Any]:
        """Run every problem ``attempts`` times and aggregate metrics.

        Args:
            problems: Problems to execute.
            attempts: Attempts per problem (``n`` in the pass@k estimator).
            k_values: k values to report. Defaults to ``[1, 3, 5]`` filtered to
                those not exceeding the attempt budget.
            verify_baseline: Execute the tests once before patching to record
                the genuine fail-to-pass baseline (doubles runner invocations).

        Returns:
            A JSON-serializable suite report.
        """
        attempts = max(int(attempts), 1)
        k_values = k_values or [value for value in (1, 3, 5) if value <= attempts] or [1]

        per_problem: list[dict[str, Any]] = []
        for problem in problems:
            runs: list[BenchmarkRunResult] = []
            for attempt_index in range(1, attempts + 1):
                runs.append(
                    self.run_problem(
                        problem,
                        attempt=attempt_index,
                        capture_before=(
                            verify_baseline
                            and attempt_index == 1
                            and bool(problem.fail_to_pass)
                        ),
                    )
                )
            successes = sum(1 for run in runs if run.resolved)
            per_problem.append(
                {
                    "problem": problem.to_dict(),
                    "runs": [run.to_dict() for run in runs],
                    "successes": successes,
                    "attempts": attempts,
                    "pass_at_k": {
                        f"pass@{k}": compute_pass_at_k(attempts, successes, k)
                        for k in k_values
                    },
                }
            )

        aggregate = self.aggregate(per_problem, k_values)
        return {
            "success": True,
            "problems": per_problem,
            "aggregate": aggregate.to_dict(),
        }

    def aggregate(
        self, per_problem: list[dict[str, Any]], k_values: list[int]
    ) -> BenchmarkAggregate:
        """Aggregate per-problem records into suite-level metrics."""
        problem_count = len(per_problem)
        resolved_count = sum(1 for entry in per_problem if entry.get("successes", 0) > 0)
        attempt_count = sum(int(entry.get("attempts", 0)) for entry in per_problem)

        pass_at_k: dict[str, float] = {}
        for k in k_values:
            scores = [
                entry.get("pass_at_k", {}).get(f"pass@{k}", 0.0) for entry in per_problem
            ]
            pass_at_k[f"pass@{k}"] = (sum(scores) / len(scores)) if scores else 0.0

        all_runs = [run for entry in per_problem for run in entry.get("runs", [])]
        repair = [run.get("repair_efficiency", 0.0) for run in all_runs]
        economy = [run.get("token_economy", 0.0) for run in all_runs]
        minimization = [run.get("diff_minimization_score", 0.0) for run in all_runs]
        durations = [run.get("duration_sec", 0.0) for run in all_runs]

        def _mean(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        return BenchmarkAggregate(
            problem_count=problem_count,
            resolved_count=resolved_count,
            attempt_count=attempt_count,
            pass_at_k=pass_at_k,
            repair_efficiency=_mean(repair),
            token_economy=_mean(economy),
            mean_diff_minimization=_mean(minimization),
            mean_duration_sec=_mean(durations),
            resolve_rate=(resolved_count / problem_count) if problem_count else 0.0,
        )

    # -- reporting ---------------------------------------------------------

    def write_reports(self, report: dict[str, Any], name: str = "benchmark") -> dict[str, str]:
        """Write JSON and Markdown artifacts for ``report``.

        Returns:
            Mapping of artifact kind to written path (empty when no output
            directory is configured).
        """
        if self.output_dir is None:
            return {}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.output_dir / f"{name}.json"
        markdown_path = self.output_dir / f"{name}.md"
        json_path.write_text(
            json.dumps(report, indent=2, sort_keys=False, default=str), encoding="utf-8"
        )
        markdown_path.write_text(self.render_markdown(report), encoding="utf-8")
        return {"json": str(json_path), "markdown": str(markdown_path)}

    @staticmethod
    def render_markdown(report: dict[str, Any]) -> str:
        """Render a human readable Markdown summary of a suite report."""
        aggregate = report.get("aggregate", {}) or {}
        lines: list[str] = [
            "# Autonomous Benchmark Report",
            "",
            "## Aggregate Metrics",
            "",
            "| Metric | Value |",
            "| --- | --- |",
        ]
        lines.append(f"| Problems | {aggregate.get('problem_count', 0)} |")
        lines.append(f"| Resolved | {aggregate.get('resolved_count', 0)} |")
        lines.append(f"| Resolve rate | {aggregate.get('resolve_rate', 0):.3f} |")
        lines.append(f"| Attempts | {aggregate.get('attempt_count', 0)} |")
        for key, value in (aggregate.get("pass_at_k", {}) or {}).items():
            lines.append(f"| {key} | {value:.3f} |")
        lines.append(f"| Repair efficiency | {aggregate.get('repair_efficiency', 0):.3f} |")
        lines.append(f"| Token economy | {aggregate.get('token_economy', 0):.3f} |")
        lines.append(
            f"| Diff minimization | {aggregate.get('mean_diff_minimization', 0):.3f} |"
        )
        lines.append(f"| Mean duration (s) | {aggregate.get('mean_duration_sec', 0):.3f} |")
        lines.append("")
        lines.append("## Per-Problem Results")
        lines.append("")
        lines.append("| Problem | Outcome | Resolved | F2P | P2P | Patch lines | Duration (s) |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for entry in report.get("problems", []):
            problem = entry.get("problem", {}) or {}
            runs = entry.get("runs", []) or []
            first = runs[0] if runs else {}
            f2p = first.get("fail_to_pass", {}) or {}
            p2p = first.get("pass_to_pass", {}) or {}
            lines.append(
                "| {pid} | {outcome} | {resolved} | {f2p} | {p2p} | {patch} | {dur} |".format(
                    pid=problem.get("problem_id", ""),
                    outcome=first.get("outcome", ""),
                    resolved="yes" if first.get("resolved") else "no",
                    f2p=f"{sum(1 for v in f2p.values() if v)}/{len(f2p)}",
                    p2p=f"{sum(1 for v in p2p.values() if v)}/{len(p2p)}",
                    patch=first.get("patch_lines", 0),
                    dur=first.get("duration_sec", 0),
                )
            )
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def problem_from_spec(spec: dict[str, Any]) -> BenchmarkProblem:
    """Build a :class:`BenchmarkProblem` from a JSON-like specification."""
    kind_raw = str(spec.get("kind", "custom")).strip().lower()
    kind = {
        "swe_bench": ProblemKind.SWE_BENCH,
        "swebench": ProblemKind.SWE_BENCH,
        "human_eval": ProblemKind.HUMAN_EVAL,
        "humaneval": ProblemKind.HUMAN_EVAL,
        "custom": ProblemKind.CUSTOM,
    }.get(kind_raw, ProblemKind.CUSTOM)

    return BenchmarkProblem(
        problem_id=str(spec.get("problem_id") or spec.get("id") or "custom-001"),
        name=str(spec.get("name", "")),
        kind=kind,
        repo_path=str(spec.get("repo_path", "") or ""),
        base_commit=str(spec.get("base_commit", "") or ""),
        files=dict(spec.get("files", {}) or {}),
        patch=str(spec.get("patch", "") or ""),
        patch_files=dict(spec.get("patch_files", {}) or {}),
        test_files=dict(spec.get("test_files", {}) or {}),
        test_patch=str(spec.get("test_patch", "") or ""),
        fail_to_pass=list(spec.get("fail_to_pass", []) or []),
        pass_to_pass=list(spec.get("pass_to_pass", []) or []),
        test_paths=list(spec.get("test_paths", []) or []),
        test_command=str(spec.get("test_command", DEFAULT_TEST_COMMAND) or DEFAULT_TEST_COMMAND),
        timeout_sec=float(spec.get("timeout_sec", DEFAULT_TIMEOUT_SEC) or DEFAULT_TIMEOUT_SEC),
        reference_patch_lines=int(
            spec.get("reference_patch_lines", DEFAULT_REFERENCE_PATCH_LINES)
            or DEFAULT_REFERENCE_PATCH_LINES
        ),
        tokens_used=int(spec.get("tokens_used", 0) or 0),
        metadata=dict(spec.get("metadata", {}) or {}),
    )
