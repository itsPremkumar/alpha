"""Three-Level Memory and Context Isolation Router (Hermes Bot Mode & Agent OS Architecture).

Enforces:
1. Level 1: Global Bot Memory (~/.agent_workspace/bots/<bot>/memory.json) - Persona, habits, style, learned lessons
2. Level 2: Shared Project Memory (~/.agent_workspace/projects/<project>/context.json) - Architecture, ADRs, constitution, active locks
3. Level 3: Task-Scoped Memory - Ephemeral scratchpad, active thread messages, recent tool results, diffs

Strict Project Isolation:
Bot A working on Project 1 never leaks Project 1 memory to Project 2.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_workspace.config.runtime_paths import runtime_home
from agent_workspace.projects import constitution as constitution_mod
from agent_workspace.projects import decisions as decisions_mod
from agent_workspace.projects import locks as locks_mod
from agent_workspace.projects import state as state_mod

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ThreeLevelContext:
    """Consolidated 3-tier context snapshot for an executing agent."""

    bot_name: str
    project_id: str
    task_id: str | None = None
    level1_bot_memory: dict[str, Any] = field(default_factory=dict)
    level2_project_memory: dict[str, Any] = field(default_factory=dict)
    level3_task_scratchpad: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def system_prompt_snippet(self, max_chars: int = 4000) -> str:
        """Render a structured, budgeted context block for system prompt injection."""
        lines = [
            f"=== 3-Tier Isolated Context [Agent: {self.bot_name} | Project: {self.project_id}] ===",
        ]

        # Level 1: Bot Personal Memory
        if self.level1_bot_memory:
            lines.append("\n[Level 1: Bot Personal Memory]")
            for k, v in self.level1_bot_memory.items():
                if k not in ("created_at", "updated_at"):
                    val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                    lines.append(f"• {k}: {val_str}")

        # Level 2: Shared Project Memory
        if self.level2_project_memory:
            lines.append("\n[Level 2: Shared Project State & Constraints]")
            for k, v in self.level2_project_memory.items():
                if k not in ("created_at", "updated_at"):
                    val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                    lines.append(f"• {k}: {val_str}")

        # Level 3: Task Ephemeral Scratchpad
        if self.level3_task_scratchpad:
            lines.append(f"\n[Level 3: Task Execution Scratchpad (Task: {self.task_id or 'ad-hoc'})]")
            for k, v in self.level3_task_scratchpad.items():
                val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                lines.append(f"• {k}: {val_str}")

        content = "\n".join(lines)
        if len(content) > max_chars:
            return content[:max_chars] + "\n...[Context Budget Truncated]"
        return content


class ThreeLevelContextRouter:
    """Thread-safe context router managing Level 1, Level 2, and Level 3 context boundaries."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else runtime_home()
        self._lock = threading.Lock()

    def _bot_memory_path(self, bot_name: str) -> Path:
        clean = bot_name.lower().strip()
        p = self.base_dir / "bots" / clean / "memory.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _project_memory_path(self, project_id: str) -> Path:
        clean = project_id.lower().strip()
        p = self.base_dir / "projects" / clean / "context.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_bot_memory(self, bot_name: str) -> dict[str, Any]:
        """Retrieve Level 1 Global Bot Memory."""
        p = self._bot_memory_path(bot_name)
        if not p.exists():
            return {
                "persona_style": "concise, direct, action-oriented",
                "learned_lessons": [],
                "preferred_tools": [],
                "updated_at": _now(),
            }
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.debug("Failed to read bot memory for %s", bot_name, exc_info=True)
            return {"updated_at": _now()}

    def update_bot_memory(self, bot_name: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update Level 1 Global Bot Memory."""
        with self._lock:
            p = self._bot_memory_path(bot_name)
            current = self.get_bot_memory(bot_name)
            current.update(updates)
            current["updated_at"] = _now()
            try:
                tmp = p.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(current, f, indent=2)
                tmp.replace(p)
            except Exception:
                logger.warning("Failed to save bot memory for %s", bot_name, exc_info=True)
            return current

    def get_project_memory(self, project_id: str) -> dict[str, Any]:
        """Retrieve Level 2 Shared Project Memory."""
        p = self._project_memory_path(project_id)
        proj_state = state_mod.get_state(project_id)
        recent_decisions = [d.to_dict() for d in decisions_mod.get_decision_log(project_id).list()[-3:]]
        active_locks = [l.to_dict() for l in locks_mod.get_lock_manager().list_locks(project_id)]
        const = constitution_mod.get_constitution(project_id)

        base_memory: dict[str, Any] = {
            "project_id": project_id,
            "goal": proj_state.goal,
            "phase": proj_state.phase,
            "recent_decisions": recent_decisions,
            "active_locks": [f"{l['scope']}:{l['path']} (by {l['owner_bot']})" for l in active_locks],
            "constitution_hash": const.sha16 if const else "none",
            "updated_at": _now(),
        }

        # The conversation itself is shared memory too. Without this the crew
        # holds meetings that evaporate: an agent joining later, or one whose
        # context rolled over, has no record of what was agreed or who asked it
        # for something. See projects/memory_bridge.py.
        try:
            from agent_workspace.projects.memory_bridge import build_memory_extras

            base_memory.update(build_memory_extras(project_id))
        except Exception:
            logger.debug("Transcript digest unavailable for %s", project_id, exc_info=True)

        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    file_data = json.load(f)
                    base_memory.update(file_data)
            except Exception:
                logger.debug("Failed to read custom project memory for %s", project_id, exc_info=True)

        return base_memory

    def update_project_memory(self, project_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update Level 2 Shared Project Memory."""
        with self._lock:
            p = self._project_memory_path(project_id)
            current = self.get_project_memory(project_id)
            current.update(updates)
            current["updated_at"] = _now()
            try:
                tmp = p.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(current, f, indent=2)
                tmp.replace(p)
            except Exception:
                logger.warning("Failed to save project memory for %s", project_id, exc_info=True)
            return current

    def build_isolated_context(
        self,
        project_id: str,
        bot_name: str,
        task_id: str | None = None,
        task_scratchpad: dict[str, Any] | None = None,
    ) -> ThreeLevelContext:
        """Assemble the 3-level context envelope with strict cross-project isolation."""
        bot_mem = self.get_bot_memory(bot_name)
        proj_mem = self.get_project_memory(project_id)
        scratchpad = task_scratchpad or {}

        return ThreeLevelContext(
            bot_name=bot_name,
            project_id=project_id,
            task_id=task_id,
            level1_bot_memory=bot_mem,
            level2_project_memory=proj_mem,
            level3_task_scratchpad=scratchpad,
        )


_DEFAULT_ROUTER: ThreeLevelContextRouter | None = None


def get_three_level_router() -> ThreeLevelContextRouter:
    global _DEFAULT_ROUTER
    if _DEFAULT_ROUTER is None:
        _DEFAULT_ROUTER = ThreeLevelContextRouter()
    return _DEFAULT_ROUTER
