"""Multi-agent group chat rooms API.

Makes the harness-layer GroupChatService a real system part: rooms with
orchestration modes (mention/moderated/quorum/parallel/round_robin),
member auto-provisioning against the BotRegistry, message posting with
@mention parsing, and deterministic next-speaker resolution.

Persistence is file-backed under AGENT_WORKSPACE_HOME (single-instance by design,
like agent_storage file backend). All sync file IO runs via asyncio.to_thread
to respect the Gateway blocking-IO gate.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/groups", tags=["groups"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage group rooms."

_ROOM_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
_BOT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VALID_MODES = ("mention", "moderated", "quorum", "parallel", "round_robin")
_VALID_INTENTS = ("discussion", "proposal", "vote", "action", "pass", "card_update")


def _validate_room_name(name: str) -> str:
    cleaned = name.strip()
    if not _ROOM_NAME_RE.match(cleaned):
        raise HTTPException(
            status_code=422,
            detail="Room name must be 1-64 chars: letters, digits, space, '_' or '-'.",
        )
    return cleaned


def _validate_members(members: list[str] | None) -> list[str] | None:
    if members is None:
        return None
    cleaned: list[str] = []
    for m in members:
        key = m.lower().strip()
        if not _BOT_NAME_RE.match(key):
            raise HTTPException(status_code=422, detail=f"Invalid member name '{m}'.")
        if key not in cleaned:
            cleaned.append(key)
    if len(cleaned) > 50:
        raise HTTPException(status_code=422, detail="A room supports at most 50 members.")
    return cleaned


def _service():
    from agent_workspace.groups.service import get_group_chat_service

    return get_group_chat_service()


def _room_to_response(room) -> dict:
    data = room.to_dict()
    return {
        "room_id": data.get("room_id"),
        "name": data.get("name"),
        "topic": data.get("topic"),
        "members": data.get("members", []),
        "mode": data.get("mode"),
        "moderator": data.get("moderator"),
        "project_id": data.get("project_id"),
        "message_count": len(data.get("log", [])),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


class RoomCreateRequest(BaseModel):
    name: str = Field(max_length=64)
    topic: str = Field(default="General Team Collaboration", max_length=500)
    members: list[str] | None = Field(default=None, max_length=50)
    mode: str = Field(default="mention")
    moderator: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)


class RoomMessageRequest(BaseModel):
    sender: str = Field(max_length=64)
    content: str = Field(min_length=1, max_length=20000)
    intent: str = Field(default="discussion")
    metadata: dict | None = None


@router.get("", summary="List group rooms")
async def list_rooms() -> dict:
    def _list():
        return [_room_to_response(r) for r in _service().list_rooms()]

    rooms = await asyncio.to_thread(_list)
    return {"rooms": rooms, "count": len(rooms)}


@router.post("", status_code=201, summary="Get or create room")
async def create_room(body: RoomCreateRequest) -> dict:
    name = _validate_room_name(body.name)
    members = _validate_members(body.members)
    mode = body.mode.strip().lower()
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {list(_VALID_MODES)}")
    moderator = body.moderator.lower().strip() if body.moderator else None
    if moderator and not _BOT_NAME_RE.match(moderator):
        raise HTTPException(status_code=422, detail="Invalid moderator name.")
    topic = body.topic.strip() or "General Team Collaboration"

    def _create():
        return _service().get_or_create_room(name, topic=topic, members=members, mode=mode, moderator=moderator, project_id=body.project_id)

    room = await asyncio.to_thread(_create)
    return _room_to_response(room)


class ProjectRoomRequest(BaseModel):
    members: list[str] = Field(..., min_length=1, max_length=50)


@router.post("/by-project/{project_id}", summary="Get or create project team room")
async def project_room(project_id: str, body: ProjectRoomRequest) -> dict:
    """Solo projects (0-1 members) get no room; teams share one channel."""
    members = _validate_members(body.members)

    def _get_or_create():
        return _service().get_or_create_project_room(project_id, members)

    room = await asyncio.to_thread(_get_or_create)
    if room is None:
        return {"project_id": project_id, "mode": "solo", "room": None}
    return {"project_id": project_id, "mode": "team", "room": _room_to_response(room)}


@router.get("/by-project/{project_id}", summary="List project team rooms")
async def project_rooms(project_id: str) -> dict:
    def _list():
        return [_room_to_response(r) for r in _service().rooms_for_project(project_id)]

    rooms = await asyncio.to_thread(_list)
    return {"project_id": project_id, "rooms": rooms, "count": len(rooms)}


@router.get("/{name}", summary="Get room with recent messages")
async def get_room(name: str, limit: int = 50) -> dict:
    key = _validate_room_name(name)
    limit = max(1, min(limit, 200))

    def _get():
        svc = _service()
        room = svc.get_room(key)
        if room is None:
            return None
        return room.to_dict()

    data = await asyncio.to_thread(_get)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")
    log = data.get("log", [])[-limit:]
    response = _room_to_response(type("R", (), {"to_dict": lambda self: data})())
    response["messages"] = log
    return response


@router.post("/{name}/messages", status_code=201, summary="Post message + resolve next speakers")
async def post_room_message(name: str, body: RoomMessageRequest) -> dict:
    key = _validate_room_name(name)
    sender = body.sender.strip()
    if not sender or len(sender) > 64:
        raise HTTPException(status_code=422, detail="sender is required (max 64 chars).")
    intent = body.intent.strip().lower()
    if intent not in _VALID_INTENTS:
        raise HTTPException(status_code=422, detail=f"intent must be one of {list(_VALID_INTENTS)}")

    def _post():
        return _service().post_message(key, sender, body.content, intent=intent, metadata=body.metadata or {})

    msg, next_speakers = await asyncio.to_thread(_post)
    return {"message": msg.to_dict(), "next_speakers": next_speakers}


class GroupRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=20000)
    members: list[str] | None = Field(default=None, max_length=50)
    moderator: str | None = Field(default=None, max_length=64)
    max_parallel: int = Field(default=3, ge=1, le=3)


@router.post("/{name}/runs", status_code=202, summary="Start autonomous team run")
async def start_group_run(name: str, body: GroupRunRequest) -> dict:
    """One prompt in, coordinated multi-agent work out.

    Fans the objective out to one subagent per member (BotProfile role + SOUL,
    no clarification blocking), then a moderator synthesis pass merges the
    outputs. Progress and the final deliverable land in the room log; poll
    the run record for status. Returns 202 immediately — execution continues
    in the background.
    """
    from agent_workspace.runtime.user_context import get_effective_user_id

    key = _validate_room_name(name)
    members = _validate_members(body.members)
    if members is not None and not members:
        raise HTTPException(status_code=422, detail="members must not be empty (omit to use the room members).")
    moderator = body.moderator.lower().strip() if body.moderator else None
    if moderator and not _BOT_NAME_RE.match(moderator):
        raise HTTPException(status_code=422, detail="Invalid moderator name.")

    try:
        # start_run must execute on the event loop (it spawns the background
        # task there); only the blocking parts run off-loop inside the service.
        from agent_workspace.groups.runner import get_group_run_service

        user_id: str | None = None
        try:
            user_id = get_effective_user_id()
        except Exception:
            user_id = None
        run = get_group_run_service().start_run(
            key,
            body.objective,
            members=members,
            moderator=moderator,
            user_id=user_id,
            max_parallel=body.max_parallel,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.to_dict()


@router.get("/{name}/runs", summary="List autonomous team runs")
async def list_group_runs(name: str) -> dict:
    key = _validate_room_name(name)

    def _list():
        from agent_workspace.groups.runner import get_group_run_service

        return [r.to_dict() for r in get_group_run_service().list_runs(room_name=key)]

    runs = await asyncio.to_thread(_list)
    return {"runs": runs, "count": len(runs)}


@router.get("/{name}/runs/{run_id}", summary="Get autonomous team run")
async def get_group_run(name: str, run_id: str) -> dict:
    key = _validate_room_name(name)

    def _get():
        from agent_workspace.groups.runner import get_group_run_service

        return get_group_run_service().get_run(run_id)

    run = await asyncio.to_thread(_get)
    if run is None or run.room_name.lower() != key.lower():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found in room '{key}'.")
    return run.to_dict()


@router.post("/{name}/runs/{run_id}/cancel", summary="Cancel autonomous team run")
async def cancel_group_run(name: str, run_id: str) -> dict:
    key = _validate_room_name(name)

    def _cancel():
        from agent_workspace.groups.runner import get_group_run_service

        svc = get_group_run_service()
        run = svc.get_run(run_id)
        if run is None or run.room_name.lower() != key.lower():
            return None
        return run if svc.cancel_run(run_id) else False

    outcome = await asyncio.to_thread(_cancel)
    if outcome is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found in room '{key}'.")
    if outcome is False:
        raise HTTPException(status_code=409, detail=f"Run '{run_id}' is already terminal.")
    return {"run_id": run_id, "status": "cancelling"}


@router.delete("/{name}", status_code=204, summary="Delete group room (admin)")
async def delete_room(name: str, request: Request) -> None:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_room_name(name)

    def _delete() -> bool:
        svc = _service()
        with svc._lock:
            removed = svc._rooms.pop(key.lower(), None)
            if removed is None:
                return False
            svc._save()
            return True

    deleted = await asyncio.to_thread(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")
