"""Project crew: the single owner of "who is on this project and how they work together".

Before this module, three stores each held part of the answer and none of them
talked to each other:

- ``membership.json``  — who is on the project (``membership.py``)
- ``rooms.json``       — how they talk (``groups/service.py``)
- ``context.json``     — what they collectively know (``context_router.py``)

That split is why ``GroupChatService.get_or_create_project_room`` was written but
never called: nothing owned the job of keeping the three consistent. This module
is that owner. Every attach/detach/read funnels through it, and it reconciles
membership and the group room on **every** call, so drift cannot accumulate.

Invariants enforced here:

1. ``room.members`` mirrors ``membership.presence()`` — membership is authoritative.
2. A room exists **iff** the crew has 2+ agents. Dropping to 1 *parks* the room
   (history is preserved) rather than deleting it.
3. Leaving releases the departing agent's locks and emits ``agent_left``.
4. Every reconciliation that changed something emits ``room_synced``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OPERATOR = "operator"
SUPERVISOR = "supervisor"
DEFAULT_MODERATOR = "architect"

ORCHESTRATION_MODES = ("mention", "moderated", "quorum", "parallel", "round_robin")
MENTION_POLICIES = ("strict", "advisory")
CONFLICT_POLICIES = ("block", "vote", "moderator")
LOCK_POLICIES = ("advisory", "strict")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    return value.lower().strip()


def _projects_root(base_dir: Path | str | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir).resolve() / "projects"
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".agent-workspace" / "projects"


@dataclass
class CollaborationConfig:
    """Per-project coordination policy surfaced as "advanced settings".

    Defaults reproduce the behaviour the codebase had before this existed, so
    enabling the crew layer changes nothing for projects that never touch it.
    """

    orchestration_mode: str = "moderated"
    moderator: str | None = None
    max_concurrent_speakers: int = 3
    mention_policy: str = "strict"
    auto_handoff: bool = True
    conflict_policy: str = "moderator"
    lock_policy: str = "advisory"
    require_evidence: bool = True
    memory_budget_chars: int = 6000
    transcript_digest_n: int = 40
    standup_interval_turns: int = 10

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollaborationConfig:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)

    def resolve_moderator(self, members: list[str]) -> str | None:
        """Pick the moderator: explicit setting, else architect, else first member."""
        if self.moderator:
            return _slug(self.moderator)
        wanted = _slug(DEFAULT_MODERATOR)
        if wanted in members:
            return wanted
        return members[0] if members else None

    def validate_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Validate a settings patch, raising ValueError on anything unknown/invalid."""
        clean: dict[str, Any] = {}
        for key, value in patch.items():
            if key not in self.__dataclass_fields__:
                raise ValueError(f"Unknown collaboration setting '{key}'")
            if key == "orchestration_mode" and value not in ORCHESTRATION_MODES:
                raise ValueError(f"orchestration_mode must be one of {ORCHESTRATION_MODES}")
            if key == "mention_policy" and value not in MENTION_POLICIES:
                raise ValueError(f"mention_policy must be one of {MENTION_POLICIES}")
            if key == "conflict_policy" and value not in CONFLICT_POLICIES:
                raise ValueError(f"conflict_policy must be one of {CONFLICT_POLICIES}")
            if key == "lock_policy" and value not in LOCK_POLICIES:
                raise ValueError(f"lock_policy must be one of {LOCK_POLICIES}")
            for bounded in ("max_concurrent_speakers", "memory_budget_chars", "transcript_digest_n", "standup_interval_turns"):
                if key == bounded and (not isinstance(value, int) or value < 1):
                    raise ValueError(f"{bounded} must be a positive integer")
            if key == "moderator" and value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("moderator must be a non-empty string or null")
            clean[key] = value
        return clean


@dataclass
class MemberBrief:
    """One agent's participation, flattened for the UI."""

    bot_name: str
    role_in_project: str = "worker"
    status: str = "active"
    current_task_id: str | None = None
    blocked_reason: str | None = None
    last_activity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoomBrief:
    """The project's group room, or None for solo projects."""

    name: str
    mode: str
    moderator: str | None
    members: list[str] = field(default_factory=list)
    message_count: int = 0
    parked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CrewView:
    """Everything the UI needs about a crew, in one payload."""

    project_id: str
    members: list[MemberBrief] = field(default_factory=list)
    room: RoomBrief | None = None
    collaboration: CollaborationConfig = field(default_factory=CollaborationConfig)
    shared_memory: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    active_locks: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["members"] = [m.to_dict() for m in self.members]
        data["room"] = self.room.to_dict() if self.room else None
        data["collaboration"] = self.collaboration.to_dict()
        return data


