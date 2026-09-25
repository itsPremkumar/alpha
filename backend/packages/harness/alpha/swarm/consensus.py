"""Evidence-aware consensus primitives for swarm outputs.

Consensus is deliberately an explicit, inspectable calculation.  It does not
turn agreement into truth: callers provide votes, optional weights, and
(optional) evidence references, and the result discloses missing inputs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class VoteStance(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConsensusVote:
    voter: str
    stance: VoteStance | str
    reason: str = ""
    evidence: tuple[str, ...] = ()
    weight: float = 1.0
    reputation: float | None = None

    def normalized(self) -> ConsensusVote:
        raw = self.stance.value if isinstance(self.stance, VoteStance) else str(self.stance).strip().lower()
        if raw in {"approve", "accept", "accepted", "pass", "passed", "true", "yes", "+1", "for"}:
            stance = VoteStance.APPROVE
        elif raw in {"reject", "rejected", "fail", "failed", "false", "no", "-1", "against"}:
            stance = VoteStance.REJECT
        elif raw in {"abstain", "abstained", "neutral", "skip"}:
            stance = VoteStance.ABSTAIN
        else:
            stance = VoteStance.UNKNOWN
        weight = max(0.0, float(self.weight))
        reputation = None if self.reputation is None else max(0.0, float(self.reputation))
        return ConsensusVote(
            voter=str(self.voter),
            stance=stance,
            reason=str(self.reason or ""),
            evidence=tuple(str(item) for item in self.evidence if item),
            weight=weight,
            reputation=reputation,
        )

    @property
    def effective_weight(self) -> float:
        normalized = self.normalized()
        if normalized.reputation is None:
            return normalized.weight
        return normalized.weight * normalized.reputation

    def to_dict(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {
            "voter": normalized.voter,
            "stance": normalized.stance.value,
            "reason": normalized.reason,
            "evidence": list(normalized.evidence),
            "weight": normalized.weight,
            "reputation": normalized.reputation,
            "effective_weight": normalized.effective_weight,
        }


@dataclass(frozen=True)
class ConsensusPolicy:
    min_voters: int = 2
    quorum: float = 0.5
    threshold: float = 0.75
    require_evidence: bool = False

    def __post_init__(self) -> None:
        if int(self.min_voters) < 1:
            raise ValueError("min_voters must be at least 1")
        if not 0.0 <= float(self.quorum) <= 1.0:
            raise ValueError("quorum must be between 0 and 1")
        if not 0.0 <= float(self.threshold) <= 1.0:
            raise ValueError("threshold must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_voters": self.min_voters,
            "quorum": self.quorum,
            "threshold": self.threshold,
            "require_evidence": self.require_evidence,
        }


@dataclass
class ConsensusResult:
    status: str
    approved: bool
    total_voters: int
    counted_voters: int
    approve_weight: float
    reject_weight: float
    abstain_weight: int
    agreement: float | None
    quorum_met: bool
    threshold_met: bool
    policy: dict[str, Any]
    votes: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    method: str = "weighted-explicit-votes-v1"
    duplicate_voters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "approved": self.approved,
            "total_voters": self.total_voters,
            "counted_voters": self.counted_voters,
            "approve_weight": round(self.approve_weight, 6),
            "reject_weight": round(self.reject_weight, 6),
            "abstain_weight": self.abstain_weight,
            "agreement": None if self.agreement is None else round(self.agreement, 6),
            "quorum_met": self.quorum_met,
            "threshold_met": self.threshold_met,
            "policy": self.policy,
            "votes": self.votes,
            "reason": self.reason,
            "method": self.method,
            "duplicate_voters": sorted(set(self.duplicate_voters)),
        }


def _coerce_vote(raw: ConsensusVote | Mapping[str, Any]) -> ConsensusVote:
    if isinstance(raw, ConsensusVote):
        return raw.normalized()
    if not isinstance(raw, Mapping):
        raise ValueError("vote must be a ConsensusVote or mapping")
    evidence = raw.get("evidence", ())
    if isinstance(evidence, str):
        evidence = (evidence,)
    return ConsensusVote(
        voter=str(raw.get("voter", raw.get("agent", "unknown"))),
        stance=raw.get("stance", raw.get("vote", raw.get("decision", "unknown"))),
        reason=str(raw.get("reason", "")),
        evidence=tuple(str(item) for item in evidence if item) if isinstance(evidence, Iterable) else (),
        weight=float(raw.get("weight", 1.0)),
        reputation=None if raw.get("reputation") is None else float(raw["reputation"]),
    ).normalized()


def evaluate_consensus(
    votes: Iterable[ConsensusVote | Mapping[str, Any]],
    policy: ConsensusPolicy | None = None,
) -> ConsensusResult:
    """Evaluate explicit votes with quorum, threshold, and evidence disclosure."""

    policy = policy or ConsensusPolicy()
    normalized: list[ConsensusVote] = []
    seen_voters: set[str] = set()
    duplicate_voters: list[str] = []
    for raw_vote in votes:
        vote = _coerce_vote(raw_vote)
        if vote.voter in seen_voters:
            duplicate_voters.append(vote.voter)
            # Preserve the submitted row for audit, but make it unusable so a
            # single voter cannot manufacture quorum by repeating itself.
            vote = replace(vote, stance=VoteStance.UNKNOWN)
        else:
            seen_voters.add(vote.voter)
        normalized.append(vote)
    total_voters = len(normalized)
    counted = [vote for vote in normalized if vote.stance != VoteStance.UNKNOWN]
    approve_weight = sum(vote.effective_weight for vote in counted if vote.stance == VoteStance.APPROVE)
    reject_weight = sum(vote.effective_weight for vote in counted if vote.stance == VoteStance.REJECT)
    abstain_weight = sum(1 for vote in counted if vote.stance == VoteStance.ABSTAIN)
    unknown_weight = sum(vote.effective_weight for vote in normalized if vote.stance == VoteStance.UNKNOWN)
    counted_weight = approve_weight + reject_weight
    agreement = approve_weight / counted_weight if counted_weight > 0 else None
    quorum_met = total_voters > 0 and (len(counted) / total_voters) >= policy.quorum
    threshold_met = agreement is not None and agreement >= policy.threshold
    missing_evidence = [vote.voter for vote in counted if policy.require_evidence and not vote.evidence]
    approved = len(counted) >= policy.min_voters and quorum_met and threshold_met and not missing_evidence and unknown_weight == 0
    if not total_voters:
        status = "unresolved"
        reason = "no votes were supplied"
    elif len(counted) < policy.min_voters:
        status = "unresolved"
        reason = f"only {len(counted)} usable voters; minimum is {policy.min_voters}"
    elif missing_evidence:
        status = "unresolved"
        reason = f"missing evidence from: {', '.join(missing_evidence)}"
    elif duplicate_voters:
        status = "unresolved"
        reason = f"duplicate voter submissions: {', '.join(sorted(set(duplicate_voters)))}"
    elif unknown_weight > 0:
        status = "unresolved"
        reason = "one or more votes had an unknown stance"
    elif not quorum_met:
        status = "rejected"
        reason = "quorum was not met"
    elif not threshold_met:
        status = "rejected"
        reason = "approval threshold was not met"
    else:
        status = "approved"
        reason = "quorum and weighted threshold met"
    return ConsensusResult(
        status=status,
        approved=approved,
        total_voters=total_voters,
        counted_voters=len(counted),
        approve_weight=approve_weight,
        reject_weight=reject_weight,
        abstain_weight=abstain_weight,
        agreement=agreement,
        quorum_met=quorum_met,
        threshold_met=threshold_met,
        policy=policy.to_dict(),
        votes=[vote.to_dict() for vote in normalized],
        reason=reason,
        duplicate_voters=duplicate_voters,
    )


def votes_from_evidence(evidence: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract only explicitly marked vote records from task evidence."""

    votes: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") not in {"consensus_vote", "vote"} and "stance" not in item and "vote" not in item:
            continue
        votes.append(dict(item))
    return votes


def parse_structured_vote(text: str) -> ConsensusVote | None:
    """Parse a deliberately narrow JSON vote envelope, if a worker emitted one."""

    candidate = str(text or "").strip()
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        match = re.search(r"\b(approve|reject|abstain)\b", candidate, re.IGNORECASE)
        if not match:
            return None
        return ConsensusVote(voter="worker", stance=match.group(1))
    if not isinstance(payload, Mapping):
        return None
    return _coerce_vote(payload)
