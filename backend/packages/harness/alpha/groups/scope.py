"""Nested group structure: where a room sits, and who may see it.

This module owns the *shape* of the room forest and nothing else. It answers
four questions and refuses to answer a fifth:

1. Is this room a child of that one?
2. What is its path and depth?
3. Which rooms may see a message relayed out of it?
4. Which rooms' policy governs it?
5. ~~Who owns it~~ — no. ``authority_parent`` is at most ONE, and that is the
   design decision that makes multi-parent visibility tractable.

The split is the whole idea. **Visibility may fan out to many parents; authority
never does.** "Who must see this message" is a set, and a set has an honest
answer. "Whose rules apply" has to be a single pointer, or policy inheritance
becomes a negotiation between rooms that cannot be resolved without a vote.

Nothing here touches ``GroupRoom.members``. That field is owned by
``alpha.projects.crew.ensure_crew`` for project rooms, and it *deletes* members
that project membership does not claim (``crew.py``: the reconcile removes any
entry not in ``wanted``). A nested or rule-based roster written into
``room.members`` would therefore be silently erased on the next project
reconcile. Everything in this file lives beside it instead — see
``alpha.groups.roster``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from alpha.groups.room import _now

#: Maximum nesting depth. Four is deliberate: deeper makes the rendered ``path``
#: unreadable in a sidebar, and relay fan-out becomes a cost problem rather
#: than a feature. A room created below this is refused, not clamped.
MAX_DEPTH = 4

RoomState = Literal["draft", "active", "parked", "archived", "dissolved"]
#: Every state a room may be in, and the legal transitions between them. A
#: transition outside this table is a programming error, not a user error.
ROOM_STATES: tuple[str, ...] = ("draft", "active", "parked", "archived", "dissolved")

#: `dissolved` is squad-only: a permanent group is archived, never dissolved.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "archived"}),
    "active": frozenset({"parked", "archived", "draft"}),
    "parked": frozenset({"active", "archived"}),
    "archived": frozenset({"active"}),
    "dissolved": frozenset(),
}

#: Relay directions. `outbound` may only name one at a time: a message
#: delivered to siblings *and* parents in the same hop is the same fan-out with
#: no way to reason about ordering.
InboundPolicy = Literal["none", "parent", "broadcast"]
OutboundPolicy = Literal["none", "parents", "siblings", "org"]

VALID_INBOUND: tuple[str, ...] = ("none", "parent", "broadcast")
VALID_OUTBOUND: tuple[str, ...] = ("none", "parents", "siblings", "org")

#: Loop guard. `outbound: siblings` combined with `inbound: broadcast` will
#: ping-pong forever without a hop ceiling, and a relay storm is far worse than
#: a missed message.
DEFAULT_MAX_HOP = 3
MAX_HOP = 8


class ScopeError(ValueError):
    """A refused scope change. Carries a reason the API can surface verbatim."""


def validate_state(current: str, target: str) -> None:
    """Refuse a lifecycle transition the table does not allow."""
    if target not in ROOM_STATES:
        raise ScopeError(f"Unknown room state '{target}'. Expected one of {list(ROOM_STATES)}.")
    if target not in _LEGAL_TRANSITIONS.get(current, frozenset()):
        allowed = sorted(_LEGAL_TRANSITIONS.get(current, frozenset()))
        raise ScopeError(f"Cannot move a room from '{current}' to '{target}'. Allowed: {allowed or 'none — this state is terminal'}.")


@dataclass
class GroupScope:
    """One room's position in the forest.

    ``room_id`` is the identity. ``parents`` holds room ids, never names: the
    ``_rooms`` dict in ``GroupChatService`` is keyed by lowercased name, so a
    name reference would break on rename. ``path`` and ``depth`` are derived
    display/locator values and are never trusted from a client.
    """

    room_id: str
    #: Rooms whose members are visible here, and whose outbound relay reaches
    #: here. May be several.
    parents: list[str] = field(default_factory=list)
    #: The ONE room whose policy (mode, moderator, quorum) governs this room.
    #: `None` means this room is a root and owns its own policy.
    authority_parent: str | None = None
    #: Human-facing locator, e.g. ``"sprint-room/backend"``. Derived; always
    #: recomputed from the tree rather than accepted as an input.
    path: str = ""
    depth: int = 0
    state: RoomState = "active"
    #: ``inbound`` — what reaches this room from elsewhere.
    inbound: InboundPolicy = "parent"
    #: ``outbound`` — where this room's messages relay to.
    outbound: OutboundPolicy = "none"
    #: Relay loop guard. Counts hops from the originating room.
    max_hop: int = DEFAULT_MAX_HOP
    #: When a relay copy is made, it is marked so it can never be mistaken for
    #: a message authored in the receiving room.
    mark_relayed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "parents": list(self.parents),
            "authority_parent": self.authority_parent,
            "path": self.path,
            "depth": self.depth,
            "state": self.state,
            "inbound": self.inbound,
            "outbound": self.outbound,
            "max_hop": self.max_hop,
            "mark_relayed": self.mark_relayed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupScope:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        scope = cls(**filtered)
        if scope.state not in ROOM_STATES:
            # A state written by a newer build must not make an older one crash
            # on load; it degrades to the safe readable state.
            scope.state = "active"
        if scope.inbound not in VALID_INBOUND:
            scope.inbound = "parent"
        if scope.outbound not in VALID_OUTBOUND:
            scope.outbound = "none"
        return scope


# ---------------------------------------------------------------------------
# Tree derivation. Pure functions over a `room_id -> GroupScope` mapping.
# ---------------------------------------------------------------------------


def children_of(scopes: dict[str, GroupScope], room_id: str) -> list[str]:
    """Direct children, in stable order.

    ``parents`` is the authoritative edge, so a child that names this room as a
    visibility parent appears here. Authority is a separate relationship and is
    not consulted.
    """
    return sorted(rid for rid, scope in scopes.items() if room_id in scope.parents)


def ancestors_of(scopes: dict[str, GroupScope], room_id: str) -> list[str]:
    """Every room reachable by walking visibility parents, nearest first.

    Bounded by ``MAX_DEPTH`` * (number of parents) rather than trusted to
    terminate: a corrupt file with a cycle must not hang a room read.
    """
    seen: set[str] = set()
    out: list[str] = []
    frontier = list(scopes.get(room_id, GroupScope(room_id=room_id)).parents)
    while frontier and len(out) <= MAX_DEPTH * 8:
        current = frontier.pop(0)
        if current in seen or current not in scopes:
            continue
        seen.add(current)
        out.append(current)
        frontier.extend(scopes[current].parents)
    return out


def descendants_of(scopes: dict[str, GroupScope], room_id: str) -> list[str]:
    """Every room beneath this one, breadth-first."""
    seen: set[str] = set()
    out: list[str] = []
    frontier = children_of(scopes, room_id)
    while frontier and len(out) <= MAX_DEPTH * 64:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
        frontier.extend(children_of(scopes, current))
    return out


def depth_of(scopes: dict[str, GroupScope], room_id: str) -> int:
    """Shortest distance from a root. A cycle reads as ``MAX_DEPTH``."""
    ancestors = ancestors_of(scopes, room_id)
    return min(len(ancestors), MAX_DEPTH)


def recompute_path(scopes: dict[str, GroupScope], room_id: str, name_of: dict[str, str]) -> str:
    """Rebuild this room's ``path`` from the tree and its authority parent.

    The path follows the **authority** chain, not the visibility chain: it is
    the policy lineage a reader expects to see, and it stays a single readable
    line even when a room has several visibility parents.
    """
    scope = scopes.get(room_id)
    if scope is None:
        return ""
    own = name_of.get(room_id, room_id)
    parent = scope.authority_parent
    if not parent or parent not in scopes:
        return own
    parent_path = recompute_path(scopes, parent, name_of)
    return f"{parent_path}/{own}" if parent_path else own


def recompute_all(scopes: dict[str, GroupScope], name_of: dict[str, str]) -> None:
    """Recompute every ``path`` and ``depth`` in place.

    Called after any structural change. Cheap because the forest is small, and
    doing it in one place is what stops a stale path from being served after a
    rename or a promote.
    """
    for room_id in scopes:
        scopes[room_id].path = recompute_path(scopes, room_id, name_of)
        scopes[room_id].depth = depth_of(scopes, room_id)


def assert_no_cycle(scopes: dict[str, GroupScope], room_id: str, new_parents: list[str]) -> None:
    """Refuse a re-parent that would make a room its own ancestor.

    The test is reachability: if the room being edited is already reachable from
    any proposed parent, adding that parent closes a loop.
    """
    if room_id in new_parents:
        raise ScopeError("A room cannot be its own parent.")
    for candidate in new_parents:
        if candidate not in scopes:
            raise ScopeError(f"Parent room '{candidate}' does not exist.")
        if room_id in descendants_of(scopes, candidate):
            raise ScopeError(f"Adding '{candidate}' as a parent would create a cycle: it already sits below this room.")


def assert_within_depth(scopes: dict[str, GroupScope], room_id: str, new_parents: list[str]) -> None:
    """Refuse a placement deeper than ``MAX_DEPTH``.

    Depth is measured against the *highest* parent, not the first: with multiple
    visibility parents, a room must fit beneath all of them, and a subtree that
    hangs off the shallowest one is what a reader sees in the sidebar.
    """
    if not new_parents:
        return
    deepest = max(depth_of(scopes, p) for p in new_parents if p in scopes)
    if deepest + 1 > MAX_DEPTH:
        raise ScopeError(
            f"Room would nest {deepest + 1} levels deep; the maximum is {MAX_DEPTH}. "
            "Promote an intermediate room or flatten the structure."
        )


def assert_authority_parent_consistent(scopes: dict[str, GroupScope], room_id: str, authority_parent: str | None) -> None:
    """The authority parent must itself be in the visibility set.

    An authority parent that is not also a visibility parent is a contradiction:
    the room would inherit rules from a room whose members and messages it
    cannot see. Requiring membership here keeps authority a refinement of
    visibility rather than a second, contradictory edge.
    """
    if authority_parent is None:
        return
    if authority_parent == room_id:
        raise ScopeError("A room cannot take authority from itself.")
    scope = scopes.get(room_id)
    if scope is not None and authority_parent not in scope.parents:
        raise ScopeError(
            "The authority parent must also be a visibility parent. "
            f"Add '{authority_parent}' to this room's parents first."
        )
    if authority_parent not in scopes:
        raise ScopeError(f"Authority parent '{authority_parent}' does not exist.")


# ---------------------------------------------------------------------------
# Relay planning
# ---------------------------------------------------------------------------


def plan_relay(
    scopes: dict[str, GroupScope],
    room_id: str,
    hop: int = 0,
) -> list[str]:
    """Rooms that receive a message posted in ``room_id``.

    ``hop`` is how many relay hops the message has already travelled. A message
    beyond ``max_hop`` relays nowhere, which is what stops
    ``outbound: siblings`` + ``inbound: broadcast`` from ping-ponging forever.

    A room is never its own destination, and a room already reached is not
    scheduled twice — a duplicate relay would double-post a message into a
    transcript.
    """
    scope = scopes.get(room_id)
    if scope is None or scope.outbound == "none":
        return []

    if scope.outbound == "parents":
        targets = list(scope.parents)
    elif scope.outbound == "siblings":
        # Siblings share a parent. A root has no siblings, and says so by
        # returning nothing rather than by fanning out to every other root.
        siblings: list[str] = []
        for parent in scope.parents:
            for child in children_of(scopes, parent):
                if child != room_id and child not in siblings:
                    siblings.append(child)
        targets = siblings
    else:  # "org" — every room except this one.
        targets = [rid for rid in scopes if rid != room_id]

    # Respect each target's own inbound policy: a target that does not accept
    # relayed traffic does not get it, even when something tried to send it.
    out: list[str] = []
    for target in targets:
        target_scope = scopes.get(target)
        if target_scope is None:
            continue
        if target not in scope.parents and target_scope.inbound == "none":
            continue
        if target_scope.inbound == "parent" and room_id not in target_scope.parents:
            # A non-parent sender only reaches a `parent`-inbound room through
            # an explicit sibling/broadcast policy on its own side.
            if target_scope.inbound != "broadcast":
                continue
        if target == room_id or target in out:
            continue
        if hop + 1 > target_scope.max_hop:
            continue
        out.append(target)
    return out


def inherited_policy(
    scopes: dict[str, GroupScope],
    room_id: str,
) -> dict[str, Any]:
    """What this room inherits, and from where.

    Returns the authority parent's effective policy plus the raw inheritance
    inputs, so a UI can disclose *what it inherited and from whom* rather than
    presenting a merged value as if it were set locally.
    """
    scope = scopes.get(room_id)
    if scope is None:
        return {"authority_parent": None, "inherits": False}
    parent = scope.authority_parent
    if not parent or parent not in scopes:
        return {"authority_parent": parent, "inherits": False, "reason": "no authority parent — this room sets its own policy"}
    return {"authority_parent": parent, "inherits": True, "parent_path": scopes[parent].path}