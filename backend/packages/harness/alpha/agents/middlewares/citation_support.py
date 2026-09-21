"""Semantic citation-support layer on top of the receipt ledger.

``alpha.agents.middlewares.receipt_verification`` already answers a
**syntactic** question: does a cited ``[rN]`` id exist in the execution record?
It is deliberately "pure functions only — no IO, no LLM calls" and its verdict
is advisory.

This module adds the **semantic** question the syntactic check cannot answer:
*does the cited receipt actually support the sentence it is attached to?*

That is precisely TypeSafe's published citation cookbook, which flagged all
four planted failures in their test at confidence >= 0.93.

Returns ``None`` for "no verdict" — callers keep the existing deterministic
verdict unchanged in that case.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from alpha.config.system_one_config import RiskTier
from alpha.models.system_one import ChoiceQuestion, evaluate_many, get_system_one_client

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "citation"

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
CONTRADICTED = "contradicted"

VERDICTS = (SUPPORTED, UNSUPPORTED, CONTRADICTED)

#: Verdicts that mean the citation should not stand.
FAILING_VERDICTS = (UNSUPPORTED, CONTRADICTED)


@dataclass
class SupportVerdict:
    """Whether one cited piece of evidence supports one claim."""

    claim: str
    citation_id: str
    verdict: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "citation_id": self.citation_id,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "model": self.model,
        }


def _question() -> ChoiceQuestion:
    return ChoiceQuestion(
        instructions=(
            "Does `evidence` support `claim`? "
            "Judge only what the evidence actually shows, not whether the claim sounds plausible. "
            "Choose contradicted when the evidence shows the opposite of the claim."
        ),
        criteria={
            SUPPORTED: "The evidence directly shows or establishes the claim.",
            UNSUPPORTED: "The evidence exists but does not establish this claim; it is silent or off-topic.",
            CONTRADICTED: "The evidence shows the opposite of, or conflicts with, the claim.",
        },
    )


async def judge_support(
    claim: str,
    evidence: str,
    *,
    citation_id: str = "",
    client: Any = None,
    tier: str | RiskTier = RiskTier.READ,
    max_chars: int = 6_000,
) -> SupportVerdict | None:
    """Judge whether `evidence` supports `claim`.

    Returns None when System One is unavailable or not confident — the caller
    must then keep the existing deterministic verdict and must not treat None
    as a pass.
    """
    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_citation_support or not cli.is_available():
        return None
    if not claim or not evidence:
        return None

    try:
        answers = await evaluate_many(
            {"claim": claim[:2000], "evidence": evidence[:max_chars]},
            {"support": _question()},
            tier=tier,
            site=SITE,
            client=cli,
        )
    except Exception:
        logger.debug("System One citation support check unavailable; no verdict.", exc_info=True)
        return None

    answer = answers.get("support")
    if answer is None or not answer.validate(VERDICTS):
        return None

    return SupportVerdict(
        claim=claim,
        citation_id=citation_id,
        verdict=str(answer.value),
        confidence=answer.confidence or 0.0,
        probabilities=dict(answer.probabilities),
    )


async def judge_batch(
    pairs: list[tuple[str, str, str]],
    *,
    client: Any = None,
    tier: str | RiskTier = RiskTier.READ,
    max_pairs: int = 40,
) -> list[SupportVerdict]:
    """Judge several (citation_id, claim, evidence) triples in one request.

    System One takes **one** state per request, so the batch goes in as a
    structured `pairs` array and each question points at its own entry with a
    backticked path (`` `pairs[3].evidence` ``). That keeps this a single
    fan-out call: checking 20 citations costs about the same as checking one.

    Pairs with no confident verdict are omitted, so the caller keeps the
    deterministic verdict for those.
    """
    cli = client or get_system_one_client()
    cfg = cli.config
    if not cfg.enabled or not cfg.enable_citation_support or not cli.is_available() or not pairs:
        return []

    batch = list(pairs)[:max_pairs]
    state = {"pairs": [{"claim": claim[:2000], "evidence": evidence[:6000]} for _cid, claim, evidence in batch]}

    questions: dict[str, ChoiceQuestion] = {}
    for i in range(len(batch)):
        questions[f"c{i}"] = ChoiceQuestion(
            instructions=(
                f"Does `pairs[{i}].evidence` support `pairs[{i}].claim`? "
                "Judge only what the evidence actually shows, not whether the claim sounds plausible. "
                "Choose contradicted when the evidence shows the opposite of the claim."
            ),
            criteria={
                SUPPORTED: "The evidence directly shows or establishes the claim.",
                UNSUPPORTED: "The evidence exists but does not establish this claim; it is silent or off-topic.",
                CONTRADICTED: "The evidence shows the opposite of, or conflicts with, the claim.",
            },
        )

    try:
        answers = await evaluate_many(state, questions, tier=tier, site=SITE, client=cli)
    except Exception:
        logger.debug("System One batch citation check unavailable; no verdicts.", exc_info=True)
        return []

    verdicts: list[SupportVerdict] = []
    for i, (cid, claim, _evidence) in enumerate(pairs):
        answer = answers.get(f"c{i}")
        if answer is None or not answer.validate(VERDICTS):
            continue
        verdicts.append(
            SupportVerdict(
                claim=claim,
                citation_id=cid,
                verdict=str(answer.value),
                confidence=answer.confidence or 0.0,
                probabilities=dict(answer.probabilities),
            )
        )
    return verdicts
