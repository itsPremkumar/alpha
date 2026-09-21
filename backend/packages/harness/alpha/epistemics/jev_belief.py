"""Calibrated probabilities as belief updates (System One, research-shaped).

``epistemics/models.Claim`` already carries ``bayesian_prior`` and
``bayesian_posterior``. What fills them today is human-authored bookkeeping.
System One returns a calibrated probability, which is exactly the likelihood
a Bayesian update needs — so the posterior can be computed instead of asserted.

The update is done in **log-odds space**, where evidence is additive:

    logit(posterior) = logit(prior) + Σ logit(P(evidenceᵢ supports claim))

with ``logit(0.5) == 0``, so evidence that says nothing contributes nothing and
independent pieces of evidence accumulate. Probabilities are clamped away from
0 and 1 because a single 0.0 or 1.0 would pin the posterior forever.

Two atomic questions per (claim, evidence) pair, one request for the lot:

* ``supports`` — does this evidence support the claim?
* ``contradicts`` — does it contradict the claim?

They are separate because evidence can do neither: a neutral tool result is
not weak support, and collapsing the two into one question is exactly the
broad-question mistake decomposition exists to avoid.

Contract: ``None`` means "no signal, leave the claim's bookkeeping alone".
Do not use this as a gate — it is a belief, not a verdict.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import (
    BooleanQuestion,
    SystemOneClient,
    get_system_one_client,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "epistemic"

#: Keep probabilities off the absorbing barriers of 0 and 1.
_EPSILON = 1e-6

#: A boolean at or above this counts as support / contradiction.
YES_AT = 0.5

#: Below this confidence (distance from 0.5) a boolean is treated as silence.
MIN_CONFIDENCE = 0.55


def logit(p: float) -> float:
    """Log-odds of ``p``, clamped away from 0 and 1."""
    clamped = min(1.0 - _EPSILON, max(_EPSILON, float(p)))
    return math.log(clamped / (1.0 - clamped))


def sigmoid(x: float) -> float:
    """Inverse of :func:`logit`."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


@dataclass
class BeliefUpdate:
    """Result of one belief update."""

    prior: float
    posterior: float
    likelihoods: list[float]
    contradictions: list[float]
    jev_used: bool = True

    @property
    def shift(self) -> float:
        return self.posterior - self.prior


def build_questions(count: int) -> dict[str, BooleanQuestion]:
    """Two questions per evidence item."""
    questions: dict[str, BooleanQuestion] = {}
    for index in range(count):
        questions[f"s{index}"] = BooleanQuestion(
            f"Does evidence item {index} support the claim?",
            {
                "true": "This evidence supports the claim.",
                "false": "This evidence does not support the claim.",
            },
        )
        questions[f"c{index}"] = BooleanQuestion(
            f"Does evidence item {index} contradict the claim?",
            {
                "true": "This evidence contradicts the claim or shows the opposite.",
                "false": "This evidence does not contradict the claim.",
            },
        )
    return questions


def build_state(claim: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "claim": (claim or "")[:2000],
        "evidence": [{"index": i, "text": (item or "")[:1200]} for i, item in enumerate(evidence)],
    }


def posterior_from(prior: float, likelihoods: list[float], contradictions: list[float]) -> float:
    """Log-odds Bayesian update. Support adds, contradiction subtracts."""
    total = logit(prior)
    for value in likelihoods:
        total += logit(value)
    for value in contradictions:
        total -= logit(value)
    # Clamp the log-odds so a run of strong evidence cannot overflow.
    total = min(20.0, max(-20.0, total))
    return round(sigmoid(total), 6)


