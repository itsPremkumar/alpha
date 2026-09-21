"""Active Memory Two-Tier Escalation Engine inspired by OpenClaw."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from alpha.memory.dreaming.store import get_dream_store

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "memory_gate"


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

    def _system_one_escalation_gate(
        self,
        query_text: str,
        lines: list[str],
        top_matches: list[str],
        top_score: float,
    ) -> bool | None:
        """Decide whether Tier-2 escalation is worth its cost.

        Returns True (escalate), False (Tier 1 answer is enough), or None
        (no confident opinion — caller keeps its threshold behaviour).

        Tier 2 is a full LLM call, so skipping it when the local hit already
        answers the question is the highest-value place to put a 100ms
        decision. A confident "not needed" saves seconds and tokens.
        """
        try:
            from alpha.models.system_one import BooleanQuestion, get_system_one_client

            client = get_system_one_client()
            cfg = client.config
            if not cfg.enabled or not cfg.enable_memory_escalation or not client.is_available():
                return None

            async def _query() -> bool | None:
                result = await client.evaluate(
                    {
                        "query": query_text,
                        "top_local_matches": top_matches[:5],
                        "local_match_score": round(top_score, 3),
                        "memory_excerpt": lines[:60],
                    },
                    {
                        "needs_deep_retrieval": BooleanQuestion(
                            instructions=(
                                "Do `top_local_matches` already answer `query` well enough that "
                                "escalating to an expensive deep-reasoning retrieval pass would be wasteful? "
                                "Answer yes if the local matches already answer it; no if deeper retrieval is genuinely needed."
                            ),
                            criteria={
                                "true": "The local matches already answer the query adequately.",
                                "false": "The local matches miss the answer; deeper retrieval is needed.",
                            },
                        ),
                    },
                    site=SITE,
                )
                if result is None:
                    return None
                answer = result.get("needs_deep_retrieval")
                if answer is None or not answer.meets(cfg.min_confidence):
                    return None
                answered_by_local = float(answer.value) >= 0.5
                return not answered_by_local  # local answered -> do not escalate

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_query())
            return None  # never block a live event loop
        except Exception:
            logger.debug("System One memory escalation gate unavailable; using threshold.", exc_info=True)
            return None

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

        # Tier 1.5: System One (Jev) gate — skip the expensive Tier-2 LLM call
        # when the local matches already answer the query.
        if escalation_handler is not None:
            gate = self._system_one_escalation_gate(query_text, lines, top_matches, top_score)
            if gate is False:
                return MemoryLookupResult(
                    query=query_text,
                    tier=1,
                    matches=top_matches,
                    confidence=max(top_score, self.escalation_threshold),
                    escalated=False,
                    reason=f"System One judged Tier 1 sufficient (score {top_score:.2f}); deep retrieval skipped.",
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
