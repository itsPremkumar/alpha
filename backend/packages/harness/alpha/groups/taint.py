"""Cross-agent taint tracking for the war room.

A war room is, structurally, the topology that prompt-infection research targets:
one agent's output is rendered into the next agent's prompt, on purpose, so the
room can deliberate. That is also exactly how an injected instruction propagates
- ``Prompt Infection`` (arXiv 2410.07283) shows malicious prompts self-replicating
across interconnected agents "even when agents do not publicly share all
communications".

This module does not claim to stop the attack; it makes propagation **visible
and bounded**:

1. :func:`screen` classifies a contribution as clean/suspect/infected and says
   why, so a poisoned receipt is never silently indistinguishable from a good one.
2. :func:`sanitize_for_prompt` strips the matched spans before a contribution is
   re-embedded in another participant's prompt, so a detected payload does not
   ride the next hop.
3. :func:`room_is_colluding` flags a suspiciously unanimous room, because
   unanimity with no independent evidence is a signal, not a result.

Every function here is deterministic and dependency-free so it can run on the
hot path between two model calls without a network round trip. A pattern list is
a mitigation, not a guarantee: the honest claim is "screened and disclosed", never
"safe".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Taint(StrEnum):
    """How far a contribution is from being a plain opinion."""

    CLEAN = "clean"
    #: Matched a heuristic, but nothing that reads as an instruction override.
    SUSPECT = "suspect"
    #: Matched an explicit instruction-override or exfiltration pattern.
    INFECTED = "infected"


#: Patterns that read as an attempt to override the recipient's instructions.
#: Grouped so a receipt can report a *category*, which is more useful to an
#: operator than "regex 3 matched".
_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("instruction_override", r"\bignore (?:all |any )?(?:your |the )?(?:previous|prior|above|earlier)\b"),
    ("instruction_override", r"\bdisregard (?:all |any )?(?:previous|prior|above|earlier|the)\b"),
    ("instruction_override", r"\bforget (?:everything|all|your instructions)\b"),
    ("instruction_override", r"\byou are now\b|\bnew instructions?\b|\bfrom now on,? you\b"),
    ("instruction_override", r"\bsystem (?:prompt|message|override)\b"),
    (
        "exfiltration",
        r"\b(?:send|post|upload|exfiltrate|transmit|forward)\b[^.\n]{0,40}\b(?:https?://|webhook|api[_-]?key|token|credential|secret|password|env(?:ironment)?\b)",
    ),
    (
        "exfiltration",
        r"\b(?:reveal|print|output|show|repeat|dump)\b[^.\n]{0,30}\b(?:system prompt|api[_-]?key|token|credential|secret|password)\b",
    ),
    ("exfiltration", r"\bcurl\b[^.\n]{0,60}\bhttps?://"),
    ("tool_abuse", r"\b(?:run|execute|invoke|call)\b[^.\n]{0,30}\bshell command\b|\bbash -c\b|\bsubprocess\b"),
    ("authority_claim", r"\b(?:as the (?:system|administrator|owner|operator)|on behalf of the system)\b"),
    ("covert_channel", r"\bdo not (?:mention|reveal|tell|log)\b[^.\n]{0,30}\b(?:this|that|the user)\b"),
    ("covert_channel", r"\bwithout (?:telling|informing|notifying|asking)\b[^.\n]{0,20}\b(?:the )?(?:user|operator|human)\b"),
)

#: Phrases that legitimately appear in security discussion, so a hit is downgraded
#: rather than treated as an attack. A red-team room *talks about* these.
_BENIGN_CONTEXT = (
    "example of",
    "e.g.",
    "for instance",
    "hypothetically",
    "if an attacker",
    "attack vector",
    "threat model",
    "do not do this",
    "never do this",
    "prompt injection",
    "injection attack",
    "red team",
    "adversarial",
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple((category, re.compile(pattern, re.IGNORECASE)) for category, pattern in _INJECTION_PATTERNS)


@dataclass(frozen=True, slots=True)
class Screening:
    """The verdict on one piece of text."""

    taint: Taint
    categories: tuple[str, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    note: str = ""

    @property
    def clean(self) -> bool:
        return self.taint is Taint.CLEAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "taint": str(self.taint),
            "categories": list(self.categories),
            "match_count": len(self.spans),
            "note": self.note,
        }


def _benign_context(text: str, start: int) -> bool:
    """True when the hit sits inside an explicitly illustrative clause."""
    window = text[max(0, start - 90) : start].lower()
    return any(marker in window for marker in _BENIGN_CONTEXT)


def screen(text: str) -> Screening:
    """Classify one contribution. Never raises; empty text is clean."""
    if not text or not text.strip():
        return Screening(Taint.CLEAN)

    categories: list[str] = []
    spans: list[tuple[int, int]] = []
    benign_hits = 0
    for category, pattern in _COMPILED:
        for match in pattern.finditer(text):
            if _benign_context(text, match.start()):
                benign_hits += 1
                continue
            categories.append(category)
            spans.append((match.start(), match.end()))

    if not spans:
        # Everything matched was illustrative. Say so rather than staying silent,
        # because "matched but dismissed" is a materially different audit record
        # from "did not match".
        return Screening(Taint.CLEAN, note=f"{benign_hits} illustrative match(es) dismissed" if benign_hits else "")

    unique = tuple(dict.fromkeys(categories))
    taint = Taint.INFECTED if unique else Taint.SUSPECT
    return Screening(taint, unique, tuple(spans), note=f"{len(spans)} match(es) across {len(unique)} categorie(s)")


def sanitize_for_prompt(text: str, screening: Screening | None = None) -> str:
    """Remove the matched spans so a detected payload cannot ride the next hop.

    Replaces each hit with a visible ``[redacted: <category>]`` marker rather than
    deleting it silently: the next participant should be able to see that
    something was removed, and a reader of the transcript should too.
    """
    if not text:
        return text
    verdict = screening or screen(text)
    if not verdict.spans:
        return text

    ordered = sorted(verdict.spans, key=lambda pair: pair[0])
    category_by_start: dict[int, str] = {}
    for category, pattern in _COMPILED:
        for match in pattern.finditer(text):
            category_by_start.setdefault(match.start(), category)

    out: list[str] = []
    cursor = 0
    for start, end in ordered:
        if start < cursor:
            continue  # overlapping hit already covered
        out.append(text[cursor:start])
        out.append(f"[redacted: {category_by_start.get(start, 'unclassified')}]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


@dataclass(slots=True)
class CollusionSignal:
    """Evidence that agreement is less independent than it looks."""

    flagged: bool
    similarity: float = 0.0
    reasons: list[str] = field(default_factory=list)
    #: Members that contributed nothing distinct from the rest.
    redundant_members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "flagged": self.flagged,
            "similarity": round(self.similarity, 3),
            "reasons": list(self.reasons),
            "redundant_members": list(self.redundant_members),
        }


def room_is_colluding(
    contributions: dict[str, str],
    *,
    similarity_threshold: float = 0.82,
    min_members: int = 3,
) -> CollusionSignal:
    """Flag a room whose agreement looks coordinated rather than earned.

    Two independent signals:

    * **near-identical text** - when every member's contribution is almost the
      same string, the "agreement" is one answer repeated, not a consensus;
    * **an empty room** - fewer than ``min_members`` real contributions cannot
      produce a majority worth reporting.

    This reports a *signal*. Unanimity is legitimate when the question is
    factual, so the flag never blocks a verdict on its own - it is surfaced so a
    human can weigh it.
    """
    usable = {name: text.strip() for name, text in contributions.items() if text and text.strip()}
    if len(usable) < min_members:
        return CollusionSignal(
            flagged=True,
            similarity=0.0,
            reasons=[f"only {len(usable)} usable contribution(s); a quorum needs at least {min_members}"],
            redundant_members=sorted(contributions),
        )

    texts = list(usable.values())
    normalised = {re.sub(r"\W+", " ", t).strip().lower() for t in texts}
    if len(normalised) == 1:
        return CollusionSignal(
            flagged=True,
            similarity=1.0,
            reasons=["every member submitted byte-identical text after normalisation"],
            redundant_members=sorted(usable),
        )

    # Token-overlap similarity: cheap, deterministic, no model call.
    token_sets = {name: set(re.sub(r"\W+", " ", text).lower().split()) for name, text in usable.items()}
    names = sorted(token_sets)
    pairs: list[float] = []
    redundant: list[str] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ta, tb = token_sets[a], token_sets[b]
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / len(ta | tb)
            pairs.append(overlap)
            if overlap >= similarity_threshold:
                redundant.extend([a, b])

    mean_overlap = sum(pairs) / len(pairs) if pairs else 0.0
    reasons: list[str] = []
    if mean_overlap >= similarity_threshold:
        reasons.append(f"mean pairwise token overlap {mean_overlap:.2f} >= {similarity_threshold}")
    if redundant:
        reasons.append(f"{len(set(redundant))} member(s) contributed near-duplicate text")

    return CollusionSignal(
        flagged=bool(reasons),
        similarity=mean_overlap,
        reasons=reasons,
        redundant_members=sorted(set(redundant)),
    )
