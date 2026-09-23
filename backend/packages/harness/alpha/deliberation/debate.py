"""Multi-Agent Sparse Debate Engine.

Features (all driven by REAL model calls through ``alpha.deliberation.invocation``):
- Multi-round adversarial exchange: every turn is a real invocation, and each
  rebuttal sees the opponent's actual previous argument (the old code filled
  both sides' arguments from templates and never exchanged real information).
- Early stopping & convergence detection from real token overlap between a
  speaker's successive positions (the old similarities were constants 0.86/0.88).
- Independent Judge verdict parsed from the judge model's actual ruling — the
  old engine hardcoded "Proponent" as the winner. An unparsable ruling is
  reported as NO RULING and never invented.
- Minority dissent preserves the losing side's real closing argument (the old
  dissent was a canned sentence unrelated to the debate).
"""

from __future__ import annotations

import logging
import time
import uuid

from alpha.deliberation import invocation, parsing
from alpha.deliberation.models import (
    DebateTurn,
    DeliberationConfidence,
    DeliberationResult,
    DeliberationStrategy,
    make_deliberation_id,
)

logger = logging.getLogger(__name__)

_PROPONENT_SYSTEM = (
    "You are the PROPONENT in a structured debate. Argue FOR the affirmative position "
    "with concrete reasons. If you have supporting data, end your reply with a line of "
    "the exact form: EVIDENCE: <item> | <item>"
)

_CRITIC_SYSTEM = (
    "You are the CRITIC (Opponent) in a structured debate. Argue AGAINST the proponent, "
    "rebutting their specific points, and raise failure modes and trade-offs. If you have "
    "supporting data, end your reply with a line of the exact form: EVIDENCE: <item> | <item>"
)

_JUDGE_SYSTEM = (
    "You are an INDEPENDENT JUDGE in a structured debate. Weigh both sides on substance. "
    "Output EXACTLY these two lines:\n"
    "WINNER: PROPONENT   (or WINNER: CRITIC)\n"
    "RATIONALE: <one sentence>"
)


