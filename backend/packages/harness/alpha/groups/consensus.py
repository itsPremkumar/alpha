"""Consensus v2: agreement measured on stated claims, not on "did it return text".

The old war-room tally derived a vote from a member's *status*:
``CONTRIBUTED -> agree``, ``EMPTY -> amend``, anything else ``-> disagree``. That
is a proxy, and a bad one: it reports agreement for any member that produced any
string at all, so a room of five near-identical one-word answers scored a
unanimous ``majority`` while containing no agreement.

This module measures the thing the tally claims to measure:

* a member's **claims** are parsed out of its contribution
  (:func:`alpha.deliberation.parsing.parse_claims_and_confidence`), so agreement
  is over *positions taken* rather than over text volume;
* a member **agrees** when it shares at least one normalised claim with the
  room's plurality cluster, and **disses** when it shares none;
* the minority view is **preserved**, never discarded - a consensus number with
  the dissent thrown away is a quieter lie than no number at all;
* **correlated voters** (the same ``model_id`` answering twice) are collapsed,
  because two calls to one model are one opinion.

Design constraints kept deliberately:

* deterministic and stdlib-only, so it runs between model calls on the hot path;
* **no self-reported confidence weighting.** Research is explicit that
  self-reported confidence does not separate correct from incorrect answers
  ("Consensus is Not Verification"), so confidence is *recorded* and never used
  to move a threshold.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

#: Reused from the deliberation package rather than reimplemented, so the war room
#: and the ``deliberate`` tool read a member's claims the same way.
from alpha.deliberation.parsing import parse_claims_and_confidence


def _normalise(claim: str) -> str:
    """Fold a claim to a comparable key: case, punctuation, articles, whitespace."""
    folded = re.sub(r"\W+", " ", (claim or "").lower()).strip()
    for article in ("the ", "a ", "an "):
        if folded.startswith(article):
            folded = folded[len(article) :]
    return folded


@dataclass(slots=True)
class MemberPosition:
    """What one member actually said, once parsed."""

    member: str
    claims: list[str] = field(default_factory=list)
    self_confidence: float | None = None
    model_id: str = ""
    #: True when the member stated nothing parseable, so agreement is unknowable
    #: rather than absent. Never counted as a vote either way.
    unparsed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "member": self.member,
            "claims": list(self.claims),
            "self_confidence": self.self_confidence,
            "model_id": self.model_id,
            "unparsed": self.unparsed,
        }


def read_position(member: str, text: str, *, model_id: str = "") -> MemberPosition:
    """Parse one contribution into a position.

    A member that said nothing parseable is ``unparsed``, which is deliberately
    distinct from "agrees with nothing" - the caller must not read silence as a
    vote.
    """
    claims, confidence = parse_claims_and_confidence(text or "")
    cleaned = [c.strip() for c in claims if c and c.strip()]
    return MemberPosition(
        member=member,
        claims=cleaned,
        self_confidence=confidence,
        model_id=model_id,
        unparsed=not cleaned,
    )


@dataclass(slots=True)
class ConsensusOutcome:
    """Agreement over claims, with the minority kept."""

    #: Members whose claims intersect the plurality cluster.
    agreeing: list[str] = field(default_factory=list)
    #: Members whose claims do not intersect it. Never dropped.
    dissenting: list[str] = field(default_factory=list)
    #: Members nobody else's position touches. They did not oppose a shared
    #: position; they simply had no company. Distinct from dissent, and kept
    #: separate so a total split is not reported as a two-sided argument.
    isolated: list[str] = field(default_factory=list)
    #: Members that said nothing parseable; excluded from both sides.
    unparsed: list[str] = field(default_factory=list)
    #: The claims the plurality cluster agrees on, normalised.
    agreed_claims: list[str] = field(default_factory=list)
    #: Verbatim dissent text, so a reader sees the objection, not just a name.
    dissent_text: dict[str, str] = field(default_factory=dict)
    #: model_id -> members collapsed into one voice.
    correlated_groups: dict[str, list[str]] = field(default_factory=dict)
    #: True when a correlated collapse changed the outcome.
    correlation_adjusted: bool = False

    @property
    def agreement_ratio(self) -> float:
        decided = len(self.agreeing) + len(self.dissenting)
        return (len(self.agreeing) / decided) if decided else 0.0

    def human_line(self) -> str:
        return f"agree={len(self.agreeing)} dissent={len(self.dissenting)} isolated={len(self.isolated)} unparsed={len(self.unparsed)} ratio={self.agreement_ratio:.2f} agreed_claims={len(self.agreed_claims)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreeing": list(self.agreeing),
            "dissenting": list(self.dissenting),
            "isolated": list(self.isolated),
            "unparsed": list(self.unparsed),
            "agreed_claims": list(self.agreed_claims),
            "dissent_text": dict(self.dissent_text),
            "correlated_groups": {k: list(v) for k, v in self.correlated_groups.items()},
            "correlation_adjusted": self.correlation_adjusted,
            "agreement_ratio": round(self.agreement_ratio, 3),
        }


def collapse_correlated(positions: list[MemberPosition]) -> tuple[list[MemberPosition], dict[str, list[str]], bool]:
    """Collapse members sharing a ``model_id`` into a single voice.

    Two calls to the same model are one opinion sampled twice. The first member
    of a group is kept as its voice; the rest are recorded in the returned map so
    the run can *disclose* the collapse rather than silently shrinking the room.

    Members with no declared ``model_id`` are never collapsed - absence of a
    declared model is not evidence of duplication.
    """
    groups: dict[str, list[str]] = {}
    for position in positions:
        if position.model_id:
            groups.setdefault(position.model_id, []).append(position.member)

    duplicates = {mid: members for mid, members in groups.items() if len(members) > 1}
    if not duplicates:
        return list(positions), {}, False

    seen_models: set[str] = set()
    kept: list[MemberPosition] = []
    for position in positions:
        if position.model_id and position.model_id in duplicates:
            # The FIRST member of a duplicated model is its voice; later ones are
            # recorded in `duplicates` and dropped from the vote.
            if position.model_id in seen_models:
                continue
            seen_models.add(position.model_id)
        kept.append(position)
    return kept, duplicates, True


def _components(members: list[str], claims_by_member: dict[str, set[str]]) -> list[list[str]]:
    """Connected components of the co-agreement graph.

    Two members are adjacent when they share at least one normalised claim, so a
    component is a set of members that agree *transitively* - A agrees with B,
    B with C, therefore the room has a three-way position even if A and C never
    said the same sentence.

    A majority-vote over individual claims gets this wrong: when every member
    states distinct claims (the normal case for genuinely independent opinions)
    every claim has count 1, so "plurality" is a coin flip between alphabetical
    order. Components have no such failure mode.
    """
    parent = {m: m for m in members}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            if claims_by_member[a] & claims_by_member[b]:
                union(a, b)

    grouped: dict[str, list[str]] = {}
    for member in members:
        grouped.setdefault(find(member), []).append(member)
    # Largest first, then alphabetical, so the outcome is deterministic.
    return sorted(grouped.values(), key=lambda group: (-len(group), sorted(group)))


def measure(positions: list[MemberPosition], *, texts: dict[str, str] | None = None) -> ConsensusOutcome:
    """Measure agreement over stated claims, preserving the minority.

    The agreeing set is the largest co-agreement component. When every member
    states a distinct claim there is no component larger than one, so nobody
    agrees - which is the truthful answer, not a failure to be papered over with
    a tie-break.
    """
    outcome = ConsensusOutcome()
    kept, correlated, adjusted = collapse_correlated(positions)
    outcome.correlated_groups = correlated
    outcome.correlation_adjusted = adjusted

    for position in kept:
        if position.unparsed:
            outcome.unparsed.append(position.member)

    parsed = [p for p in kept if not p.unparsed]
    if not parsed:
        return outcome

    claims_by_member: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    for position in parsed:
        keys = {_normalise(c) for c in position.claims}
        keys.discard("")
        if not keys:
            outcome.unparsed.append(position.member)
            continue
        claims_by_member[position.member] = keys
        for key in keys:
            display.setdefault(key, position.claims[position.claims.index(next(c for c in position.claims if _normalise(c) == key))])

    if not claims_by_member:
        return outcome

    members = sorted(claims_by_member)
    groups = _components(members, claims_by_member)
    # Only a component of two or more is agreement. A lone position is neither
    # agreement nor opposition, so it is reported as isolated.
    winning = groups[0] if groups and len(groups[0]) > 1 else set()
    agreeing_group = set(winning)
    losers = [g for g in groups[1:] if len(g) > 1]
    singletons = [g[0] for g in groups if len(g) == 1]

    if agreeing_group:
        counts = Counter(key for member in agreeing_group for key in claims_by_member[member])
        shared = {key for key, count in counts.items() if count > 1}
        outcome.agreed_claims = sorted(display[key] for key in shared)

    for group in losers:
        outcome.dissenting.extend(group)
    outcome.isolated.extend(singletons)

    # Every non-agreeing view is preserved verbatim, whether it opposed a
    # winning position or simply stood alone. Both are positions a reader needs.
    for member in outcome.dissenting + outcome.isolated:
        if texts and member in texts:
            outcome.dissent_text[member] = texts[member]

    outcome.agreeing = sorted(agreeing_group)
    outcome.dissenting.sort()
    outcome.isolated.sort()
    outcome.unparsed.sort()
    return outcome
