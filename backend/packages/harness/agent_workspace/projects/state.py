"""Canonical shared project state: one reduced snapshot per project.

Derived from the event bus, never hand-maintained by agents. Agents query
this before acting instead of trusting their own conversation-local view.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_workspace.projects.events import ProjectEvent, get_event_bus

logger = logging.getLogger(__name__)

LIFECYCLE = ("create", "understand", "plan", "staff", "execute", "coordinate", "verify", "review", "improve", "complete", "archive")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _projects_root() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "projects"


def state_path(project_id: str) -> Path:
    return _projects_root() / project_id / "project-config" / "state.json"


@dataclass
class ProjectState:
    project_id: str
    goal: str = ""
    phase: str = "create"
    active_tasks: int = 0
    blocked_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    active_agents: int = 0
    arch_version: str = "v0.1"
    latest_decision: str | None = None
    open_conflicts: int = 0
    open_risks: list[str] = field(default_factory=list)
    last_verified: str | None = None
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, project_id: str, data: dict[str, Any]) -> ProjectState:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        filtered.pop("project_id", None)
        return cls(project_id=project_id, **filtered)


_lock = threading.Lock()


def _apply(state: ProjectState, ev: ProjectEvent) -> None:
    t, p = ev.type, ev.payload
    if t == "agent_joined":
        state.active_agents += 1
    elif t == "agent_left":
        state.active_agents = max(0, state.active_agents - 1)
    elif t == "task_created":
        state.active_tasks += 1
    elif t in ("task_completed", "task_failed"):
        state.active_tasks = max(0, state.active_tasks - 1)
        if t == "task_completed":
            state.completed_tasks += 1
        else:
            state.failed_tasks += 1
        if p.get("verified_at"):
            state.last_verified = p["verified_at"]
    elif t == "task_blocked":
        state.blocked_tasks += 1
    elif t == "decision_made":
        state.latest_decision = p.get("decision_id") or p.get("title")
        if p.get("arch_version"):
            state.arch_version = p["arch_version"]
    elif t == "conflict_detected":
        state.open_conflicts += 1
    elif t == "conflict_resolved":
        state.open_conflicts = max(0, state.open_conflicts - 1)
    elif t == "phase_changed" and p.get("phase") in LIFECYCLE:
        state.phase = p["phase"]
    if p.get("risk") and p["risk"] not in state.open_risks:
        state.open_risks.append(p["risk"])


def rebuild_state(project_id: str, *, goal: str = "") -> ProjectState:
    """Fold the full event log into a fresh canonical snapshot."""
    state = ProjectState(project_id=project_id, goal=goal)
    for ev in get_event_bus(project_id).read(limit=100000):
        _apply(state, ev)
    state.updated_at = _now()
    return state


def get_state(project_id: str) -> ProjectState:
    path = state_path(project_id)
    if path.exists():
        try:
            return ProjectState.from_dict(project_id, json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            logger.warning("Project state read failed for %s; rebuilding", project_id, exc_info=True)
    state = rebuild_state(project_id)
    save_state(state)
    return state


def save_state(state: ProjectState) -> None:
    path = state_path(state.project_id)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)


def refresh_state(project_id: str) -> ProjectState:
    """Re-fold the log and persist. Call after bursts of events, not per event."""
    current = get_state(project_id)
    state = rebuild_state(project_id, goal=current.goal)
    save_state(state)
    return state


def set_phase(project_id: str, phase: str, actor: str = "supervisor") -> ProjectState:
    if phase not in LIFECYCLE:
        raise ValueError(f"Unknown lifecycle phase '{phase}'")
    get_event_bus(project_id).emit("phase_changed", actor, {"phase": phase})
    return refresh_state(project_id)
