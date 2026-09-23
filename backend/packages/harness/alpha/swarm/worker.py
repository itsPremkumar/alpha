"""Swarm Worker Backends: Permanent Bots, Ephemeral Subagents, and Git Worktrees.

All three backends execute their objective for real: each runs one model
invocation and reports the model's actual output as the task summary. There is
no canned summary, no fabricated confidence score, and no unconditional
success — when no chat models are configured the worker raises, the runner's
existing exception path records an honest task failure, and the plan fails.

Test seam: workers resolve their model through :func:`_resolve_model`, which
tests replace to stay offline (``test_swarm_worker_execution.py`` and the
``_offline_worker_models`` fixture in ``test_swarm_advanced_features.py``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from alpha.bots.health import get_health_monitor
from alpha.bots.registry import BotRegistry
from alpha.sandbox.worktrees import WorktreeManager
from alpha.swarm.models import SwarmPlan, SwarmTaskNode

logger = logging.getLogger(__name__)


class SwarmWorkerBackend(Protocol):
    """Protocol defining a worker execution backend."""

    def execute_task(self, task: SwarmTaskNode, plan: SwarmPlan) -> dict[str, Any]:
        """Executes a single swarm task and returns outcome summary and artifacts."""
        ...


def _resolve_model(model_name: str | None, *, worker_label: str):
    """Resolve the chat model a worker will invoke.

    Raises ``RuntimeError`` when no chat models are configured so the failure
    reaches the runner's ``mark_failed`` path — an unconfigurable worker must
    never report a fabricated success.
    """
    from alpha.config import get_app_config

    app_config = get_app_config()
    if not getattr(app_config, "models", None):
        raise RuntimeError(
            f"No chat models configured; {worker_label} cannot execute its task objective."
        )
    from alpha.models import create_chat_model

    return create_chat_model(model_name, app_config=app_config)


def _deliver_objective(*, model, system_prompt: str, objective: str, worker_label: str) -> str:
    """Run one model invocation and return its non-empty text output."""
    from langchain_core.messages import HumanMessage, SystemMessage

    response = model.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=objective),
        ]
    )
    content = getattr(response, "content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    # Preserve the model's exact output — a patch file must not lose its
    # trailing newline to a cosmetic strip — while still rejecting a
    # whitespace-only response as an empty one.
    text = str(content)
    if not text.strip():
        raise RuntimeError(f"{worker_label} model returned an empty response for its objective.")
    return text


def _model_label(model, requested: str | None) -> str:
    """Name of the model that actually produced the output (for evidence)."""
    return str(
        getattr(model, "model_name", None)
        or getattr(model, "model", None)
        or requested
        or "default"
    )


class SpecialistBotWorker:
    """Worker backed by a permanent Autonomous Specialist Bot profile from the BotRegistry."""

    def __init__(self, bot_name: str, registry: BotRegistry | None = None):
        self.bot_name = bot_name
        self.registry = registry or BotRegistry()
        self.health_monitor = get_health_monitor()

    def execute_task(self, task: SwarmTaskNode, plan: SwarmPlan) -> dict[str, Any]:
        bot = self.registry.get_bot(self.bot_name)
        if not bot:
            # Fallback to general worker if specified bot profile not found
            bot = self.registry.get_or_create(self.bot_name)

        # Record heartbeat & lease for the bot
        self.health_monitor.record_heartbeat(self.bot_name, task_id=task.task_id, lease_seconds=60.0)

        label = f"bot @{self.bot_name}"
        system_prompt = (
            f"{bot.soul}\n\n"
            f"You are executing a swarm task as @{bot.name} ({bot.role}) within the plan "
            f"'{plan.goal}'. Complete the objective and report what you actually did. "
            "Never ask for clarification."
        )
        model = _resolve_model(bot.model, worker_label=label)
        summary = _deliver_objective(
            model=model,
            system_prompt=system_prompt,
            objective=f"Task {task.task_id}: {task.objective}",
            worker_label=label,
        )
        # Evidence records what really ran — no invented confidence score.
        evidence = [
            {
                "source": f"bot:{self.bot_name}",
                "role": bot.role,
                "model": _model_label(model, bot.model),
            }
        ]
        return {
            "status": "success",
            "summary": summary,
            "evidence": evidence,
            "artifacts": list(task.output_artifacts),
        }


# Transparent alias for backward compatibility
HermesBotWorker = SpecialistBotWorker


class EphemeralSubagentWorker:
    """Lightweight, scoped subagent worker spun up for a single task."""

    def __init__(self, worker_id: str, model: str | None = None):
        self.worker_id = worker_id
        # None = the configured default model. The old "fast-model" fallback
        # was a name resolvable nowhere in the repo but here.
        self.model = model

    def execute_task(self, task: SwarmTaskNode, plan: SwarmPlan) -> dict[str, Any]:
        label = f"ephemeral worker {self.worker_id}"
        system_prompt = (
            f"You are ephemeral swarm worker {self.worker_id} executing the plan "
            f"'{plan.goal}'. Complete the task objective and report what you actually did."
        )
        model = _resolve_model(self.model, worker_label=label)
        summary = _deliver_objective(
            model=model,
            system_prompt=system_prompt,
            objective=f"Task {task.task_id}: {task.objective}",
            worker_label=label,
        )
        evidence = [
            {
                "worker_id": self.worker_id,
                "model": _model_label(model, self.model),
            }
        ]
        return {
            "status": "success",
            "summary": summary,
            "evidence": evidence,
            "artifacts": list(task.output_artifacts),
        }


class CodingWorktreeWorker:
    """Executes code modification tasks in an isolated Git worktree."""

    def __init__(self, repo_root: Path | str, branch_name: str):
        self.manager = WorktreeManager(repo_root=repo_root)
        self.branch_name = branch_name

    def execute_task(self, task: SwarmTaskNode, plan: SwarmPlan) -> dict[str, Any]:
        label = "worktree coder"
        system_prompt = (
            f"You are authoring a code change for the plan '{plan.goal}' in an isolated "
            "git worktree. Output ONLY a unified diff (--- a/... +++ b/... hunks) that "
            "implements the objective. If the objective needs no file edit, output "
            "NO_CHANGES."
        )
        # Fail fast on the model BEFORE provisioning a worktree so a no-models
        # run leaves no half-made side effects behind.
        model = _resolve_model(None, worker_label=label)
        diff_text = _deliver_objective(
            model=model,
            system_prompt=system_prompt,
            objective=f"Task {task.task_id}: {task.objective}",
            worker_label=label,
        )

        worktree = self.manager.create_worktree(branch_name=self.branch_name)
        patch_file = Path(worktree.path) / f"{task.task_id}.patch"
        patch_file.write_text(diff_text, encoding="utf-8")

        return {
            "status": "success",
            "summary": (
                f"Authored patch {patch_file.name} in worktree {Path(worktree.path).name} "
                f"({self.branch_name}) for: {task.objective}"
            ),
            "worktree_path": str(worktree.path),
            "evidence": [
                {
                    "source": "worktree",
                    "branch": self.branch_name,
                    "patch_file": str(patch_file),
                    "model": _model_label(model, None),
                }
            ],
            "artifacts": list(task.output_artifacts) + [str(patch_file)],
        }
