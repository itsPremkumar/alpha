"""AST Safety Invariant Checker and Ephemeral Canary Sandboxing.

Enforces zero-trust AST invariants and executes candidate self-mutations
in isolated shadow environments with parallel verification and automatic rollback.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafetyViolation:
    rule_name: str
    message: str
    line_number: Optional[int] = None
    severity: str = "FATAL"  # FATAL, WARNING


@dataclass
class SafetyCheckResult:
    passed: bool
    violations: list[SafetyViolation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [
                {
                    "rule": v.rule_name,
                    "message": v.message,
                    "line": v.line_number,
                    "severity": v.severity,
                }
                for v in self.violations
            ],
        }


class ASTSafetyInvariantChecker(ast.NodeVisitor):
    """AST static analyzer for detecting privilege escalation and dangerous constructs."""

    BANNED_CALLS = {
        "eval",
        "exec",
        "__import__",
        "globals",
        "locals",
        "getattr_from_untrusted",
    }

    BANNED_MODULES = {
        "ctypes",
        "pty",
        "socketserver",
    }

    IMMUTABLE_CORE_PATHS = {
        "security.py",
        "auth.py",
        "manifest.sha256",
        ".alpha/security",
    }

    def __init__(self, max_cyclomatic_complexity: int = 25) -> None:
        self.max_complexity = max_cyclomatic_complexity
        self.violations: list[SafetyViolation] = []
        self._current_complexity = 1

    def check_file_path(self, target_file_path: str) -> None:
        """Verifies target file is not an immutable core security file."""
        norm = target_file_path.replace("\\", "/")
        for immutable in self.IMMUTABLE_CORE_PATHS:
            if immutable in norm:
                self.violations.append(
                    SafetyViolation(
                        rule_name="IMMUTABLE_FILE_VIOLATION",
                        message=f"Direct modification of security core file '{target_file_path}' is forbidden.",
                    )
                )

    def check_source_code(self, source_code: str, file_path: str = "") -> SafetyCheckResult:
        """Runs full static AST safety invariant check on code string."""
        self.violations = []
        if file_path:
            self.check_file_path(file_path)

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            self.violations.append(
                SafetyViolation(
                    rule_name="SYNTAX_ERROR",
                    message=f"Syntax error: {e.msg} at line {e.lineno}",
                    line_number=e.lineno,
                )
            )
            return SafetyCheckResult(passed=False, violations=self.violations)

        self.visit(tree)
        passed = not any(v.severity == "FATAL" for v in self.violations)
        return SafetyCheckResult(passed=passed, violations=self.violations)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = ""
        if isinstance(node.func, ast.Name):
            call_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            # Check os.system, subprocess.call, etc.
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "system":
                self.violations.append(
                    SafetyViolation(
                        rule_name="BANNED_OS_SYSTEM",
                        message=f"Unsanitized os.system call detected at line {node.lineno}.",
                        line_number=node.lineno,
                    )
                )
            call_name = node.func.attr

        if call_name in self.BANNED_CALLS:
            self.violations.append(
                SafetyViolation(
                    rule_name="BANNED_DYNAMIC_EXECUTION",
                    message=f"Forbidden dynamic execution primitive '{call_name}' detected at line {node.lineno}.",
                    line_number=node.lineno,
                )
            )

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in self.BANNED_MODULES:
                self.violations.append(
                    SafetyViolation(
                        rule_name="BANNED_MODULE_IMPORT",
                        message=f"Forbidden module import '{alias.name}' detected at line {node.lineno}.",
                        line_number=node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module in self.BANNED_MODULES:
            self.violations.append(
                SafetyViolation(
                    rule_name="BANNED_MODULE_IMPORT",
                    message=f"Forbidden import from '{node.module}' at line {node.lineno}.",
                    line_number=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self._current_complexity += 1
        if self._current_complexity > self.max_complexity:
            self.violations.append(
                SafetyViolation(
                    rule_name="CYCLOMATIC_COMPLEXITY_EXCEEDED",
                    message=f"Cyclomatic complexity exceeded threshold ({self._current_complexity} > {self.max_complexity}).",
                    line_number=node.lineno,
                    severity="WARNING",
                )
            )
        self.generic_visit(node)


@dataclass
class CanaryExecutionResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    rolled_back: bool
    safety_violations: list[SafetyViolation] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 2),
            "rolled_back": self.rolled_back,
            "message": self.message,
            "violations_count": len(self.safety_violations),
        }


class EphemeralCanarySandbox:
    """Provisions an isolated shadow sandbox, runs parallel canary execution, and handles automatic rollback."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.invariant_checker = ASTSafetyInvariantChecker()

    def run_canary_test(
        self,
        candidate_files: dict[str, str],  # rel_path -> candidate_content
        test_command: str,
        timeout_seconds: int = 45,
    ) -> CanaryExecutionResult:
        """Executes candidate modifications in an isolated canary sandbox.

        If execution fails, times out, or violates invariants, automatically rolls back
        without altering the main workspace.
        """
        # 1. AST Safety Invariant Gate
        for rel_path, content in candidate_files.items():
            check = self.invariant_checker.check_source_code(content, file_path=rel_path)
            if not check.passed:
                return CanaryExecutionResult(
                    success=False,
                    exit_code=-1,
                    stdout="",
                    stderr="AST Safety Invariant check failed.",
                    duration_seconds=0.0,
                    rolled_back=True,
                    safety_violations=check.violations,
                    message="Canary blocked: AST safety invariants violated.",
                )

        # 2. Ephemeral Sandbox Provisioning
        start_time = time.time()
        with tempfile.TemporaryDirectory(prefix="alpha_canary_") as tmp_dir:
            canary_root = Path(tmp_dir)

            # Copy essential workspace files (or relevant packages) into canary sandbox
            try:
                for rel_path, content in candidate_files.items():
                    dest_file = canary_root / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    dest_file.write_text(content, encoding="utf-8")

                # Also link or copy test harness files if present
                for harness_item in ("tests", "pytest.ini", "pyproject.toml", "package.json"):
                    src = self.workspace_root / harness_item
                    if src.exists():
                        dst = canary_root / harness_item
                        if src.is_dir():
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src, dst)
            except Exception as exc:
                return CanaryExecutionResult(
                    success=False,
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    duration_seconds=round(time.time() - start_time, 2),
                    rolled_back=True,
                    message=f"Failed to provision canary sandbox: {exc}",
                )

            # 3. Parallel Shadow Execution inside Canary
            try:
                env = os.environ.copy()
                python_path = str(canary_root) + os.pathsep + str(self.workspace_root)
                harness_path = str(self.workspace_root / "backend" / "packages" / "harness")
                python_path += os.pathsep + harness_path
                if "PYTHONPATH" in env:
                    python_path += os.pathsep + env["PYTHONPATH"]
                env["PYTHONPATH"] = python_path
                env["PATH"] = str(Path(os.sys.executable).parent) + os.pathsep + env.get("PATH", "")

                proc = subprocess.run(
                    test_command,
                    shell=True,
                    cwd=str(canary_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout_seconds,
                    env=env,
                )
                duration = round(time.time() - start_time, 2)
                success = (proc.returncode == 0)

                return CanaryExecutionResult(
                    success=success,
                    exit_code=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    duration_seconds=duration,
                    rolled_back=not success,
                    message="Canary verification succeeded." if success else "Canary test failed; automatic rollback executed.",
                )

            except subprocess.TimeoutExpired:
                duration = round(time.time() - start_time, 2)
                return CanaryExecutionResult(
                    success=False,
                    exit_code=-2,
                    stdout="",
                    stderr=f"Canary execution timed out after {timeout_seconds} seconds.",
                    duration_seconds=duration,
                    rolled_back=True,
                    message="Canary timed out; automatic rollback executed.",
                )
            except Exception as exc:
                duration = round(time.time() - start_time, 2)
                return CanaryExecutionResult(
                    success=False,
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    duration_seconds=duration,
                    rolled_back=True,
                    message=f"Canary execution encountered unexpected error: {exc}",
                )
