"""Multi-agent group chat rooms API.

Makes the harness-layer GroupChatService a real system part: rooms with
orchestration modes (mention/moderated/quorum/parallel/round_robin),
member auto-provisioning against the BotRegistry, message posting with
@mention parsing, and deterministic next-speaker resolution.

Persistence is file-backed under ALPHA_HOME (single-instance by design,
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

from alpha.groups.presence import resolve_room_presence
from alpha.groups.roster import RosterError, VALID_RULE_FIELDS, VALID_RULE_OPS
from alpha.groups.room import REACTION_EMOJI, VALID_INTENTS
from alpha.groups.scope import (
    MAX_DEPTH,
    MAX_HOP,
    ROOM_STATES,
    ScopeError,
    VALID_INBOUND,
    VALID_OUTBOUND,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/groups", tags=["groups"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage group rooms."
#: The operator identity recorded on roster changes. A room member added by a
#: human is attributed here; agent senders are recorded on the message itself.
OPERATOR_ID = "operator"

_ROOM_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
_BOT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VALID_MODES = ("mention", "moderated", "quorum", "parallel", "round_robin")
#: `presence` derives effective membership, so a nested room shows the bots it
#: inherits. The single source of truth is the service's `resolved_roster`.
_ROSTER_PRESENCE = "roster"
#: Route aliases the presence layer answers, so an older Gateway path stays
#: reachable. Removed when the dashboard no longer links to it.
_PRESENCE_ALIASES = ("presence", "attendance", "roll_call")
# Declared literally rather than prefixed with `name`: a `/{name}/{alias}` pattern
# would shadow every sibling route above it, since Starlette matches in
# registration order.
#: The room's intents are the harness's single vocabulary
#: (`alpha.groups.room.MessageIntent`), imported rather than restated. This
#: module used to declare its own six-value tuple while the operator composer
#: offered thirteen, so seven kinds — including `decision`, which is what "Post
#: as group decision" sends — were answered with a 422.
_VALID_INTENTS = VALID_INTENTS
#: Emoji a reaction may use. Bounded on the server so the stored set cannot grow
#: without a code change.
_VALID_REACTIONS = REACTION_EMOJI
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: Suffixes that must stay reachable below the `/{name}` catch-all. Starlette
#: matches in registration order, so `GET /tree` declared after `GET /{name}`
#: answers `Room 'tree' not found` — indistinguishable from an absent route.
#: Same trap as `skills/{skill_name}` and `workflows/{workflow_id}`.
_LITERAL_PREFIXES = ("by-project", "tree")


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
    from alpha.groups.service import get_group_chat_service

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
    #: One-line description of the whole group, shown when collapsed.
    summary: str = Field(default="", max_length=280)
    #: Nest this room under an existing group, by name. `None` makes a root.
    parent: str | None = Field(default=None, max_length=64)


class RoomMessageRequest(BaseModel):
    sender: str = Field(max_length=64)
    content: str = Field(min_length=1, max_length=20000)
    intent: str = Field(default="discussion")
    metadata: dict | None = None
    #: Id of the message this one replies to, in the same room. Optional and
    #: unvalidated on purpose: a reply whose target was deleted is a real
    #: state, and rejecting the post would lose the reply too.
    reply_to: str | None = Field(default=None, max_length=64)


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
    parent = body.parent.strip() if body.parent else None

    def _create():
        svc = _service()
        if parent:
            # Nesting is explicit and validated; it is never inferred from a
            # topic string or a slash in the name.
            try:
                return svc.create_subgroup(parent, name, topic=topic, members=members, summary=body.summary)
            except ScopeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        room = svc.get_or_create_room(name, topic=topic, members=members, mode=mode, moderator=moderator, project_id=body.project_id)
        if body.summary.strip():
            room.summary = body.summary.strip()
        return room

    room = await asyncio.to_thread(_create)
    return _room_to_response(room)


# ---------------------------------------------------------------------------
# Nesting — the WhatsApp-community shape.
#
# `GET /tree` is declared before `GET /{name}` on purpose: Starlette matches in
# registration order, so a literal declared after the single-segment catch-all
# answers `Room 'tree' not found` instead of reaching its handler.
# ---------------------------------------------------------------------------


class SubgroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    topic: str = Field(default="", max_length=500)
    summary: str = Field(default="", max_length=280)
    members: list[str] | None = Field(default=None, max_length=50)
    #: Copy the parent's current members into the child. False starts it empty.
    inherit: bool = True


class RoomPolicyRequest(BaseModel):
    inbound: str | None = Field(default=None, max_length=16)
    outbound: str | None = Field(default=None, max_length=16)
    max_hop: int | None = Field(default=None, ge=1, le=MAX_HOP)
    authority_parent: str | None = Field(default=None, max_length=64)
    clear_authority: bool = False


class LifecycleRequest(BaseModel):
    state: str = Field(max_length=16)


class MoveRequest(BaseModel):
    parents: list[str] = Field(default_factory=list, max_length=8)
    authority_parent: str | None = Field(default=None, max_length=64)


class MemberRequest(BaseModel):
    bot_name: str = Field(min_length=1, max_length=64)
    #: Borrow a member in from another group instead of adding them outright.
    from_room: str | None = Field(default=None, max_length=64)
    #: ISO stamp at which a borrowed member is released. None means permanent.
    expires_at: str | None = Field(default=None, max_length=40)


class RuleRequest(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    field: str = Field(max_length=32)
    op: str = Field(max_length=16)
    value: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    label: str = Field(default="", max_length=120)


class ExcludeRequest(BaseModel):
    excluded: bool = True


def _scope_error(exc: Exception, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/tree", summary="Whole group forest")
async def group_tree() -> dict:
    """Every room with its scope, child count, and direct/effective membership.

    Includes `draft` rooms with their state attached — the sidebar filters them
    out, but a room the operator just created must not be invisible to them.
    """
    return await asyncio.to_thread(_service().tree)


@router.post("/{name}/subgroups", status_code=201, summary="Create a nested group")
async def create_subgroup(name: str, body: SubgroupRequest) -> dict:
    """Add a group inside this one, at any time.

    Inheriting copies the parent's *current* members so the child starts
    staffed; membership gained by the parent afterwards arrives through
    inheritance at read time, not by another write.
    """
    child_name = _validate_room_name(body.name)
    members = _validate_members(body.members)
    key = _validate_room_name(name)

    def _create():
        return _service().create_subgroup(
            key, child_name, topic=body.topic, members=members, summary=body.summary, inherit=body.inherit
        )

    try:
        room = await asyncio.to_thread(_create)
    except ScopeError as exc:
        raise _scope_error(exc) from exc
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    return _room_to_response(room)


@router.get("/{name}/children", summary="Direct nested groups")
async def list_children(name: str) -> dict:
    key = _validate_room_name(name)

    def _read():
        svc = _service()
        room = svc.get_room(key)
        if room is None:
            return None
        from alpha.groups.scope import children_of

        ids = children_of(svc._scopes, room.room_id)
        return [
            _room_to_response(next((r for r in svc._rooms.values() if r.room_id == i), room))
            for i in ids
        ]

    children = await asyncio.to_thread(_read)
    if children is None:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")
    return {"room": key, "children": children, "count": len(children)}


@router.get("/{name}/ancestors", summary="Breadcrumb chain")
async def list_ancestors(name: str) -> dict:
    """Root-first ancestor chain for the header breadcrumb."""
    key = _validate_room_name(name)

    def _read():
        return _service().breadcrumbs(key)

    try:
        chain = await asyncio.to_thread(_read)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    scope = await asyncio.to_thread(lambda: _service()._scope_for(_service()._require_room(key).room_id).to_dict())
    return {"room": key, "breadcrumbs": chain, "scope": scope, "max_depth": MAX_DEPTH}


@router.get("/{name}/descendants", summary="Whole subtree")
async def list_descendants(name: str) -> dict:
    key = _validate_room_name(name)

    def _read():
        svc = _service()
        room = svc.get_room(key)
        if room is None:
            return None
        from alpha.groups.scope import descendants_of

        ids = descendants_of(svc._scopes, room.room_id)
        return [
            _room_to_response(next((r for r in svc._rooms.values() if r.room_id == i), room)) for i in ids
        ]

    nodes = await asyncio.to_thread(_read)
    if nodes is None:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")
    return {"room": key, "descendants": nodes, "count": len(nodes)}


@router.patch("/{name}/lifecycle", summary="Change lifecycle state")
async def set_lifecycle(name: str, body: LifecycleRequest) -> dict:
    key = _validate_room_name(name)

    def _set():
        return _service().set_room_lifecycle(key, body.state.strip().lower())

    try:
        room = await asyncio.to_thread(_set)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except ScopeError as exc:
        raise _scope_error(exc, 422) from exc
    return _room_to_response(room)


@router.patch("/{name}/policy", summary="Change relay policy / authority parent")
async def set_policy(name: str, body: RoomPolicyRequest) -> dict:
    key = _validate_room_name(name)

    def _set():
        return _service().set_room_policy(
            key,
            inbound=body.inbound.strip().lower() if body.inbound else None,
            outbound=body.outbound.strip().lower() if body.outbound else None,
            max_hop=body.max_hop,
            authority_parent=body.authority_parent,
            clear_authority=body.clear_authority,
        )

    try:
        room = await asyncio.to_thread(_set)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except ScopeError as exc:
        raise _scope_error(exc, 422) from exc
    scope = await asyncio.to_thread(lambda: _service()._scope_for(room.room_id).to_dict())
    response = _room_to_response(room)
    response["scope"] = scope
    return response


@router.post("/{name}/move", summary="Re-parent a group")
async def move_group(name: str, body: MoveRequest) -> dict:
    key = _validate_room_name(name)

    def _move():
        return _service().move_room(key, parents=body.parents, authority_parent=body.authority_parent)

    try:
        room = await asyncio.to_thread(_move)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except ScopeError as exc:
        raise _scope_error(exc, 409) from exc
    return _room_to_response(room)


@router.post("/{name}/promote", summary="Detach a group from its parents")
async def promote_group(name: str) -> dict:
    """Make this group a root. Descendant paths are rewritten, not left stale."""
    key = _validate_room_name(name)

    def _promote():
        return _service().promote_room(key)

    try:
        room = await asyncio.to_thread(_promote)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    return _room_to_response(room)


@router.post("/{name}/merge", summary="Fold nested groups into this one")
async def merge_groups(name: str) -> dict:
    """Merge every direct child into this group.

    The response reports which children merged and which did not, plus why —
    "merged 3 of 4" is the honest form of a partial merge.
    """
    key = _validate_room_name(name)

    def _merge():
        return _service().merge_children(key)

    try:
        result = await asyncio.to_thread(_merge)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    return result


# ── Roster ────────────────────────────────────────────────────────────────


@router.get("/{name}/roster", summary="Membership split by origin")
async def get_roster(name: str) -> dict:
    """Direct, rule-matched, and inherited members, each labelled.

    Both counts are reported: a header saying "3 members" while six are visible
    is a fabricated count, so `effective_count` and `direct_count` travel
    together and the client renders both.
    """
    key = _validate_room_name(name)

    def _read():
        svc = _service()
        room = svc.get_room(key)
        if room is None:
            return None
        roster = svc.resolved_roster(key)
        scope = svc._scope_for(room.room_id)
        return {
            **roster.to_dict(),
            "room": room.name,
            "scope": scope.to_dict(),
            "rules": [r.to_dict() for r in svc._rosters.get(room.room_id, svc._roster_for(room.room_id)).rules],
        }

    payload = await asyncio.to_thread(_read)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")
    return payload


@router.post("/{name}/members", status_code=201, summary="Add or borrow a member")
async def add_member(name: str, body: MemberRequest) -> dict:
    """Add a member, or borrow one in with an expiry (a temporary squad)."""
    key = _validate_room_name(name)
    member = body.bot_name.lower().strip()
    if not _BOT_NAME_RE.match(member):
        raise HTTPException(status_code=422, detail=f"Invalid member name '{body.bot_name}'.")

    def _add():
        return _service().add_member(
            key, member, by=OPERATOR_ID, from_room=body.from_room, expires_at=body.expires_at
        )

    try:
        roster = await asyncio.to_thread(_add)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except RosterError as exc:
        raise _scope_error(exc, 422) from exc
    return roster.to_dict()


@router.delete("/{name}/members/{bot_name}", summary="Remove a direct member")
async def remove_member(name: str, bot_name: str) -> dict:
    """Remove a hand-added member.

    A member present only by rule or by inheritance is refused with the reason
    naming that source — deleting the row would leave them visibly present with
    no explanation.
    """
    key = _validate_room_name(name)

    def _remove():
        return _service().remove_member(key, bot_name)

    try:
        roster = await asyncio.to_thread(_remove)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except RosterError as exc:
        raise _scope_error(exc, 409) from exc
    return roster.to_dict()


@router.post("/{name}/members/{bot_name}/exclude", summary="Exclude or re-include a member")
async def exclude_member(name: str, bot_name: str, body: ExcludeRequest) -> dict:
    """Pull a member out of this group only, or put them back.

    This is how "a subgroup for just the backend work" is expressed: the member
    stays in the parent and is excluded here, rather than being removed
    everywhere.
    """
    key = _validate_room_name(name)

    def _set():
        return _service().set_excluded(key, bot_name, body.excluded)

    try:
        roster = await asyncio.to_thread(_set)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    return roster.to_dict()


@router.post("/{name}/rules", status_code=201, summary="Declare a membership rule")
async def add_rule(name: str, body: RuleRequest) -> dict:
    """Declare who belongs, rather than typing who is there.

    The response reports the rule's current matches, because a mistyped rule
    that matches nobody is indistinguishable from a group nobody joined.
    """
    key = _validate_room_name(name)

    def _add():
        return _service().add_rule(key, body.model_dump())

    try:
        rule = await asyncio.to_thread(_add)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except RosterError as exc:
        raise _scope_error(exc, 422) from exc
    preview = await asyncio.to_thread(_service().preview_rules, key)
    matched = next((p for p in preview if p["id"] == rule["id"]), None)
    rule["matches"] = matched["matches"] if matched else 0
    rule["matched_names"] = matched["matched_names"] if matched else []
    return rule


@router.delete("/{name}/rules/{rule_id}", summary="Remove a membership rule")
async def remove_rule(name: str, rule_id: str) -> dict:
    key = _validate_room_name(name)

    def _remove():
        return _service().remove_rule(key, rule_id)

    try:
        roster = await asyncio.to_thread(_remove)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    except RosterError as exc:
        raise _scope_error(exc, 404) from exc
    return roster.to_dict()


@router.get("/{name}/rules/preview", summary="Who each rule matches right now")
async def preview_roster_rules(name: str) -> dict:
    key = _validate_room_name(name)

    def _preview():
        svc = _service()
        if svc.get_room(key) is None:
            raise KeyError(key)
        return svc.preview_rules(key)

    try:
        rules = await asyncio.to_thread(_preview)
    except KeyError as exc:
        raise _scope_error(exc, 404) from exc
    return {"room": key, "rules": rules, "valid_fields": list(VALID_RULE_FIELDS), "valid_ops": list(VALID_RULE_OPS)}


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
        return _service().post_message(
            key,
            sender,
            body.content,
            intent=intent,
            metadata=body.metadata or {},
            reply_to=body.reply_to,
        )

    msg, next_speakers = await asyncio.to_thread(_post)
    return {"message": msg.to_dict(), "next_speakers": next_speakers}


class MessageEditRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class ReactionRequest(BaseModel):
    #: The actor toggling the reaction — normally the operator.
    actor: str = Field(min_length=1, max_length=64)
    emoji: str = Field(min_length=1, max_length=8)


class ForwardRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=64)
    target_room: str = Field(min_length=1, max_length=64)
    content: str | None = Field(default=None, max_length=20000)
    intent: str | None = Field(default=None, max_length=32)


def _validate_message_id(message_id: str) -> str:
    cleaned = message_id.strip()
    if not _MESSAGE_ID_RE.match(cleaned):
        raise HTTPException(status_code=422, detail="message id must be 1-64 chars: letters, digits, '_' or '-'.")
    return cleaned


@router.patch("/{name}/messages/{message_id}", summary="Edit a message")
async def edit_room_message(name: str, message_id: str, body: MessageEditRequest) -> dict:
    key = _validate_room_name(name)
    mid = _validate_message_id(message_id)

    def _edit():
        return _service().edit_message(key, mid, body.content)

    try:
        msg = await asyncio.to_thread(_edit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": msg.to_dict()}


@router.delete("/{name}/messages/{message_id}", summary="Delete a message")
async def delete_room_message(name: str, message_id: str) -> dict:
    key = _validate_room_name(name)
    mid = _validate_message_id(message_id)

    def _delete():
        return _service().delete_message(key, mid)

    try:
        msg = await asyncio.to_thread(_delete)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": msg.to_dict()}


@router.post("/{name}/messages/{message_id}/reactions", summary="Toggle a reaction")
async def react_to_room_message(name: str, message_id: str, body: ReactionRequest) -> dict:
    """Add or remove one actor's reaction.

    The response carries the whole reaction map rather than a bare boolean: the
    client re-renders from the server's state, so two browser tabs cannot
    disagree about who reacted.
    """
    key = _validate_room_name(name)
    mid = _validate_message_id(message_id)
    if body.emoji not in _VALID_REACTIONS:
        raise HTTPException(status_code=422, detail=f"emoji must be one of {list(_VALID_REACTIONS)}")

    def _react():
        return _service().toggle_reaction(key, mid, body.emoji, body.actor.strip())

    try:
        reactions = await asyncio.to_thread(_react)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message_id": mid, "reactions": reactions}


@router.post("/{name}/messages/{message_id}/forward", status_code=201, summary="Forward a message")
async def forward_room_message(name: str, message_id: str, body: ForwardRequest) -> dict:
    """Re-post a message into another room.

    The copy gets a fresh id and a `forwarded_from` stamp. Reusing the source id
    would make reactions and later edits ambiguous across the two rooms.
    """
    key = _validate_room_name(name)
    mid = _validate_message_id(message_id)
    target = _validate_room_name(body.target_room)
    intent = body.intent.strip().lower() if body.intent else None
    if intent is not None and intent not in _VALID_INTENTS:
        raise HTTPException(status_code=422, detail=f"intent must be one of {list(_VALID_INTENTS)}")

    def _forward():
        return _service().forward_message(
            key,
            mid,
            target,
            sender=body.sender.strip(),
            content=body.content,
            intent=intent,
        )

    try:
        msg, _next_speakers = await asyncio.to_thread(_forward)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": msg.to_dict(), "target_room": target}


@router.get("/{name}/members", summary="List room members with resolved presence")
@router.get("/{name}/presence", summary="Alias of /members")
@router.get("/{name}/attendance", summary="Alias of /members")
@router.get("/{name}/roll_call", summary="Alias of /members")
async def list_room_members(name: str) -> dict:
    """Every member of the room, with a measured presence state.

    The participants count in the UI comes from `room.members`; this route adds
    the *state* each member is actually in. Presence is resolved from the Bot
    Registry (lifecycle + last activity) and company attendance (live
    liveness) — never from the member name, and never defaulted to online. A
    member no owning system knows reads as `unknown`, which is a different
    claim from `offline`.
    """
    key = _validate_room_name(name)

    def _read():
        svc = _service()
        room = svc.get_room(key)
        if room is None:
            return None
        return list(room.members)

    members = await asyncio.to_thread(_read)
    if members is None:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")

    presence = await asyncio.to_thread(resolve_room_presence, members)
    counts: dict[str, int] = {}
    for entry in presence:
        counts[entry.state] = counts.get(entry.state, 0) + 1

    # Both membership counts travel together. A client that renders only
    # `direct_count` would say "3 members" about a room with six visible bots.
    direct_count = None
    effective = members
    try:
        roster = await asyncio.to_thread(_service().resolved_roster, key)
        direct_count = roster.direct_count
        effective = roster.effective
        counts["_direct_count"] = roster.direct_count
        counts["_effective_count"] = len(effective)
    except KeyError:
        pass

    return {
        "room": key,
        "members": [entry.to_dict() for entry in presence],
        "count": len(presence),
        "by_state": counts,
        "direct_count": direct_count,
        "effective_members": effective,
        "effective_count": len(effective),
    }


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
    from alpha.runtime.user_context import get_effective_user_id

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
        from alpha.groups.runner import get_group_run_service

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
        from alpha.groups.runner import get_group_run_service

        return [r.to_dict() for r in get_group_run_service().list_runs(room_name=key)]

    runs = await asyncio.to_thread(_list)
    return {"runs": runs, "count": len(runs)}


@router.get("/{name}/runs/{run_id}", summary="Get autonomous team run")
async def get_group_run(name: str, run_id: str) -> dict:
    key = _validate_room_name(name)

    def _get():
        from alpha.groups.runner import get_group_run_service

        return get_group_run_service().get_run(run_id)

    run = await asyncio.to_thread(_get)
    if run is None or run.room_name.lower() != key.lower():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found in room '{key}'.")
    return run.to_dict()


@router.post("/{name}/runs/{run_id}/cancel", summary="Cancel autonomous team run")
async def cancel_group_run(name: str, run_id: str) -> dict:
    key = _validate_room_name(name)

    def _cancel():
        from alpha.groups.runner import get_group_run_service

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
async def delete_room(name: str, request: Request, cascade: bool = False) -> None:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_room_name(name)

    def _delete() -> tuple[bool, list[str], int]:
        svc = _service()
        with svc._lock:
            room = svc._rooms.get(key.lower())
            if room is None:
                return False, [], 0
            from alpha.groups.scope import children_of

            kids = children_of(svc._scopes, room.room_id)
            subtree = descendants_of(svc._scopes, room.room_id) if kids else []
            # Refuse rather than silently orphaning a subtree. `cascade=true` is
            # an explicit opt-in, and the response names what was removed.
            if kids and not cascade:
                return False, [next((r.name for r in svc._rooms.values() if r.room_id == k), k) for k in kids], len(subtree)
            removed = svc._rooms.pop(key.lower(), None)
            if removed is None:
                return False, [], 0
            targets = [room.room_id, *subtree] if cascade else [room.room_id]
            for rid in targets:
                svc._scopes.pop(rid, None)
                svc._rosters.pop(rid, None)
            svc._recompute_scope()
            svc._save()
            return True, [], len(subtree)

    deleted, children, subtree_size = await asyncio.to_thread(_delete)
    if children:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Room '{key}' still contains {len(children)} group(s) ({', '.join(children)}). "
                f"Merge them with POST /api/groups/{key}/merge, promote them, or repeat with ?cascade=true to delete "
                f"{subtree_size + 1} rooms."
            ),
        )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Room '{key}' not found")
