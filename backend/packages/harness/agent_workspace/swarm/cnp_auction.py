"""Contract Net Protocol (CNP) Multi-Agent Auction Engine for 1,000+ Agent Swarms.

Distributes decomposed tasks dynamically using market auctions, capability scoring,
and digital stigmergy on the federated blackboard.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent_workspace.blackboard.federated_blackboard import FederatedBlackboard, PheromoneType

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "description": self.description,
            "domain_tags": self.domain_tags,
            "token_budget": self.token_budget,
            "deadline_seconds": self.deadline_seconds,
        }


@dataclass
class BidProposal:
    agent_id: str
    task_id: str
    relevance_score: float  # 0.0 to 1.0 match with domain tags / capability
    current_load: float  # 0.0 (idle) to 1.0 (saturated)
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


class SwarmWorkerAgent:
    """Participant in Contract Net Protocol auctions."""

    def __init__(
        self,
        agent_id: str,
        capabilities: list[str],
        base_load: float = 0.0,
        executor_fn: Optional[Callable[[TaskAnnouncement], Any]] = None,
    ) -> None:
        self.agent_id = agent_id
        self.capabilities = set(c.lower() for c in capabilities)
        self.current_load = base_load
        self.executor_fn = executor_fn

    def calculate_bid(self, task: TaskAnnouncement) -> Optional[BidProposal]:
        """Calculates suitability bid for the task announcement."""
        if not task.domain_tags:
            relevance = 0.5
        else:
            matching_tags = [tag for tag in task.domain_tags if tag.lower() in self.capabilities]
            if not matching_tags:
                return None  # Agent lacks domain capability
            relevance = len(matching_tags) / len(task.domain_tags)

        # Token estimate inversely relates to specialization
        est_tokens = int(task.token_budget * (1.2 - 0.4 * relevance))

        return BidProposal(
            agent_id=self.agent_id,
            task_id=task.task_id,
            relevance_score=relevance,
            current_load=self.current_load,
            token_estimate=est_tokens,
        )

    def execute_task(self, task: TaskAnnouncement) -> Any:
        self.current_load = min(1.0, self.current_load + 0.2)
        try:
            if self.executor_fn:
                return self.executor_fn(task)
            return {"status": "completed", "agent_id": self.agent_id, "task_id": task.task_id}
        finally:
            self.current_load = max(0.0, self.current_load - 0.2)


class ContractNetAuctionEngine:
    """Manages decentralized market-based task allocation across 1,000+ swarm agents."""

    def __init__(
        self,
        blackboard: Optional[FederatedBlackboard] = None,
        weight_relevance: float = 0.6,
        weight_capacity: float = 0.3,
        weight_cost: float = 0.1,
    ) -> None:
        self.blackboard = blackboard or FederatedBlackboard()
        self.w_relevance = weight_relevance
        self.w_capacity = weight_capacity
        self.w_cost = weight_cost
        self.workers: dict[str, SwarmWorkerAgent] = {}
        self.active_tasks: dict[str, TaskAnnouncement] = {}
        self.awards: dict[str, ContractAward] = {}

    def register_worker(self, worker: SwarmWorkerAgent) -> None:
        self.workers[worker.agent_id] = worker

    def register_workers_batch(self, workers: list[SwarmWorkerAgent]) -> None:
        for w in workers:
            self.workers[w.agent_id] = w

    def score_bid(self, bid: BidProposal, max_budget: int) -> float:
        """Composite Bid Score: w1*Relevance + w2*(1 - Load) - w3*(Cost / MaxBudget)."""
        idle_capacity = max(0.0, 1.0 - bid.current_load)
        cost_ratio = min(1.0, bid.token_estimate / (max_budget or 1))

        score = (
            (self.w_relevance * bid.relevance_score)
            + (self.w_capacity * idle_capacity)
            - (self.w_cost * cost_ratio)
        )
        bid.composite_bid = score
        return score

    def conduct_auction(self, task: TaskAnnouncement) -> Optional[ContractAward]:
        """Runs full Contract Net Protocol auction for a task announcement."""
        self.active_tasks[task.task_id] = task
        
        # 1. Announce task on federated blackboard
        topic_shard = f"cluster:{task.topic}"
        self.blackboard.write_entry(topic_shard, f"task:{task.task_id}", task.to_dict())

        # 2. Collect bids from all capable registered workers
        bids: list[BidProposal] = []
        for worker in self.workers.values():
            bid = worker.calculate_bid(task)
            if bid:
                self.score_bid(bid, max_budget=task.token_budget)
                bids.append(bid)

        if not bids:
            logger.warning("No capable bids received for task %s", task.task_id)
            return None

        # 3. Select optimal contractor (highest composite score)
        bids.sort(key=lambda b: b.composite_bid, reverse=True)
        winning_bid = bids[0]

        # 4. Atomic lease acquisition on blackboard
        lease_ok = self.blackboard.claim_lease(
            resource_id=f"task:{task.task_id}",
            agent_id=winning_bid.agent_id,
            duration_seconds=task.deadline_seconds,
        )

        award = ContractAward(
            task_id=task.task_id,
            contractor_agent_id=winning_bid.agent_id,
            winning_bid_score=winning_bid.composite_bid,
            lease_acquired=lease_ok,
        )
        self.awards[task.task_id] = award

        # 5. Stigmergic reinforcement
        self.blackboard.deposit_trace(
            resource_id=f"cluster:{task.topic}",
            trace_type=PheromoneType.ACTIVITY,
            intensity=1.5,
        )

        return award

    def execute_and_report(self, task: TaskAnnouncement, award: ContractAward) -> Any:
        """Executes awarded contract and updates digital stigmergic state."""
        worker = self.workers.get(award.contractor_agent_id)
        if not worker:
            return None

        result = worker.execute_task(task)

        # Release lease and mark completion on blackboard
        self.blackboard.release_lease(f"task:{task.task_id}", award.contractor_agent_id)
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
