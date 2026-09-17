"""ConfirmationPolicy: Enforces execution gating based on Shell AST security reports."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from agent_workspace.security.shell_ast.analyzer import (
    AnalysisReport,
    RiskLevel,
    ShellASTSecurityAnalyzer,
)
from agent_workspace.security.shell_ast.parser import ShellASTParser

logger = logging.getLogger(__name__)


class ExecutionDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    BLOCK = "block"


class ShellSecurityException(Exception):
    """Raised when a shell command is blocked by security policy."""
    def __init__(self, message: str, report: AnalysisReport):
        super().__init__(message)
        self.report = report


@dataclass
class PolicyEvaluationResult:
    decision: ExecutionDecision
    command: str
    report: AnalysisReport
    explanation: str

    @property
    def is_allowed(self) -> bool:
        return self.decision == ExecutionDecision.ALLOW

    @property
    def is_blocked(self) -> bool:
        return self.decision == ExecutionDecision.BLOCK


class ConfirmationPolicy:
    """Security policy engine evaluating shell commands prior to execution."""

    def __init__(
        self,
        auto_confirm_medium: bool = True,
        auto_confirm_high: bool = False,
    ):
        self.parser = ShellASTParser()
        self.analyzer = ShellASTSecurityAnalyzer()
        self.auto_confirm_medium = auto_confirm_medium
        self.auto_confirm_high = auto_confirm_high

    def evaluate(self, command_line: str) -> PolicyEvaluationResult:
        """Parse command line and evaluate policy decision."""
        ast_root = self.parser.parse(command_line)
        report = self.analyzer.analyze(ast_root)

        if report.risk_level == RiskLevel.BLOCKED:
            violations_text = "; ".join(v.message for v in report.violations)
            return PolicyEvaluationResult(
                decision=ExecutionDecision.BLOCK,
                command=command_line,
                report=report,
                explanation=f"Command BLOCKED by security policy: {violations_text}",
            )

        if report.risk_level == RiskLevel.HIGH:
            if self.auto_confirm_high:
                return PolicyEvaluationResult(
                    decision=ExecutionDecision.ALLOW,
                    command=command_line,
                    report=report,
                    explanation="High risk command auto-confirmed by policy.",
                )
            return PolicyEvaluationResult(
                decision=ExecutionDecision.REQUIRE_CONFIRMATION,
                command=command_line,
                report=report,
                explanation="High risk command requires user/operator confirmation.",
            )

        if report.risk_level == RiskLevel.MEDIUM:
            if self.auto_confirm_medium:
                return PolicyEvaluationResult(
                    decision=ExecutionDecision.ALLOW,
                    command=command_line,
                    report=report,
                    explanation="Medium risk command allowed under standard policy.",
                )
            return PolicyEvaluationResult(
                decision=ExecutionDecision.REQUIRE_CONFIRMATION,
                command=command_line,
                report=report,
                explanation="Medium risk command requires operator confirmation.",
            )

        return PolicyEvaluationResult(
            decision=ExecutionDecision.ALLOW,
            command=command_line,
            report=report,
            explanation="Command verified safe by Shell AST analyzer.",
        )

    def validate_or_raise(self, command_line: str) -> None:
        """Validate command and raise ShellSecurityException if blocked."""
        eval_result = self.evaluate(command_line)
        if eval_result.decision == ExecutionDecision.BLOCK:
            raise ShellSecurityException(
                f"Shell AST Security Guard blocked command: {eval_result.explanation}",
                report=eval_result.report,
            )
