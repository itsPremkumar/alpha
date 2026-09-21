"""Does this prompt need the flagship model? (System One fast path.)

The asymmetry that makes this worth doing, from Ronacher's write-up of System
One: **a wrong escalation costs money, a wrong downgrade costs quality.** So
the decision is biased toward escalating — any question System One cannot
answer confidently resolves to "escalate", and only a confident "no" downgrades.

Three atomic questions, one request:

===================  ===========================================================
question             what it measures
===================  ===========================================================
``needs_reasoning``  multi-step reasoning, planning, or synthesis required
``needs_precision``  the answer must be exactly right (code, numbers, citations)
``downgrade_risk``   ordered rubric: how bad is it if a cheap model answers
===================  ===========================================================

They are combined in code rather than asked as one broad "is this hard?"
question, because decomposition is what makes System One calibrated.

Contract: ``None`` means "no signal, use the existing keyword/category router".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import (
    BooleanQuestion,
    ScoreQuestion,
    SystemOneClient,
    get_system_one_client,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "escalation"

#: Rubric for `downgrade_risk`, 1..4.
DOWNGRADE_RISK_LEVELS = [
    "A cheap model answers this as well as a flagship one.",
    "A cheap model is usually fine; occasional shallow answers are acceptable.",
    "A cheap model is likely to be noticeably worse or incomplete.",
    "A cheap model will very likely get this wrong, and being wrong is costly.",
]

#: Escalate once the risk rubric reaches this level.
ESCALATE_AT_LEVEL = 3.0

#: Below this the risk rubric says a cheap model is fine; between the two the
#: reading is too close to call and we escalate.
_SAFE_AT_LEVEL = 2.0

#: A boolean at or above this is read as "yes".
YES_AT = 0.5


def build_questions() -> dict[str, Any]:
    """The three escalation questions."""
    return {
        "needs_reasoning": BooleanQuestion(
            "Considering this request, is the following true?",
            {
                "true": "Answering well requires multi-step reasoning, planning, comparison, or synthesis.",
                "false": "A direct, shallow answer is enough.",
            },
        ),
        "needs_precision": BooleanQuestion(
            "Considering this request, is the following true?",
            {
                "true": "The answer must be exactly right — code, numbers, identifiers, or verifiable facts where a near-miss is a failure.",
                "false": "An approximate or stylistically imperfect answer is acceptable.",
            },
        ),
        "downgrade_risk": ScoreQuestion(
            "How bad is it if a cheap, fast model answers this request instead of the flagship model?",
            DOWNGRADE_RISK_LEVELS,
        ),
    }


def _build_state(prompt: str, context: str = "", task_type: str = "") -> dict[str, Any]:
    state: dict[str, Any] = {"request": prompt[:4000]}
    if context:
        state["context"] = context[:2000]
    if task_type:
        state["declared_task_type"] = task_type
    return state


def combine(
    *,
    needs_reasoning: float | None,
    needs_precision: float | None,
    downgrade_risk: float | None,
    threshold: float,
) -> bool | None:
    """Combine the atomic answers into an escalate / don't-escalate call.

    Returns True (escalate), False (cheap model is fine), or None (no signal).

    The bias is deliberate: an answer System One is *not confident about*
    escalates. Only confident "no" on every axis downgrades.
    """
    if needs_reasoning is None and needs_precision is None and downgrade_risk is None:
        return None

    escalate = False
    saw_signal = False

    for value in (needs_reasoning, needs_precision):
        if value is None:
            # Missing signal is not a licence to downgrade.
            escalate = True
            continue
        saw_signal = True
        # Confidence for a boolean is distance from the coin-flip midpoint.
        confidence = abs(value - 0.5) * 2
        if value >= YES_AT:
            escalate = True
        elif confidence < threshold:
            # Too close to call -> escalate rather than risk quality.
            escalate = True

    if downgrade_risk is not None:
        saw_signal = True
        if downgrade_risk >= ESCALATE_AT_LEVEL:
            escalate = True
        elif downgrade_risk > _SAFE_AT_LEVEL:
            # Between "usually fine" and "noticeably worse": too close to call,
            # and a wrong downgrade costs quality, so escalate.
            escalate = True
    else:
        escalate = True

    if not saw_signal:
        return None
    return escalate


async def needs_flagship_model(
    prompt: str,
    *,
    context: str = "",
    task_type: str = "",
    tier: str | RiskTier = RiskTier.READ,
    client: SystemOneClient | None = None,
) -> bool | None:
    """True = send to the flagship model. None = no signal, use current routing.

    Never raises.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return None

    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_model_routing:
        return None

    threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(_build_state(prompt, context, task_type), build_questions(), min_confidence=threshold, site=SITE)
    except Exception as exc:
        logger.debug("System One escalation check failed (%s); falling back.", exc)
        return None
    if result is None:
        return None

    def _boolean(qid: str) -> float | None:
        ans = result.get(qid)
        if ans is None or ans.type != "boolean":
            return None
        return ans.boolean

    risk_answer = result.get("downgrade_risk")
    risk = risk_answer.score if risk_answer is not None else None

    return combine(
        needs_reasoning=_boolean("needs_reasoning"),
        needs_precision=_boolean("needs_precision"),
        downgrade_risk=risk,
        threshold=threshold,
    )


def needs_flagship_model_sync(prompt: str, **kwargs: Any) -> bool | None:
    """Sync wrapper; returns None (fall back) rather than blocking a live loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(needs_flagship_model(prompt, **kwargs))
    logger.debug("needs_flagship_model_sync inside a running loop; falling back.")
    return None


__all__ = [
    "DOWNGRADE_RISK_LEVELS",
    "ESCALATE_AT_LEVEL",
    "YES_AT",
    "build_questions",
    "combine",
    "needs_flagship_model",
    "needs_flagship_model_sync",
]
