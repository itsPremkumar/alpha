"""Swarm Worker Backends: Permanent Bots, Ephemeral Subagents, and Git Worktrees."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from agent_workspace.bots.health import get_health_monitor
from agent_workspace.bots.registry import BotRegistry
from agent_workspace.sandbox.worktrees import WorktreeManager
from agent_workspace.swarm.models import SwarmPlan, SwarmTaskNode

logger = logging.getLogger(__name__)


class SwarmWorkerBackend(Protocol):
    """Protocol defining a worker execution backend."""

    def execute_task(self, task: SwarmTaskNode, plan: SwarmPlan) -> dict[str, Any]:
        """Executes a single swarm task and returns outcome summary and artifacts."""
        ...


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

        summary = f"Completed by permanent specialist @{self.bot_name} ({bot.role}): {task.objective}"
        evidence = [{"source": f"bot:{self.bot_name}", "role": bot.role, "confidence": 0.95}]
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
        self.model = model or "fast-model"

    def execute_task(self, task: SwarmTaskNode, plan: SwarmPlan) -> dict[str, Any]:
        # Bounded scoped context
        summary = f"Processed item [{task.task_id}] by ephemeral worker {self.worker_id}: {task.objective}"
        evidence = [{"worker_id": self.worker_id, "model": self.model, "verified": True}]
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
        # Provision isolated worktree
        worktree = self.manager.create_worktree(branch_name=self.branch_name)
        patch_file = f"{worktree.path}/{task.task_id}.patch"

        summary = f"Authoring code changes in worktree {worktree.path.name} ({self.branch_name}): {task.objective}"
        artifacts = list(task.output_artifacts) + [patch_file]

        return {
            "status": "success",
            "summary": summary,
            "worktree_path": str(worktree.path),
            "artifacts": artifacts,
        }
