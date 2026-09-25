"""Capability-based task allocation for decentralized swarms.

The main Alpha scheduler remains the lifecycle owner.  This module supplies an
optional contract-net allocator for plans that explicitly opt into capability
auctions; it does not create a second task state machine.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from alpha.blackboard.federated_blackboard import FederatedBlackboard, PheromoneType

logger = logging.getLogger(__name__)


@dataclass
class TaskAnnouncement:
    task_id: str
    topic: str
    description: str
    domain_tags: list[str]
    token_budget: int = 4000
    deadline_seconds: float = 60.0
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.task_id or not self.topic:
            raise ValueError("task_id and topic are required")
        if int(self.token_budget) <= 0:
            raise ValueError("token_budget must be positive")
        if float(self.deadline_seconds) <= 0:
            raise ValueError("deadline_seconds must be positive")
        self.domain_tags = [str(tag).strip().lower() for tag in self.domain_tags if str(tag).strip()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "description": self.description,
            "domain_tags": list(self.domain_tags),
            "token_budget": self.token_budget,
            "deadline_seconds": self.deadline_seconds,
        }


@dataclass
class BidProposal:
    agent_id: str
    task_id: str
    relevance_score: float
    current_load: float
    token_estimate: int
    composite_bid: float = 0.0
    proposed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "relevance_score": round(self.relevance_score, 3),
            "current_load": round(self.current_load, 3),
            "token_estimate": self.token_estimate,
            "composite_bid": round(self.composite_bid, 3),
        }


@dataclass
class ContractAward:
    task_id: str
    contractor_agent_id: str
    winning_bid_score: float
    awarded_at: float = field(default_factory=time.time)
    lease_acquired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "contractor_agent_id": self.contractor_agent_id,
            "winning_bid_score": round(self.winning_bid_score, 3),
            "lease_acquired": self.lease_acquired,
        }


@dataclass(frozen=True)
class LeaderCandidate:
    agent_id: str
    capabilities: frozenset[str]
    current_load: float = 0.0
    reputation: float = 0.5
    evidence_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "capabilities": sorted(self.capabilities),
            "current_load": round(self.current_load, 3),
            "reputation": round(self.reputation, 3),
            "evidence_count": self.evidence_count,
        }


@dataclass
class LeaderElection:
    leader: str | None
    score: float
    method: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "leader": self.leader,
            "score": round(self.score, 6),
            "method": self.method,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


def elect_leader(
    candidates: Iterable[LeaderCandidate | Mapping[str, Any]],
    *,
    required_capabilities: Iterable[str] = (),
) -> LeaderElection:
    """Elect a leader using explicit capability, load, and measured reputation.

    Reputation defaults to a disclosed neutral prior only when a candidate has
    no evidence; it is never presented as a measured quality score.
    """

    required = {str(item).strip().lower() for item in required_capabilities if str(item).strip()}
    normalized: list[LeaderCandidate] = []
    for raw in candidates:
        if isinstance(raw, LeaderCandidate):
            candidate = raw
        elif isinstance(raw, Mapping):
            candidate = LeaderCandidate(
                agent_id=str(raw.get("agent_id", raw.get("name", ""))),
                capabilities=frozenset(str(item).lower() for item in raw.get("capabilities", []) if str(item)),
                current_load=float(raw.get("current_load", 0.0)),
                reputation=float(raw.get("reputation", 0.5)),
                evidence_count=int(raw.get("evidence_count", 0) or 0),
            )
        else:
            continue
        if not candidate.agent_id:
            continue
        if required and not required.issubset(candidate.capabilities):
            continue
        normalized.append(candidate)
    if not normalized:
        return LeaderElection(None, 0.0, "capability-load-reputation-v1", [], "no candidate satisfies the required capabilities")

    def score(candidate: LeaderCandidate) -> float:
        capability_score = len(candidate.capabilities) / max(1, len(required) or len(candidate.capabilities))
        reputation = max(0.0, min(1.0, candidate.reputation))
        return round(0.55 * capability_score + 0.30 * (1.0 - max(0.0, min(1.0, candidate.current_load))) + 0.15 * reputation, 6)

    ranked = sorted(((score(candidate), candidate) for candidate in normalized), key=lambda pair: (-pair[0], pair[1].agent_id))
    winning_score, winner = ranked[0]
    return LeaderElection(
        leader=winner.agent_id,
        score=winning_score,
        method="capability-load-reputation-v1",
        candidates=[{**candidate.to_dict(), "score": candidate_score} for candidate_score, candidate in ranked],
        reason="highest deterministic fitness score; reputation is caller-supplied evidence or neutral prior",
    )


class SwarmWorkerAgent:
    """Participant in a contract-net auction."""

    def __init__(
        self,
        agent_id: str,
        capabilities: list[str],
        base_load: float = 0.0,
        executor_fn: Callable[[TaskAnnouncement], Any] | None = None,
        *,
        reputation: float = 0.5,
    ) -> None:
        if not agent_id:
            raise ValueError("agent_id is required")
        self.agent_id = agent_id
        self.capabilities = {str(c).lower() for c in capabilities if str(c).strip()}
        self.current_load = max(0.0, min(1.0, float(base_load)))
        self.executor_fn = executor_fn
        self.reputation = max(0.0, min(1.0, float(reputation)))
        self.successful_tasks = 0
        self.failed_tasks = 0
        self._lock = threading.RLock()

    def calculate_bid(self, task: TaskAnnouncement) -> BidProposal | None:
        """Calculate a suitability bid, returning no bid when incapable."""

        if not task.domain_tags:
            relevance = 0.5
        else:
            matching_tags = [tag for tag in task.domain_tags if tag.lower() in self.capabilities]
            if not matching_tags:
                return None
            relevance = len(matching_tags) / len(task.domain_tags)
        with self._lock:
            load = self.current_load
        token_estimate = max(1, int(task.token_budget * (1.2 - 0.4 * relevance)))
        return BidProposal(
            agent_id=self.agent_id,
            task_id=task.task_id,
            relevance_score=relevance,
            current_load=load,
            token_estimate=token_estimate,
        )

    def record_outcome(self, *, success: bool) -> None:
        """Update reputation with a disclosed Laplace-smoothed outcome count."""

        with self._lock:
            if success:
                self.successful_tasks += 1
            else:
                self.failed_tasks += 1
            self.reputation = (self.successful_tasks + 1.0) / (self.successful_tasks + self.failed_tasks + 2.0)

    def execute_task(self, task: TaskAnnouncement) -> Any:
        with self._lock:
            self.current_load = min(1.0, self.current_load + 0.2)
        success = False
        try:
            result = self.executor_fn(task) if self.executor_fn else {"status": "completed", "agent_id": self.agent_id, "task_id": task.task_id}
            success = True
            return result
        finally:
            with self._lock:
                self.current_load = max(0.0, self.current_load - 0.2)
            self.record_outcome(success=success)


class ContractNetAuctionEngine:
    """Manage capability-based task awards and TTL resource leases."""

    def __init__(
        self,
        blackboard: FederatedBlackboard | None = None,
        weight_relevance: float = 0.6,
        weight_capacity: float = 0.3,
        weight_cost: float = 0.1,
    ) -> None:
        if weight_relevance < 0 or weight_capacity < 0 or weight_cost < 0:
            raise ValueError("auction weights must be non-negative")
        if weight_relevance + weight_capacity + weight_cost <= 0:
            raise ValueError("at least one auction weight must be positive")
        self.blackboard = blackboard or FederatedBlackboard()
        self.w_relevance = float(weight_relevance)
        self.w_capacity = float(weight_capacity)
        self.w_cost = float(weight_cost)
        self.workers: dict[str, SwarmWorkerAgent] = {}
        self.active_tasks: dict[str, TaskAnnouncement] = {}
        self.awards: dict[str, ContractAward] = {}
        self._lock = threading.RLock()

    def register_worker(self, worker: SwarmWorkerAgent) -> None:
        with self._lock:
            self.workers[worker.agent_id] = worker

    def register_workers_batch(self, workers: list[SwarmWorkerAgent]) -> None:
        for worker in workers:
            self.register_worker(worker)

    def score_bid(self, bid: BidProposal, max_budget: int) -> float:
        """Score relevance, spare capacity, and estimated cost."""

        idle_capacity = max(0.0, 1.0 - bid.current_load)
        cost_ratio = min(1.0, bid.token_estimate / max(1, int(max_budget)))
        bid.composite_bid = self.w_relevance * bid.relevance_score + self.w_capacity * idle_capacity - self.w_cost * cost_ratio
        return bid.composite_bid

    def conduct_auction(self, task: TaskAnnouncement) -> ContractAward | None:
        """Conduct an idempotent auction and acquire a blackboard lease."""

        with self._lock:
            existing = self.awards.get(task.task_id)
            if existing is not None:
                return existing
            self.active_tasks[task.task_id] = task
            self.blackboard.write_entry(f"cluster:{task.topic}", f"task:{task.task_id}", task.to_dict())
            bids: list[BidProposal] = []
            for worker in self.workers.values():
                bid = worker.calculate_bid(task)
                if bid:
                    self.score_bid(bid, max_budget=task.token_budget)
                    bids.append(bid)
            if not bids:
                logger.warning("No capable bids received for task %s", task.task_id)
                self.active_tasks.pop(task.task_id, None)
                return None
            winning_bid = sorted(bids, key=lambda bid: (-bid.composite_bid, bid.agent_id))[0]
            lease_ok = self.blackboard.claim_lease(
                resource_id=f"task:{task.task_id}",
                agent_id=winning_bid.agent_id,
                duration_seconds=task.deadline_seconds,
            )
            award = ContractAward(task.task_id, winning_bid.agent_id, winning_bid.composite_bid, lease_acquired=lease_ok)
            self.awards[task.task_id] = award
            if not lease_ok:
                self.active_tasks.pop(task.task_id, None)
            self.blackboard.deposit_trace(
                resource_id=f"cluster:{task.topic}",
                trace_type=PheromoneType.ACTIVITY,
                intensity=1.5,
            )
            return award

    def execute_and_report(self, task: TaskAnnouncement, award: ContractAward) -> Any:
        """Execute an awarded contract and always release its lease."""

        if not award.lease_acquired:
            return None
        worker = self.workers.get(award.contractor_agent_id)
        if not worker:
            self.blackboard.release_lease(f"task:{task.task_id}", award.contractor_agent_id)
            return None
        try:
            result = worker.execute_task(task)
        except Exception as exc:
            self.blackboard.write_entry(
                f"cluster:{task.topic}",
                f"result:{task.task_id}",
                {"status": "failed", "error": str(exc)},
                agent_id=award.contractor_agent_id,
            )
            raise
        else:
            self.blackboard.write_entry(
                f"cluster:{task.topic}",
                f"result:{task.task_id}",
                result,
                agent_id=award.contractor_agent_id,
            )
            self.blackboard.deposit_trace(
                resource_id=f"task:{task.task_id}",
                trace_type=PheromoneType.COMPLETED,
                intensity=2.0,
            )
            return result
        finally:
            self.blackboard.release_lease(f"task:{task.task_id}", award.contractor_agent_id)
            with self._lock:
                self.active_tasks.pop(task.task_id, None)
