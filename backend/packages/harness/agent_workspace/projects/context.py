"""Project context distribution: role-filtered context, never full dumps.

Each agent receives only the slice relevant to its role plus what it
explicitly requests. Keeps prompts small and prevents cross-role confusion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent_workspace.projects import constitution as constitution_mod
from agent_workspace.projects import decisions as decisions_mod
from agent_workspace.projects import state as state_mod

ROLE_SECTIONS: dict[str, set[str]] = {
    "backend": {"overview", "tasks", "architecture", "decisions", "dependencies", "files_backend", "activity"},
    "frontend": {"overview", "tasks", "architecture", "decisions", "dependencies", "files_frontend", "activity"},
    "security": {"overview", "tasks", "decisions", "risks", "activity", "constitution"},
    "qa": {"overview", "tasks", "decisions", "tests", "activity"},
    "research": {"overview", "documents", "decisions", "activity"},
    "architect": {"overview", "tasks", "architecture", "decisions", "dependencies", "risks", "constitution", "activity"},
    "devops": {"overview", "tasks", "architecture", "dependencies", "risks", "activity"},
}

DEFAULT_SECTIONS = {"overview", "tasks", "decisions", "activity"}


@dataclass
class ProjectContext:
    project_id: str
    bot_role: str
    sections: dict[str, Any] = field(default_factory=dict)
    constitution_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def allowed_sections(bot_role: str) -> set[str]:
    role = (bot_role or "").lower()
    for key, sections in ROLE_SECTIONS.items():
        if key in role:
            return set(sections)
    return set(DEFAULT_SECTIONS)


def build_context(
    project_id: str,
    bot_role: str,
    *,
    extra_sections: set[str] | None = None,
    overview: str = "",
    tasks: list[dict[str, Any]] | None = None,
    architecture: str = "",
    files: list[str] | None = None,
    documents: list[str] | None = None,
    max_chars: int = 8000,
) -> ProjectContext:
    """Assemble a budgeted, role-filtered context snapshot (pure function)."""
    sections_allowed = allowed_sections(bot_role) | (extra_sections or set())
    sections: dict[str, Any] = {}
    budget = max_chars

    def take(name: str, value: Any) -> None:
        nonlocal budget
        if name not in sections_allowed or value in (None, "", [], {}):
            return
        text = value if isinstance(value, str) else str(value)
        if len(text) > budget:
            text = text[:budget] + "…[truncated]"
        sections[name] = value if isinstance(value, str) and len(value) <= budget else text
        budget = max(0, budget - len(text))

    state = state_mod.get_state(project_id)
    take("overview", overview or f"Goal: {state.goal} | Phase: {state.phase} | Active: {state.active_tasks} Blocked: {state.blocked_tasks} Done: {state.completed_tasks}")
    take("tasks", tasks or [])
    take("architecture", architecture or f"arch {state.arch_version}")
    recent = [d.to_dict() for d in decisions_mod.get_decision_log(project_id).list()[-5:]]
    take("decisions", recent)
    take("risks", state.open_risks)
    take("files_backend", [f for f in (files or []) if "backend" in f or f.endswith(".py")][:20])
    take("files_frontend", [f for f in (files or []) if "frontend" in f or f.endswith((".tsx", ".ts", ".css"))][:20])
    take("documents", (documents or [])[:20])
    take("activity", f"agents={state.active_agents} conflicts={state.open_conflicts} verified={state.last_verified}")

    const = constitution_mod.get_constitution(project_id)
    ctx = ProjectContext(project_id=project_id, bot_role=bot_role, sections=sections, constitution_hash=const.sha16 if const else None)
    if "constitution" in sections_allowed and const:
        take("constitution", const.markdown[:2000])
        ctx.sections["constitution"] = sections.get("constitution")
    return ctx


def get_three_level_context(
    project_id: str,
    bot_name: str,
    task_id: str | None = None,
    task_scratchpad: dict[str, Any] | None = None,
):
    """Retrieve the consolidated 3-tier isolated context envelope."""
    from agent_workspace.projects.context_router import get_three_level_router

    router = get_three_level_router()
    return router.build_isolated_context(
        project_id=project_id,
        bot_name=bot_name,
        task_id=task_id,
        task_scratchpad=task_scratchpad,
    )

