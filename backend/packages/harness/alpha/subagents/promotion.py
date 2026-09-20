"""Sub-Agent Promotion Engine: Promotes Recurring Ephemeral Subagents into Permanent Specialist Bots.

Tracks subagent execution frequency, reliability score, and domain specialization.
When a temporary role exhibits consistent high-performance across recurring tasks,
the Promotion Engine materializes it into a permanent Specialist Bot profile with
persistent identity and tool configurations.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_PROMOTION_MANAGER: SubagentPromotionManager | None = None


@dataclass
class SubagentRoleMetric:
    role: str
    total_executions: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_runtime_seconds: float = 0.0
    last_used: str = ""
    sample_instructions: list[str] = field(default_factory=list)
    common_skills: list[str] = field(default_factory=list)
    common_tools: list[str] = field(default_factory=list)

    @property
    def reliability(self) -> float:
        if self.total_executions == 0:
            return 0.0
        return self.success_count / self.total_executions

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reliability"] = round(self.reliability, 3)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubagentRoleMetric:
        clean = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**clean)


def get_subagent_promotion_manager(storage_dir: Path | str | None = None) -> SubagentPromotionManager:
    """Returns singleton instance of SubagentPromotionManager."""
    global _GLOBAL_PROMOTION_MANAGER
    if _GLOBAL_PROMOTION_MANAGER is None:
        _GLOBAL_PROMOTION_MANAGER = SubagentPromotionManager(storage_dir=storage_dir)
    return _GLOBAL_PROMOTION_MANAGER


class SubagentPromotionManager:
    """Tracks subagent role metrics and executes promotions to permanent Hermes Bots."""

    MIN_EXECUTIONS_FOR_PROMOTION = 5
    MIN_RELIABILITY_FOR_PROMOTION = 0.80

    def __init__(self, storage_dir: Path | str | None = None):
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base = os.environ.get("AGENT_WORKSPACE_HOME", "~/.agent-workspace")
            self.storage_dir = Path(os.path.expanduser(base)) / "subagents" / "metrics"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._metrics: dict[str, SubagentRoleMetric] = {}
        self._load_metrics()

    def _load_metrics(self) -> None:
        if not self.storage_dir.exists():
            return
        for f in self.storage_dir.glob("*.json"):
            try:
                role = f.stem
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                    self._metrics[role] = SubagentRoleMetric.from_dict(data)
            except Exception as exc:
                logger.warning(f"Failed to load subagent metric {f}: {exc}")

    def record_execution(
        self,
        role: str,
        success: bool,
        runtime_seconds: float = 0.0,
        instruction: str = "",
        skills: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> SubagentRoleMetric:
        """Records an execution outcome for a subagent role archetype."""
        role_key = role.lower().strip()
        if role_key not in self._metrics:
            self._metrics[role_key] = SubagentRoleMetric(role=role_key)

        m = self._metrics[role_key]
        m.total_executions += 1
        if success:
            m.success_count += 1
        else:
            m.failure_count += 1

        m.total_runtime_seconds += runtime_seconds
        m.last_used = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if instruction and len(m.sample_instructions) < 5:
            m.sample_instructions.append(instruction[:200])

        if skills:
            curr_skills = set(m.common_skills)
            curr_skills.update(skills)
            m.common_skills = list(curr_skills)[:10]

        if tools:
            curr_tools = set(m.common_tools)
            curr_tools.update(tools)
            m.common_tools = list(curr_tools)[:10]

        # Checkpoint to disk
        self._save_metric(role_key)
        return m

    def _save_metric(self, role_key: str) -> None:
        m = self._metrics.get(role_key)
        if not m:
            return
        target = self.storage_dir / f"{role_key}.json"
        try:
            with open(target, "w", encoding="utf-8") as fp:
                json.dump(m.to_dict(), fp, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save subagent metric for {role_key}: {exc}")

    def check_promotion_eligibility(self, role: str) -> tuple[bool, dict[str, Any]]:
        """Checks if a role qualifies for promotion to a permanent Specialist Bot."""
        role_key = role.lower().strip()
        m = self._metrics.get(role_key)
        if not m:
            return False, {"reason": f"Role '{role}' has no recorded executions."}

        is_eligible = m.total_executions >= self.MIN_EXECUTIONS_FOR_PROMOTION and m.reliability >= self.MIN_RELIABILITY_FOR_PROMOTION

        return is_eligible, {
            "role": m.role,
            "total_executions": m.total_executions,
            "reliability": m.reliability,
            "required_executions": self.MIN_EXECUTIONS_FOR_PROMOTION,
            "required_reliability": self.MIN_RELIABILITY_FOR_PROMOTION,
            "eligible": is_eligible,
        }

    def list_candidates(self) -> list[dict[str, Any]]:
        """Lists all roles that meet promotion criteria."""
        candidates = []
        for role_key, m in self._metrics.items():
            if m.total_executions >= self.MIN_EXECUTIONS_FOR_PROMOTION and m.reliability >= self.MIN_RELIABILITY_FOR_PROMOTION:
                candidates.append(m.to_dict())
        return candidates

    def promote_to_specialist_bot(
        self,
        role: str,
        bot_name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Materializes the subagent role into a permanent Specialist Bot definition."""
        role_key = role.lower().strip()
        m = self._metrics.get(role_key)

        clean_name = (bot_name or f"bot-{role_key.replace('_', '-')}").lower().strip()
        clean_display = display_name or f"{role.replace('_', ' ').title()} Bot"
        clean_desc = description or f"Promoted permanent Specialist Bot specializing in {role} operations."

        system_prompt = (
            f"You are {clean_display}, a permanent Specialist Bot.\n"
            f"Role: {role}\n"
            f"Description: {clean_desc}\n\n"
            f"Historical background: Promoted from an autonomous subagent with "
            f"{m.total_executions if m else 0} successful task executions."
        )

        bot_profile = {
            "name": clean_name,
            "display_name": clean_display,
            "description": clean_desc,
            "system_prompt": system_prompt,
            "system_role": "specialist",
            "skills": m.common_skills if m else [],
            "tools": m.common_tools if m else [],
            "origin": "promoted_subagent",
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # Persist to permanent bots directory if configured
        base = os.environ.get("AGENT_WORKSPACE_HOME", "~/.agent-workspace")
        bots_dir = Path(os.path.expanduser(base)) / "bots" / "profiles"
        bots_dir.mkdir(parents=True, exist_ok=True)
        bot_file = bots_dir / f"{clean_name}.json"
        try:
            with open(bot_file, "w", encoding="utf-8") as fp:
                json.dump(bot_profile, fp, indent=2)
            logger.info(f"Subagent role '{role}' successfully promoted to permanent Specialist Bot '{clean_name}'.")
        except Exception as exc:
            logger.warning(f"Failed to persist promoted bot profile {bot_file}: {exc}")

        return bot_profile

    # Backward compatibility alias
    promote_to_hermes_bot = promote_to_specialist_bot
