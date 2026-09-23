"""Structured-output parsers for deliberation prompts.

The deliberation strategies prompt their models for a small, fixed trailing
block (claims/self-confidence, rubric scores, judge ruling, evidence lines).
These parsers extract those blocks; a parse failure returns ``None``/``[]``
so the caller can retry or skip honestly — the old code never asked a model
anything and simply hardcoded all of these values.
"""

from __future__ import annotations

import re

_RUBRIC_KEYS = ("CORRECTNESS", "EVIDENCE", "REASONING", "COMPLETENESS", "CLARITY")


def parse_claims_and_confidence(text: str) -> tuple[list[str], float | None]:
    """Extract ``STATED CLAIMS: a | b`` and ``SELF CONFIDENCE: 0.xx`` lines.

    Returns ``(claims, confidence)`` where either may be empty/``None`` when
    the model did not follow the requested trailing format.
    """
    claims: list[str] = []
    confidence: float | None = None

    claims_match = re.search(r"^STATED CLAIMS:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE)
    if claims_match:
        claims = [c.strip() for c in claims_match.group(1).split("|") if c.strip()]

    conf_match = re.search(
        r"^SELF CONFIDENCE:\s*([0-9]*\.?[0-9]+)", text or "", re.MULTILINE | re.IGNORECASE
    )
    if conf_match:
        try:
            confidence = min(1.0, max(0.0, float(conf_match.group(1))))
        except ValueError:
            confidence = None
    return claims, confidence


def parse_rubric(text: str) -> dict | None:
    """Extract the five rubric scores plus critique/flaws text.

    Returns ``{"scores": {name: 0..1}, "critique": str, "flaws": [str, ...]}``
    or ``None`` when any numeric score is missing/unparseable — callers then
    retry once and skip the review honestly rather than inventing numbers.
    """
    scores: dict[str, float] = {}
    for key in _RUBRIC_KEYS:
        match = re.search(rf"^{key}:\s*([0-9]*\.?[0-9]+)", text or "", re.MULTILINE | re.IGNORECASE)
        if not match:
            return None
        try:
            scores[key.lower()] = min(1.0, max(0.0, float(match.group(1))))
        except ValueError:
            return None

    critique_match = re.search(r"^CRITIQUE:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE)
    flaws_match = re.search(r"^FLAWS:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE)
    flaws_text = flaws_match.group(1).strip() if flaws_match else ""
    return {
        "scores": scores,
        "critique": critique_match.group(1).strip() if critique_match else "",
        "flaws": [f.strip() for f in flaws_text.split("|") if f.strip()],
    }


def parse_judge_verdict(text: str) -> tuple[str | None, str]:
    """Extract ``WINNER: PROPONENT|CRITIC`` and ``RATIONALE: ...``.

    Returns ``(winner, rationale)``; ``winner`` is ``None`` when the judge
    output could not be parsed — callers must then report *no ruling*
    instead of inventing one (the old code hardcoded "Proponent" wins).
    """
    winner_match = re.search(
        r"^WINNER:\s*(PROPONENT|CRITIC)\b", text or "", re.MULTILINE | re.IGNORECASE
    )
    rationale_match = re.search(r"^RATIONALE:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE
    )
    winner = winner_match.group(1).strip().lower() if winner_match else None
    rationale = rationale_match.group(1).strip() if rationale_match else ""
    return winner, rationale


def parse_evidence(text: str) -> list[str]:
    """Extract every ``EVIDENCE: item | item`` line's items (possibly empty)."""
    items: list[str] = []
    for match in re.finditer(r"^EVIDENCE:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE):
        items.extend(part.strip() for part in match.group(1).split("|") if part.strip())
    return items
