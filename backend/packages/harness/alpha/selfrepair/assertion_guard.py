"""Test Assertion Immutability Guard.

Strictly protects test suites during Automated Program Repair (APR).
Guarantees that test assertions (assert, expect, assertEqual) cannot be weakened,
modified, commented out, or deleted by the repair agent to game benchmark scores.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class AssertionImmutabilityViolationError(Exception):
    """Raised when a candidate patch attempts to delete or weaken a test assertion."""
    pass


@dataclass
class GuardDecision:
    allowed: bool
    reason: str = ""
    deleted_assertions: list[str] = field(default_factory=list)
    original_assertion_count: int = 0
    patched_assertion_count: int = 0


class TestAssertionImmutabilityGuard:
    """AST-level and lexical verification that test assertions remain immutable."""

    TEST_FILE_PATTERNS = (
        r"^tests?/",
        r"/tests?/",
        r"test_[^/]+\.py$",
        r"[^/]+_test\.py$",
        r"[^/]+\.test\.[jt]sx?$",
        r"[^/]+\.spec\.[jt]sx?$",
    )

    def is_test_file(self, file_path: str) -> bool:
        """Determines if the given file path belongs to a test suite."""
        norm = file_path.replace("\\", "/")
        return any(re.search(pat, norm) for pat in self.TEST_FILE_PATTERNS)

    def extract_python_assertions(self, code: str) -> list[str]:
        """Extracts canonical assertion statements from Python code via AST."""
        try:
            tree = ast.parse(code)
        except Exception:
            # Fallback regex
            return self._extract_regex_assertions(code)

        assertions: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                try:
                    assertions.append(ast.unparse(node.test).strip())
                except Exception:
                    assertions.append("assert")
            elif isinstance(node, ast.Call):
                # Check for self.assert*, pytest.raises, etc.
                call_name = ""
                if isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    call_name = node.func.id
                if call_name.startswith("assert") or call_name in ("expect", "raises"):
                    try:
                        assertions.append(ast.unparse(node).strip())
                    except Exception:
                        assertions.append(call_name)
        return assertions

    def _extract_regex_assertions(self, code: str) -> list[str]:
        """Regex fallback for JS/TS/Python assertions."""
        patterns = [
            r"assert\s+[^\n;]+",
            r"expect\([^)]+\)\.[^\n;]+",
            r"self\.assert\w+\([^)]+\)",
            r"assert\.\w+\([^)]+\)",
        ]
        results = []
        for pat in patterns:
            results.extend([m.strip() for m in re.findall(pat, code)])
        return results

    def validate_patch(
        self,
        file_path: str,
        original_content: str = "",
        patched_content: str = "",
        original_code: str = "",
        patched_code: str = "",
    ) -> GuardDecision:
        """Validates that a patch does not violate assertion immutability."""
        orig = original_content or original_code
        patched = patched_content or patched_code
        if not self.is_test_file(file_path):
            # Production code edits do not violate test assertion immutability
            return GuardDecision(allowed=True, reason="Not a test file")

        orig_assertions = self.extract_python_assertions(orig)
        patched_assertions = self.extract_python_assertions(patched)

        orig_counts = Counter(orig_assertions)
        patched_counts = Counter(patched_assertions)

        # Check if any original assertions were deleted or reduced in frequency
        deleted: list[str] = []
        for assertion, count in orig_counts.items():
            if patched_counts[assertion] < count:
                for _ in range(count - patched_counts[assertion]):
                    deleted.append(assertion)

        if deleted:
            return GuardDecision(
                allowed=False,
                reason=(
                    f"Test assertion immutability violated: {len(deleted)} original "
                    f"assertion(s) were deleted or weakened in '{file_path}'. "
                    f"APR must fix the underlying implementation, not alter tests."
                ),
                deleted_assertions=deleted,
                original_assertion_count=len(orig_assertions),
                patched_assertion_count=len(patched_assertions),
            )

        if len(patched_assertions) < len(orig_assertions):
            return GuardDecision(
                allowed=False,
                reason=(
                    f"Test assertion count decreased from {len(orig_assertions)} to "
                    f"{len(patched_assertions)} in '{file_path}'."
                ),
                original_assertion_count=len(orig_assertions),
                patched_assertion_count=len(patched_assertions),
            )

        return GuardDecision(
            allowed=True,
            reason="All original test assertions preserved.",
            original_assertion_count=len(orig_assertions),
            patched_assertion_count=len(patched_assertions),
        )
