from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeEntry:
    """Entry in the domain knowledge base K."""
    category: str  # "spec", "reference", "pattern", "anti_pattern"
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    impact_description: str = ""
    created_at: float = field(default_factory=time.time)

    def matches_query(self, query: str) -> bool:
        q_lower = query.lower()
        if q_lower in self.title.lower() or q_lower in self.content.lower():
            return True
        return any(q_lower in t.lower() for t in self.tags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "impact_description": self.impact_description,
            "created_at": self.created_at,
        }


class DomainKnowledgeBase:
    """
    Domain Knowledge Base K for Autonomous Agentic Variation Operators (AVO).
    Stores architectural specs, hardware constraints, reference implementations,
    and accumulated positive/negative design patterns discovered across evolution runs.
    """

    def __init__(self) -> None:
        self.entries: list[KnowledgeEntry] = []
        self._seed_default_knowledge()

    def _seed_default_knowledge(self) -> None:
        """Seeds foundational hardware/algorithmic knowledge for AVO optimization."""
        # 1. Branchless accumulator rescaling (v20 optimization in AVO paper)
        self.add_entry(
            category="pattern",
            title="Branchless Accumulator Rescaling",
            content=(
                "Replace conditional branch checking of row-maximum updates with predicated select "
                "substituting 1.0 when rescaling is unnecessary. Eliminates warp synchronization and divergence, "
                "allowing non-blocking memory fences."
            ),
            tags=["branchless", "accumulator", "warp-divergence", "fences", "attention"],
            impact_description="+8.1% non-causal throughput on Blackwell B200",
        )
        # 2. Pipeline overlap (v30 optimization in AVO paper)
        self.add_entry(
            category="pattern",
            title="Correction and MMA Pipeline Overlap",
            content=(
                "Overlap tensor core matrix multiplication (MMA) with accumulator correction stages "
                "by staging asynchronous GMEM/SMEM loads and scheduling warp groups cooperatively."
            ),
            tags=["pipeline-overlap", "mma", "async-copy", "warp-specialization"],
            impact_description="+1.1% causal throughput overlap",
        )
        # 3. Register rebalancing (v33 optimization in AVO paper)
        self.add_entry(
            category="pattern",
            title="Register Rebalancing Across Warp Groups",
            content=(
                "Rebalance register budget across specialized producer and consumer warp groups "
                "to prevent spill while maximizing occupancy on Blackwell architecture."
            ),
            tags=["registers", "occupancy", "warp-groups", "spill"],
            impact_description="+2.1% throughput gain",
        )
        # 4. Anti-pattern: Warp divergence in inner loop
        self.add_entry(
            category="anti_pattern",
            title="Divergent Branching in Core Computation Loop",
            content=(
                "Avoid divergent if-else branches inside inner tile iteration loops. "
                "Serializes thread execution within the warp and forces heavyweight memory barriers."
            ),
            tags=["warp-divergence", "anti-pattern", "inner-loop", "branch"],
            impact_description="Severe degradation of SM instruction issue rate",
        )

    def add_entry(
        self,
        category: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        impact_description: str = "",
    ) -> KnowledgeEntry:
        entry = KnowledgeEntry(
            category=category,
            title=title,
            content=content,
            tags=tags or [],
            impact_description=impact_description,
        )
        self.entries.append(entry)
        return entry

    def record_negative_lesson(
        self,
        attempt_hypothesis: str,
        failure_reason: str,
        tags: list[str] | None = None,
    ) -> KnowledgeEntry:
        """Records an unsuccessful exploration attempt into anti-pattern memory."""
        return self.add_entry(
            category="anti_pattern",
            title=f"Failed Variation: {attempt_hypothesis[:60]}",
            content=f"Hypothesis '{attempt_hypothesis}' failed with diagnostic: {failure_reason}",
            tags=tags or ["failure", "rejection"],
            impact_description="Candidate rejected by correctness or performance gate",
        )

    def record_positive_pattern(
        self,
        hypothesis: str,
        modification_summary: str,
        measured_gain: str,
        tags: list[str] | None = None,
    ) -> KnowledgeEntry:
        """Records a committed breakthrough into the pattern library."""
        return self.add_entry(
            category="pattern",
            title=f"Breakthrough: {hypothesis[:60]}",
            content=f"Optimization '{modification_summary}' successfully improved performance.",
            tags=tags or ["breakthrough", "success"],
            impact_description=measured_gain,
        )

    def query(self, text: str, max_results: int = 5) -> list[KnowledgeEntry]:
        """Queries knowledge base using tokens/substrings."""
        tokens = [t.strip().lower() for t in text.replace(",", " ").replace(";", " ").split() if len(t) > 2]
        scored: list[tuple[int, KnowledgeEntry]] = []

        for entry in self.entries:
            score = 0
            for tok in tokens:
                if tok in entry.title.lower():
                    score += 3
                if tok in entry.content.lower():
                    score += 2
                if any(tok in tag.lower() for tag in entry.tags):
                    score += 4
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:max_results]]

    def get_all(self, category: str | None = None) -> list[KnowledgeEntry]:
        if category:
            return [e for e in self.entries if e.category == category]
        return list(self.entries)

    def stats(self) -> dict[str, Any]:
        return {
            "total_entries": len(self.entries),
            "patterns": len([e for e in self.entries if e.category == "pattern"]),
            "anti_patterns": len([e for e in self.entries if e.category == "anti_pattern"]),
            "specs": len([e for e in self.entries if e.category == "spec"]),
        }
