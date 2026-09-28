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
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_PROMOTION_MANAGER: SubagentPromotionManager | None = None

#: A metric-role key and a promoted Bot profile name are both used as *single
#: filename components* (``subagents/metrics/<role>.json`` and
#: ``bots/profiles/<name>.json``), and both are supplied by a caller: ``role``
#: and ``bot_name`` reach this module straight from the model-facing
#: ``subagent_control`` tool. ``.lower().strip()`` removes neither a path
#: separator nor ``..``, so ``"../../escaped"`` used to write a file outside the
#: profile/metrics directory. The component is therefore restricted to a
#: traversal-free charset that still admits every name this module itself
#: generates (``bot-postgres-optimizer``) and the conventional Bot names an
#: operator or model asks for.
_SAFE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")


def _safe_name_component(value: str, *, field_name: str) -> str:
    """Return *value* as one lowercase, traversal-free path component.

    Raises:
        ValueError: the name is empty, starts with a dot, or contains anything
            outside ``[a-z0-9._-]`` (which excludes ``/``, ``\\``, ``..`` and
            every path-ambiguous character on any supported platform).
    """
    name = value.lower().strip()
    if not _SAFE_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid {field_name} {value!r}: expected a lowercase name of letters, digits, '-', '_' or '.' with no path separators")
    return name


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
    """Tracks subagent role metrics and executes promotions to permanent specialist bots."""

    MIN_EXECUTIONS_FOR_PROMOTION = 5
    MIN_RELIABILITY_FOR_PROMOTION = 0.80

    def __init__(self, storage_dir: Path | str | None = None):
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base = os.environ.get("ALPHA_HOME", "~/.alpha")
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
        """Records an execution outcome for a subagent role archetype.

        Raises:
            ValueError: *role* is not usable as a metric filename component
                (see :func:`_safe_name_component`). The role is the
                ``subagents/metrics/<role>.json`` filename, so an unvalidated
                value would let a caller write outside the metrics directory.
        """
        role_key = _safe_name_component(role, field_name="role")
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
        # Defense in depth: ``role_key`` is validated at its entry points, and
        # this re-checks that the joined path is still a plain child of the
        # metrics directory before anything is written.
        target = self.storage_dir / f"{_safe_name_component(role_key, field_name='role')}.json"
        if target.parent.resolve() != self.storage_dir.resolve():
            raise ValueError(f"Refusing to write subagent metric outside {self.storage_dir}")
        try:
            with open(target, "w", encoding="utf-8") as fp:
                json.dump(m.to_dict(), fp, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save subagent metric for {role_key}: {exc}")

    def check_promotion_eligibility(self, role: str) -> tuple[bool, dict[str, Any]]:
        """Checks if a role qualifies for promotion to a permanent Specialist Bot."""
        role_key = _safe_name_component(role, field_name="role")
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
        """Materializes the subagent role into a permanent Specialist Bot definition.

        The persisted profile's own history line reports ``success_count``, not
        ``total_executions``: the latter includes every failed execution, so
        quoting it as *"successful task executions"* overstated the track
        record of any role promoted below 100% reliability (a role can be
        promoted at 0.80, i.e. one failure in five).

        Raises:
            ValueError: *role* or *bot_name* is not usable as a filename
                component (see :func:`_safe_name_component`).
        """
        role_key = _safe_name_component(role, field_name="role")
        m = self._metrics.get(role_key)

        total_executions = m.total_executions if m is not None else 0
        successful_executions = m.success_count if m is not None else 0
        failed_executions = m.failure_count if m is not None else 0
        history = f"{successful_executions} successful task executions out of {total_executions} recorded ({successful_executions}/{total_executions})" if total_executions else "no recorded executions"

        clean_name = _safe_name_component(bot_name, field_name="bot_name") if bot_name else f"bot-{role_key.replace('_', '-')}"
        clean_display = display_name or f"{role.replace('_', ' ').title()} Bot"
        clean_desc = description or f"Promoted permanent Specialist Bot specializing in {role} operations."

        system_prompt = f"You are {clean_display}, a permanent Specialist Bot.\nRole: {role}\nDescription: {clean_desc}\n\nHistorical background: Promoted from an autonomous subagent with {history}."

        bot_profile = {
            "name": clean_name,
            "display_name": clean_display,
            "description": clean_desc,
            "system_prompt": system_prompt,
            "system_role": "specialist",
            "skills": m.common_skills if m else [],
            "tools": m.common_tools if m else [],
            "origin": "promoted_subagent",
            "total_executions": total_executions,
            "successful_executions": successful_executions,
            "failed_executions": failed_executions,
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # Persist to permanent bots directory if configured
        base = os.environ.get("ALPHA_HOME", "~/.alpha")
        bots_dir = Path(os.path.expanduser(base)) / "bots" / "profiles"
        bots_dir.mkdir(parents=True, exist_ok=True)
        bot_file = bots_dir / f"{clean_name}.json"
        # Defense in depth: ``clean_name`` is already a validated component, so
        # this can only fail if a name slipped past the guard above. Refuse
        # rather than write outside the profile directory.
        if bot_file.parent.resolve() != bots_dir.resolve():
            raise ValueError(f"Invalid bot_name {bot_name!r}: refusing to write a profile outside {bots_dir}")
        try:
            with open(bot_file, "w", encoding="utf-8") as fp:
                json.dump(bot_profile, fp, indent=2)
            logger.info(f"Subagent role '{role}' successfully promoted to permanent Specialist Bot '{clean_name}'.")
        except Exception as exc:
            logger.warning(f"Failed to persist promoted bot profile {bot_file}: {exc}")

        return bot_profile
