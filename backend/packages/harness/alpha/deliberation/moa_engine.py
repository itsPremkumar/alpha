"""Mixture-of-Agents (MoA) Multi-Model Advisory Engine.

Executes parallel reference-model advisory passes, scrubs PII and credentials,
and synthesizes consensus outputs with confidence scoring.
"""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional


class PIIFilter:
    """Redacts emails, phone numbers, and secrets from advisor text."""

    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _PHONE_RE = re.compile(r"(?:\+?1[ .-])?(?:\(\d{3}\)[ .-]?|\d{3}[.-])\d{3}[.-]\d{4}")
    _SECRET_RE = re.compile(r"(?:sk-[a-zA-Z0-9_-]{20,}|Bearer\s+[a-zA-Z0-9_.\-]{20,}|ghp_[a-zA-Z0-9]{20,})")

    @classmethod
    def redact(cls, text: str) -> str:
        if not text:
            return ""
        s = cls._EMAIL_RE.sub("[redacted email]", text)
        s = cls._PHONE_RE.sub("[redacted phone]", s)
        s = cls._SECRET_RE.sub("[redacted credential]", s)
        return s


@dataclass
class MoAAdvisory:
    advisor_name: str
    perspective: str
    output: str
    confidence: float = 1.0


@dataclass
class MoAConsensus:
    query: str
    consensus_summary: str
    advisories: list[MoAAdvisory]
    agreement_score: float
    total_advisors: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "consensus_summary": self.consensus_summary,
            "agreement_score": round(self.agreement_score, 3),
            "total_advisors": self.total_advisors,
            "advisories": [asdict(a) for a in self.advisories],
        }


class MoAEngine:
    """Mixture-of-Agents orchestrator synthesizing multi-model perspectives."""

    def __init__(self):
        self._advisors: dict[str, tuple[str, Callable[[str], str]]] = {}

    def register_advisor(self, name: str, perspective: str, generate_fn: Callable[[str], str]) -> None:
        self._advisors[name] = (perspective, generate_fn)

    def execute_advisory_pass(self, query: str) -> list[MoAAdvisory]:
        """Execute registered advisors in parallel with PII filtering."""
        advisories: list[MoAAdvisory] = []

        def _run_single(name: str, perspective: str, fn: Callable[[str], str]) -> MoAAdvisory:
            try:
                raw = fn(query)
                clean = PIIFilter.redact(raw)
                return MoAAdvisory(advisor_name=name, perspective=perspective, output=clean)
            except Exception as e:
                return MoAAdvisory(advisor_name=name, perspective=perspective, output=f"Advisor error: {e}", confidence=0.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self._advisors))) as pool:
            futures = [
                pool.submit(_run_single, name, pers, fn)
                for name, (pers, fn) in self._advisors.items()
            ]
            for f in concurrent.futures.as_completed(futures):
                advisories.append(f.result())

        return advisories

    def synthesize_consensus(
        self,
        query: str,
        advisories: list[MoAAdvisory],
        aggregator_fn: Optional[Callable[[str, list[str]], str]] = None,
    ) -> MoAConsensus:
        """Synthesize final consensus with lexical convergence scoring."""
        if not advisories:
            return MoAConsensus(
                query=query,
                consensus_summary="No advisor outputs available.",
                advisories=[],
                agreement_score=0.0,
                total_advisors=0,
            )

        # Compute pair-wise agreement score
        outputs = [a.output for a in advisories if a.confidence > 0.0]
        score = self._compute_lexical_agreement(outputs)

        if aggregator_fn:
            summary = aggregator_fn(query, outputs)
        else:
            summary = f"Synthesized consensus across {len(outputs)} advisors (agreement: {score:.1%}):\n" + "\n".join(
                f"- [{a.advisor_name} / {a.perspective}]: {a.output[:120]}..."
                for a in advisories
            )

        return MoAConsensus(
            query=query,
            consensus_summary=summary,
            advisories=advisories,
            agreement_score=score,
            total_advisors=len(advisories),
        )

    @staticmethod
    def _compute_lexical_agreement(texts: list[str]) -> float:
        if len(texts) < 2:
            return 1.0

        def _words(t: str) -> set[str]:
            return set(re.findall(r"\b\w{3,}\b", t.lower()))

        word_sets = [_words(t) for t in texts if t]
        if not word_sets:
            return 0.0

        pairs = []
        for i in range(len(word_sets)):
            for j in range(i + 1, len(word_sets)):
                s1, s2 = word_sets[i], word_sets[j]
                sim = len(s1.intersection(s2)) / len(s1.union(s2)) if s1.union(s2) else 0.0
                pairs.append(sim)

        return sum(pairs) / len(pairs) if pairs else 1.0