class ProjectCrewService:
    """Reconciles membership, group room, and shared memory for a project."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else _projects_root().parent
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ config

    def _config_path(self, project_id: str) -> Path:
        return _projects_root(self.base_dir) / _slug(project_id) / "project-config" / "collaboration.json"

    def get_collaboration(self, project_id: str) -> CollaborationConfig:
        path = self._config_path(project_id)
        if not path.exists():
            return CollaborationConfig()
        try:
            with open(path, encoding="utf-8") as f:
                return CollaborationConfig.from_dict(json.load(f))
        except Exception:
            logger.warning("Collaboration config load failed for %s; using defaults", project_id, exc_info=True)
            return CollaborationConfig()

    def set_collaboration(self, project_id: str, **patch: Any) -> CollaborationConfig:
        """Merge a validated patch into the project's collaboration settings."""
        current = self.get_collaboration(project_id)
        clean = current.validate_patch(patch)
        with self._lock:
            updated = CollaborationConfig.from_dict({**current.to_dict(), **clean})
            path = self._config_path(project_id)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(updated.to_dict(), f, indent=2)
                tmp.replace(path)
            except Exception:
                logger.warning("Collaboration config save failed for %s", project_id, exc_info=True)
                raise
        return updated

    # ------------------------------------------------------------------- sync

    def _sync_room(self, project_id: str, wanted: list[str]) -> tuple[Any, list[str]]:
        """Reconcile the project's group room against ``wanted`` membership.

        Returns ``(room | None, changes)``. ``changes`` is empty when the room
        was already consistent, which keeps ``crew_view`` cheap on repeat reads.
        """
        from alpha.groups.service import get_group_chat_service

        svc = get_group_chat_service()
        cfg = self.get_collaboration(project_id)
        changes: list[str] = []

        rooms = svc.rooms_for_project(project_id)
        room = rooms[0] if rooms else None

        if room is None:
            if len(wanted) < 2:
                return None, changes
            room = svc.get_or_create_project_room(project_id, wanted)
            if room is None:
                return None, changes
            changes.append("room_created")

        # Membership is authoritative: anything in the room but not on the crew
        # is stale and gets removed. The human operator is never auto-removed.
        for name in wanted:
            if name not in room.members:
                room.members.append(name)
                changes.append(f"joined:{name}")
        for name in list(room.members):
            if name == OPERATOR:
                continue
            if name not in wanted:
                room.members.remove(name)
                changes.append(f"removed:{name}")

        if room.mode != cfg.orchestration_mode:
            room.mode = cfg.orchestration_mode  # type: ignore[assignment]
            changes.append("mode")
        moderator = cfg.resolve_moderator(wanted)
        if moderator and room.moderator != moderator:
            room.moderator = moderator
            changes.append("moderator")

        if changes:
            svc.persist()
        return room, changes

    def _emit_sync(self, project_id: str, room: Any, changes: list[str]) -> None:
        from alpha.projects.events import get_event_bus

        get_event_bus(project_id).emit(
            "room_synced",
            SUPERVISOR,
            {"room": getattr(room, "name", None), "changes": changes},
        )

    # ------------------------------------------------------------------- view

    def _view(self, project_id: str, members: list[Any], room: Any) -> CrewView:
        from alpha.projects.context_router import get_three_level_router
        from alpha.projects.locks import get_lock_manager
        from alpha.projects.state import get_state

        briefs = [
            MemberBrief(
                bot_name=m.bot_name,
                role_in_project=m.role_in_project,
                status=m.status,
                current_task_id=m.current_task_id,
                blocked_reason=m.blocked_reason,
                last_activity=m.last_activity,
            )
            for m in members
        ]

        room_brief = None
        if room is not None:
            room_brief = RoomBrief(
                name=room.name,
                mode=room.mode,
                moderator=room.moderator,
                members=list(room.members),
                message_count=len(room.log),
                parked=len(members) < 2,
            )

        try:
            shared = get_three_level_router().get_project_memory(project_id)
        except Exception:
            logger.debug("Shared memory unavailable for %s", project_id, exc_info=True)
            shared = {}

        try:
            proj_state = get_state(project_id)
            state_payload = {
                "goal": proj_state.goal,
                "phase": proj_state.phase,
                "active_agents": proj_state.active_agents,
                "open_conflicts": proj_state.open_conflicts,
                "blocked_tasks": proj_state.blocked_tasks,
                "updated_at": proj_state.updated_at,
            }
        except Exception:
            logger.debug("Project state unavailable for %s", project_id, exc_info=True)
            state_payload = {}

        try:
            locks = [lock.to_dict() for lock in get_lock_manager().list_locks(project_id)]
        except Exception:
            logger.debug("Lock listing unavailable for %s", project_id, exc_info=True)
            locks = []

        try:
            from alpha.projects.events import get_event_bus

            events = [ev.to_dict() for ev in get_event_bus(project_id).read(limit=20)]
        except Exception:
            logger.debug("Event read unavailable for %s", project_id, exc_info=True)
            events = []

        return CrewView(
            project_id=project_id,
            members=briefs,
            room=room_brief,
            collaboration=self.get_collaboration(project_id),
            shared_memory=shared,
            state=state_payload,
            active_locks=locks,
            recent_events=events,
        )

    # ------------------------------------------------------------------- API

    def _maybe_compact(self, project_id: str) -> dict[str, Any] | None:
        """Fold transcript overflow into Level-2 memory. Never fails the caller."""
        try:
            from alpha.projects.memory_bridge import maybe_compact

            return maybe_compact(project_id)
        except Exception:
            logger.debug("Transcript compaction skipped for %s", project_id, exc_info=True)
            return None

    def compact_transcript(self, project_id: str) -> dict[str, Any] | None:
        """Force a compaction pass; returns None when already inside budget."""
        with self._lock:
            return self._maybe_compact(project_id)

    def ensure_crew(self, project_id: str) -> CrewView:
        """Make the crew consistent. Safe to call on every read."""
        from alpha.projects.membership import get_membership_store
        from alpha.projects.workspace import ensure_workspace

        with self._lock:
            ensure_workspace(project_id)
            members = get_membership_store().presence(project_id)
            room, changes = self._sync_room(project_id, [m.bot_name for m in members])
            if changes:
                self._emit_sync(project_id, room, changes)
            self._maybe_compact(project_id)
            return self._view(project_id, members, room)

    def attach(
        self,
        project_id: str,
        bots: list[str] | list[tuple[str, str]],
        role: str = "worker",
    ) -> CrewView:
        """Add one or more agents to the crew and reconcile the room.

        ``bots`` accepts either names (``["coder", "reviewer"]``) or
        ``(name, role)`` pairs (``[("coder", "lead"), ("reviewer", "qa")]``).
        """
        from alpha.bots.registry import get_bot_registry
        from alpha.projects.events import get_event_bus
        from alpha.projects.membership import get_membership_store
        from alpha.projects.workspace import ensure_workspace

        with self._lock:
            ensure_workspace(project_id)
            store = get_membership_store()
            registry = get_bot_registry()

            for entry in bots:
                name, entry_role = (entry, role) if isinstance(entry, str) else (entry[0], entry[1] or role)
                clean = _slug(name)
                if not clean:
                    continue
                registry.get_or_create(clean)
                store.join(project_id, clean, entry_role)
                get_event_bus(project_id).emit("agent_joined", clean, {"role": entry_role})

            members = store.presence(project_id)
            room, changes = self._sync_room(project_id, [m.bot_name for m in members])
            if changes:
                self._emit_sync(project_id, room, changes)
            return self._view(project_id, members, room)

    def detach(self, project_id: str, bot_name: str) -> CrewView:
        """Remove an agent, release its locks, and reconcile the room."""
        from alpha.projects.events import get_event_bus
        from alpha.projects.locks import get_lock_manager
        from alpha.projects.membership import get_membership_store
        from alpha.projects.workspace import ensure_workspace

        clean = _slug(bot_name)
        with self._lock:
            ensure_workspace(project_id)
            store = get_membership_store()
            removed = store.leave(project_id, clean)

            if removed:
                get_event_bus(project_id).emit("agent_left", clean, {})
                # A departing agent must not keep files locked forever.
                manager = get_lock_manager()
                for lock in manager.list_locks(project_id):
                    if _slug(lock.owner_bot) == clean:
                        try:
                            manager.release(lock.lock_id, SUPERVISOR)
                        except Exception:
                            logger.warning("Lock release failed for %s on %s", clean, lock.lock_id, exc_info=True)

            members = store.presence(project_id)
            room, changes = self._sync_room(project_id, [m.bot_name for m in members])
            if changes:
                self._emit_sync(project_id, room, changes)
            return self._view(project_id, members, room)

    def crew_view(self, project_id: str) -> CrewView:
        """Read-only view; still reconciles so the UI never renders stale drift."""
        return self.ensure_crew(project_id)


_service: ProjectCrewService | None = None
_service_path: str | None = None
_service_lock = threading.Lock()


def get_crew_service(base_dir: Path | str | None = None) -> ProjectCrewService:
    """Return the process-wide crew service.

    Same stale-path rebuild contract as ``get_membership_store`` and
    ``get_group_chat_service``: an explicit path wins, otherwise the live
    ``runtime_home()`` location is used so a moving ``AGENT_WORKSPACE_HOME``
    never leaves the crew reading another directory's config.
    """
    global _service, _service_path
    with _service_lock:
        if base_dir is not None:
            return ProjectCrewService(base_dir)
        try:
            live = str(_projects_root().parent.resolve())
        except Exception:
            live = None
        if _service is None or _service_path != live:
            _service = ProjectCrewService()
            _service_path = str(_service.base_dir)
        return _service
