"""Master Deliberation Engine: Orchestrates Router, Council, Debate & Verifier.

Coordinates the complete deliberation pipeline:
1. Task classification & strategy routing (Single vs Ensemble vs Council vs Debate)
2. Concurrent generation & peer review / debate rounds
3. Independent Verification & Contradiction calibration
4. Emits structured DeliberationResult with calibrated confidence and minority reports

Every strategy runs REAL model calls through :mod:`alpha.deliberation.invocation`;
the ensemble contributions, consensus and confidence are computed from those
real responses. With no chat models configured — or a forced multi-model
strategy on a single-model config — ``deliberate`` raises instead of
returning a fabricated success.
"""

from __future__ import annotations

import logging
import time

from alpha.deliberation import invocation
from alpha.deliberation.council import CouncilEngine
from alpha.deliberation.debate import DebateEngine
from alpha.deliberation.models import (
    DeliberationConfidence,
    DeliberationResult,
    DeliberationStrategy,
    make_deliberation_id,
)
from alpha.deliberation.router import DeliberationRouter
from alpha.deliberation.verifier import DeliberationVerifier

logger = logging.getLogger(__name__)

_GLOBAL_DELIBERATION_ENGINE: MasterDeliberationEngine | None = None


def get_master_deliberation_engine() -> MasterDeliberationEngine:
    """Returns singleton instance of MasterDeliberationEngine."""
    global _GLOBAL_DELIBERATION_ENGINE
    if _GLOBAL_DELIBERATION_ENGINE is None:
        _GLOBAL_DELIBERATION_ENGINE = MasterDeliberationEngine()
    return _GLOBAL_DELIBERATION_ENGINE


