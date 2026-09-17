"""AgentFinishedCritic: Evaluates execution history to ensure all tool errors and goals are settled."""

from __future__ import annotations

import logging
from typing import Any

from agent_workspace.critic.base import BaseCritic, CriticResult, CriticVerdict

logger = logging.getLogger(__name__)


class AgentFinishedCritic(BaseCritic):
    """Verifies that the agent is not exiting with pending uncaught errors or unfulfilled deliverables."""

    name: str = "agent_finished_critic"

    def __init__(self, check_unresolved_errors: bool = True, min_actions: int = 1):
        self.check_unresolved_errors = check_unresolved_errors
        self.min_actions = min_actions

    def evaluate(
        self,
        task_description: str,
        execution_history: list[dict[str, Any]] | None = None,
        workspace_dir: str | None = None,
        **kwargs: Any,
    ) -> CriticResult:
        history = execution_history or []

        # 1. Action count sanity check
        if len(history) < self.min_actions and len(task_description.split()) > 5:
            return CriticResult(
                verdict=CriticVerdict.WARNING,
                reason=f"Agent attempting completion after only {len(history)} action(s).",
                diagnostic_prompt="Are you certain the task was fulfilled? Very few actions were performed.",
                critic_name=self.name,
            )

        # 2. Check the most recent actions for unresolved failures
        if self.check_unresolved_errors and history:
            # Look at the last action
            last_entry = history[-1]
            last_status = last_entry.get("status")
            last_exit_code = last_entry.get("exit_code")
            last_error = last_entry.get("error")

            is_failed = (
                last_status in {"error", "failed"}
                or (last_exit_code is not None and last_exit_code != 0)
                or bool(last_error)
            )

            if is_failed:
                error_msg = str(last_error or f"exit code {last_exit_code}")
                return CriticResult(
                    verdict=CriticVerdict.REJECTED,
                    reason=f"The most recent action failed ({error_msg}) and was not followed by a recovery step.",
                    diagnostic_prompt=(
                        f"Critic rejection: Your last action encountered an error: {error_msg}. "
                        "Please diagnose the error, apply a fix, and verify before declaring completion."
                    ),
                    critic_name=self.name,
                    metadata={"last_entry": last_entry},
                )

        return CriticResult(
            verdict=CriticVerdict.APPROVED,
            reason="Execution history checks passed without unresolved errors.",
            critic_name=self.name,
            metadata={"history_length": len(history)},
        )
