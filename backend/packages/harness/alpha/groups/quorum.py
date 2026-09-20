"""Consensus Quorum and Voting Engine for Multi-Agent Group Deliberation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

VoteChoice = Literal["agree", "disagree", "amend"]
ProposalStatus = Literal["in_progress", "approved", "rejected"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Proposal:
    """A formal proposal submitted to a group room for consensus voting."""

    proposal_id: str
    room_id: str
    proposer: str
    question: str
    threshold: float = 0.5
    status: ProposalStatus = "in_progress"
    votes: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    closed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuorumEngine:
    """Manages proposals, votes, and threshold consensus tallying."""

    def __init__(self):
        self._proposals: dict[str, Proposal] = {}

    def create_proposal(
        self,
        room_id: str,
        proposer: str,
        question: str,
        threshold: float = 0.5,
    ) -> Proposal:
        pid = f"prop_{uuid4().hex[:8]}"
        proposal = Proposal(
            proposal_id=pid,
            room_id=room_id,
            proposer=proposer,
            question=question,
            threshold=threshold,
        )
        self._proposals[pid] = proposal
        return proposal

    def cast_vote(
        self,
        proposal_id: str,
        voter: str,
        choice: VoteChoice,
        comment: str = "",
    ) -> dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return {"status": "error", "error": f"Proposal '{proposal_id}' not found."}
        if proposal.status != "in_progress":
            return {"status": "error", "error": f"Proposal '{proposal_id}' is already closed."}

        proposal.votes[voter] = {
            "choice": choice,
            "comment": comment,
            "voted_at": _now(),
        }
        return {"status": "ok", "proposal_id": proposal_id, "voter": voter, "choice": choice}

    def tally(self, proposal_id: str, total_eligible_voters: int) -> dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return {"status": "error", "error": "Proposal not found."}

        agree_count = sum(1 for v in proposal.votes.values() if v["choice"] == "agree")
        disagree_count = sum(1 for v in proposal.votes.values() if v["choice"] == "disagree")
        amend_count = sum(1 for v in proposal.votes.values() if v["choice"] == "amend")
        total_votes = len(proposal.votes)

        # Quorum threshold check
        ratio = (agree_count / total_eligible_voters) if total_eligible_voters > 0 else 0
        if ratio >= proposal.threshold:
            proposal.status = "approved"
            proposal.closed_at = _now()
        elif (disagree_count / total_eligible_voters) > (1 - proposal.threshold):
            proposal.status = "rejected"
            proposal.closed_at = _now()

        return {
            "proposal_id": proposal_id,
            "status": proposal.status,
            "agree": agree_count,
            "disagree": disagree_count,
            "amend": amend_count,
            "total_votes": total_votes,
            "eligible": total_eligible_voters,
            "ratio": ratio,
            "threshold": proposal.threshold,
        }

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        return self._proposals.get(proposal_id)
