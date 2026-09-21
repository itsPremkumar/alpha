"""Deciding what survives compaction (System One fast path).

Compaction is a judgement about the future: will this chunk still matter later
in the task? Position and size thresholds answer a different question ("is it
old?") and get it wrong in both directions — a decision made forty turns ago
is load-bearing, while last turn's verbose tool dump is not.

One score question per chunk, all on one request:

==================  ===========================================================
level               meaning
==================  ===========================================================
1                   safe to drop — restated later or purely procedural
2                   compress — useful but not load-bearing
3                   keep — a decision, constraint, or result the task needs
4                   keep verbatim — an identifier, path, number, or commitment
==================  ===========================================================

This site is on the hot path, so it is deliberately paranoid: it is skipped
entirely when disabled, caps how many chunks it scores, and any chunk System
One does not return a score for **keeps its existing treatment** rather than
being dropped.

Contract: ``None`` means "no signal, use the threshold/position policy".
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
SITE = "retention"

RETENTION_LEVELS = [
    "Safe to drop — restated later, superseded, or purely procedural.",
    "Compress — useful context but not load-bearing.",
    "Keep — contains a decision, constraint, or result the task depends on.",
    "Keep verbatim — contains an identifier, path, number, or commitment that must survive exactly.",
]

#: Chunks at or above this level are protected from compaction.
KEEP_AT = 3.0

#: Hard cap per call. Compaction runs on long histories; scoring every chunk
#: would put the fast path on the slow path.
MAX_CHUNKS = 32

#: Per-chunk excerpt sent as evidence.
_CHUNK_CHARS = 700


@dataclass
class RetentionScores:
    """Normalised 0..1 retention score per chunk index."""

    levels: dict[int, float]
    jev_used: bool = True

    def level_for(self, index: int) -> float | None:
        return self.levels.get(index)

    def should_keep(self, index: int) -> bool | None:
        """True = protect, False = compactable, None = no signal for this chunk."""
        level = self.levels.get(index)
        if level is None:
            return None
        return level >= KEEP_AT


def build_questions(count: int) -> dict[str, ScoreQuestion]:
    return {
        f"c{index}": ScoreQuestion(
            f"Given the task, how much of chunk {index} must survive context compaction?",
            RETENTION_LEVELS,
        )
        for index in range(count)
    }


def build_state(task: str, chunks: list[str]) -> dict[str, Any]:
    return {
        "task": task[:1500],
        "chunks": [{"index": i, "text": (text or "")[:_CHUNK_CHARS]} for i, text in enumerate(chunks)],
    }


async def retention_scores(
    task: str,
    chunks: list[str],
    *,
    tier: str | RiskTier = RiskTier.READ,
    client: SystemOneClient | None = None,
) -> RetentionScores | None:
    """Score how much each chunk matters. None = no signal.

    ``chunks`` is capped at :data:`MAX_CHUNKS`; callers pre-filter (longest or
    oldest first) before calling. Never raises.
    """
    if not chunks:
        return None

    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_compaction_retention:
        return None

    pool = list(chunks[:MAX_CHUNKS])
    if not pool:
        return None

    threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(build_state(task, pool), build_questions(len(pool)), min_confidence=threshold, site=SITE)
    except Exception as exc:
        logger.debug("System One retention scoring failed (%s); falling back.", exc)
        return None
    if result is None:
        return None

    levels: dict[int, float] = {}
    for index in range(len(pool)):
        answer = result.get(f"c{index}")
        if answer is None or answer.type != "score":
            continue
        level = answer.score
        if level is None:
            continue
        levels[index] = float(level)
    if not levels:
        return None
    return RetentionScores(levels=levels)


def retention_scores_sync(task: str, chunks: list[str], **kwargs: Any) -> RetentionScores | None:
    """Sync wrapper; falls back rather than blocking a live loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(retention_scores(task, chunks, **kwargs))
    logger.debug("retention_scores_sync inside a running loop; falling back.")
    return None


__all__ = [
    "KEEP_AT",
    "MAX_CHUNKS",
    "RETENTION_LEVELS",
    "RetentionScores",
    "build_questions",
    "build_state",
    "retention_scores",
    "retention_scores_sync",
]
