"""Skill catalog — deferred skill discovery at runtime.

Mirrors ``DeferredToolCatalog`` from ``tool_search.py``: an immutable, searchable
catalog that lets the LLM discover skill metadata on demand rather than having
every skill's full description baked into the system prompt.

The agent sees skill names in ``<skill_index>`` but cannot read their metadata
until it calls ``describe_skill``.  This keeps the system prompt compact and
prefix-cache friendly while still giving the model autonomous skill discovery.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from alpha.skills.retrieval import DEFAULT_MIN_SCORE, score_skills
from alpha.skills.types import Skill

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def _compile_catalog_regex(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern`` case-insensitively, falling back to literal match.

    Search queries come from the model, so an invalid regex (e.g. an unbalanced
    paren) must degrade to a literal substring match rather than raise.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


# NOTE: frozen=True without slots=True keeps __dict__, which is what lets the
# @cached_property fields below cache (they write to instance.__dict__, bypassing
# the frozen __setattr__). Do NOT add slots=True or hash/names break at runtime.
@dataclass(frozen=True)
class SkillCatalog:
    """Immutable catalog of skills.  Pure search, no mutation.

    Query forms (mirror ``DeferredToolCatalog.search``):

    - ``"select:data-analysis,deep-research"`` — exact match by name.
    - ``"+podcast gen"`` — require *podcast* in the name, rank by *gen*.
    - ``"chart visualization"`` — regex match on name + description.
    """

    skills: tuple[Skill, ...]

    @cached_property
    def names(self) -> frozenset[str]:
        """All skill names in insertion order."""
        return frozenset(s.name for s in self.skills)

    def search(self, query: str) -> list[Skill]:
        """Match *query* against skill names and descriptions.

        Returns at most ``MAX_RESULTS`` skills, ranked by relevance. The
        ranked modes consume the disclosed lexical score from
        ``alpha.skills.retrieval`` (keyword/tag/tool-requirement matching, no
        embeddings): it breaks ties inside the historic regex tiers and
        surfaces skills the literal pattern missed entirely, so existing
        callers automatically benefit while their documented behavior holds.
        """
        query = query.strip()
        if not query:
            return []

        # ── Exact selection ────────────────────────────────────────────
        if query.startswith("select:"):
            wanted = {n.strip() for n in query[7:].split(",")}
            return [s for s in self.skills if s.name in wanted]

        # ── Required-prefix search ─────────────────────────────────────
        if query.startswith("+"):
            parts = query[1:].split(None, 1)
            if not parts:
                return []  # bare "+" with no required token
            required = parts[0].lower()
            candidates = [(index, s) for index, s in enumerate(self.skills) if required in s.name.lower()]
            if len(parts) > 1:
                pattern = _compile_catalog_regex(parts[1])
                # Regex hit-count still ranks first (historic behavior); the
                # disclosed retrieval score only breaks ties, and the explicit
                # index keeps equal-score ordering identical to catalog order.
                retrieval_scores = _retrieval_scores(parts[1], self.skills)
                candidates.sort(
                    key=lambda item: (-_catalog_regex_score(pattern, item[1]), -retrieval_scores.get(item[1].name, 0.0), item[0]),
                )
            return [s for _, s in candidates[:MAX_RESULTS]]

        # ── Free-text regex search ─────────────────────────────────────
        regex = _compile_catalog_regex(query)
        retrieval_scores = _retrieval_scores(query, self.skills)
        scored: list[tuple[int, float, int, Skill]] = []
        for index, s in enumerate(self.skills):
            searchable = f"{s.name} {s.description or ''}"
            retrieval_score = retrieval_scores.get(s.name, 0.0)
            if regex.search(searchable):
                # Name match scores higher than description-only match; the
                # lexical retrieval score breaks ties inside each regex tier.
                scored.append((2 if regex.search(s.name) else 1, retrieval_score, index, s))
            elif retrieval_score >= DEFAULT_MIN_SCORE:
                # Retrieval-only candidate: the literal pattern missed this
                # skill but the disclosed lexical scorer found enough overlap
                # to pass the same min-score cutoff retrieval.py uses.
                scored.append((0, retrieval_score, index, s))
        # Regex tier first (-item[0]), then retrieval score, then catalog
        # order: a superset of the old ranking, still capped at MAX_RESULTS.
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [s for _, _, _, s in scored][:MAX_RESULTS]

    async def asearch_smart(self, query: str, *, limit: int = MAX_RESULTS) -> list[Skill]:
        """Deterministic search, then System One re-ranks what the regex missed.

        The regex hits always lead, so a literal name match can never be lost —
        System One only appends candidates the regex scorer ranked below the cut
        (or missed entirely, e.g. "chart" vs "visualise").

        ``select:`` is returned untouched and **uncapped**: it names skills
        explicitly, so truncating would silently drop ones the model asked for.
        """
        stripped = (query or "").strip()
        if stripped.startswith("select:"):
            return self.search(stripped)
        base = self.search(stripped)[:limit]
        ranked = await arank_skills(stripped, list(self.skills), limit=limit)
        return merge_skill_results(base, ranked)[:limit]

    def search_smart(self, query: str, *, limit: int = MAX_RESULTS) -> list[Skill]:
        """Sync wrapper; returns the plain search when inside a running loop."""
        stripped = (query or "").strip()
        if stripped.startswith("select:"):
            return self.search(stripped)
        return search_skills_smart(stripped, list(self.skills), limit=limit)


def _catalog_regex_score(pattern: re.Pattern[str], s: Skill) -> int:
    """Count regex hits across name + description for ranking."""
    return len(pattern.findall(f"{s.name} {s.description or ''}"))


def _retrieval_scores(query: str, skills: tuple[Skill, ...]) -> dict[str, float]:
    """Lexical retrieval scores from ``alpha.skills.retrieval`` keyed by name.

    Skills with no lexical overlap are absent (treated as 0.0). The scoring
    method is disclosed in that module; this helper only consumes it.
    """
    return {match.skill.name: match.match_score for match in score_skills(query, skills)}


# --------------------------------------------------------------------------
# System One ranking layer
#
# The regex search above is the fallback and stays the fallback. When System
# One is available it re-ranks the whole catalog in one request and merges its
# order *behind* the regex hits, so a literal name match can never be lost.
# --------------------------------------------------------------------------

#: How many skills go into one ranking request.
RANK_POOL = 60


def _rank_candidates() -> list[Any]:
    """Imported lazily so importing the catalog never pulls in the HTTP client."""
    from alpha.tools.selection import Candidate

    return Candidate  # type: ignore[return-value]


def build_skill_candidates(skills: list[Skill]) -> list[Any]:
    """Adapt skills into rankable candidates."""
    candidate_cls = _rank_candidates()
    return [
        candidate_cls(
            id=s.name,
            title=s.name,
            summary=(s.description or "")[:280],
            full=(s.description or "")[:1200],
        )
        for s in skills[:RANK_POOL]
    ]


async def arank_skills(query: str, skills: list[Skill], *, limit: int = MAX_RESULTS) -> list[Skill] | None:
    """Re-rank `skills` for `query` using System One. None = no signal."""
    if not query.strip() or len(skills) < 2:
        return None
    try:
        from alpha.tools.selection import rank_candidates
    except Exception:
        return None
    try:
        ranking = await rank_candidates(query, build_skill_candidates(skills), top_n=limit, site="skill_select")
    except Exception:
        logger.debug("System One skill ranking unavailable.", exc_info=True)
        return None
    if ranking is None:
        return None
    by_name = {s.name: s for s in skills}
    return [by_name[name] for name in ranking.ids if name in by_name]


def merge_skill_results(primary: list[Skill], ranked: list[Skill] | None) -> list[Skill]:
    """Regex results first, then anything System One ranked that they missed."""
    if not ranked:
        return primary
    seen = {s.name for s in primary}
    return [*primary, *(s for s in ranked if s.name not in seen)]


async def asearch_skills_smart(query: str, skills: list[Skill], *, limit: int = MAX_RESULTS) -> list[Skill]:
    """Semantic ranking on top of the deterministic catalog search."""
    catalog = SkillCatalog(tuple(skills))
    base = catalog.search(query)[:limit]
    ranked = await arank_skills(query, skills, limit=limit)
    return merge_skill_results(base, ranked)[:limit]


def search_skills_smart(query: str, skills: list[Skill], *, limit: int = MAX_RESULTS) -> list[Skill]:
    """Sync wrapper; returns the plain catalog search when inside a live loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asearch_skills_smart(query, skills, limit=limit))
    logger.debug("search_skills_smart inside a running loop; using catalog search.")
    return SkillCatalog(tuple(skills)).search(query)[:limit]