class DebateEngine:
    """Executes multi-round sparse debate with convergence-based early stopping."""

    MAX_ROUNDS = 3
    CONVERGENCE_THRESHOLD = 0.85

    @classmethod
    def run_debate(
        cls,
        query: str,
        roster: list[str] | None = None,
        max_rounds: int = 3,
    ) -> DeliberationResult:
        start_time = time.time()
        deliberation_id = make_deliberation_id()
        models = roster or invocation.configured_model_roster()
        if len(models) < 2:
            raise RuntimeError(
                f"Debate requires at least 2 configured chat models; found {len(models)}. "
                "Add another model under `models:` in config.yaml."
            )

        debaters = [models[0], models[1]]
        # Judge: a third configured model when available; otherwise disclose
        # the fallback instead of naming a model that does not exist
        # (the old default roster was ["advocate-alpha", "critic-beta",
        # "independent-judge"] — none resolvable).
        if len(models) > 2:
            judge_model = models[2]
            judge_note = ""
        else:
            judge_model = models[0]
            judge_note = (
                f" Judge fallback: no third model is configured, so {judge_model} "
                "(a debater) served as judge."
            )

        turns: list[DebateTurn] = []
        stopped_early = False
        final_round = 1
        last_proponent = ""
        last_critic = ""

        # ---------------------------------------------------------------------
        # Multi-Round Sparse Debate Loop (each turn = one REAL invocation)
        # ---------------------------------------------------------------------
        for r in range(1, min(max_rounds, cls.MAX_ROUNDS) + 1):
            final_round = r

            # Turn 1: Proponent — later rounds rebut the critic's actual argument.
            prop_user = f"QUERY: {query}"
            if last_critic:
                prop_user += (
                    f"\n\nThe opponent's latest argument:\n{last_critic.strip()}\n\n"
                    "Rebut it and advance the affirmative case."
                )
            prop_text = invocation.invoke_model(debaters[0], system=_PROPONENT_SYSTEM, user=prop_user)
            prop_sim = (
                invocation.response_similarity(prop_text, last_proponent) if last_proponent else 0.0
            )
            t1 = DebateTurn(
                turn_id=f"turn-{uuid.uuid4().hex[:6]}",
                round_number=r,
                speaker_candidate_id=debaters[0],
                speaker_label="Proponent",
                target_candidate_id=debaters[1],
                argument=prop_text.strip(),
                rebuttal_to=turns[-1].turn_id if turns else None,
                new_evidence=parsing.parse_evidence(prop_text),
                similarity_with_previous=prop_sim,
            )
            turns.append(t1)
            last_proponent = prop_text

            # Turn 2: Critic — always sees the proponent's real argument.
            crit_user = (
                f"QUERY: {query}\n\nThe proponent's position:\n{prop_text.strip()}\n\n"
                "Argue AGAINST it, rebutting specifics."
            )
            crit_text = invocation.invoke_model(debaters[1], system=_CRITIC_SYSTEM, user=crit_user)
            crit_sim = (
                invocation.response_similarity(crit_text, last_critic) if last_critic else 0.0
            )
            t2 = DebateTurn(
                turn_id=f"turn-{uuid.uuid4().hex[:6]}",
                round_number=r,
                speaker_candidate_id=debaters[1],
                speaker_label="Opponent/Critic",
                target_candidate_id=debaters[0],
                argument=crit_text.strip(),
                rebuttal_to=t1.turn_id,
                new_evidence=parsing.parse_evidence(crit_text),
                similarity_with_previous=crit_sim,
            )
            turns.append(t2)
            last_critic = crit_text

            # Convergence Check: Early Stopping — both sides stuck to their
            # previous positions (real token overlap, not a constant).
            if (
                r > 1
                and prop_sim >= cls.CONVERGENCE_THRESHOLD
                and crit_sim >= cls.CONVERGENCE_THRESHOLD
            ):
                stopped_early = True
                break

        # ---------------------------------------------------------------------
        # Independent Judge Verdict (parsed, never invented)
        # ---------------------------------------------------------------------
        transcript = "\n\n".join(
            f"[Round {t.round_number}] {t.speaker_label}: {t.argument}" for t in turns
        )
        judge_text = invocation.invoke_model(
            judge_model,
            system=_JUDGE_SYSTEM,
            user=f"QUERY: {query}\n\nDEBATE TRANSCRIPT:\n{transcript}",
        )
        winner, judge_rationale = parsing.parse_judge_verdict(judge_text)

        proponent_turns = [t for t in turns if t.speaker_label == "Proponent"]
        critic_turns = [t for t in turns if t.speaker_label == "Opponent/Critic"]

        if winner is None:
            logger.warning(
                "Debate judge output unparsable; recording NO ruling instead of inventing one."
            )
            winner_label = "Unresolved (judge ruling unparsed)"
            judge_ruling = (
                "The judge's response could not be parsed into a WINNER line, so NO ruling "
                f"is recorded. Judge output excerpt: {judge_text.strip()[:200]}"
            )
            winning_turn = proponent_turns[-1]
        else:
            winner_label = "Proponent" if winner == "proponent" else "Critic"
            judge_ruling = (
                f"Judge ({judge_model}) Verdict: "
                f"{judge_rationale or (winner_label + ' favoured on the merits')}."
                + judge_note
            )
            winning_turn = proponent_turns[-1] if winner == "proponent" else critic_turns[-1]

        # Real consensus: positional overlap between the final opposing sides.
        consensus_pct = round(
            invocation.response_similarity(last_proponent, last_critic) * 100.0, 1
        )

        # Minority report = the LOSING side's real closing argument.
        if winner == "critic":
            minority_who, minority_turn = "Proponent", proponent_turns[-1]
        else:
            # No ruling -> preserve the critic's closing (the opposition).
            minority_who, minority_turn = "Critic", critic_turns[-1]
        minority_dissent = (
            f"Minority report — {minority_who} closing position preserved: "
            f"{minority_turn.argument.strip()[:400]}"
        )

        # Documented confidence model over REAL signals:
        #   base 0.5 + 0.3 * positional consensus + 0.15 for a parsed ruling
        #   (cap 0.95); an unparsed ruling leaves only the consensus term with
        #   a 0.35 floor. The old engine returned a constant 0.91.
        if winner is None:
            confidence = round(max(0.35, consensus_pct / 100.0), 3)
            confidence_level = DeliberationConfidence.LOW_CONFIDENCE
        else:
            confidence = round(min(0.95, 0.5 + 0.3 * (consensus_pct / 100.0) + 0.15), 3)
            confidence_level = (
                DeliberationConfidence.HIGH_CONFIDENCE
                if confidence >= 0.8
                else DeliberationConfidence.MEDIUM_CONFIDENCE
            )

        # Evidence actually cited by the debaters (parsed), deduplicated.
        key_evidence = sorted({item for t in turns for item in t.new_evidence})

        final_answer = (
            f"### ⚖️ Multi-Agent Debate Verdict ({final_round} Rounds"
            f"{' - Early Convergence' if stopped_early else ''})\n\n"
            f"**Winning Position ({winner_label})**:\n"
            f"{winning_turn.argument}\n\n"
            f"**Judge Ruling**:\n"
            f"{judge_ruling}\n\n"
            f"**Reconciled Arguments**:\n"
            + "\n".join(
                f"- Round {t.round_number} [{t.speaker_label}]: {t.argument[:90]}..."
                for t in turns[-4:]
            )
        )

        def _verdict(label: str) -> str:
            if winner is None:
                return "unresolved"
            if (winner == "proponent") == (label == "Proponent"):
                return "winner"
            return "dissenting_advocate"

        return DeliberationResult(
            deliberation_id=deliberation_id,
            query=query,
            strategy_used=DeliberationStrategy.DEBATE,
            final_answer=final_answer,
            confidence_score=confidence,
            confidence_level=confidence_level,
            consensus_percentage=consensus_pct,
            key_evidence=key_evidence,
            minority_dissent=minority_dissent,
            candidate_rankings=[
                {"label": "Proponent", "model_id": debaters[0], "verdict": _verdict("Proponent")},
                {"label": "Critic", "model_id": debaters[1], "verdict": _verdict("Critic")},
            ],
            verdict_rationale=judge_ruling,
            verification_status="consensus_supported",
            duration_seconds=round(time.time() - start_time, 2),
        )
