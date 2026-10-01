"""Roster membership: who is in a room, and *why*.

Three separate things decide membership, and the UI must be able to tell them
apart because each has a different owner and a different lifetime:

- **direct** — a hand-added member. Its own record of who added it and when.
- **rule-matched** — resolved from declared rules against the Bot Registry,
  live on every read. Hire a new tester and a rule for ``role=tester``
  populates the room with no join step and no stale roster to reconcile.
- **inherited** — derived from a visibility parent's roster, never stored. It
  is a projection, so it cannot go stale and cannot be written back.

**This module never writes ``GroupRoom.members``.** For a project room that
field belongs to ``alpha.projects.crew.ensure_crew``, which *deletes* any entry
project membership does not claim. Nested or rule-based members written there
would be erased on the next reconcile. That constraint is the whole reason a
separate roster exists.

The temporary-squad feature is also here, because a borrowed member is a
``direct`` entry with an expiry and a source room — not a fourth mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from alpha.groups.room import _now

#: Which registry field a rule matches on. Bounded on purpose: an open field
#: name would let a rule read anything the profile happens to carry, including
#: the SOUL, and become a prompt-injection surface.
RuleField = Literal["role", "skill", "toolset", "department", "model", "capability"]
RuleOp = Literal["eq", "contains", "intersects"]

VALID_RULE_FIELDS: tuple[str, ...] = ("role", "skill", "toolset", "department", "model", "capability")
VALID_RULE_OPS: tuple[str, ...] = ("eq", "contains", "intersects")

#: A rule value is matched against short profile strings. The ceiling keeps one
#: rule from becoming an unbounded scan of the SOUL.
_MAX_RULE_VALUE = 120


class RosterError(ValueError):
    """A refused roster change, with a reason the API can surface verbatim."""


@dataclass
class MembershipRule:
    """A declared membership predicate, resolved live against the registry.

    ``skill``/``toolset``/``capability`` are list-valued on a ``BotProfile``, so
    they use ``intersects``/``contains``; the rest are single strings and use
    ``eq``. The op is explicit rather than inferred, because "does this rule
    match" must be answerable without re-deriving the schema on both sides.
    """

    id: str
    field: RuleField
    op: RuleOp
    value: str
    enabled: bool = True
    #: Optional human label so a room's roster can read as intent, not as a
    #: query. Never used for matching.
    label: str = ""

    def matches(self, profile: dict[str, Any]) -> bool:
        raw = profile.get(self.field)
        needle = self.value.strip().lower()
        if self.op == "eq":
            if raw is None:
                return False
            return str(raw).strip().lower() == needle
        if self.op == "contains":
            # `contains` is for a single-valued field, and it must NOT substring
            # match: `role contains "test"` matching `contest-writer` is the
            # same defect as the presence dot's `active`/`inactive` bug.
            if raw is None:
                return False
            return str(raw).strip().lower() == needle
        if self.op == "intersects":
            if not isinstance(raw, list):
                return False
            return any(str(item).strip().lower() == needle for item in raw)
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "field": self.field,
            "op": self.op,
            "value": self.value,
            "enabled": self.enabled,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MembershipRule:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


def validate_rule(data: dict[str, Any]) -> MembershipRule:
    """Validate a rule body, returning the coerced record or refusing it."""
    field_name = str(data.get("field", "")).strip().lower()
    if field_name not in VALID_RULE_FIELDS:
        raise RosterError(f"Rule field must be one of {list(VALID_RULE_FIELDS)}.")
    op = str(data.get("op", "")).strip().lower()
    if op not in VALID_RULE_OPS:
        raise RosterError(f"Rule op must be one of {list(VALID_RULE_OPS)}.")
    value = str(data.get("value", "")).strip()
    if not value:
        raise RosterError("Rule value is required.")
    if len(value) > _MAX_RULE_VALUE:
        raise RosterError(f"Rule value must be at most {_MAX_RULE_VALUE} characters.")
    # A list-valued field with `eq` can never match anything: comparing a whole
    # list to a string is a silent always-false. Refuse it rather than store a
    # rule that appears valid and populates nothing.
    if field_name in ("skill", "toolset", "capability") and op == "eq":
        raise RosterError(f"Field '{field_name}' holds a list — use op 'intersects', not 'eq'.")
    return MembershipRule(
        id=str(data.get("id", "")).strip(),
        field=field_name,  # type: ignore[arg-type]
        op=op,  # type: ignore[arg-type]
        value=value,
        enabled=bool(data.get("enabled", True)),
        label=str(data.get("label", "")).strip()[:_MAX_RULE_VALUE],
    )


@dataclass
class GroupRoster:
    """One room's declared membership.

    ``direct`` records who added a member and when, because a borrowed squad
    member and a standing member are different claims and the room has to be
    able to label them differently.
    """

    room_id: str
    #: bot name -> {"by": actor, "at": stamp, "from_room": source room or None,
    #:               "expires_at": stamp or None}
    direct: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: list[MembershipRule] = field(default_factory=list)
    #: Members pulled out of this room even though a parent or rule grants them.
    #: Without this, "a subgroup for just the backend work" is impossible.
    excluded: list[str] = field(default_factory=list)

    def add(self, name: str, *, by: str, from_room: str | None = None, expires_at: str | None = None) -> None:
        clean = (name or "").strip().lower()
        if not clean:
            raise RosterError("Member name is required.")
        self.direct[clean] = {"by": by, "at": _now(), "from_room": from_room, "expires_at": expires_at}

    def remove(self, name: str) -> bool:
        clean = (name or "").strip().lower()
        if clean in self.direct:
            del self.direct[clean]
            return True
        return False

    def exclude(self, name: str) -> None:
        clean = (name or "").strip().lower()
        if clean and clean not in self.excluded:
            self.excluded.append(clean)

    def include(self, name: str) -> bool:
        clean = (name or "").strip().lower()
        if clean in self.excluded:
            self.excluded.remove(clean)
            return True
        return False

    def expired(self, now: str | None = None) -> list[str]:
        """Direct members whose borrow window has closed.

        The record is kept after expiry rather than dropped, so the room can
        still say *this member was borrowed from sprint-room and has expired*
        instead of silently forgetting them.
        """
        moment = now or _now()
        out: list[str] = []
        for name, meta in self.direct.items():
            deadline = meta.get("expires_at")
            if deadline and str(deadline) <= moment:
                out.append(name)
        return sorted(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "direct": {k: dict(v) for k, v in self.direct.items()},
            "rules": [r.to_dict() for r in self.rules],
            "excluded": list(self.excluded),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupRoster:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        roster = cls(**filtered)
        roster.rules = [MembershipRule.from_dict(r) for r in roster.rules]
        return roster


# ---------------------------------------------------------------------------
# Resolution. Everything below is derived; nothing is stored twice.
# ---------------------------------------------------------------------------


def _profiles() -> dict[str, dict[str, Any]]:
    """The Bot Registry as plain matchable dicts.

    A registry that cannot be read yields ``{}``, so rules resolve to nothing
    and the room reports zero rule matches — never a crash and never a guess.
    """
    try:
        from alpha.bots.registry import get_bot_registry

        bots = get_bot_registry().list_bots()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for bot in bots:
        name = (getattr(bot, "name", "") or "").strip().lower()
        if not name:
            continue
        out[name] = {
            "name": name,
            "role": getattr(bot, "role", None),
            "department": getattr(bot, "department", None),
            "model": getattr(bot, "model", None),
            "skill": list(getattr(bot, "skills", []) or []),
            "toolset": list(getattr(bot, "toolsets", []) or []),
            "capability": list(getattr(bot, "capabilities", []) or []),
        }
    return out


def resolve_rule(
    rule: MembershipRule,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Who this rule matches, right now."""
    if not rule.enabled:
        return []
    table = profiles if profiles is not None else _profiles()
    return sorted(name for name, profile in table.items() if rule.matches(profile))


