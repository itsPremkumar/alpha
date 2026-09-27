"""Adaptive, guarded deliberation over swarm consensus votes.

Fixed-round consensus has two failure modes that agreement metrics cannot see.
It spends the full round budget even when the evidence settled two votes in,
and it reports a clean approval when one high-weight voter dominates or when
voters simply converged on the loudest position without new evidence.

This module fixes both, honestly:

* **Sequential stopping** applies a Wald sequential probability ratio test to
  the ordered votes.  It stops early *for or against* a configured target
  approval rate, or reports ``unresolved`` when the sample never reaches a
  boundary.  It never converts an undecided sample into an approval.
* **Guards can only block.**  Domination, evidence-free approval, and
  sycophancy each downgrade an otherwise-clean approval to ``manual_review``.
  No guard, and no amount of extra computation, can upgrade a rejection — the
  tests pin that direction, because a safety net that can raise a verdict is a
  verdict forger.

Rounds are optional: one batch of votes is a legitimate single round, so the
adaptive path is usable by existing single-shot callers.  Later rounds are
appended only when a caller genuinely polls again.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.swarm.consensus import ConsensusVote, VoteStance

__all__ = [
    "DeliberationPolicy",
    "DeliberationReport",
    "DeliberationRound",
    "run_deliberation",
]


@dataclass(frozen=True)
class DeliberationPolicy:
    """Sequential-test parameters and the guards' blocking thresholds."""

    target_approve_rate: float = 0.75
    indifference: float = 0.10
    alpha: float = 0.05
    beta: float = 0.10
    max_rounds: int = 5
    min_votes: int = 2
    domination_threshold: float = 2.0
    sycophancy_threshold: float = 0.50
    require_evidence: bool = False

    def __post_init__(self) -> None:
        if not 0.0 < float(self.target_approve_rate) < 1.0:
            raise ValueError("target_approve_rate must be between 0 and 1")
        if not 0.0 < float(self.indifference) < 1.0:
            raise ValueError("indifference must be between 0 and 1")
        p0 = self.target_approve_rate - self.indifference
        p1 = self.target_approve_rate + self.indifference
        if p0 <= 0.0 or p1 >= 1.0:
            raise ValueError("target_approve_rate +/- indifference must stay inside (0, 1)")
        if not 0.0 < float(self.alpha) < 1.0 or not 0.0 < float(self.beta) < 1.0:
            raise ValueError("alpha and beta must be between 0 and 1")
        if int(self.max_rounds) < 1:
            raise ValueError("max_rounds must be at least 1")
        if int(self.min_votes) < 1:
            raise ValueError("min_votes must be at least 1")
        if float(self.domination_threshold) < 1.0:
            raise ValueError("domination_threshold must be at least 1.0 (1.0 = a perfectly fair share)")
        if not 0.0 <= float(self.sycophancy_threshold) <= 1.0:
            raise ValueError("sycophancy_threshold must be in [0, 1]")

    @property
    def p0(self) -> float:
        return self.target_approve_rate - self.indifference

    @property
    def p1(self) -> float:
        return self.target_approve_rate + self.indifference

    @property
    def upper_bound(self) -> float:
        """Wald's ``A``: cross it and H1 (target rate reached) is accepted."""

        return math.log((1.0 - self.beta) / self.alpha)

    @property
    def lower_bound(self) -> float:
        """Wald's ``B``: cross it and H0 (rate below target) is accepted."""

        return math.log(self.beta / (1.0 - self.alpha))

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_approve_rate": self.target_approve_rate,
            "indifference": self.indifference,
            "alpha": self.alpha,
            "beta": self.beta,
            "max_rounds": self.max_rounds,
            "min_votes": self.min_votes,
            "domination_threshold": self.domination_threshold,
            "sycophancy_threshold": self.sycophancy_threshold,
            "require_evidence": self.require_evidence,
            "upper_bound": round(self.upper_bound, 6),
            "lower_bound": round(self.lower_bound, 6),
        }


