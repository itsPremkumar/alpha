"""ExperienceRetriever: Discovers and ranks relevant past experiences for task prompts."""

from __future__ import annotations

import re
import time
from typing import Any

from alpha.learning.experience.models import ExperienceKind, ExperienceRecord
from alpha.learning.experience.store import ExperienceStore

# Disclosed score method for `relevant_for`: pure lexical token overlap. No
# semantic model, no embedding, no verification is implied by these scores.
SCORE_METHOD = "lexical_overlap_v1"


class ExperienceRetriever:
    """Semantic and lexical retriever ranking past experiences against active goals."""

    def __init__(self, store: ExperienceStore | None = None):
        self.store = store or ExperienceStore()

    def retrieve(
        self,
        query: str,
        limit: int = 3,
        min_score: float = 0.1,
    ) -> list[ExperienceRecord]:
        """Retrieve top matching experience records ordered by relevance score."""
        tokens = self._tokenize(query)
        if not tokens:
            return []

        scored_records: list[tuple[float, ExperienceRecord]] = []

        for rec in self.store.list_all():
            score = self._compute_relevance(tokens, rec)
            if score >= min_score:
                scored_records.append((score, rec))

        scored_records.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored_records[:limit]]

    def relevant_for(
        self,
        query: str,
        top_k: int = 5,
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """FACT/TIP items relevant to ``query`` with disclosed match reasons.

        Designed for prompt-injection sites: returns plain dicts (not records)
        that each carry ``score_method`` plus a human-readable ``reason`` saying
        exactly how the match was produced — a disclosed lexical token-overlap
        heuristic (``|query ∩ item| / (|query| + 1)``), not a semantic model.
        Defaults to FACT and TIP kinds; expired records are skipped. The
        existing ``retrieve()`` / ``render_lessons_prompt()`` API is unchanged.
        """
        tokens = self._tokenize(query)
        if not tokens:
            return []

        if kinds is None:
            wanted = {ExperienceKind.FACT, ExperienceKind.TIP}
        else:
            wanted = {ExperienceKind(str(k).strip().upper()) for k in kinds}

        now = time.time()
        scored: list[tuple[float, dict[str, Any]]] = []
        for rec in self.store.list_all():
            if rec.kind not in wanted:
                continue
            if rec.is_expired(now):
                continue
            statement = rec.statement or rec.task_goal
            target_tokens = self._tokenize(f"{statement} {' '.join(rec.tags)}")
            shared = sorted(tokens & target_tokens)
            if not shared:
                continue
            score = len(shared) / (len(tokens) + 1.0)
            scored.append(
                (
                    score,
                    {
                        "experience_id": rec.experience_id,
                        "kind": rec.kind.value,
                        "statement": statement,
                        "score": round(score, 4),
                        "score_method": SCORE_METHOD,
                        "reason": (f"lexical token overlap: matched {shared} (heuristic score = shared/({len(tokens)} query tokens + 1) = {len(shared)}/{len(tokens) + 1}); no semantic model used"),
                        "confidence": rec.confidence,
                        "evidence": list(rec.evidence),
                        "tags": list(rec.tags),
                    },
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def render_lessons_prompt(self, query: str, limit: int = 3) -> str:
        """Render a formatted markdown section containing actionable past lessons."""
        matches = self.retrieve(query, limit=limit)
        if not matches:
            return ""

        sections = ["## Relevant Past Lessons & Pitfalls (From Episodic Memory)\n"]
        for rec in matches:
            sections.append(f"### Context: {rec.task_goal} ({rec.outcome.value.upper()})")
            if rec.lessons_learned:
                sections.append("**Lessons Learned:**")
                for lesson in rec.lessons_learned:
                    sections.append(f"  * {lesson}")
            if rec.pitfalls_to_avoid:
                sections.append("**Pitfalls to Avoid:**")
                for p in rec.pitfalls_to_avoid:
                    sections.append(f"  * {p}")
            sections.append("")

        return "\n".join(sections)

    def _compute_relevance(self, query_tokens: set[str], record: ExperienceRecord) -> float:
        """Compute keyword and semantic overlap score between query and record."""
        target_tokens = self._tokenize(f"{record.task_goal} {' '.join(record.tags)} {' '.join(record.error_types)}")
        if not target_tokens:
            return 0.0

        overlap = len(query_tokens.intersection(target_tokens))
        return overlap / (len(query_tokens) + 1.0)

    def _tokenize(self, text: str) -> set[str]:
        words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", text.lower())
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "into", "over", "what", "how", "why"}
        return {w for w in words if w not in stopwords}
