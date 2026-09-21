"""Per-criterion acceptance judgement for undecidable criteria.

``subagents/acceptance_checks.check_acceptance_criteria`` is deterministic and
deliberately so. Anything it cannot decide gets ``family="undecidable"``,
``checked=False`` — which is honest, but leaves the lead with a checklist full
of UNVERIFIED lines. Closing that gap today means the selective judge: a whole
one-shot LLM review, off by default because it is slow.

This is the typed alternative: **one boolean per criterion, one request.** The
criteria were written by the delegating agent; each is a yes/no property of the
result, which is exactly the boolean primitive.

The asymmetry that shapes the default
-------------------------------------
A wrong "holds" lets broken work through to the user. A wrong "does not hold"
costs a retry. Those are not the same price, so by default System One may only
*narrow*: a confident "this criterion is not met" fills the leaf, while a
confident "it is met" leaves it UNVERIFIED. Callers who have measured their own
false-negative rate can opt in with ``widen=True``.

Contract: ``None`` means "no signal, leave the leaf as it was".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import (
    BooleanQuestion,
    SystemOneClient,
    get_system_one_client,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "acceptance"

#: A boolean at or above this is read as "criterion is met".
MET_AT = 0.5

#: Distance from 0.5 below which a boolean is treated as unconfident.
MIN_CONFIDENCE = 0.6

#: Leaves filled by this module carry this family so the render can say so.
FAMILY = "system_one"


def build_questions(criteria: list[str]) -> dict[str, BooleanQuestion]:
    """One boolean per criterion, indexed to match the state."""
    return {
        f"c{index}": BooleanQuestion(
            f"Does the result satisfy acceptance criterion {index}?",
            {
                "true": "The result satisfies this criterion.",
                "false": "The result does not satisfy this criterion, or there is no evidence it does.",
            },
        )
        for index in range(len(criteria))
    }


def build_state(task: str, result_text: str, criteria: list[str]) -> dict[str, Any]:
    return {
        "task": (task or "")[:2000],
        "result": (result_text or "")[:8000],
        "criteria": [{"index": i, "text": c[:400]} for i, c in enumerate(criteria)],
    }


def interpret(value: float | None, *, met_at: float = MET_AT, min_confidence: float = MIN_CONFIDENCE) -> bool | None:
    """Turn a calibrated boolean into met / not-met / no-signal."""
    if value is None:
        return None
    confidence = abs(value - 0.5) * 2
    if confidence < min_confidence:
        return None
    return value >= met_at


async def evaluate_criteria(
    task: str,
    result_text: str,
    criteria: list[str],
    *,
    tier: str | RiskTier = RiskTier.WRITE,
    client: SystemOneClient | None = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> dict[int, bool | None]:
    """Judge each criterion. Missing indices had no signal. Never raises."""
    if not criteria or not (result_text or "").strip():
        return {}

    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_acceptance:
        return {}

    questions = build_questions(criteria)
    threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(build_state(task, result_text, criteria), questions, min_confidence=threshold, site=SITE)
    except Exception as exc:
        logger.debug("System One acceptance evaluation failed (%s); falling back.", exc)
        return {}
    if result is None:
        return {}

    verdicts: dict[int, bool | None] = {}
    for index in range(len(criteria)):
        answer = result.get(f"c{index}")
        if answer is None or answer.type != "boolean":
            verdicts[index] = None
            continue
        verdicts[index] = interpret(answer.boolean, min_confidence=min_confidence)
    return verdicts


def apply_to_verdict(
    verdict: dict[str, Any],
    judgements: Mapping[int, bool | None],
    *,
    widen: bool = False,
) -> dict[str, Any]:
    """Fill `undecidable` leaves in place. Returns a new verdict dict.

    ``widen=False`` (default) only ever turns UNVERIFIED into "does not hold".
    ``widen=True`` also turns it into "holds" when System One is confident.

    Leaves that System One had no signal for are left untouched, so the outcome
    is never worse than the deterministic one.
    """
    if not judgements:
        return verdict
    leaves = verdict.get("leaves")
    if not isinstance(leaves, list):
        return verdict

    updated: list[dict[str, Any]] = []
    changed = False
    for index, leaf in enumerate(leaves):
        if not isinstance(leaf, dict) or leaf.get("checked") or leaf.get("family") != "undecidable":
            updated.append(leaf)
            continue
        judgement = judgements.get(index)
        if judgement is None:
            updated.append(leaf)
            continue
        if judgement is True and not widen:
            updated.append(leaf)
            continue
        new_leaf = dict(leaf)
        new_leaf["checked"] = True
        new_leaf["holds"] = bool(judgement)
        new_leaf["family"] = FAMILY
        confidence = "confident" if judgement is False else "confident (widening enabled)"
        new_leaf["detail"] = f"system one: {'met' if judgement else 'not met'} — {confidence}"
        updated.append(new_leaf)
        changed = True

    if not changed:
        return verdict
    merged = dict(verdict)
    merged["leaves"] = updated
    merged["unchecked"] = [leaf.get("criterion", "") for leaf in updated if not leaf.get("checked")]
    merged["all_hold"] = all(bool(leaf.get("checked")) and bool(leaf.get("holds")) for leaf in updated)
    return merged


async def acriteria_check_smart(
    task: str,
    result_text: str,
    criteria: list[str],
    *,
    widen: bool = False,
    tier: str | RiskTier = RiskTier.WRITE,
    client: SystemOneClient | None = None,
) -> dict[int, bool | None]:
    """Convenience: evaluate then return judgements keyed by criterion text."""
    judgements = await evaluate_criteria(task, result_text, criteria, tier=tier, client=client)
    return judgements


def evaluate_criteria_sync(task: str, result_text: str, criteria: list[str], **kwargs: Any) -> dict[int, bool | None]:
    """Sync wrapper; returns {} (fall back) inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(evaluate_criteria(task, result_text, criteria, **kwargs))
    logger.debug("evaluate_criteria_sync inside a running loop; falling back.")
    return {}


__all__ = [
    "FAMILY",
    "MET_AT",
    "MIN_CONFIDENCE",
    "acriteria_check_smart",
    "apply_to_verdict",
    "build_questions",
    "build_state",
    "evaluate_criteria",
    "evaluate_criteria_sync",
    "interpret",
]