async def update_belief(
    claim: str,
    evidence: list[str],
    *,
    prior: float = 0.5,
    tier: str | RiskTier = RiskTier.READ,
    client: SystemOneClient | None = None,
) -> BeliefUpdate | None:
    """Compute a posterior for `claim` given `evidence`. None = no signal.

    Never raises. Evidence beyond the cap is ignored rather than batched, so a
    large evidence list cannot turn one cheap call into many.
    """
    if not (claim or "").strip() or not evidence:
        return None

    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_epistemic:
        return None

    threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(build_state(claim, evidence), build_questions(len(evidence)), min_confidence=threshold, site=SITE)
    except Exception as exc:
        logger.debug("System One belief update failed (%s); falling back.", exc)
        return None
    if result is None:
        return None

    likelihoods: list[float] = []
    contradictions: list[float] = []
    for index in range(len(evidence)):
        support = result.get(f"s{index}")
        if support is not None and support.type == "boolean" and support.boolean is not None:
            value = support.boolean
            if abs(value - 0.5) * 2 >= MIN_CONFIDENCE and value >= YES_AT:
                likelihoods.append(value)
        against = result.get(f"c{index}")
        if against is not None and against.type == "boolean" and against.boolean is not None:
            value = against.boolean
            if abs(value - 0.5) * 2 >= MIN_CONFIDENCE and value >= YES_AT:
                contradictions.append(value)

    if not likelihoods and not contradictions:
        return None

    return BeliefUpdate(
        prior=round(prior, 6),
        posterior=posterior_from(prior, likelihoods, contradictions),
        likelihoods=likelihoods,
        contradictions=contradictions,
    )


async def evidence_likelihood(
    claim: str,
    evidence: str,
    *,
    supporting: bool = True,
    tier: str | RiskTier = RiskTier.READ,
    client: SystemOneClient | None = None,
) -> float | None:
    """Likelihood ratio for one piece of evidence. None = keep the caller's fixed value.

    Converts the calibrated probability into the odds form the engine already
    uses (``post_odds = prior_odds * LR``):

    * supporting evidence  -> ``LR = p / (1 - p)`` where p = P(supports)
    * contradicting        -> ``LR = (1 - q) / q`` where q = P(contradicts)

    Both give ``LR = 1`` at p = 0.5 — evidence that says nothing does nothing,
    which is the property a hand-tuned constant does not have.
    """
    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_epistemic or not cli.is_available():
        return None

    threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(build_state(claim, [evidence]), build_questions(1), min_confidence=threshold, site=f"{SITE}:likelihood")
    except Exception as exc:
        logger.debug("System One evidence likelihood failed (%s); falling back.", exc)
        return None
    if result is None:
        return None

    answer = result.get("s0") if supporting else result.get("c0")
    if answer is None or answer.type != "boolean":
        return None
    value = answer.boolean
    if value is None or abs(value - 0.5) * 2 < MIN_CONFIDENCE:
        return None
    clamped = min(0.99, max(0.01, value))
    return (clamped / (1.0 - clamped)) if supporting else ((1.0 - clamped) / clamped)


def evidence_likelihood_sync(claim: str, evidence: str, **kwargs: Any) -> float | None:
    """Sync wrapper; returns None (fall back) inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(evidence_likelihood(claim, evidence, **kwargs))
    logger.debug("evidence_likelihood_sync inside a running loop; falling back.")
    return None


def update_claim(claim_obj: Any, evidence: list[str], **kwargs: Any) -> Any:
    """Convenience for ``epistemics.models.Claim``: returns a new posterior or None.

    Sync wrapper; returns None inside a running loop rather than blocking it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.debug("update_claim inside a running loop; falling back.")
        return None
    try:
        text = str(getattr(claim_obj, "text", ""))
        prior = float(getattr(claim_obj, "bayesian_prior", 0.5) or 0.5)
        update = asyncio.run(update_belief(text, evidence, prior=prior, **kwargs))
    except Exception as exc:
        logger.debug("System One claim update failed (%s); falling back.", exc)
        return None
    return update.posterior if update else None


__all__ = [
    "MIN_CONFIDENCE",
    "YES_AT",
    "BeliefUpdate",
    "build_questions",
    "build_state",
    "evidence_likelihood",
    "evidence_likelihood_sync",
    "logit",
    "posterior_from",
    "sigmoid",
    "update_belief",
    "update_claim",
]
