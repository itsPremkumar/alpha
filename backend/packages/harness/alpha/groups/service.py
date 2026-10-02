"""Group Chat Service with Zero-Config Room and Member Auto-Provisioning."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpha.bots.registry import get_bot_registry
from alpha.groups.orchestration import GroupOrchestrator
from alpha.groups.quorum import QuorumEngine
from alpha.groups.room import GroupMessage, GroupRoom, OrchestrationMode, _now
from alpha.groups.roster import (
    GroupRoster,
    ResolvedRoster,
    RosterError,
    resolve_roster,
    resolve_rule,
    validate_rule,
)
from alpha.groups.scope import (
    MAX_HOP,
    VALID_INBOUND,
    VALID_OUTBOUND,
    GroupScope,
    ScopeError,
    ancestors_of,
    assert_authority_parent_consistent,
    assert_no_cycle,
    assert_within_depth,
    children_of,
    descendants_of,
    plan_relay,
    recompute_all,
    validate_state,
)

logger = logging.getLogger(__name__)

_DEFAULT_GROUPS_DIR = "groups"


def _default_storage_path() -> Path:
    """Resolve rooms.json under the writable runtime home (ALPHA_HOME-aware)."""
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / _DEFAULT_GROUPS_DIR / "rooms.json"
    except Exception:
        return Path.cwd() / ".alpha" / _DEFAULT_GROUPS_DIR / "rooms.json"


class GroupChatService:
    """Manages group chat rooms, member enrollment, and multi-agent coordination."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rooms: dict[str, GroupRoom] = {}
        #: Forest shape, keyed by room_id. See `alpha.groups.scope`.
        self._scopes: dict[str, GroupScope] = {}
        #: Declared membership per room. NEVER merged into `GroupRoom.members` —
        #: `alpha.projects.crew` owns that field and deletes what it does not
        #: claim, so a nested or rule-based member written there would be erased
        #: on the next project reconcile. See `alpha.groups.roster`.
        self._rosters: dict[str, GroupRoster] = {}
        self.orchestrator = GroupOrchestrator()
        self.quorum = QuorumEngine()
        # Reentrant on purpose. The nesting methods compose each other — a
        # mutation holds the lock and then calls a reader that takes it again
        # (`_require_room` -> `get_room`, `resolved_roster` -> its parent) — and a
        # plain `Lock` deadlocks on that first nested acquisition. Composing
        # these operations is the whole point of the layer, so the lock is
        # reentrant rather than each caller re-implementing a private unlocked
        # path.
        self._lock = threading.RLock()
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
            for item in data.get("scopes", []):
                scope = GroupScope.from_dict(item)
                self._scopes[scope.room_id] = scope
            for item in data.get("rosters", []):
                roster = GroupRoster.from_dict(item)
                self._rosters[roster.room_id] = roster
            # Rebuild derived paths/depths: they are never trusted from disk,
            # because a rename or promote would leave them stale.
            self._recompute_scope()
        except Exception:
            logger.warning("Group rooms load failed; starting empty", exc_info=True)

    def _name_index(self) -> dict[str, str]:
        return {room.room_id: room.name for room in self._rooms.values()}

    def _recompute_scope(self) -> None:
        recompute_all(self._scopes, self._name_index())
        # A room persisted before this feature has no scope record; give it a
        # root one so `GET /tree` includes every existing room rather than only
        # the newly-created ones.
        for room in self._rooms.values():
            if room.room_id not in self._scopes:
                self._scopes[room.room_id] = GroupScope(
                    room_id=room.room_id,
                    parents=list(room.parent_ids),
                    state=room.lifecycle,
                )

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 2,
                "rooms": [r.to_dict() for r in self._rooms.values()],
                "scopes": [s.to_dict() for s in self._scopes.values()],
                "rosters": [r.to_dict() for r in self._rosters.values()],
                "updated_at": _now(),
            }
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Group rooms save failed", exc_info=True)

    def persist(self) -> None:
        """Flush in-memory room mutations to disk.

        The crew layer (``projects/crew.py``) reconciles room membership against
        project membership and needs an explicit flush point; going through
        ``post_message`` just to trigger a save would pollute the transcript.
        """
        self._save()

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
            # Backfill the scope record for the new root. Structural readers
            # (`move_room`, `tree`, `descendants_of`) resolve parents against
            # `_scopes`, so a room created without one is invisible to them —
            # and `assert_no_cycle` then refuses a legitimate move with
            # "parent does not exist".
            self._recompute_scope()
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

    # ── Forest readers ──────────────────────────────────────────────────────
    #
    # Thin wrappers over the `scope` module so callers holding a room name never
    # have to know whether they need a room id or the raw scope map.

    def children_of(self, room_id: str) -> list[str]:
        """Direct child room ids of `room_id`."""
        with self._lock:
            return children_of(self._scopes, room_id)

    def descendants_of(self, room_id: str) -> list[str]:
        """Every room id beneath `room_id`, breadth-first."""
        with self._lock:
            return descendants_of(self._scopes, room_id)

    def ancestors_of(self, room_id: str) -> list[str]:
        """Every room id above `room_id`, nearest first."""
        with self._lock:
            return ancestors_of(self._scopes, room_id)

    def post_message(
        self,
        room_name: str,
        sender: str,
        content: str,
        *,
        intent: str = "discussion",
        metadata: dict[str, Any] | None = None,
        reply_to: str | None = None,
        forwarded_from: dict[str, str] | None = None,
        relay: bool = True,
    ) -> tuple[GroupMessage, list[str]]:
        """Post a message into a room and compute next scheduled speakers.

        ``reply_to`` is a message id in the same room. It is stored as given and
        validated by the caller, not here: a dangling reference is a real state
        (the quoted row can be deleted later) and inventing a placeholder would
        claim a message exists when none does.
        """
        room = self.get_or_create_room(room_name)
        mentions = self.orchestrator.parse_mentions(content, room.members)

        msg = room.append_message(
            sender=sender,
            content=content,
            intent=intent,  # type: ignore[arg-type]
            mentions=mentions,
            metadata=metadata or {},
            reply_to=reply_to,
            forwarded_from=forwarded_from,
        )
        # Relay before the save so the copies land in the same snapshot; a relay
        # that survives a restart but whose origin did not would show a message
        # in a room that never received it.
        if relay:
            self.relay_all()
        self._save()

        next_speakers = self.orchestrator.resolve_next_speakers(room, msg)
        return msg, next_speakers

    # ── Message features ──────────────────────────────────────────────────
    #
    # Each of these resolves the message first and raises rather than returning a
    # falsy value, so a caller cannot render a failed edit as a successful one.

    def edit_message(self, room_name: str, message_id: str, content: str) -> GroupMessage:
        """Replace a message's content in place, stamping ``edited_at``."""
        room = self._require_room(room_name)
        msg = room.find_message(message_id)
        if msg is None:
            raise KeyError(f"Message '{message_id}' not found in room '{room_name}'.")
        if msg.deleted:
            raise ValueError("A deleted message cannot be edited.")
        trimmed = content.strip()
        if not trimmed:
            raise ValueError("Edited message content must not be empty.")
        with self._lock:
            msg.content = trimmed
            msg.edited_at = _now()
            room.updated_at = _now()
            self._save()
        return msg

    def delete_message(self, room_name: str, message_id: str) -> GroupMessage:
        """Soft-delete a message.

        The row is kept so a reply still has a target and reactions are not
        silently dropped; ``deleted`` is what a client renders as withheld.
        Deleting twice is an error rather than an idempotent success, so a
        double-click is visible instead of looking like one action.
        """
        room = self._require_room(room_name)
        msg = room.find_message(message_id)
        if msg is None:
            raise KeyError(f"Message '{message_id}' not found in room '{room_name}'.")
        if msg.deleted:
            raise ValueError(f"Message '{message_id}' is already deleted.")
        with self._lock:
            msg.deleted = True
            msg.content = ""
            msg.edited_at = _now()
            room.updated_at = _now()
            self._save()
        return msg

    def toggle_reaction(self, room_name: str, message_id: str, emoji: str, actor: str) -> dict[str, list[str]]:
        """Add or remove ``actor``'s ``emoji`` reaction.

        A toggle rather than a blind append: the same click twice has to remove
        the reaction, or the UI would offer no way to take one back.
        """
        room = self._require_room(room_name)
        msg = room.find_message(message_id)
        if msg is None:
            raise KeyError(f"Message '{message_id}' not found in room '{room_name}'.")
        if msg.deleted:
            raise ValueError("A deleted message cannot be reacted to.")
        with self._lock:
            actors = msg.reactions.setdefault(emoji, [])
            if actor in actors:
                actors.remove(actor)
            else:
                actors.append(actor)
            if not actors:
                del msg.reactions[emoji]
            room.updated_at = _now()
            self._save()
            return dict(msg.reactions)

    def forward_message(
        self,
        source_room: str,
        message_id: str,
        target_room: str,
        *,
        sender: str,
        content: str | None = None,
        intent: str | None = None,
    ) -> tuple[GroupMessage, list[str]]:
        """Re-post a message into another room, recording where it came from.

        The forwarded copy carries a fresh id and a ``forwarded_from`` stamp
        rather than reusing the source id: two rooms holding one id would make
        reactions and edits ambiguous across the boundary.
        """
        source = self._require_room(source_room)
        origin = source.find_message(message_id)
        if origin is None:
            raise KeyError(f"Message '{message_id}' not found in room '{source_room}'.")
        if origin.deleted:
            raise ValueError("A deleted message cannot be forwarded.")
        return self.post_message(
            target_room,
            sender,
            content if content is not None else origin.content,
            intent=intent or origin.intent,
            forwarded_from={"room": source.name, "sender": origin.sender, "message_id": origin.id},
        )

    def _require_room(self, room_name: str) -> GroupRoom:
        room = self.get_room(room_name)
        if room is None:
            raise KeyError(f"Room '{room_name}' not found.")
        return room

    def _scope_for(self, room_id: str) -> GroupScope:
        scope = self._scopes.get(room_id)
        if scope is None:
            scope = GroupScope(room_id=room_id)
            self._scopes[room_id] = scope
        return scope

    def _roster_for(self, room_id: str) -> GroupRoster:
        roster = self._rosters.get(room_id)
        if roster is None:
            roster = GroupRoster(room_id=room_id)
            self._rosters[room_id] = roster
        return roster

    # ── Nesting ────────────────────────────────────────────────────────────
    #
    # The WhatsApp-community shape: any group can contain other groups, at any
    # time, without disturbing the parent. Every operation below is a local,
    # validated edit to the forest — none of them rewrite a transcript.

    def create_subgroup(
        self,
        parent_name: str,
        name: str,
        *,
        topic: str = "",
        members: Sequence[str] | None = None,
        summary: str = "",
        inherit: bool = True,
    ) -> GroupRoom:
        """Create a group nested under ``parent_name``.

        ``inherit=True`` copies the parent's current members as the child's
        *direct* members, so the child starts staffed. Inheritance of future
        membership is separate and automatic — see :meth:`resolved_roster`.
        """
        with self._lock:
            parent = self._require_room(parent_name)
            key = name.lower().strip()
            if key in self._rooms:
                raise ScopeError(f"A group named '{name}' already exists.")
            clean_members: list[str] = []
            for m in list(parent.members) if inherit else list(members or []):
                clean = m.lower().strip()
                if clean and clean not in clean_members:
                    get_bot_registry().get_or_create(clean)
                    clean_members.append(clean)
            for m in members or []:
                clean = m.lower().strip()
                if clean and clean not in clean_members:
                    get_bot_registry().get_or_create(clean)
                    clean_members.append(clean)

            room = GroupRoom(
                room_id=f"room_{uuid4().hex[:8]}",
                name=key,
                topic=topic.strip() or f"Sub-group of {parent.name}",
                members=clean_members,
                parent_ids=[parent.room_id],
                summary=summary.strip(),
                moderator=parent.moderator,
            )
            self._rooms[key] = room
            self._scope_for(room.room_id).parents.append(parent.room_id)
            # The child's first policy owner is its parent. It may narrow that
            # later; it never silently becomes its own root.
            self._scope_for(room.room_id).authority_parent = parent.room_id
            self._recompute_scope()
            self._save()
            return room

    def move_room(
        self,
        name: str,
        *,
        parents: list[str],
        authority_parent: str | None = None,
    ) -> GroupRoom:
        """Re-parent a room. The one operation that can fail on a cycle."""
        with self._lock:
            room = self._require_room(name)
            resolved = [self._resolve_id(p) for p in parents if p]
            assert_no_cycle(self._scopes, room.room_id, resolved)
            assert_within_depth(self._scopes, room.room_id, resolved)
            scope = self._scope_for(room.room_id)
            if authority_parent is not None:
                scope.parents = list(resolved)
            assert_authority_parent_consistent(self._scopes, room.room_id, authority_parent)
            scope.parents = list(resolved)
            scope.authority_parent = authority_parent
            room.parent_ids = list(resolved)
            self._recompute_scope()
            self._save()
            return room

    def promote_room(self, name: str) -> GroupRoom:
        """Detach a room from every parent, making it a root.

        Rewrites descendant paths because a promote changes the whole subtree's
        displayed location, not just the promoted room's own.
        """
        with self._lock:
            room = self._require_room(name)
            scope = self._scope_for(room.room_id)
            scope.parents = []
            scope.authority_parent = None
            room.parent_ids = []
            self._recompute_scope()
            self._save()
            return room

    def set_room_lifecycle(self, name: str, state: str) -> GroupRoom:
        validate_state(self._scope_for(self._require_room(name).room_id).state, state)
        with self._lock:
            room = self._require_room(name)
            scope = self._scope_for(room.room_id)
            scope.state = state  # type: ignore[assignment]
            room.lifecycle = state  # type: ignore[assignment]
            self._save()
            return room

    def set_room_policy(
        self,
        name: str,
        *,
        inbound: str | None = None,
        outbound: str | None = None,
        max_hop: int | None = None,
        authority_parent: str | None = None,
        clear_authority: bool = False,
    ) -> GroupRoom:
        """Update relay policy and the authority parent."""
        with self._lock:
            room = self._require_room(name)
            scope = self._scope_for(room.room_id)
            if inbound is not None:
                if inbound not in VALID_INBOUND:
                    raise ScopeError(f"inbound must be one of {list(VALID_INBOUND)}.")
                scope.inbound = inbound  # type: ignore[assignment]
            if outbound is not None:
                if outbound not in VALID_OUTBOUND:
                    raise ScopeError(f"outbound must be one of {list(VALID_OUTBOUND)}.")
                scope.outbound = outbound  # type: ignore[assignment]
            if max_hop is not None:
                if not 1 <= max_hop <= MAX_HOP:
                    raise ScopeError(f"max_hop must be between 1 and {MAX_HOP}.")
                scope.max_hop = max_hop
            if clear_authority:
                scope.authority_parent = None
            elif authority_parent is not None:
                resolved = self._resolve_id(authority_parent)
                assert_authority_parent_consistent(self._scopes, room.room_id, resolved)
                scope.authority_parent = resolved
            self._save()
            return room

    def tree(self) -> dict[str, Any]:
        """The whole forest: every room, its scope, and its child count.

        Includes `draft` rooms with their state intact — the sidebar filters
        them out, but omitting them here would make a draft room invisible to
        the operator who created it.
        """
        with self._lock:
            nodes: list[dict[str, Any]] = []
            for room in self._rooms.values():
                scope = self._scope_for(room.room_id)
                resolved = self.resolved_roster(room.name)
                nodes.append(
                    {
                        "room_id": room.room_id,
                        "name": room.name,
                        "topic": room.topic,
                        "summary": room.summary,
                        "project_id": room.project_id,
                        "mode": room.mode,
                        "moderator": room.moderator,
                        "message_count": len(room.log),
                        "created_at": room.created_at,
                        "updated_at": room.updated_at,
                        "scope": scope.to_dict(),
                        "child_count": len(children_of(self._scopes, room.room_id)),
                        "direct_count": resolved.direct_count,
                        "effective_count": resolved.effective_count,
                    }
                )
            nodes.sort(key=lambda n: (n["scope"]["depth"], n["scope"]["path"]))
            return {"nodes": nodes, "count": len(nodes)}

    def breadcrumbs(self, name: str) -> list[dict[str, Any]]:
        """Root-first ancestor chain, for the header path."""
        with self._lock:
            room = self._require_room(name)
            names = self._name_index()
            chain: list[dict[str, Any]] = []
            for rid in reversed(ancestors_of(self._scopes, room.room_id)):
                scope = self._scopes[rid]
                chain.append(
                    {
                        "room_id": rid,
                        "name": names.get(rid, rid),
                        "path": scope.path,
                        "depth": scope.depth,
                        "inherited_count": len(children_of(self._scopes, rid)),
                    }
                )
            return chain

    # ── Roster ─────────────────────────────────────────────────────────────

    def add_member(
        self,
        room_name: str,
        member: str,
        *,
        by: str,
        from_room: str | None = None,
        expires_at: str | None = None,
    ) -> ResolvedRoster:
        """Add a direct member, or borrow one into a temporary squad."""
        with self._lock:
            room = self._require_room(room_name)
            self._roster_for(room.room_id).add(member, by=by, from_room=from_room, expires_at=expires_at)
            self._save()
            return self.resolved_roster(room_name)

    def remove_member(self, room_name: str, member: str) -> ResolvedRoster:
        """Remove a direct member.

        A member who is present *only* because of a rule or a parent is not
        silently dropped: the route layer turns that into a refusal naming the
        real reason, because removing the row would leave the member visibly
        present with no explanation.
        """
        with self._lock:
            room = self._require_room(room_name)
            resolved = self.resolved_roster(room_name)
            clean = member.strip().lower()
            if clean in resolved.rule_matched and clean not in resolved.direct:
                raise RosterError(f"'{clean}' is in this group by rule, not by hand. Remove the rule, or exclude the member instead.")
            if clean in resolved.inherited:
                raise RosterError(f"'{clean}' is inherited from a parent group. Exclude the member in this group instead.")
            self._roster_for(room.room_id).remove(clean)
            if clean in room.members:
                room.members.remove(clean)
            self._save()
            return self.resolved_roster(room_name)

    def set_excluded(self, room_name: str, member: str, excluded: bool) -> ResolvedRoster:
        with self._lock:
            room = self._require_room(room_name)
            roster = self._roster_for(room.room_id)
            if excluded:
                roster.exclude(member)
            else:
                roster.include(member)
            self._save()
            return self.resolved_roster(room_name)

    def add_rule(self, room_name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Declare a membership rule and report what it matches right now.

        The match list travels with the response because a rule that matches
        nobody looks exactly like a room nobody joined, and the operator cannot
        tell the two apart otherwise.
        """
        with self._lock:
            room = self._require_room(room_name)
            roster = self._roster_for(room.room_id)
            rule = validate_rule(body)
            if not rule.id:
                rule.id = f"rule_{uuid4().hex[:8]}"
            roster.rules = [r for r in roster.rules if r.id != rule.id]
            roster.rules.append(rule)
            matched = resolve_rule(rule)
            self._save()
            return {
                **rule.to_dict(),
                "room": room.name,
                "matches": len(matched),
                "matched_names": matched,
            }

    def remove_rule(self, room_name: str, rule_id: str) -> ResolvedRoster:
        with self._lock:
            room = self._require_room(room_name)
            roster = self._roster_for(room.room_id)
            before = len(roster.rules)
            roster.rules = [r for r in roster.rules if r.id != rule_id]
            if len(roster.rules) == before:
                raise RosterError(f"Rule '{rule_id}' not found in room '{room_name}'.")
            self._save()
            return self.resolved_roster(room_name)

    def preview_rules(self, room_name: str) -> list[dict[str, Any]]:
        """Every rule with its current match list.

        Exists because a mistyped rule that matches nobody looks exactly like a
        room nobody joined, and the operator cannot tell them apart otherwise.
        """
        with self._lock:
            room = self._require_room(room_name)
            from alpha.groups.roster import preview_rules

            return preview_rules(self._roster_for(room.room_id).rules)

    def resolved_roster(self, room_name: str) -> ResolvedRoster:
        """This room's membership split by origin, with inherited derived live.

        Inheritance is recomputed from each visibility parent's own effective
        roster on every read. It is never stored, so an inherited member cannot
        go stale and editing the child cannot orphan it.
        """
        room = self._require_room(room_name)
        inherited_by_parent: dict[str, list[str]] = {}
        for parent_id in self._scope_for(room.room_id).parents:
            parent = next((r for r in self._rooms.values() if r.room_id == parent_id), None)
            if parent is None:
                continue
            inherited_by_parent[parent_id] = self.resolved_roster(parent.name).effective
        return resolve_roster(
            room.room_id,
            self._rosters.get(room.room_id),
            room.members,
            inherited_by_parent,
        )

    def effective_members(self, room_name: str) -> list[str]:
        """Direct + rule-matched + inherited, minus excluded and expired.

        This is what a group run fans out to and what a presence count reports.
        The *direct* count is reported beside it, never in its place.

        Returned in the room's own member order first, then rule-matched and
        inherited names appended. `ResolvedRoster.effective` sorts, which is
        right for a set comparison but wrong for a transcript: the ordering a
        room was staffed in is information, and sorting it away makes the same
        room read differently on every call.
        """
        resolved = self.resolved_roster(room_name)
        room = self._require_room(room_name)
        dead = set(resolved.excluded) | set(resolved.expired)
        ordered: list[str] = []
        seen: set[str] = set()
        for name in [*room.members, *resolved.direct, *resolved.rule_matched, *resolved.inherited]:
            clean = (name or "").strip().lower()
            if not clean or clean in seen or clean in dead:
                continue
            seen.add(clean)
            ordered.append(clean)
        return ordered

    # ── Relay ──────────────────────────────────────────────────────────────

    def _relay_once(self, room_id: str, hop: int) -> None:
        """Deliver a posted message to every room its policy names.

        A relay is a COPY with provenance, never the same row: two rooms holding
        one message id would make reactions and later edits ambiguous across the
        boundary. This is the same rule the forward route follows.
        """
        for target_id in plan_relay(self._scopes, room_id, hop=hop):
            target = next((r for r in self._rooms.values() if r.room_id == target_id), None)
            if target is None:
                continue
            origin = next((r for r in self._rooms.values() if r.room_id == room_id), None)
            if origin is None:
                continue
            target.append_message(
                sender=origin.name,
                content=origin.log[-1].content,
                intent=origin.log[-1].intent,  # type: ignore[arg-type]
                metadata={"relayed": True, "hop": hop + 1},
                forwarded_from={"room": origin.name, "sender": origin.log[-1].sender, "message_id": origin.log[-1].id},
            )

    def relay_all(self) -> dict[str, Any]:
        """Deliver every unread native message to the rooms its policy names.

        A room is a frontier entry once **per message**, not once per room, so
        relaying is bounded by the actual work: a room with three unread
        messages contributes three hops, not one. Previously the frontier was
        seeded from "the last message is not a relay", which silently skipped a
        room whenever a relay copy happened to be its most recent row.

        `max_hop` stops `outbound: siblings` + `inbound: broadcast` from
        ping-ponging forever. The receipt reports how many copies landed and
        where, because "relayed" as an unverifiable claim is exactly what this
        layer exists to avoid.
        """
        with self._lock:
            delivered: list[dict[str, str]] = []
            # (room_id, hop) for each NATIVE message not yet relayed.
            frontier: list[tuple[str, int]] = []
            for room in self._rooms.values():
                for msg in room.log:
                    if msg.metadata.get("relayed"):
                        continue
                    frontier.append((room.room_id, 0))

            seen: set[tuple[str, str, int]] = set()
            guard = 0
            while frontier and guard < 256:
                guard += 1
                room_id, hop = frontier.pop(0)
                room = next((r for r in self._rooms.values() if r.room_id == room_id), None)
                if room is None or not room.log:
                    continue
                msg = room.log[-1]
                key = (room_id, msg.id, hop)
                if key in seen:
                    continue
                seen.add(key)
                for target_id in plan_relay(self._scopes, room_id, hop=hop):
                    target = next((r for r in self._rooms.values() if r.room_id == target_id), None)
                    if target is None:
                        continue
                    before = len(target.log)
                    self._relay_once(room_id, hop)
                    if len(target.log) > before:
                        delivered.append({"to": target.name, "hop": str(hop + 1), "message_id": msg.id})
                    frontier.append((target_id, hop + 1))
                # A room whose policy relays nowhere still consumes its frontier
                # entry so the loop cannot spin on it.
                if not plan_relay(self._scopes, room_id, hop=hop):
                    continue

            if delivered:
                self._save()
            return {"delivered_count": len(delivered), "delivered": delivered}

    def merge_children(self, name: str) -> dict[str, Any]:
        """Fold every direct child into this room.

        Messages merge in **timestamp order**, not child order, so the parent
        transcript reads chronologically. Each child's roster is unioned in, and
        the child's state is reported — a partial merge says which children
        moved, never a bare success.
        """
        with self._lock:
            room = self._require_room(name)
            kids = children_of(self._scopes, room.room_id)
            merged: list[str] = []
            failed: list[dict[str, str]] = []
            collected: list[GroupMessage] = list(room.log)
            roster = self._roster_for(room.room_id)
            for kid_id in kids:
                kid = next((r for r in self._rooms.values() if r.room_id == kid_id), None)
                if kid is None:
                    failed.append({"room_id": kid_id, "reason": "room record missing"})
                    continue
                collected.extend(kid.log)
                for member in kid.members:
                    if member not in room.members:
                        room.members.append(member)
                    roster.add(member, by=f"merge:{kid.name}")
                del self._rooms[kid.name]
                self._scopes.pop(kid_id, None)
                self._rosters.pop(kid_id, None)
                merged.append(kid.name)
            collected.sort(key=lambda m: m.created_at)
            room.log = collected
            room.updated_at = _now()
            self._recompute_scope()
            self._save()
            return {
                "room": room.name,
                "merged": merged,
                "merged_count": len(merged),
                "failed": failed,
                "children_total": len(kids),
                "message_count": len(room.log),
            }

    def _resolve_id(self, room: str) -> str:
        """Accept a room id or a name for the same room.

        Route callers hold names; the scope graph holds ids. Accepting both here
        is what keeps every caller from having to know which one it has.
        """
        key = (room or "").strip().lower()
        direct = self._rooms.get(key)
        if direct is not None:
            return direct.room_id
        if key in self._scopes:
            return key
        raise ScopeError(f"Room '{room}' not found.")


_global_groups: GroupChatService | None = None
_global_groups_path: str | None = None


def get_group_chat_service(storage_path: str | Path | None = None) -> GroupChatService:
    """Return the process-wide group chat service (ALPHA_HOME-aware).

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