@dataclass(frozen=True)
class DeliberationRound:
    """One polling round's ordered votes and the running log-likelihood ratio."""

    round_index: int
    approvals: int
    rejections: int
    abstains: int
    llr: float
    voters: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_index": self.round_index,
            "approvals": self.approvals,
            "rejections": self.rejections,
            "abstains": self.abstains,
            "llr": round(self.llr, 6),
            "voters": list(self.voters),
        }


@dataclass(frozen=True)
class DeliberationReport:
    """Outcome, stopping reason, and every guard that fired."""

    status: str
    approved: bool
    stop_reason: str
    rounds_used: int
    votes_used: int
    approvals: int
    rejections: int
    abstains: int
    llr: float
    guards: tuple[str, ...] = ()
    domination_ratio: float = 1.0
    sycophancy_rate: float = 0.0
    dissent: tuple[str, ...] = ()
    explanation: str = ""
    rounds: tuple[DeliberationRound, ...] = field(default_factory=tuple)
    policy: dict[str, Any] = field(default_factory=dict)

    @property
    def stopped_early(self) -> bool:
        return self.stop_reason in {"sprt_approved", "sprt_rejected"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "approved": self.approved,
            "stop_reason": self.stop_reason,
            "rounds_used": self.rounds_used,
            "votes_used": self.votes_used,
            "approvals": self.approvals,
            "rejections": self.rejections,
            "abstains": self.abstains,
            "llr": round(self.llr, 6),
            "guards": list(self.guards),
            "domination_ratio": round(self.domination_ratio, 6),
            "sycophancy_rate": round(self.sycophancy_rate, 6),
            "dissent": list(self.dissent),
            "explanation": self.explanation,
            "stopped_early": self.stopped_early,
            "rounds": [entry.to_dict() for entry in self.rounds],
            "policy": dict(self.policy),
        }


def _coerce(raw: ConsensusVote | Mapping[str, Any]) -> ConsensusVote:
    if isinstance(raw, ConsensusVote):
        return raw.normalized()
    stance = raw.get("stance", raw.get("vote", VoteStance.UNKNOWN))
    return ConsensusVote(
        voter=str(raw.get("voter", "")),
        stance=stance,
        reason=str(raw.get("reason", "")),
        evidence=raw.get("evidence", ()) or (),
        weight=float(raw.get("weight", 1.0) or 1.0),
        reputation=raw.get("reputation"),
    ).normalized()


def _dedupe_round(votes: Sequence[ConsensusVote]) -> list[ConsensusVote]:
    """Last vote per voter within one round; a re-poll is a legitimate update."""

    latest: dict[str, ConsensusVote] = {}
    for vote in votes:
        if vote.voter:
            latest[vote.voter] = vote
    return list(latest.values())


def _llr(observations: Sequence[int], p0: float, p1: float) -> float:
    approvals = sum(observations)
    rejections = len(observations) - approvals
    if not observations:
        return 0.0
    # Guard the degenerate ends so a boundary probability never becomes inf/nan.
    safe_p0 = min(max(p0, 1e-9), 1.0 - 1e-9)
    safe_p1 = min(max(p1, 1e-9), 1.0 - 1e-9)
    return approvals * math.log(safe_p1 / safe_p0) + rejections * math.log((1.0 - safe_p1) / (1.0 - safe_p0))


def _domination_ratio(votes: Sequence[ConsensusVote]) -> float:
    """Largest voter's weight as a multiple of a fair share (1.0 = perfectly even).

    A raw share is the wrong measure: two equal voters *always* hold 50% each,
    which would flag every healthy two-voter panel.  Multiplying the largest
    share by the number of voters removes that baseline, so 1.0 means "exactly
    fair" and only genuine concentration trips the guard.
    """

    weights = [vote.effective_weight for vote in votes if vote.effective_weight > 0]
    if not weights:
        return 1.0
    # Aggregate per voter: a voter re-polling in a later round must not be
    # counted as several independent influencers.
    peak: dict[str, float] = {}
    for vote in votes:
        if vote.effective_weight <= 0:
            continue
        peak[vote.voter] = max(peak.get(vote.voter, 0.0), vote.effective_weight)
    distinct = len(peak)
    if distinct <= 1:
        return 1.0
    total = sum(peak.values())
    if total <= 0:
        return 1.0
    return (max(peak.values()) / total) * distinct


