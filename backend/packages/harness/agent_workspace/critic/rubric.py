"""RubricEvaluator: Evaluates task completion against explicit rubrics or test acceptance suites."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_workspace.critic.base import BaseCritic, CriticResult, CriticVerdict

logger = logging.getLogger(__name__)


@dataclass
class RubricCriterion:
    """A single criterion in an evaluation rubric."""
    name: str
    description: str
    required: bool = True
    test_command: str | None = None
    required_file: str | None = None
    custom_checker: Callable[[dict[str, Any]], bool] | None = None


class RubricEvaluator(BaseCritic):
    """Evaluates task execution against a checklist of acceptance criteria."""

    name: str = "rubric_evaluator"

    def __init__(self, criteria: list[RubricCriterion] | None = None):
        self.criteria: list[RubricCriterion] = criteria or []

    def add_criterion(self, criterion: RubricCriterion) -> None:
        self.criteria.append(criterion)

    def evaluate(
        self,
        task_description: str,
        execution_history: list[dict[str, Any]] | None = None,
        workspace_dir: str | None = None,
        **kwargs: Any,
    ) -> CriticResult:
        if not self.criteria:
            return CriticResult(
                verdict=CriticVerdict.APPROVED,
                reason="No rubrics defined; evaluation passes.",
                critic_name=self.name,
            )

        target_dir = workspace_dir or os.getcwd()
        passed_criteria: list[str] = []
        failed_criteria: list[str] = []
        details: dict[str, Any] = {}

        for criterion in self.criteria:
            crit_name = criterion.name
            passed = True
            failure_reason = ""

            # 1. File existence check
            if criterion.required_file:
                fp = Path(target_dir) / criterion.required_file
                if not fp.exists():
                    passed = False
                    failure_reason = f"Required file '{criterion.required_file}' not found."

            # 2. Test command execution check
            if passed and criterion.test_command:
                try:
                    res = subprocess.run(
                        criterion.test_command,
                        cwd=target_dir,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                    )
                    if res.returncode != 0:
                        passed = False
                        failure_reason = f"Command '{criterion.test_command}' failed with exit code {res.returncode}:\n{res.stderr[:200]}"
                except Exception as e:
                    passed = False
                    failure_reason = f"Command '{criterion.test_command}' execution error: {e}"

            # 3. Custom lambda / checker
            if passed and criterion.custom_checker:
                try:
                    context_data = {
                        "workspace_dir": target_dir,
                        "task_description": task_description,
                        "history": execution_history or [],
                    }
                    if not criterion.custom_checker(context_data):
                        passed = False
                        failure_reason = "Custom rubric check returned False."
                except Exception as e:
                    passed = False
                    failure_reason = f"Custom rubric checker error: {e}"

            details[crit_name] = {"passed": passed, "reason": failure_reason}
            if passed:
                passed_criteria.append(crit_name)
            else:
                failed_criteria.append(crit_name)

        if failed_criteria:
            failed_str = ", ".join(failed_criteria)
            return CriticResult(
                verdict=CriticVerdict.REJECTED,
                reason=f"Failed rubrics: {failed_str}",
                diagnostic_prompt=(
                    f"Critic rejection: The following acceptance criteria failed verification: {failed_str}. "
                    f"Details: {details}"
                ),
                critic_name=self.name,
                metadata={"passed": passed_criteria, "failed": failed_criteria, "details": details},
            )

        return CriticResult(
            verdict=CriticVerdict.APPROVED,
            reason=f"All {len(self.criteria)} rubric criteria passed.",
            critic_name=self.name,
            metadata={"passed": passed_criteria, "details": details},
        )
