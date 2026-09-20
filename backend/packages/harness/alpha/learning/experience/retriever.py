"""ExperienceRetriever: Discovers and ranks relevant past experiences for task prompts."""

from __future__ import annotations

import re

from alpha.learning.experience.models import ExperienceRecord
from alpha.learning.experience.store import ExperienceStore


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
                for l in rec.lessons_learned:
                    sections.append(f"  * {l}")
            if rec.pitfalls_to_avoid:
                sections.append("**Pitfalls to Avoid:**")
                for p in rec.pitfalls_to_avoid:
                    sections.append(f"  * {p}")
            sections.append("")

        return "\n".join(sections)

    def _compute_relevance(self, query_tokens: set[str], record: ExperienceRecord) -> float:
        """Compute keyword and semantic overlap score between query and record."""
        target_tokens = self._tokenize(
            f"{record.task_goal} {' '.join(record.tags)} {' '.join(record.error_types)}"
        )
        if not target_tokens:
            return 0.0

        overlap = len(query_tokens.intersection(target_tokens))
        return overlap / (len(query_tokens) + 1.0)

    def _tokenize(self, text: str) -> set[str]:
        words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", text.lower())
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "into", "over", "what", "how", "why"}
        return {w for w in words if w not in stopwords}
