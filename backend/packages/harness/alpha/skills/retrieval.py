"""Scored skill retrieval over the installed skill catalog.

Motivation (WorkSwarm-style gap): given a task-shaped query and the skills
that are actually installed, return a *small, scored candidate list with cited
evidence* instead of injecting the whole catalog into a prompt.

Honesty contract -- read before adding a scoring signal:

- Every score is produced by the disclosed lexical heuristic below. Every
  candidate carries ``score_method`` ("lexical-heuristic") plus ``reasons``
  citing the actual matched evidence, and the result payload carries a
  ``note`` stating that scores are heuristic, not measured relevance.
- This module is LEXICAL ONLY. No embeddings are computed and no network
  embedding API is called. The repo has no offline embedding infrastructure
  usable for skill text today: ``alpha.memory.cognitive.retrieval``'s TF-IDF
  vectors are scoped to that engine's own fact store, and System One selection
  (``alpha.tools.selection``) is a network model client, not an offline
  embedder. If a real offline embedder lands later, wire it in here as an
  optional, config-gated path and disclose it in ``score_method``.
- Retrieval is always capped by ``top_k`` and a ``min_score`` cutoff. It never
  returns "everything"; when the catalog or the cutoff yields nothing, the
  empty list is returned with an honest note instead of a fabricated result.

Scoring heuristic (deterministic, order-stable):

1. Tokenize the query: lowercase, split on non-alphanumerics, keep terms of
   length >= 3, drop a small English function-word list.
2. Per term take the best-matching component weight (``WEIGHTS``): name token
   > required tool > name substring > tag > description token >
   description substring.
3. ``match_score`` = mean of the per-term best weights -- i.e. how much of the
   query the skill covers lexically. It is NOT a relevance judgment, success
   rate, or measured outcome.
4. Rank by ``(-match_score, name)`` so ordering is fully deterministic.

Tags: ``Skill`` frontmatter has no tag field, so the tag component matches
``SkillCategory`` (public/custom/integrations/legacy) plus any caller-supplied
``tags_by_name`` mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from alpha.skills.types import Skill

#: Disclosed scoring method stamped on every result.
SCORE_METHOD = "lexical-heuristic"

#: Default maximum number of candidates returned.
DEFAULT_TOP_K = 5

#: Default minimum ``match_score`` a candidate must reach to be returned.
DEFAULT_MIN_SCORE = 0.2

#: Minimum query-term length (short terms like "ai" match too much noise).
MIN_TERM_LENGTH = 3

#: Component weights for one query term (deterministic; exposed for disclosure).
WEIGHTS: dict[str, float] = {
    "name_token": 0.6,
    "tool_requirement": 0.5,
    "name_substring": 0.45,
    "tag": 0.35,
    "description_token": 0.3,
    "description_substring": 0.25,
}

#: Small English function-word list dropped from queries before scoring.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "are",
        "was",
        "were",
        "you",
        "your",
        "its",
        "into",
        "over",
        "via",
        "not",
        "but",
        "any",
        "all",
    }
)

#: Base disclosure note attached to every retrieval result.
NOTE: str = (
    "Lexical heuristic only: match_score is term/tag/tool overlap over skill names, descriptions, "
    "allowed-tools, and category/tags (weights disclosed in `weights`). No embeddings were computed and "
    "no network call was made; scores are a heuristic ranking, not measured relevance."
)

_TOKEN_SPLIT = re.compile(r"[^a-zA-Z0-9]+")


def query_terms(query: str) -> list[str]:
    """Tokenize *query* into deduplicated lowercase terms (first-seen order).

    Terms shorter than ``MIN_TERM_LENGTH`` or in ``STOPWORDS`` are dropped so
    stopwords cannot manufacture matches (e.g. "the" inside "gather").
    """
    terms: list[str] = []
    for raw in _TOKEN_SPLIT.split((query or "").lower()):
        if len(raw) < MIN_TERM_LENGTH or raw in STOPWORDS:
            continue
        if raw not in terms:
            terms.append(raw)
    return terms


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def _skill_tags(skill: Skill, tags_by_name: dict[str, tuple[str, ...]] | None) -> list[str]:
    """Tags for *skill*: its category plus caller-supplied tags (both lowercase)."""
    tags: list[str] = []
    category = getattr(skill, "category", None)
    if category is not None:
        tags.append(str(getattr(category, "value", category)).lower())
    if tags_by_name:
        for tag in tags_by_name.get(getattr(skill, "name", ""), ()) or ():
            value = str(tag).lower()
            if value and value not in tags:
                tags.append(value)
    return tags


def _score_one(skill: Skill, terms: list[str], tags_by_name: dict[str, tuple[str, ...]] | None) -> SkillMatch | None:
    """Score one skill against *terms*; None when nothing matched at all."""
    name = skill.name or ""
    name_lower = name.lower()
    name_tokens = _tokens(name_lower)
    description = skill.description or ""
    desc_lower = description.lower()
    desc_tokens = _tokens(desc_lower)
    tools = [str(tool).lower() for tool in (getattr(skill, "allowed_tools", None) or ())]
    tags = _skill_tags(skill, tags_by_name)

    reasons: list[str] = []
    total = 0.0
    for term in terms:
        best = 0.0
        if term in name_tokens:
            best = max(best, WEIGHTS["name_token"])
            reasons.append(f"matched term '{term}' in name '{name}'")
        hit_tool = next((tool for tool in tools if term in tool), None)
        if hit_tool is not None:
            best = max(best, WEIGHTS["tool_requirement"])
            reasons.append(f"matched term '{term}' against required tool '{hit_tool}'")
        if term not in name_tokens and term in name_lower:
            best = max(best, WEIGHTS["name_substring"])
            reasons.append(f"matched term '{term}' inside name '{name}'")
        hit_tag = next((tag for tag in tags if term in tag), None)
        if hit_tag is not None:
            best = max(best, WEIGHTS["tag"])
            reasons.append(f"matched tag '{hit_tag}'")
        if term in desc_tokens:
            best = max(best, WEIGHTS["description_token"])
            reasons.append(f"matched term '{term}' in the description of '{name}'")
        elif term in desc_lower:
            best = max(best, WEIGHTS["description_substring"])
            reasons.append(f"matched term '{term}' inside the description of '{name}'")
        total += best

    if total <= 0.0 or not reasons:
        return None
    match_score = total / len(terms)
    return SkillMatch(skill=skill, match_score=match_score, reasons=tuple(reasons))


@dataclass(frozen=True)
class SkillMatch:
    """One scored skill candidate with its disclosed evidence."""

    skill: Skill
    match_score: float
    reasons: tuple[str, ...]
    score_method: str = SCORE_METHOD

    def to_dict(self) -> dict[str, Any]:
        category = getattr(self.skill, "category", None)
        return {
            "name": self.skill.name,
            "description": self.skill.description or "",
            "category": str(getattr(category, "value", category)) if category is not None else None,
            "match_score": round(self.match_score, 4),
            "reasons": list(self.reasons),
            "score_method": self.score_method,
        }


@dataclass(frozen=True)
class RetrievalResult:
    """A capped, cutoff-filtered retrieval response with its disclosure note."""

    query: str
    matches: tuple[SkillMatch, ...]
    top_k: int
    min_score: float
    catalog_size: int
    score_method: str = SCORE_METHOD
    note: str = NOTE

    @property
    def empty(self) -> bool:
        return not self.matches

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "catalog_size": self.catalog_size,
            "score_method": self.score_method,
            "note": self.note,
            "weights": dict(WEIGHTS),
            "results": [match.to_dict() for match in self.matches],
        }


def score_skills(
    query: str,
    skills: Any,
    *,
    tags_by_name: dict[str, tuple[str, ...]] | None = None,
) -> list[SkillMatch]:
    """Score every skill with positive lexical overlap against *query*.

    Returns matches sorted by ``(-match_score, skill.name)``. This is the raw
    scorer; use :func:`retrieve_skills` for the capped, cutoff-filtered API.
    """
    terms = query_terms(query)
    if not terms:
        return []
    scored: list[SkillMatch] = []
    for skill in skills:
        match = _score_one(skill, terms, tags_by_name)
        if match is not None:
            scored.append(match)
    scored.sort(key=lambda item: (-item.match_score, item.skill.name))
    return scored


def retrieve_skills(
    query: str,
    skills: Any,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    tags_by_name: dict[str, tuple[str, ...]] | None = None,
) -> RetrievalResult:
    """Scored retrieval over *skills* with a ``top_k`` cap and ``min_score`` cutoff.

    Raises ``ValueError`` on nonsensical cutoffs rather than silently clamping
    them into a result that does not mean what the caller asked for.
    """
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    if not 0.0 <= min_score <= 1.0:
        raise ValueError("min_score must be within [0, 1]")
    catalog = tuple(skills)
    matches = tuple(match for match in score_skills(query, catalog, tags_by_name=tags_by_name) if match.match_score >= min_score)[:top_k]

    suffixes: list[str] = []
    if not catalog:
        suffixes.append("The installed skill catalog is empty, so the result list is honestly empty.")
    elif not (query or "").strip():
        suffixes.append("The query is empty; nothing can be scored.")
    elif not matches:
        suffixes.append(f"No candidate reached min_score={min_score}; the empty result list is a real cutoff outcome, not an error.")
    note = NOTE + (" " + " ".join(suffixes) if suffixes else "")

    return RetrievalResult(
        query=query or "",
        matches=matches,
        top_k=top_k,
        min_score=min_score,
        catalog_size=len(catalog),
        note=note,
    )
