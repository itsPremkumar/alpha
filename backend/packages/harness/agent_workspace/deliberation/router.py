"""Strategic Deliberation Router & Worthwhile Predictor.

Analyzes input task difficulty, risk, and ambiguity to determine whether
multi-model deliberation is worthwhile, selecting the optimal strategy:
- SINGLE: Trivial/simple tasks (zero overhead, immediate response)
- ENSEMBLE: Fast parallel candidate generation
- COUNCIL: 3-stage blind peer review for nuanced architecture, security, and strategy
- DEBATE: Multi-round adversarial challenge for contested trade-offs
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from agent_workspace.deliberation.models import DeliberationStrategy


class TaskDifficulty(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    HIGH_RISK = "high_risk"
    AMBIGUOUS = "ambiguous"


class TaskRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RouterEvaluation:
    difficulty: TaskDifficulty
    risk: TaskRisk
    strategy: DeliberationStrategy
    roster_models: list[str]
    rationale: str
    worthwhile: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["difficulty"] = self.difficulty.value
        d["risk"] = self.risk.value
        d["strategy"] = self.strategy.value
        return d


class DeliberationRouter:
    """Evaluates prompts and selects the optimal deliberation strategy and model roster."""

    TRIVIAL_PATTERNS = [
        r"^hi\b",
        r"^hello\b",
        r"^format this",
        r"^fix (a )?typo",
        r"^convert .* to json",
        r"^what is \d+ \+ \d+",
    ]

    DEBATE_PATTERNS = [
        r"\bvs\b",
        r"\bversus\b",
        r"\bpros and cons\b",
        r"\btrade-?offs?\b",
        r"\bdebate\b",
        r"\bcompare\b",
    ]

    COUNCIL_PATTERNS = [
        r"\barchitecture\b",
        r"\bcouncil\b",
        r"\bconsensus\b",
        r"\bsecurity audit\b",
        r"\bdesign review\b",
        r"\brefactor system\b",
        r"\bstrategy\b",
    ]

    CRITICAL_PATTERNS = [
        r"\bdrop table\b",
        r"\brm -rf\b",
        r"\bproduction deploy\b",
        r"\bdelete database\b",
        r"\bsecurity breach\b",
    ]

    @classmethod
    def classify(
        cls,
        prompt: str,
        user_strategy: DeliberationStrategy | str = DeliberationStrategy.AUTO,
    ) -> RouterEvaluation:
        """Classifies the prompt and returns the recommended strategy and participant roster."""
        prompt_lower = (prompt or "").lower().strip()
        if isinstance(user_strategy, str):
            try:
                user_strategy = DeliberationStrategy(user_strategy.lower().strip())
            except ValueError:
                user_strategy = DeliberationStrategy.AUTO

        # 1. User explicit strategy
        if user_strategy != DeliberationStrategy.AUTO:
            strategy = user_strategy
            worthwhile = strategy != DeliberationStrategy.SINGLE
            return RouterEvaluation(
                difficulty=TaskDifficulty.COMPLEX if worthwhile else TaskDifficulty.SIMPLE,
                risk=TaskRisk.MEDIUM if worthwhile else TaskRisk.LOW,
                strategy=strategy,
                roster_models=cls._get_roster_for_strategy(strategy),
                rationale=f"User explicitly selected deliberation strategy '{strategy.value}'.",
                worthwhile=worthwhile,
            )

        # 2. Critical & Destructive Risk Patterns
        if any(re.search(pat, prompt_lower) for pat in cls.CRITICAL_PATTERNS):
            return RouterEvaluation(
                difficulty=TaskDifficulty.HIGH_RISK,
                risk=TaskRisk.CRITICAL,
                strategy=DeliberationStrategy.COUNCIL,
                roster_models=["security-lead", "adversarial-critic", "independent-verifier"],
                rationale="Critical destructive or production-impacting operation detected; routing to high-stakes Council.",
                worthwhile=True,
            )

        # 3. Comparative & Trade-off Debate Patterns
        if any(re.search(pat, prompt_lower) for pat in cls.DEBATE_PATTERNS):
            return RouterEvaluation(
                difficulty=TaskDifficulty.COMPLEX,
                risk=TaskRisk.MEDIUM,
                strategy=DeliberationStrategy.DEBATE,
                roster_models=["advocate-alpha", "critic-beta", "independent-judge"],
                rationale="Contested architectural trade-off or comparative inquiry detected; routing to Sparse Multi-Agent Debate.",
                worthwhile=True,
            )

        # 4. High-Impact Architecture & Council Patterns
        if any(re.search(pat, prompt_lower) for pat in cls.COUNCIL_PATTERNS) or any(w in prompt_lower for w in ("security", "payment", "auth", "migration")):
            return RouterEvaluation(
                difficulty=TaskDifficulty.COMPLEX,
                risk=TaskRisk.HIGH,
                strategy=DeliberationStrategy.COUNCIL,
                roster_models=["candidate-1", "candidate-2", "candidate-3", "chairman-synthesizer"],
                rationale="High-impact or ambiguous system decision detected; routing to 3-Stage Anonymous Peer Review Council.",
                worthwhile=True,
            )

        # 5. Parallel Exploration Ensemble Patterns
        if any(w in prompt_lower for w in ("brainstorm", "options", "ideas", "explore")):
            return RouterEvaluation(
                difficulty=TaskDifficulty.MEDIUM,
                risk=TaskRisk.LOW,
                strategy=DeliberationStrategy.ENSEMBLE,
                roster_models=["proposer-1", "proposer-2", "proposer-3", "aggregator"],
                rationale="Broad candidate exploration inquiry detected; routing to Parallel Ensemble.",
                worthwhile=True,
            )

        # 6. Trivial / Low-Risk Queries (Single Model Fast-Path)
        if any(re.search(pat, prompt_lower) for pat in cls.TRIVIAL_PATTERNS) or len(prompt.split()) <= 4:
            return RouterEvaluation(
                difficulty=TaskDifficulty.TRIVIAL,
                risk=TaskRisk.LOW,
                strategy=DeliberationStrategy.SINGLE,
                roster_models=["lead-model"],
                rationale="Task is simple or low-risk; routing to single model to eliminate deliberation latency.",
                worthwhile=False,
            )

        # 7. Default Fallback
        return RouterEvaluation(
            difficulty=TaskDifficulty.MEDIUM,
            risk=TaskRisk.LOW,
            strategy=DeliberationStrategy.COUNCIL,
            roster_models=["candidate-1", "candidate-2", "candidate-3", "chairman-synthesizer"],
            rationale="Moderate complexity prompt; routing to standard Council deliberation.",
            worthwhile=True,
        )

    @classmethod
    def _get_roster_for_strategy(cls, strategy: DeliberationStrategy) -> list[str]:
        if strategy == DeliberationStrategy.SINGLE:
            return ["lead-model"]
        if strategy == DeliberationStrategy.DEBATE:
            return ["advocate-alpha", "critic-beta", "independent-judge"]
        if strategy == DeliberationStrategy.ENSEMBLE:
            return ["proposer-1", "proposer-2", "proposer-3", "aggregator"]
        return ["candidate-1", "candidate-2", "candidate-3", "chairman-synthesizer"]
