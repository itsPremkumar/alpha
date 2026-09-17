"""EmptyPatchCritic: ensures code-modifying tasks actually produce a non-empty patch."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from agent_workspace.critic.base import BaseCritic, CriticResult, CriticVerdict

logger = logging.getLogger(__name__)

# Keywords indicating that the task entails modifying or writing files/code
CODE_MODIFICATION_KEYWORDS: set[str] = {
    "fix", "bug", "implement", "add", "refactor", "change", "update",
    "modify", "create", "write", "patch", "delete", "remove", "build",
    "optimize", "clean", "rewrite", "replace", "introduce", "develop",
}

READ_ONLY_KEYWORDS: set[str] = {
    "explain", "what is", "why", "how does", "summarize", "analyze",
    "find", "search", "where is", "list", "describe", "check whether",
}


class EmptyPatchCritic(BaseCritic):
    """Rejects agent completion if a code modification was expected but no files were altered."""

    name: str = "empty_patch_critic"

    def __init__(self, force_check: bool = False):
        self.force_check = force_check

    def requires_patch(self, task_description: str) -> bool:
        """Heuristically determine if the task implies workspace modifications."""
        if self.force_check:
            return True

        text = task_description.lower()
        words = set(text.replace(".", " ").replace(",", " ").split())

        # If explicitly read-only questions without modification request
        if any(ro in text for ro in READ_ONLY_KEYWORDS) and not any(w in words for w in CODE_MODIFICATION_KEYWORDS):
            return False

        return any(keyword in words for keyword in CODE_MODIFICATION_KEYWORDS)

    def evaluate(
        self,
        task_description: str,
        execution_history: list[dict[str, Any]] | None = None,
        workspace_dir: str | None = None,
        **kwargs: Any,
    ) -> CriticResult:
        """Inspect the git status or workspace files to ensure a patch exists."""
        if not self.requires_patch(task_description):
            return CriticResult(
                verdict=CriticVerdict.APPROVED,
                reason="Task appears to be read-only or investigatory; no code patch required.",
                critic_name=self.name,
                metadata={"is_code_task": False},
            )

        target_dir = workspace_dir or os.getcwd()

        try:
            # Check if git is present and target_dir is a git repository
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )

            if res.returncode == 0:
                porcelain_output = res.stdout.strip()
                diff_res = subprocess.run(
                    ["git", "diff", "--stat"],
                    cwd=target_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                )
                diff_stat = diff_res.stdout.strip() if diff_res.returncode == 0 else ""

                if not porcelain_output and not diff_stat:
                    return CriticResult(
                        verdict=CriticVerdict.REJECTED,
                        reason="Task requested code modifications, but working tree is clean with no staged or unstaged changes.",
                        diagnostic_prompt=(
                            "Critic rejection: You attempted to complete a code modification task, "
                            "but `git status` shows zero modified or created files. "
                            "Please ensure you have edited the relevant files, run any necessary tests, "
                            "and verified your changes before completing the task."
                        ),
                        critic_name=self.name,
                        metadata={"git_porcelain": "", "is_code_task": True},
                    )

                return CriticResult(
                    verdict=CriticVerdict.APPROVED,
                    reason=f"Workspace contains changes:\n{porcelain_output[:200]}",
                    critic_name=self.name,
                    metadata={
                        "git_porcelain": porcelain_output,
                        "diff_stat": diff_stat,
                        "is_code_task": True,
                    },
                )
        except Exception as e:
            logger.warning(f"EmptyPatchCritic git check failed: {e}")

        # Fallback: Check execution history for any file writing/editing tool calls
        if execution_history:
            has_write = any(
                step.get("tool_name") in {"write_to_file", "replace_file_content", "edit_file", "write_file", "hashline_edit"}
                or step.get("type") in {"file_edit", "write_file"}
                for step in execution_history
            )
            if has_write:
                return CriticResult(
                    verdict=CriticVerdict.APPROVED,
                    reason="Execution history contains file write or edit operations.",
                    critic_name=self.name,
                    metadata={"history_write_found": True},
                )

        return CriticResult(
            verdict=CriticVerdict.APPROVED,
            reason="Git check unavailable, skipping empty patch assertion.",
            critic_name=self.name,
            metadata={"skipped": True},
        )
