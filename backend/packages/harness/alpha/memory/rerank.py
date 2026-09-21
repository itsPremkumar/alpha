"""Query -> candidate relevance reranking with System One.

Keyword and embedding retrieval both return a list; both are blind to whether
the candidate actually *answers* the query. TypeSafe's cookbook measures the
gap on legal search: top-1 accuracy **5% -> 18%**, top-10 **38% -> 62%**.

The shape that gets that result is one score question per (query, candidate)
pair, all on a single request — viable only because System One evaluates every
question in parallel and output tokens are free. Still capped: a very large
candidate set should be narrowed by the cheap retriever first.

Contract: ``None`` means "no signal, keep the retriever's order". Callers merge
rather than replace so a strong lexical hit can never be pushed out by a
probabilistic tie.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import (
    ScoreQuestion,
    SystemOneClient,
    get_system_one_client,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "rerank"

#: Ordered rubric, 1..4. Relevance is genuinely ordinal, which is what `score`
#: is for (as opposed to `choice`, which is unordered).
RELEVANCE_LEVELS = [
    "Irrelevant — does not address the query at all.",
    "Tangential — on the same topic but does not help answer the query.",
    "Helpful — partially answers the query.",
    "Directly answers the query.",
]

#: Highest rubric level, used to normalise onto 0..1.
_MAX_LEVEL = float(len(RELEVANCE_LEVELS))


@dataclass
class RerankResult:
    """Ordered candidate indices with their normalised scores."""

    order: list[int]
    scores: dict[int, float]
    jev_used: bool = True

    def top(self, n: int) -> list[int]:
        return self.order[:n]


def build_questions(count: int) -> dict[str, ScoreQuestion]:
    """One relevance question per candidate index."""
    return {
        f"c{index}": ScoreQuestion(
            f"Is the candidate numbered {index} relevant to the query?",
            RELEVANCE_LEVELS,
        )
        for index in range(count)
    }


def build_state(query: str, candidates: list[str]) -> dict[str, Any]:
    return {
        "query": query[:2000],
        "candidates": [{"index": i, "text": (text or "")[:1200]} for i, text in enumerate(candidates)],
    }


def normalise(level: float) -> float:
    """Map a 1..N rubric level onto 0..1."""
    if _MAX_LEVEL <= 1.0:
        return 0.0
    return round(min(1.0, max(0.0, (float(level) - 1.0) / (_MAX_LEVEL - 1.0))), 4)


async def rerank(
    query: str,
    candidates: list[str],
    *,
    top_n: int | None = None,
    tier: str | RiskTier = RiskTier.READ,
    site: str | None = None,
    client: SystemOneClient | None = None,
) -> RerankResult | None:
    """Reorder `candidates` by relevance to `query`.

    Returns indices (positions in the input list), most relevant first, or None
    when System One has no signal. Never raises.

    Args:
        site: Override the call-site label recorded in the decision log, so
            RAG reranking and session-memory reranking can be measured apart.
    """
    query = (query or "").strip()
    if not query or not candidates:
        return None

    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_rag_rerank:
        return None

    pool = list(candidates[: cfg.max_rerank_candidates])
    if len(pool) < 2:
        return None

    questions = build_questions(len(pool))
    threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(build_state(query, pool), questions, min_confidence=threshold, site=site or SITE)
    except Exception as exc:
        logger.debug("System One rerank failed (%s); falling back.", exc)
        return None
    if result is None:
        return None

    scores: dict[int, float] = {}
    for index in range(len(pool)):
        answer = result.get(f"c{index}")
        if answer is None or answer.type != "score":
            continue
        level = answer.score
        if level is None:
            continue
        scores[index] = normalise(level)

    if not scores:
        return None

    # Missing indices keep their input position behind everything scored, so a
    # partial answer can never silently drop a candidate.
    order = sorted(scores, key=lambda i: -scores[i])
    order.extend(i for i in range(len(pool)) if i not in scores)
    if top_n is not None:
        order = order[:top_n]
    return RerankResult(order=order, scores=scores)


def rerank_sync(query: str, candidates: list[str], **kwargs: Any) -> RerankResult | None:
    """Sync wrapper; falls back rather than blocking a live loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(rerank(query, candidates, **kwargs))
    logger.debug("rerank_sync inside a running loop; falling back.")
    return None


def apply_rerank(items: list[Any], result: RerankResult | None) -> list[Any]:
    """Reorder `items` by a rerank result, appending anything unscored.

    Unscored items are appended in their original order rather than dropped, so
    the worst case is "the retriever's order", never "lost a result".
    """
    if result is None:
        return items
    ordered = [items[i] for i in result.order if 0 <= i < len(items)]
    seen = set(result.order)
    ordered.extend(item for index, item in enumerate(items) if index not in seen)
    return ordered


__all__ = [
    "RELEVANCE_LEVELS",
    "RerankResult",
    "apply_rerank",
    "build_questions",
    "build_state",
    "normalise",
    "rerank",
    "rerank_sync",
]