class MasterDeliberationEngine:
    """Universal Deliberation Engine coordinating Multi-LLM Councils and Debates."""

    def __init__(self):
        self.router = DeliberationRouter()

    def deliberate(
        self,
        prompt: str,
        strategy: DeliberationStrategy | str = DeliberationStrategy.AUTO,
        max_rounds: int = 3,
        code_test_command: str | None = None,
        roster: list[str] | None = None,
    ) -> DeliberationResult:
        """Main entry point: classifies prompt, selects strategy, executes deliberation, and verifies output.

        Args:
            prompt: The query to deliberate.
            strategy: Requested strategy; ``auto`` lets the router choose.
            max_rounds: Debate round ceiling.
            code_test_command: Optional shell command executed for REAL
                deterministic verification (see ``DeliberationVerifier``).
            roster: Optional explicit model roster; defaults to the router's
                roster built from the configured models.
        """
        start_time = time.time()

        # 1. Strategic Routing
        eval_result = self.router.classify_smart(prompt, user_strategy=strategy)
        selected = eval_result.strategy
        effective_roster = list(roster) if roster is not None else list(eval_result.roster_models)
        forced = strategy != DeliberationStrategy.AUTO

        if not effective_roster:
            raise RuntimeError("No chat models configured; deliberation cannot run.")

        # Capability down-rank: a peer-reviewed Council/Debate is impossible
        # with fewer than 2 distinct models. An AUTO request falls back to a
        # multi-perspective Ensemble (disclosed); an explicitly forced
        # strategy raises so the caller hears the truth instead of a fiction.
        if selected in (DeliberationStrategy.COUNCIL, DeliberationStrategy.DEBATE) and len(effective_roster) < 2:
            if forced:
                raise RuntimeError(
                    f"{selected.value} requires at least 2 configured chat models; "
                    f"found {len(effective_roster)}. Add another model under `models:` in config.yaml."
                )
            selected = DeliberationStrategy.ENSEMBLE
            eval_result.rationale += (
                f" Down-ranked to ensemble: {selected.value} needs >=2 configured models "
                f"(found {len(effective_roster)})."
            )

        # 2. Execution across Selected Strategy
        if selected == DeliberationStrategy.SINGLE:
            result = self._execute_single(prompt, effective_roster, start_time)
        elif selected == DeliberationStrategy.ENSEMBLE:
            result = self._execute_ensemble(prompt, effective_roster, start_time)
        elif selected == DeliberationStrategy.DEBATE:
            result = DebateEngine.run_debate(
                prompt, roster=effective_roster, max_rounds=max_rounds
            )
        else:  # COUNCIL / DEFAULT
            result = CouncilEngine.run_council(prompt, roster=effective_roster)

        # 3. Apply Verifier Hierarchy
        verified_result = DeliberationVerifier.verify_and_calibrate(result, code_test_command=code_test_command)
        return verified_result

    def _execute_single(self, prompt: str, roster: list[str], start_time: float) -> DeliberationResult:
        """Fast-path execution with zero deliberation overhead — one REAL call."""
        deliberation_id = make_deliberation_id()
        model = roster[0]

        answer = invocation.invoke_model(
            model,
            system=(
                "You are a direct, accurate assistant. Answer the user's query "
                "concisely and correctly. Do not mention deliberation."
            ),
            user=prompt,
        )

        # Honest bookkeeping: with one model there is no cross-check, so the
        # confidence stays at the neutral unverified baseline (0.5) until the
        # verifier can lift it with a real deterministic check — not the old
        # fabricated constant of 0.95/"verified".
        return DeliberationResult(
            deliberation_id=deliberation_id,
            query=prompt,
            strategy_used=DeliberationStrategy.SINGLE,
            final_answer=answer,
            confidence_score=0.5,
            confidence_level=DeliberationConfidence.MEDIUM_CONFIDENCE,
            consensus_percentage=100.0,
            key_evidence=[],
            minority_dissent=None,
            candidate_rankings=[
                {"label": "Single model", "model_id": model, "score": None}
            ],
            verdict_rationale=(
                f"Single-model fast path answered by {model}; no cross-check was performed, "
                "so confidence is held at the neutral baseline until verified."
            ),
            verification_status="consensus_supported",
            duration_seconds=round(time.time() - start_time, 3),
        )

    def _execute_ensemble(self, prompt: str, roster: list[str], start_time: float) -> DeliberationResult:
        """Parallel scatter-gather ensemble over REAL responses.

        Each configured model (cycled across the three focus perspectives when
        fewer models than perspectives are configured) answers for real; the
        synthesis, rankings and consensus are computed from those responses.
        """
        deliberation_id = make_deliberation_id()
        perspectives = [
            ("Proposer: system structure", "Focus on system architecture, boundaries, and structure."),
            ("Proposer: failure modes", "Focus on failure modes, edge conditions, and invariants."),
            ("Proposer: efficiency", "Focus on execution efficiency, latency, and resource use."),
        ]

        responses: list[dict] = []
        for i, (label, focus) in enumerate(perspectives):
            model = roster[i % len(roster)]
            text = invocation.invoke_model(
                model,
                system=(
                    f"You are one perspective in a parallel ensemble answering the same query. "
                    f"{focus} Give your independent take in a few sentences."
                ),
                user=prompt,
            )
            responses.append({"label": label, "model_id": model, "response": text})

        # Real consensus: mean pairwise token overlap across actual responses.
        n = len(responses)
        pairwise = [
            invocation.response_similarity(responses[i]["response"], responses[j]["response"])
            for i in range(n)
            for j in range(i + 1, n)
        ]
        consensus = round((sum(pairwise) / len(pairwise)) * 100.0, 1) if pairwise else 100.0

        # Each response's ranking score = its mean similarity to the others.
        rankings = []
        for i, r in enumerate(responses):
            others = [
                invocation.response_similarity(r["response"], responses[j]["response"])
                for j in range(n)
                if j != i
            ]
            score = round(sum(others) / len(others), 3) if others else 1.0
            rankings.append(
                {"label": r["label"], "model_id": r["model_id"], "score": score}
            )

        answer_lines = ["### Parallel Ensemble Consensus"]
        for r in responses:
            answer_lines.append(f"**{r['label']}** ({r['model_id']}):")
            answer_lines.append(r["response"].strip())
            answer_lines.append("")
        answer = "\n".join(answer_lines)

        # Confidence derived from the real consensus signal (documented):
        # baseline 0.4 up to 0.9 as mean pairwise overlap rises from 0 to 1.
        confidence = round(min(0.9, 0.4 + 0.5 * (consensus / 100.0)), 3)

        return DeliberationResult(
            deliberation_id=deliberation_id,
            query=prompt,
            strategy_used=DeliberationStrategy.ENSEMBLE,
            final_answer=answer,
            confidence_score=confidence,
            confidence_level=(
                DeliberationConfidence.HIGH_CONFIDENCE
                if confidence >= 0.8
                else DeliberationConfidence.MEDIUM_CONFIDENCE
            ),
            consensus_percentage=consensus,
            key_evidence=[
                f"{r['label']} ({r['model_id']}): {r['response'].strip()[:120]}"
                for r in responses
            ],
            minority_dissent=None,
            candidate_rankings=rankings,
            verdict_rationale=(
                f"{n} real model responses aggregated; consensus {consensus}% computed "
                "from mean pairwise token overlap."
            ),
            verification_status="consensus_supported",
            duration_seconds=round(time.time() - start_time, 3),
        )
