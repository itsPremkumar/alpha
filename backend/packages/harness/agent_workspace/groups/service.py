"""Group Chat Service with Zero-Config Room and Member Auto-Provisioning."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_workspace.bots.registry import get_bot_registry
from agent_workspace.groups.orchestration import GroupOrchestrator
from agent_workspace.groups.quorum import QuorumEngine
from agent_workspace.groups.room import GroupMessage, GroupRoom, OrchestrationMode, _now

logger = logging.getLogger(__name__)

_DEFAULT_GROUPS_DIR = "groups"


def _default_storage_path() -> Path:
    """Resolve rooms.json under the writable runtime home (AGENT_WORKSPACE_HOME-aware)."""
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / _DEFAULT_GROUPS_DIR / "rooms.json"
    except Exception:
        return Path.cwd() / ".deerflow" / _DEFAULT_GROUPS_DIR / "rooms.json"


class GroupChatService:
    """Manages group chat rooms, member enrollment, and multi-agent coordination."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rooms: dict[str, GroupRoom] = {}
        self.orchestrator = GroupOrchestrator()
        self.quorum = QuorumEngine()
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("rooms", []):
                room = GroupRoom.from_dict(item)
                self._rooms[room.name.lower()] = room
        except Exception:
            logger.warning("Group rooms load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "rooms": [r.to_dict() for r in self._rooms.values()],
                "updated_at": _now(),
            }
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Group rooms save failed", exc_info=True)

    def get_or_create_room(
        self,
        name: str,
        topic: str = "General Team Collaboration",
        members: Sequence[str] | None = None,
        mode: OrchestrationMode = "mention",
        moderator: str | None = None,
        project_id: str | None = None,
    ) -> GroupRoom:
        """Fetch existing room or auto-provision a new room and all missing bot members."""
        key = name.lower().strip()
        bot_registry = get_bot_registry()

        with self._lock:
            if key in self._rooms:
                room = self._rooms[key]
                # If new members are supplied, add them
                if members:
                    for m in members:
                        m_clean = m.lower().strip()
                        bot_registry.get_or_create(m_clean)
                        if m_clean not in room.members:
                            room.members.append(m_clean)
                    self._save()
                if project_id and not room.project_id:
                    room.project_id = project_id
                    self._save()
                return room

            # Auto-provision members in the bot registry
            default_members = list(members) if members else ["architect", "coder", "reviewer"]
            clean_members: list[str] = []
            for m in default_members:
                m_clean = m.lower().strip()
                bot_registry.get_or_create(m_clean)
                clean_members.append(m_clean)

            assigned_moderator = moderator.lower().strip() if moderator else clean_members[0]

            room = GroupRoom(
                room_id=f"room_{uuid4().hex[:8]}",
                name=key,
                topic=topic,
                members=clean_members,
                mode=mode,
                moderator=assigned_moderator,
                project_id=project_id,
            )
            self._rooms[key] = room
            self._save()
            return room

    def get_or_create_project_room(self, project_id: str, members: Sequence[str] | None = None) -> GroupRoom | None:
        """Bind a room to a project. Solo projects (0-1 members) get no room.

        Returns the room for multi-agent projects, None for solo work.
        """
        with self._lock:
            for room in self._rooms.values():
                if room.project_id == project_id:
                    if members:
                        bot_registry = get_bot_registry()
                        for m in members:
                            m_clean = m.lower().strip()
                            bot_registry.get_or_create(m_clean)
                            if m_clean not in room.members:
                                room.members.append(m_clean)
                        self._save()
                    return room
            unique = sorted({m.lower().strip() for m in (members or [])})
            if len(unique) < 2:
                return None
        # Outside the lock: get_or_create_room takes it again (non-reentrant).
        return self.get_or_create_room(
            f"project-{project_id}",
            topic=f"Project {project_id} team channel",
            members=unique,
            mode="moderated",
            moderator=unique[0],
            project_id=project_id,
        )

    def rooms_for_project(self, project_id: str) -> list[GroupRoom]:
        with self._lock:
            return [r for r in self._rooms.values() if r.project_id == project_id]

    def get_room(self, name: str) -> GroupRoom | None:
        with self._lock:
            return self._rooms.get(name.lower().strip())

    def list_rooms(self) -> list[GroupRoom]:
        with self._lock:
            return list(self._rooms.values())

    def post_message(
        self,
        room_name: str,
        sender: str,
        content: str,
        *,
        intent: str = "discussion",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[GroupMessage, list[str]]:
        """Post a message into a room and compute next scheduled speakers."""
        room = self.get_or_create_room(room_name)
        mentions = self.orchestrator.parse_mentions(content, room.members)

        msg = room.append_message(
            sender=sender,
            content=content,
            intent=intent,  # type: ignore[arg-type]
            mentions=mentions,
            metadata=metadata or {},
        )
        self._save()

        next_speakers = self.orchestrator.resolve_next_speakers(room, msg)
        return msg, next_speakers


_global_groups: GroupChatService | None = None
_global_groups_path: str | None = None


def get_group_chat_service(storage_path: str | Path | None = None) -> GroupChatService:
    """Return the process-wide group chat service (AGENT_WORKSPACE_HOME-aware).

    Same stale-path rebuild contract as get_bot_registry: an explicit path
    wins, otherwise the live runtime_home() location is used so import-time
    CWD never pins Gateway/Electron/Docker to the wrong directory.
    """
    global _global_groups, _global_groups_path
    if storage_path is not None:
        resolved = str(Path(storage_path).resolve())
        if _global_groups is None or _global_groups_path != resolved:
            _global_groups = GroupChatService(storage_path=resolved)
            _global_groups_path = resolved
        return _global_groups
    if _global_groups is None:
        _global_groups = GroupChatService()
        try:
            _global_groups_path = str(_global_groups.storage_path.resolve())
        except Exception:
            _global_groups_path = None
        return _global_groups
    try:
        live = str(_default_storage_path().resolve())
    except Exception:
        return _global_groups
    if _global_groups_path != live:
        _global_groups = GroupChatService()
        _global_groups_path = live
    return _global_groups