def _sycophancy_rate(rounds: Sequence[Sequence[ConsensusVote]]) -> float:
    """Share of stance changes that move toward the prior plurality with no new evidence.

    A voter switching position *because the argument improved* carries a new
    evidence handle; a voter switching because everyone else already had is the
    invisible failure mode the consensus score would otherwise celebrate.
    """

    if len(rounds) < 2:
        return 0.0
    changes = 0
    sycophantic = 0
    previous: dict[str, ConsensusVote] = {}
    for current_round in rounds:
        current = {vote.voter: vote for vote in current_round}
        if previous:
            prior_counts: dict[str, int] = {}
            for vote in previous.values():
                prior_counts[vote.stance.value] = prior_counts.get(vote.stance.value, 0) + 1
            plurality = max(sorted(prior_counts), key=lambda stance: prior_counts[stance]) if prior_counts else None
            for voter, vote in current.items():
                before = previous.get(voter)
                if before is None or before.stance == vote.stance:
                    continue
                if before.stance == VoteStance.ABSTAIN or vote.stance == VoteStance.ABSTAIN:
                    continue
                changes += 1
                toward_plurality = plurality is not None and vote.stance.value == plurality and before.stance.value != plurality
                new_evidence = set(vote.evidence) - set(before.evidence)
                if toward_plurality and not new_evidence:
                    sycophantic += 1
        previous = current
    return (sycophantic / changes) if changes else 0.0


def run_deliberation(
    rounds: Sequence[Sequence[ConsensusVote | Mapping[str, Any]]],
    policy: DeliberationPolicy | None = None,
) -> DeliberationReport:
    """Run sequential deliberation over one or more polling rounds.

    Rounds are consumed in order and the test stops as soon as a boundary is
    crossed, so ``rounds_used`` below ``len(rounds)`` is the saved work.
    """

    policy = policy or DeliberationPolicy()
    normalized_rounds: list[list[ConsensusVote]] = []
    for raw_round in rounds or ():
        normalized_rounds.append(_dedupe_round([_coerce(vote) for vote in raw_round]))

    # Bounded: a caller that hands over more polls than the policy allows gets
    # the policy's cap, so extra rounds cannot quietly buy extra deliberation.
    truncated = normalized_rounds[: policy.max_rounds]

    observations: list[int] = []
    round_entries: list[DeliberationRound] = []
    approvals = rejections = abstains = 0
    voters_seen: list[str] = []
    stop_reason = "max_rounds"
    llr = 0.0
    rounds_used = 0

    for index, current_round in enumerate(truncated, start=1):
        rounds_used = index
        for vote in current_round:
            if vote.voter and vote.voter not in voters_seen:
                voters_seen.append(vote.voter)
            if vote.stance == VoteStance.APPROVE:
                approvals += 1
                observations.append(1)
            elif vote.stance == VoteStance.REJECT:
                rejections += 1
                observations.append(0)
            else:
                abstains += 1
        llr = _llr(observations, policy.p0, policy.p1)
        round_entries.append(
            DeliberationRound(
                round_index=index,
                approvals=approvals,
                rejections=rejections,
                abstains=abstains,
                llr=llr,
                voters=tuple(voters_seen),
            )
        )
        if len(observations) < policy.min_votes:
            stop_reason = "min_votes_pending"
            continue
        if llr >= policy.upper_bound:
            stop_reason = "sprt_approved"
            break
        if llr <= policy.lower_bound:
            stop_reason = "sprt_rejected"
            break
    else:
        # No boundary was crossed.  Distinguish "we ran out of polls" from
        # "there was never enough signal to test", because they call for
        # different operator actions.
        stop_reason = "insufficient_votes" if (approvals + rejections) < policy.min_votes else "max_rounds"

    counted = approvals + rejections
    dissent = tuple(sorted({vote.voter for round_votes in truncated for vote in round_votes if vote.stance == VoteStance.REJECT and vote.voter}))

    # Domination is measured over every counted vote, not only approvals:
    # weight concentration is about influence, and abstaining still costs weight.
    all_votes = [vote for round_votes in truncated for vote in round_votes]
    domination = _domination_ratio(all_votes)
    sycophancy = _sycophancy_rate(truncated)

    guards: list[str] = []
    if domination > policy.domination_threshold:
        guards.append("domination")
    if sycophancy > policy.sycophancy_threshold:
        guards.append("sycophancy")
    if policy.require_evidence and approvals and any(vote.stance == VoteStance.APPROVE and not vote.evidence for round_votes in truncated for vote in round_votes):
        guards.append("evidence_free_approval")
    if counted < policy.min_votes:
        guards.append("insufficient_votes")

    if stop_reason == "sprt_approved" and not guards:
        status, approved = "approved", True
        explanation = f"sequential test accepted H1 (approve rate >= {policy.p1:.2f}) after {counted} counted votes"
    elif stop_reason == "sprt_approved":
        status, approved = "manual_review", False
        explanation = f"sequential test accepted H1 after {counted} counted votes, but guards blocked approval: {', '.join(guards)}"
    elif stop_reason == "sprt_rejected":
        status, approved = "rejected", False
        explanation = f"sequential test accepted H0 (approve rate <= {policy.p0:.2f}) after {counted} counted votes"
    elif guards:
        status, approved = "unresolved", False
        explanation = f"sample never reached a boundary (llr={llr:.3f} within [{policy.lower_bound:.3f}, {policy.upper_bound:.3f}]) and guards fired: {', '.join(guards)}"
    else:
        status, approved = "unresolved", False
        explanation = f"sample never reached a boundary (llr={llr:.3f} within [{policy.lower_bound:.3f}, {policy.upper_bound:.3f}]) after {rounds_used} round(s)"

    if not normalized_rounds:
        status, approved = "unresolved", False
        stop_reason = "no_votes"
        guards = sorted(set(guards) | {"insufficient_votes"})
        explanation = "no votes were supplied; deliberation cannot produce a verdict"

    return DeliberationReport(
        status=status,
        approved=approved,
        stop_reason=stop_reason,
        rounds_used=rounds_used,
        votes_used=len(all_votes),
        approvals=approvals,
        rejections=rejections,
        abstains=abstains,
        llr=llr,
        guards=tuple(sorted(set(guards))),
        domination_ratio=domination,
        sycophancy_rate=sycophancy,
        dissent=dissent,
        explanation=explanation,
        rounds=tuple(round_entries),
        policy=policy.to_dict(),
    )