def preview_rules(
    rules: list[MembershipRule],
    profiles: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Every rule with its current match count.

    This is what ``/rules/preview`` returns, and it exists because a mistyped
    rule that silently matches nobody looks exactly like a room nobody joined.
    """
    table = profiles if profiles is not None else _profiles()
    return [
        {**rule.to_dict(), "matches": len(resolve_rule(rule, table)), "matched_names": resolve_rule(rule, table)}
        for rule in rules
    ]


@dataclass
class ResolvedRoster:
    """A room's full membership, split by why each member is present.

    The split is the deliverable. A single flat list makes an inherited member
    look hand-added and a rule-matched member look invited, which is how a
    nested roster becomes incomprehensible.
    """

    room_id: str
    direct: list[str]
    rule_matched: list[str]
    inherited: list[str]
    #: parent room id -> members this room inherited from it
    inherited_from: dict[str, list[str]] = field(default_factory=dict)
    #: rule id -> members it granted
    rule_sources: dict[str, list[str]] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)

    @property
    def effective(self) -> list[str]:
        """Direct + rule-matched + inherited, minus excluded, minus expired.

        This is what the runner fans out to and what a presence count reports.
        The direct count is reported *beside* it, never in place of it.
        """
        dead = set(self.excluded) | set(self.expired)
        live = set(self.direct) | set(self.rule_matched) | set(self.inherited)
        return sorted(live - dead)

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "direct": list(self.direct),
            "rule_matched": list(self.rule_matched),
            "inherited": list(self.inherited),
            "inherited_from": {k: list(v) for k, v in self.inherited_from.items()},
            "rule_sources": {k: list(v) for k, v in self.rule_sources.items()},
            "excluded": list(self.excluded),
            "expired": list(self.expired),
            "effective": self.effective,
            "effective_count": len(self.effective),
            "direct_count": len(self.direct),
        }


def resolve_roster(
    room_id: str,
    roster: GroupRoster | None,
    direct_members: list[str],
    inherited_by_parent: dict[str, list[str]] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> ResolvedRoster:
    """Compute one room's membership from every source.

    ``direct_members`` is the room's own ``GroupRoom.members`` — passed in rather
    than read here, because for a project room that list is crew-owned and this
    module must not be the thing that mutates it.

    Inheritance is a **projection**, computed from the parents' effective
    rosters at read time. It is never written into any room's own roster, which
    is what makes an inherited member impossible to orphan by editing the child.
    """
    table = profiles if profiles is not None else _profiles()
    by_parent = inherited_by_parent or {}

    direct = sorted({(m or "").strip().lower() for m in direct_members if (m or "").strip()})

    rule_matched: set[str] = set()
    rule_sources: dict[str, list[str]] = {}
    for rule in (roster.rules if roster else []):
        matched = resolve_rule(rule, table)
        rule_sources[rule.id] = matched
        rule_matched.update(matched)

    declared_direct = sorted(roster.direct) if roster else []
    all_direct = sorted(set(direct) | set(declared_direct))

    inherited: set[str] = set()
    for members in by_parent.values():
        inherited.update(members)
    # A member already direct is not also "inherited"; listing them twice makes
    # the three-way split in the UI lie about its own arithmetic.
    inherited -= set(all_direct)

    excluded = sorted(roster.excluded) if roster else []
    expired = roster.expired() if roster else []

    return ResolvedRoster(
        room_id=room_id,
        direct=all_direct,
        rule_matched=sorted(rule_matched),
        inherited=sorted(inherited),
        inherited_from={parent: sorted(members) for parent, members in by_parent.items()},
        rule_sources=rule_sources,
        excluded=excluded,
        expired=expired,
    )