"""Active Memory Two-Tier Escalation Engine inspired by OpenClaw."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from alpha.memory.dreaming.store import get_dream_store


@dataclass
class MemoryLookupResult:
    """Outcome of active memory retrieval."""

    query: str
    tier: int  # 1 (deterministic) or 2 (escalation)
    matches: list[str] = field(default_factory=list)
    confidence: float = 0.0
    escalated: bool = False
    reason: str = ""


class ActiveMemoryRouter:
    """Two-tier memory retrieval router combining instant local lookup with deep escalation."""

    def __init__(
        self,
        memory_files: list[Path] | None = None,
        escalation_threshold: float = 0.60,
    ):
        self.memory_files = memory_files or []
        self.escalation_threshold = escalation_threshold

    def _collect_memory_lines(self) -> list[str]:
        lines: list[str] = []
        # If no explicit files passed, pull from DreamStore
        if not self.memory_files:
            store = get_dream_store()
            mem_text = store.read_memory()
            lines.extend(line.strip() for line in mem_text.splitlines() if line.strip())
        else:
            for p in self.memory_files:
                if p.exists():
                    text = p.read_text(encoding="utf-8", errors="replace")
                    lines.extend(line.strip() for line in text.splitlines() if line.strip())
        return lines

    def query(
        self,
        query_text: str,
        escalation_handler: Callable[[str, list[str]], str] | None = None,
    ) -> MemoryLookupResult:
        """Route query through Tier 1 deterministic lookup, escalating to Tier 2 if needed."""
        query_text = query_text.strip()
        lines = self._collect_memory_lines()

        if not lines:
            if escalation_handler:
                ans = escalation_handler(query_text, [])
                return MemoryLookupResult(
                    query=query_text,
                    tier=2,
                    matches=[ans],
                    confidence=0.85,
                    escalated=True,
                    reason="No local memory files found; escalated to Tier 2.",
                )
            return MemoryLookupResult(
                query=query_text,
                tier=1,
                matches=[],
                confidence=0.0,
                escalated=False,
                reason="Empty memory store.",
            )

        # Tier 1: Deterministic Keyword / Substring Scoring
        tokens = [t.lower() for t in re.findall(r"\w+", query_text) if len(t) > 2]
        scored_lines: list[tuple[float, str]] = []

        for line in lines:
            if line.startswith("#"):
                continue  # Skip headers
            line_lower = line.lower()
            if not tokens:
                continue

            matches_count = sum(1 for token in tokens if token in line_lower)
            score = matches_count / len(tokens)
            if score > 0:
                scored_lines.append((score, line))

        scored_lines.sort(key=lambda x: x[0], reverse=True)

        top_score = scored_lines[0][0] if scored_lines else 0.0
        top_matches = [line for score, line in scored_lines[:3]]

        # Check if Tier 1 confidence satisfies threshold
        if top_score >= self.escalation_threshold and top_matches:
            return MemoryLookupResult(
                query=query_text,
                tier=1,
                matches=top_matches,
                confidence=top_score,
                escalated=False,
                reason=f"Resolved deterministically via Tier 1 with confidence {top_score:.2f}.",
            )

        # Tier 2: Escalation Check
        if escalation_handler is not None:
            escalation_output = escalation_handler(query_text, lines)
            return MemoryLookupResult(
                query=query_text,
                tier=2,
                matches=[escalation_output],
                confidence=0.90,
                escalated=True,
                reason=f"Tier 1 confidence ({top_score:.2f}) below threshold ({self.escalation_threshold}); escalated to Tier 2 deep reasoning.",
            )

        # Fallback to whatever Tier 1 found if no escalation handler
        return MemoryLookupResult(
            query=query_text,
            tier=1,
            matches=top_matches,
            confidence=top_score,
            escalated=False,
            reason="Tier 1 confidence low, but no Tier 2 escalation handler registered.",
        )


_global_memory_router = ActiveMemoryRouter()


def get_active_memory_router() -> ActiveMemoryRouter:
    return _global_memory_router
