"""Ranking candidate skills / tools with System One.

Both ``skills/catalog.py`` and ``tools/search/catalog.py`` score candidates with
regex today: a hit on the name beats a hit on the description, and "chart"
matches nothing named "visualise". That works until the words do not overlap.

TypeSafe's own cookbook shape is used here, and it is the one place where
**two requests are genuinely correct**:

1. **Coarse pass** — one choice question over every candidate using its short
   summary. System One returns calibrated probabilities across all options, so
   ranking is free: one call produces a full ordering of up to 255 candidates.
2. **Refine pass** — re-read the top few with their *full* text and re-judge.
   The first pass deliberately sees less text than exists; the second judges
   against better evidence.

Contract: ``None`` means "no signal, keep the regex score". Callers must merge,
never replace — a System One miss on a literal name match would be a
regression, so the wired call sites interleave their own top hit first.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import (
    ChoiceQuestion,
    SystemOneClient,
    get_system_one_client,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "selection"

#: System One choice heads allow at most 255 options.
MAX_OPTIONS = 255

#: Default number of candidates re-judged with full text.
DEFAULT_REFINE = 3


@dataclass
class Candidate:
    """One rankable candidate (a skill, a tool, anything with a name + text)."""

    id: str
    title: str
    summary: str = ""
    full: str = ""

    @property
    def coarse_text(self) -> str:
        return self.summary or self.full or self.title

    @property
    def full_text(self) -> str:
        return self.full or self.summary or self.title


@dataclass
class Ranking:
    """Ordered result of :func:`rank_candidates`."""

    ids: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    refined: bool = False
    jev_used: bool = False

    def top(self, n: int) -> list[str]:
        return self.ids[:n]


def _slug(candidate: Candidate, index: int) -> str:
    """Stable option id. Kept short — it is sent as a criteria key."""
    ident = (candidate.id or "").strip()
    return ident or f"cand_{index}"


def build_coarse_question(candidates: list[Candidate]) -> ChoiceQuestion:
    """One choice over every candidate, judged on its short summary."""
    criteria = {_slug(c, i): f"{c.title}: {c.coarse_text}" for i, c in enumerate(candidates)}
    return ChoiceQuestion(
        "Which single candidate best fits what the request is asking for?",
        criteria,
    )


def build_refine_question(candidates: list[Candidate]) -> ChoiceQuestion:
    """A second choice over the shortlist, judged on full text."""
    criteria = {_slug(c, i): f"{c.title}: {c.full_text}" for i, c in enumerate(candidates)}
    return ChoiceQuestion(
        "Given the full text of each candidate, which one best fits what the request is asking for?",
        criteria,
    )


def build_state(task: str, candidates: list[Candidate]) -> dict[str, Any]:
    return {
        "request": task[:3000],
        "candidates": [{"id": _slug(c, i), "title": c.title} for i, c in enumerate(candidates)],
    }


async def rank_candidates(
    task: str,
    candidates: list[Candidate],
    *,
    top_n: int = 5,
    refine: int = DEFAULT_REFINE,
    tier: str | RiskTier = RiskTier.READ,
    site: str | None = None,
    client: SystemOneClient | None = None,
) -> Ranking | None:
    """Order `candidates` by fit for `task`. None = no signal, keep regex order.

    Never raises. Caps at :data:`MAX_OPTIONS` candidates; beyond that the
    caller's own pre-filter decides who gets in.

    Args:
        site: Override the call-site label recorded in the decision log. Several
            callers share this helper (skill ranking, tool ranking) and they may
            calibrate differently, so each should pass its own label.
    """
    task = (task or "").strip()
    if not task or not candidates:
        return None

    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_selection:
        return None

    pool = list(candidates[: min(MAX_OPTIONS, cfg.max_selection_candidates)])
    if len(pool) < 2:
        return None

    threshold = cli.threshold_for(tier)
    state = build_state(task, pool)
    label = site or SITE

    coarse = build_coarse_question(pool)
    allowed = set(coarse.criteria)
    try:
        result = await cli.evaluate(state, {"pick": coarse}, min_confidence=threshold, site=label)
    except Exception as exc:
        logger.debug("System One coarse ranking failed (%s); falling back.", exc)
        return None
    if result is None:
        return None
    answer = result.get("pick")
    if answer is None or answer.type != "choice" or not answer.validate(allowed):
        logger.debug("System One coarse ranking answer malformed; falling back.")
        return None

    ranking = Ranking(jev_used=True)
    ranking.scores = dict(answer.probabilities)
    ranking.ids = sorted(allowed, key=lambda k: -ranking.scores.get(k, 0.0))

    if refine <= 0 or len(pool) < 2:
        ranking.ids = ranking.ids[:top_n]
        return ranking

    shortlist_ids = ranking.ids[: min(refine, len(pool))]
    by_id = {_slug(c, i): c for i, c in enumerate(pool)}
    shortlist = [by_id[i] for i in shortlist_ids if i in by_id]
    if len(shortlist) < 2:
        ranking.ids = ranking.ids[:top_n]
        return ranking

    refined_q = build_refine_question(shortlist)
    refined_allowed = set(refined_q.criteria)
    try:
        refined_result = await cli.evaluate(state, {"pick": refined_q}, min_confidence=threshold, site=f"{label}:refine")
    except Exception as exc:
        logger.debug("System One refine ranking failed (%s); keeping coarse order.", exc)
        refined_result = None
    if refined_result is not None:
        refined_answer = refined_result.get("pick")
        if refined_answer is not None and refined_answer.type == "choice" and refined_answer.validate(refined_allowed):
            ranking.refined = True
            refined_order = sorted(refined_allowed, key=lambda k: -refined_answer.probabilities.get(k, 0.0))
            # Shortlist first (better evidence), then the untouched remainder.
            ranking.ids = [*refined_order, *(i for i in ranking.ids if i not in refined_order)]
            ranking.scores.update(refined_answer.probabilities)

    ranking.ids = ranking.ids[:top_n]
    return ranking


def rank_candidates_sync(task: str, candidates: list[Candidate], **kwargs: Any) -> Ranking | None:
    """Sync wrapper; falls back rather than blocking a live loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(rank_candidates(task, candidates, **kwargs))
    logger.debug("rank_candidates_sync inside a running loop; falling back.")
    return None


def interleave(primary: list[str], fallback: list[str]) -> list[str]:
    """Merge System One's order with the caller's existing order.

    The existing scorer wins ties: its hits lead, then everything System One
    ranked that the existing list did not already contain. This is what makes
    the System One path an *upgrade* that cannot regress a literal name match.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


__all__ = [
    "DEFAULT_REFINE",
    "MAX_OPTIONS",
    "Candidate",
    "Ranking",
    "build_coarse_question",
    "build_refine_question",
    "build_state",
    "interleave",
    "rank_candidates",
    "rank_candidates_sync",
]
