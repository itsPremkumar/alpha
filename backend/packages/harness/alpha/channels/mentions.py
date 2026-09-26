"""The ONE mention grammar for chat addressing.

Why this module exists
----------------------
Mention handling was scattered across roughly fifty files with no shared
grammar. Every one of them re-implemented "find an ``@`` and hope the token
after it names somebody", which is why tagging was unreliable: a handle that
matched nothing usually fell through to a default speaker, so a typo silently
addressed a wrong-but-plausible bot.

This module defines the grammar once, parses once, and returns a resolution
that the caller routes on. The two hard rules:

1. An unknown or ambiguous handle resolves to NOTHING and says why. It never
   resolves to a near match. ``@rev`` is not ``@reviewer`` unless ``reviewer``
   is the only roster entry it can mean.
2. Resolution is a pure function of ``(text, roster, roles)``. There is no
   I/O, no registry lookup, and no fallback-to-a-default path, so a routing
   decision can be replayed and tested exactly.

Grammar (the whole of it), with EBNF punctuation spelled out::

    mention  := AT kind COLON body   -- explicit, unambiguous
              | AT body              -- bare handle, resolved against the roster
    kind     := "bot" | "role" | "everyone"
    body     := one or more of [A-Za-z0-9_.-]
                -- note: no spaces, so a mention can never swallow a sentence

A bare ``@body`` is a bot handle, and it matches a roster entry EXACTLY after
case folding. It is never a prefix, substring or fuzzy match: ``@rev`` does not
address ``reviewer``. That is the single most important property of this module,
because a near match is precisely how a message lands on a bot nobody named.

Role and roster selectors MUST be explicit (``@role:reviewer``). Roles and bot
names are overlapping namespaces, and guessing which namespace a bare token
belongs to is the other half of the ambiguity this module refuses.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The complete token grammar. Deliberately narrow: no spaces (so a mention can
#: never swallow a sentence), no path separators, no unicode confusables.
_MENTION_RE = re.compile(
    r"@(?P<kind>bot|role|everyone):(?P<qualified>[A-Za-z0-9_.\-]+)"
    r"|@(?P<bare>[A-Za-z0-9_.\-]+)"
)

#: Explicit selector kinds.
KIND_BOT = "bot"
KIND_ROLE = "role"
KIND_ALL = "all"

#: The selector that fans a message out to every participant.
ALL_SELECTOR = "everyone"

#: Accepted spellings of the fan-out selector. ``@all`` is a bare token that
#: would otherwise be resolved as a bot handle, so it is matched as a selector
#: spelling; an unknown bare token is still refused, never fanned out.
ALL_SELECTOR_ALIASES: frozenset[str] = frozenset({"all", "everyone"})

#: Upper bound on resolved targets for one message. A selector that would
#: address more than this is refused rather than fanned out, so one message
#: cannot be used to stampede the fleet.
MAX_TARGETS_PER_MESSAGE = 32


def normalise_handle(raw: str) -> str:
    """Fold a handle to its comparison form.

    Case-folding only. Punctuation is NOT stripped: ``rev-1`` and ``rev_1`` are
    different handles, and treating them as the same one is how a message ends
    up on a bot nobody named.
    """
    return (raw or "").strip().lower()


@dataclass(frozen=True, slots=True)
class MentionSpan:
    """One ``@token`` found in the text, with its byte offsets in the source."""

    start: int
    end: int
    raw: str
    kind: str
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "raw": self.raw,
            "kind": self.kind,
            "body": self.body,
        }


@dataclass(frozen=True, slots=True)
class MentionTarget:
    """A mention that resolved to at least one concrete addressee.

    ``via`` records HOW it resolved, which is what an operator needs to see when
    a role selector pulled in three bots: the reason is on the record, not
    inferred later.
    """

    kind: str
    selector: str
    resolved: tuple[str, ...]
    via: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "selector": self.selector,
            "resolved": list(self.resolved),
            "via": self.via,
        }


@dataclass(frozen=True, slots=True)
class MentionResolution:
    """The result of parsing one message body.

    ``targets`` is what to dispatch to. ``unresolved`` is what a human has to
    fix. Both are always present, and ``unresolved`` is never empty when
    ``targets`` silently absorbed a bad token, because it cannot be: an
    unresolvable mention never produces a target.
    """

    text: str
    spans: tuple[MentionSpan, ...] = ()
    targets: tuple[MentionTarget, ...] = ()
    unresolved: tuple[dict[str, str], ...] = ()
    role_index: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def resolved_handles(self) -> tuple[str, ...]:
        """Every concrete addressee, de-duplicated, in first-mention order."""
        seen: dict[str, None] = {}
        for target in self.targets:
            for handle in target.resolved:
                seen.setdefault(handle, None)
        return tuple(seen)

    @property
    def ok(self) -> bool:
        """True when every mention in the text resolved."""
        return not self.unresolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "spans": [s.to_dict() for s in self.spans],
            "targets": [t.to_dict() for t in self.targets],
            "unresolved": [dict(u) for u in self.unresolved],
            "resolved_handles": list(self.resolved_handles),
            "ok": self.ok,
        }

    def human_line(self) -> str:
        """One operator-readable line describing what resolved and what did not."""
        if not self.spans:
            return "no mentions"
        parts = [f"{t.selector}->{','.join(t.resolved)}" for t in self.targets]
        bad = [f"{u['raw']} ({u['reason']})" for u in self.unresolved]
        return "; ".join(parts + bad) if (parts or bad) else "no mentions"


class MentionResolutionError(ValueError):
    """Raised when the roster or role index itself is unusable."""


def build_role_index(
    roles: Mapping[str, Iterable[str]] | None,
) -> dict[str, tuple[str, ...]]:
    """Normalise a ``{role: [members]}`` mapping into a lookup table.

    Rejects a role whose members include a handle that is not in the roster at
    resolve time (checked lazily, because the roster is not known here) and
    rejects a role that would address nobody, which is always a configuration
    mistake rather than a runtime condition.
    """
    index: dict[str, tuple[str, ...]] = {}
    for raw_role, members in (roles or {}).items():
        role = normalise_handle(raw_role)
        if not role:
            raise MentionResolutionError("role name must not be blank")
        handles = tuple(dict.fromkeys(normalise_handle(m) for m in members if normalise_handle(m)))
        if not handles:
            raise MentionResolutionError(f"role {role!r} has no members; a selector that resolves to nobody is a configuration error")
        index[role] = handles
    return index


def parse_mentions(
    text: str,
    *,
    roster: Sequence[str],
    roles: Mapping[str, Iterable[str]] | None = None,
    sender: str | None = None,
) -> MentionResolution:
    """Parse ``text`` once and return the complete routing decision.

    ``roster`` is the authoritative set of addressable handles for this room.
    ``roles`` maps a role name to the handles that hold it. ``sender`` is
    excluded from ``@all`` fan-out so a bot cannot address itself into a loop.

    Resolution never guesses. See the module docstring.
    """
    body = text or ""
    known: dict[str, list[str]] = {}
    for handle in roster:
        key = normalise_handle(handle)
        if key:
            known.setdefault(key, []).append(handle)
    role_index = build_role_index(roles)

    spans: list[MentionSpan] = []
    targets: list[MentionTarget] = []
    unresolved: list[dict[str, str]] = []
    emitted: set[tuple[str, str]] = set()

    def _emit(kind: str, selector: str, resolved: Sequence[str], via: str) -> None:
        key = (kind, selector)
        if key in emitted:
            return
        emitted.add(key)
        targets.append(
            MentionTarget(kind=kind, selector=selector, resolved=tuple(resolved), via=via)
        )

    for match in _MENTION_RE.finditer(body):
        kind = match.group("kind")
        raw = match.group(0)
        if kind:
            body_token = normalise_handle(match.group("qualified"))
        else:
            body_token = normalise_handle(match.group("bare"))
            # A bare "all"/"everyone" is the fan-out selector, never a handle.
            if body_token in ALL_SELECTOR_ALIASES:
                kind = KIND_ALL
            else:
                kind = KIND_BOT

        span = MentionSpan(
            start=match.start(), end=match.end(), raw=raw, kind=kind, body=body_token
        )
        spans.append(span)

        if kind == KIND_ALL:
            everyone = [h for h in known if normalise_handle(h) != normalise_handle(sender or "")]
            if not everyone:
                unresolved.append(
                    {
                        "raw": raw,
                        "reason": f"@{ALL_SELECTOR} resolved to nobody (the sender is the only member)",
                    }
                )
                continue
            if len(everyone) > MAX_TARGETS_PER_MESSAGE:
                unresolved.append(
                    {
                        "raw": raw,
                        "reason": (
                            f"@{ALL_SELECTOR} would address {len(everyone)} handles, above the "
                            f"{MAX_TARGETS_PER_MESSAGE} fan-out ceiling"
                        ),
                    }
                )
                continue
            _emit(KIND_ALL, ALL_SELECTOR, sorted(everyone), "all-selector")
            continue

        if kind == KIND_ROLE:
            members = role_index.get(body_token)
            if members is None:
                known_roles = ", ".join(sorted(role_index)) or "(none defined)"
                unresolved.append(
                    {
                        "raw": raw,
                        "reason": f"unknown role selector; defined roles: {known_roles}",
                    }
                )
                continue
            absent = [m for m in members if m not in known]
            if absent:
                unresolved.append(
                    {
                        "raw": raw,
                        "reason": f"role {body_token!r} references {len(absent)} handle(s) not in this room: {','.join(sorted(absent))}",
                    }
                )
                continue
            if len(members) > MAX_TARGETS_PER_MESSAGE:
                unresolved.append(
                    {
                        "raw": raw,
                        "reason": f"role {body_token!r} would address {len(members)} handles, above the {MAX_TARGETS_PER_MESSAGE} fan-out ceiling",
                    }
                )
                continue
            _emit(KIND_ROLE, body_token, list(members), "role-selector")
            continue

        # Bare handle: the bot case.
        matches = known.get(body_token)
        if not matches:
            unresolved.append(
                {
                    "raw": raw,
                    "reason": "unknown handle; this message addresses nobody. Use @role:<name> for a role selector.",
                }
            )
            continue
        if len(matches) > 1:
            unresolved.append(
                {
                    "raw": raw,
                    "reason": f"ambiguous handle; {len(matches)} roster entries match: {','.join(sorted(matches))}",
                }
            )
            continue
        _emit(KIND_BOT, body_token, matches, "explicit-handle")

    return MentionResolution(
        text=body,
        spans=tuple(spans),
        targets=tuple(targets),
        unresolved=tuple(unresolved),
        role_index=role_index,
    )


def mention_capabilities(handles: Sequence[str]) -> tuple[str, ...]:
    """Capability tags implied by addressing a set of bots.

    Used by the role-selector path so a dispatch decision carries the same tags
    a capability match would look for, instead of routing on a display name.
    """
    return tuple(sorted({f"mention:{normalise_handle(h)}" for h in handles if normalise_handle(h)}))
