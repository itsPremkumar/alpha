"""Data models for the Test-Driven Autonomous Reproduction Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReproductionStatus(str, Enum):
    NOT_STARTED = "not_started"
    PRE_FIX_FAILED = "pre_fix_failed"       # Expected: Bug reproduced successfully before fix
    PRE_FIX_PASSED = "pre_fix_passed"       # Failure: Bug failed to reproduce (test already passed)
    POST_FIX_PASSED = "post_fix_passed"     # Expected: Bug is proven fixed
    POST_FIX_FAILED = "post_fix_failed"     # Failure: Fix was unsuccessful
    REGRESSION_DETECTED = "regression"      # Failure: Adjacent existing tests failed
    VERIFIED_SOLVED = "verified_solved"     # Complete success


@dataclass
class ReproductionScript:
    """Reproduction script definition."""
    code: str
    script_name: str = "reproduce_issue.py"
    target_module: str | None = None
    description: str = ""


@dataclass
class GateResult:
    """Outcome of a reproduction or regression gate check."""
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    message: str
    duration_seconds: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReproductionReport:
    """Comprehensive report summarizing all reproduction gates and verification."""
    status: ReproductionStatus
    pre_fix_result: GateResult | None = None
    post_fix_result: GateResult | None = None
    regression_result: GateResult | None = None
    reproduction_script_path: str | None = None
    diagnostics: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_verified(self) -> bool:
        return self.status == ReproductionStatus.VERIFIED_SOLVED
