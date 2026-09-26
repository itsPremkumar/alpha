"""The auxiliary judge's verdict: a closed grammar, parsed without charity.

Every safety property of the goal loop bottoms out here, so the rules are
deliberately unforgiving:

1. **A verdict that does not parse is not ``done``.**  ``parse_verdict``
   returns ``None`` for anything that is not exactly one JSON object with a
   ``verdict`` key drawn from :data:`GoalVerdict`.  The caller
   (:mod:`alpha.mission.goalloop.judge`) turns ``None`` into ``continue``,
   because an unreadable judge is a broken judge and a broken judge must
   neither fake completion nor wedge the loop.
2. **The legacy ``{"done": <bool>}`` shape is NOT accepted**, even though
   upstream Hermes still tolerates it.  Accepting it would mean a response
   that never mentioned the word ``done`` could still be read as ``done``,
   which is exactly the "looks done" failure this loop exists to stop.
3. **An unknown verdict string is not ``continue`` either** - it is a parse
   failure.  Guessing the nearest verdict would let a truncated or
   hallucinated label drive the loop.
4. ``reason`` is required to be a string when present and is truncated to a
   bounded length.  A missing reason is tolerated (the loop does not depend on
   it) but a non-string reason is a parse failure, because a judge that
   returned ``{"reason": {"nested": true}}`` is not speaking the protocol.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class GoalVerdict(StrEnum):
    """The four verdicts the judge may return. Nothing else is legal."""

    DONE = "done"
    BLOCKED = "blocked"
    CONTINUE = "continue"
    WAIT = "wait"


#: Longest reason we will keep. A judge that writes an essay is not more
#: authoritative than one that writes a sentence, and an unbounded reason would
#: land in the continuation prompt where it costs cache and context.
MAX_REASON_CHARS: Final[int] = 400

#: Reason recorded when the judge's own output could not be read. Kept as a
#: stable constant so tests and operators can match on it.
JUDGE_UNREADABLE_REASON: Final[str] = "judge verdict was unreadable; continuing conservatively"

#: Reason recorded when the judge call itself failed (network, timeout, no aux
#: model configured). Deliberately the same fail-open direction as
#: :data:`JUDGE_UNREADABLE_REASON` and for the same reason.
JUDGE_UNAVAILABLE_REASON: Final[str] = "judge was unavailable; continuing conservatively"


@dataclass(frozen=True)
class ParsedVerdict:
    """A verdict that parsed cleanly."""

    verdict: GoalVerdict
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict.value, "reason": self.reason}


def _coerce_reason(raw: Any) -> str | None:
    """Return a bounded reason string, or ``None`` if ``raw`` is not a string."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return None
    collapsed = " ".join(raw.split())
    return collapsed[:MAX_REASON_CHARS]


def parse_verdict(raw: str | bytes | None) -> ParsedVerdict | None:
    """Parse the judge's one-line JSON, or return ``None``.

    ``None`` means "the judge did not speak the protocol". It never means
    ``done`` and it never means ``continue`` on its own - the caller decides,
    and the only caller decides ``continue`` (see :mod:`judge`).

    Accepted input is exactly one JSON object whose ``verdict`` is one of the
    four :class:`GoalVerdict` values. Leading/trailing whitespace, and a
    fenced ```json block around the object, are tolerated because both are
    ordinary model output; a *sentence* around the object is not.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    text = raw.strip()
    if not text:
        return None

    # A model that wraps its JSON in a fence is still speaking the protocol -
    # but only if the fence is CLOSED. An unterminated fence means the response
    # was truncated, and a truncated response is not evidence of anything.
    if text.startswith("```"):
        newline = text.find("\n")
        if newline == -1:
            return None
        text = text[newline + 1 :]
        fence = text.rfind("```")
        if fence == -1:
            return None
        text = text[:fence].strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    raw_verdict = parsed.get("verdict")
    if not isinstance(raw_verdict, str):
        # No ``verdict`` key at all: this is the legacy shape, and it is
        # refused on purpose (see the module docstring).
        return None
    try:
        verdict = GoalVerdict(raw_verdict.strip().lower())
    except ValueError:
        return None

    reason = _coerce_reason(parsed.get("reason"))
    if reason is None:
        return None

    return ParsedVerdict(verdict=verdict, reason=reason)


__all__ = [
    "JUDGE_UNAVAILABLE_REASON",
    "JUDGE_UNREADABLE_REASON",
    "MAX_REASON_CHARS",
    "GoalVerdict",
    "ParsedVerdict",
    "parse_verdict",
]
