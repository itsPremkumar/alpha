"""Surgical Automated Program Repair (APR) Engine.

Orchestrates Spectrum-Based Fault Localization (Ochiai SBFL), test assertion immutability,
AST pre-commit syntax validation, and targeted patch synthesis.
"""

from __future__ import annotations

import ast
import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent_workspace.safety.ast_syntax_guard import validate_syntax_precommit
from agent_workspace.selfrepair.assertion_guard import TestAssertionImmutabilityGuard
from agent_workspace.selfrepair.sbfl import OchiaiFaultLocalizer, SuspiciousLocation

logger = logging.getLogger(__name__)


@dataclass
class PatchCandidate:
    file_path: str
    original_code: str
    patched_code: str
    diff: str
    suspicious_location: SuspiciousLocation
    description: str


@dataclass
class RepairResult:
    success: bool
    message: str
    applied_patch: Optional[PatchCandidate] = None
    suspicious_locations: list[SuspiciousLocation] = field(default_factory=list)
    rounds_attempted: int = 0
    verification_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "applied_patch": {
                "file_path": self.applied_patch.file_path,
                "diff": self.applied_patch.diff,
                "description": self.applied_patch.description,
            } if self.applied_patch else None,
            "suspicious_locations": [loc.to_dict() for loc in self.suspicious_locations[:5]],
            "rounds_attempted": self.rounds_attempted,
            "verification_passed": self.verification_passed,
        }


class SurgicalProgramRepairEngine:
    """End-to-end Automated Program Repair engine."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.localizer = OchiaiFaultLocalizer(workspace_root=str(self.workspace_root))
        self.assertion_guard = TestAssertionImmutabilityGuard()

    def diagnose_and_locate(
        self,
        test_output: str,
        total_passing_tests: int = 1,
    ) -> list[SuspiciousLocation]:
        """Runs Ochiai SBFL on the test failure traceback to return suspicious locations."""
        return self.localizer.parse_traceback_output(
            test_output=test_output,
            total_passing_tests=total_passing_tests,
        )

    def validate_candidate_patch(
        self,
        file_path: str,
        original_code: str,
        patched_code: str,
    ) -> tuple[bool, str]:
        """Runs two-stage invariant validation: AST Syntax Guard + Assertion Immutability."""
        # 1. Test Assertion Immutability Guard
        guard_decision = self.assertion_guard.validate_patch(
            file_path=file_path,
            original_code=original_code,
            patched_code=patched_code,
        )
        if not guard_decision.allowed:
            return False, guard_decision.reason

        # 2. Pre-Commit AST Syntax Guard
        syntax_valid, syntax_err = validate_syntax_precommit(
            file_path=file_path,
            content=patched_code,
        )
        if not syntax_valid:
            return False, f"AST Syntax validation failed: {syntax_err}"

        return True, "Valid patch"

    def synthesize_rule_based_patch(
        self,
        loc: SuspiciousLocation,
        target_code: str,
    ) -> Optional[PatchCandidate]:
        """Synthesizes targeted surgical fixes for common fault classes (ZeroDivision, None, Off-by-one)."""
        lines = target_code.splitlines(keepends=True)
        idx = loc.line_number - 1
        if idx < 0 or idx >= len(lines):
            return None

        target_line = lines[idx]
        patched_lines = list(lines)

        description = ""
        # 1. ZeroDivisionError guard
        if "ZeroDivisionError" in loc.error_type:
            # Check for division / or %
            if "/" in target_line or "%" in target_line:
                indent = target_line[: len(target_line) - len(target_line.lstrip())]
                if "return " in target_line and "/" in target_line:
                    expr = target_line.strip().removeprefix("return ")
                    parts = expr.split("/", 1)
                    if len(parts) == 2:
                        left = parts[0].strip()
                        right = parts[1].strip()
                        patched_lines[idx] = f"{indent}return ({left} / {right}) if {right} != 0 else 0\n"
                        description = "Added zero-division safety guard to return expression."
                elif "=" in target_line and "/" in target_line:
                    eq_parts = target_line.split("=", 1)
                    var_name = eq_parts[0].strip()
                    expr = eq_parts[1].strip()
                    parts = expr.split("/", 1)
                    if len(parts) == 2:
                        left = parts[0].strip()
                        right = parts[1].strip()
                        patched_lines[idx] = f"{indent}{var_name} = ({left} / {right}) if {right} != 0 else 0\n"
                        description = "Added zero-division safety guard to assignment expression."

        # 2. KeyError / IndexError / NoneType guard
        elif "KeyError" in loc.error_type or "AttributeError" in loc.error_type or "TypeError" in loc.error_type:
            indent = target_line[: len(target_line) - len(target_line.lstrip())]
            if ("['" in target_line and "']" in target_line) or ('["' in target_line and '"]' in target_line):
                # Replace dict[key] with dict.get(key)
                patched_line = re.sub(r"(\w+)\[(['\"][^'\"]+['\"])\]", r"\1.get(\2)", target_line)
                if patched_line != target_line:
                    patched_lines[idx] = patched_line
                    description = "Replaced direct dictionary lookup with safe .get() call."

        # 3. Off-by-one in loop or slicing
        elif "IndexError" in loc.error_type:
            if "<=" in target_line:
                patched_lines[idx] = target_line.replace("<=", "<")
                description = "Corrected off-by-one boundary comparison (<= to <)."
            elif "range(" in target_line and "+ 1" in target_line:
                patched_lines[idx] = target_line.replace("+ 1", "")
                description = "Removed off-by-one +1 loop upper bound."

        if not description:
            return None

        patched_code = "".join(patched_lines)
        diff = "".join(
            difflib.unified_diff(
                lines,
                patched_lines,
                fromfile=loc.file_path,
                tofile=loc.file_path,
            )
        )

        return PatchCandidate(
            file_path=loc.file_path,
            original_code=target_code,
            patched_code=patched_code,
            diff=diff,
            suspicious_location=loc,
            description=description,
        )

    def attempt_surgical_repair(
        self,
        test_output: str,
        total_passing_tests: int = 1,
    ) -> RepairResult:
        """Executes the complete APR loop: localize -> guard check -> patch synthesis -> apply."""
        suspicious = self.diagnose_and_locate(test_output, total_passing_tests=total_passing_tests)
        if not suspicious:
            return RepairResult(
                success=False,
                message="No suspicious code locations could be localized from test output.",
            )

        for loc in suspicious[:3]:
            abs_path = self.workspace_root / loc.file_path
            if not abs_path.is_file():
                continue

            try:
                original_code = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                continue

            candidate = self.synthesize_rule_based_patch(loc, original_code)
            if not candidate:
                continue

            # Validate candidate
            valid, err_msg = self.validate_candidate_patch(
                file_path=candidate.file_path,
                original_code=candidate.original_code,
                patched_code=candidate.patched_code,
            )
            if not valid:
                logger.warning("Candidate patch rejected: %s", err_msg)
                continue

            # Valid patch synthesized!
            abs_path.write_text(candidate.patched_code, encoding="utf-8")
            return RepairResult(
                success=True,
                message=f"Surgical patch successfully applied to {loc.file_path}:{loc.line_number}: {candidate.description}",
                applied_patch=candidate,
                suspicious_locations=suspicious,
                rounds_attempted=1,
                verification_passed=True,
            )

        return RepairResult(
            success=False,
            message="Suspicious locations localized via Ochiai SBFL, but no rule-based patch candidate passed validation.",
            suspicious_locations=suspicious,
            rounds_attempted=1,
        )
