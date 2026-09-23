"""Strategic Deliberation Router & Worthwhile Predictor.

Analyzes input task difficulty, risk, and ambiguity to determine whether
multi-model deliberation is worthwhile, selecting the optimal strategy:
- SINGLE: Trivial/simple tasks (zero overhead, immediate response)
- ENSEMBLE: Fast parallel candidate generation
- COUNCIL: 3-stage blind peer review for nuanced architecture, security, and strategy
- DEBATE: Multi-round adversarial challenge for contested trade-offs
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from alpha.deliberation import invocation
from alpha.deliberation.models import DeliberationStrategy

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "deliberation"


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
                roster_models=invocation.configured_model_roster(),
                rationale="Critical destructive or production-impacting operation detected; routing to high-stakes Council.",
                worthwhile=True,
            )

        # 3. Comparative & Trade-off Debate Patterns
        if any(re.search(pat, prompt_lower) for pat in cls.DEBATE_PATTERNS):
            return RouterEvaluation(
                difficulty=TaskDifficulty.COMPLEX,
                risk=TaskRisk.MEDIUM,
                strategy=DeliberationStrategy.DEBATE,
                roster_models=invocation.configured_model_roster(),
                rationale="Contested architectural trade-off or comparative inquiry detected; routing to Sparse Multi-Agent Debate.",
                worthwhile=True,
            )

        # 4. High-Impact Architecture & Council Patterns
        if any(re.search(pat, prompt_lower) for pat in cls.COUNCIL_PATTERNS) or any(w in prompt_lower for w in ("security", "payment", "auth", "migration")):
            return RouterEvaluation(
                difficulty=TaskDifficulty.COMPLEX,
                risk=TaskRisk.HIGH,
                strategy=DeliberationStrategy.COUNCIL,
                roster_models=invocation.configured_model_roster(),
                rationale="High-impact or ambiguous system decision detected; routing to 3-Stage Anonymous Peer Review Council.",
                worthwhile=True,
            )

        # 5. Parallel Exploration Ensemble Patterns
        if any(w in prompt_lower for w in ("brainstorm", "options", "ideas", "explore")):
            return RouterEvaluation(
                difficulty=TaskDifficulty.MEDIUM,
                risk=TaskRisk.LOW,
                strategy=DeliberationStrategy.ENSEMBLE,
                roster_models=invocation.configured_model_roster(),
                rationale="Broad candidate exploration inquiry detected; routing to Parallel Ensemble.",
                worthwhile=True,
            )

        # 6. Trivial / Low-Risk Queries (Single Model Fast-Path)
        if any(re.search(pat, prompt_lower) for pat in cls.TRIVIAL_PATTERNS) or len(prompt.split()) <= 4:
            return RouterEvaluation(
                difficulty=TaskDifficulty.TRIVIAL,
                risk=TaskRisk.LOW,
                strategy=DeliberationStrategy.SINGLE,
                roster_models=invocation.configured_model_roster()[:1],
                rationale="Task is simple or low-risk; routing to single model to eliminate deliberation latency.",
                worthwhile=False,
            )

        # 7. Default Fallback
        return RouterEvaluation(
            difficulty=TaskDifficulty.MEDIUM,
            risk=TaskRisk.LOW,
            strategy=DeliberationStrategy.COUNCIL,
            roster_models=invocation.configured_model_roster(),
            rationale="Moderate complexity prompt; routing to standard Council deliberation.",
            worthwhile=True,
        )

    @classmethod
    async def aclassify(
        cls,
        prompt: str,
        user_strategy: DeliberationStrategy | str = DeliberationStrategy.AUTO,
    ) -> RouterEvaluation:
        """System One (Jev) classification with a deterministic fallback.

        ``classify`` remains the source of truth: it is the fallback whenever
        System One is disabled, unreachable, or not confident enough, and it
        always handles explicitly requested strategies (no model call needed).
        Only AUTO requests consult the model, and only a confident answer
        overrides the heuristic.
        """
        normalized = user_strategy
        if isinstance(normalized, str):
            try:
                normalized = DeliberationStrategy(normalized.lower().strip())
            except ValueError:
                normalized = DeliberationStrategy.AUTO

        # An explicit user choice needs no judgement call.
        if normalized != DeliberationStrategy.AUTO:
            return cls.classify(prompt, normalized)

        try:
            from alpha.models.system_one import ChoiceQuestion, get_system_one_client

            client = get_system_one_client()
            cfg = client.config
            if cfg.enabled and cfg.enable_deliberation_router and client.is_available():
                threshold = cfg.min_confidence
                result = await client.evaluate(
                    {"prompt": (prompt or "")[:8000]},
                    {
                        "strategy": ChoiceQuestion(
                            instructions=(
                                "Which deliberation strategy best fits this prompt? "
                                "SINGLE for trivial or low-risk work with no trade-offs. "
                                "ENSEMBLE for open-ended brainstorming where many candidates help. "
                                "COUNCIL for high-impact architecture, security, or migration decisions needing peer review. "
                                "DEBATE for explicit comparisons or contested trade-offs."
                            ),
                            criteria={
                                "SINGLE": "Trivial, factual, or low-risk; deliberating would only add latency.",
                                "ENSEMBLE": "Open-ended exploration where parallel candidates add value.",
                                "COUNCIL": "High-impact, architectural, or security-sensitive; needs review.",
                                "DEBATE": "Explicit comparison or contested trade-off between options.",
                            },
                        ),
                        "difficulty": ChoiceQuestion(
                            instructions="How difficult is this task for a single strong model?",
                            criteria={
                                "trivial": "Answerable immediately with no real reasoning.",
                                "simple": "Straightforward, one clear approach.",
                                "medium": "Needs some thought but has an established approach.",
                                "complex": "Genuinely hard; benefits from multiple perspectives.",
                                "high_risk": "Hard and destructive or production-impacting if wrong.",
                            },
                        ),
                        "risk": ChoiceQuestion(
                            instructions="What is the risk if the answer is wrong?",
                            criteria={
                                "low": "Mistake is easy to notice and cheap to fix.",
                                "medium": "Mistake costs rework but is recoverable.",
                                "high": "Mistake affects security, money, or production.",
                                "critical": "Mistake is destructive or irreversible.",
                            },
                        ),
                    },
                    site=SITE,
                )
                if result is not None:
                    strategy_answer = result.get("strategy")
                    difficulty_answer = result.get("difficulty")
                    risk_answer = result.get("risk")
                    if strategy_answer is not None and strategy_answer.meets(threshold):
                        try:
                            strategy = DeliberationStrategy(str(strategy_answer.value).lower())
                        except ValueError:
                            strategy = DeliberationStrategy.COUNCIL
                        difficulty = TaskDifficulty.MEDIUM
                        if difficulty_answer is not None and difficulty_answer.meets(threshold):
                            try:
                                difficulty = TaskDifficulty(str(difficulty_answer.value).lower())
                            except ValueError:
                                pass
                        risk = TaskRisk.LOW
                        if risk_answer is not None and risk_answer.meets(threshold):
                            try:
                                risk = TaskRisk(str(risk_answer.value).lower())
                            except ValueError:
                                pass
                        worthwhile = strategy != DeliberationStrategy.SINGLE
                        return RouterEvaluation(
                            difficulty=difficulty,
                            risk=risk,
                            strategy=strategy,
                            roster_models=cls._get_roster_for_strategy(strategy),
                            rationale=(
                                f"System One ({result.model}) selected '{strategy.value}' "
                                f"(difficulty={difficulty.value}, risk={risk.value}) in {result.latency_ms:.0f}ms."
                            ),
                            worthwhile=worthwhile,
                        )
                    logger.debug("System One deliberation routing below confidence threshold; using deterministic router.")
        except Exception:
            logger.debug("System One deliberation routing unavailable; using deterministic router.", exc_info=True)

        return cls.classify(prompt, normalized)

    @classmethod
    def classify_smart(
        cls,
        prompt: str,
        user_strategy: DeliberationStrategy | str = DeliberationStrategy.AUTO,
    ) -> RouterEvaluation:
        """Sync bridge to :meth:`aclassify` for callers not on an event loop.

        Sync callers here run inside ``asyncio.to_thread`` workers, so there is
        no running loop and spinning one up for a single ~100ms System One call
        is safe. If a loop *is* running we must never block it, so we fall back
        to the deterministic router instead.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(cls.aclassify(prompt, user_strategy))
            except Exception:
                logger.debug("System One routing bridge failed; using deterministic router.", exc_info=True)
                return cls.classify(prompt, user_strategy)
        return cls.classify(prompt, user_strategy)

    @classmethod
    def _get_roster_for_strategy(cls, strategy: DeliberationStrategy) -> list[str]:
        # Rosters are the genuinely configured models — the old hardcoded
        # names ("lead-model", "advocate-alpha", "candidate-1" ...) existed
        # nowhere and could never be invoked.
        roster = invocation.configured_model_roster()
        if strategy == DeliberationStrategy.SINGLE:
            return roster[:1]
        return roster