def rounds_from_evidence(evidence: Iterable[Mapping[str, Any]]) -> list[list[ConsensusVote]]:
    """Group raw consensus evidence into polling rounds.

    The vote filter matches :func:`alpha.swarm.consensus.votes_from_evidence`
    exactly, so deliberation sees the same records the explicit policy counted
    rather than a quietly different set.  An entry carrying an explicit
    ``round``/``poll`` index becomes its own round in ascending order; entries
    without one form round 1.  Missing structure stays missing — this never
    fabricates a second round, because a fake round would let the sequential
    test appear to converge on evidence that was only ever observed once.

    Vote records with no declared voter get a positional identity.  They are
    counted (they are real votes) but can never dedupe against each other,
    because their actual identity is unknown and inventing a shared one would
    collapse distinct voters into one.
    """

    grouped: dict[int, list[ConsensusVote]] = {}
    position = 0
    for entry in evidence or ():
        if not isinstance(entry, Mapping):
            continue
        if entry.get("kind") not in {"consensus_vote", "vote"} and "stance" not in entry and "vote" not in entry:
            continue
        position += 1
        raw_round = entry.get("round", entry.get("poll", 1))
        try:
            index = max(1, int(raw_round))
        except (TypeError, ValueError):
            index = 1
        payload = dict(entry)
        if not str(payload.get("voter") or payload.get("agent") or "").strip():
            payload["voter"] = f"anonymous-{position}"
        vote = _coerce(payload)
        if not vote.voter:
            continue
        grouped.setdefault(index, []).append(vote)
    if not grouped:
        return []
    return [grouped[key] for key in sorted(grouped)]
