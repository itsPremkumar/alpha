"""Reputation-Aware Task Auction & Bidding Matchmaker.

Implements a market-based decentralized task allocation protocol. Available bots
compute and submit bids on unassigned tasks based on toolset match, current workload,
and historical completion reputation, maximizing parallel throughput across the fleet.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from alpha.bots.registry import get_bot_registry
from alpha.projects.contracts import TaskContract, get_contract_gatekeeper
from alpha.projects.events import get_event_bus
from alpha.projects.locks import get_lock_manager

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TaskBid:
    """A formal bid submitted by a bot candidate for a task contract."""

    bid_id: str
    task_id: str
    bot_name: str
    bid_score: float
    capability_score: float
    workload_penalty: float
    reputation_multiplier: float
    rationale: str
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskBid:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class AuctionEngine:
    """Evaluates multi-agent bids and assigns task contracts to optimal candidates."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._lock = threading.Lock()

    def solicit_bids(
        self,
        task_id: str,
        *,
        required_capabilities: list[str] | None = None,
        candidate_bots: list[str] | None = None,
    ) -> list[TaskBid]:
        """Solicit and score bids from eligible bots for a task contract."""
        registry = get_bot_registry()
        lock_mgr = get_lock_manager()
        active_locks = lock_mgr.list_locks(self.project_id)
        bot_lock_counts = {l.owner_bot: 0 for l in active_locks}
        for l in active_locks:
            bot_lock_counts[l.owner_bot] = bot_lock_counts.get(l.owner_bot, 0) + 1

        req_caps = [c.lower().strip() for c in (required_capabilities or [])]

        bids: list[TaskBid] = []
        eligible_candidates = candidate_bots or [b.name for b in registry.list_bots(status="active")]

        for name in eligible_candidates:
            bot = registry.get_bot(name)
            if not bot or bot.status != "active":
                continue

            # 1. Capability Match Score (0.0 to 1.0)
            bot_caps = {c.lower() for c in bot.capabilities}
            bot_role = bot.role.lower()
            if req_caps:
                matched = sum(1 for c in req_caps if c in bot_caps or c in bot_role)
                cap_score = round(matched / len(req_caps), 2)
            else:
                cap_score = 0.8  # General fit

            # 2. Workload Penalty (0.0 to 0.5)
            held_locks = bot_lock_counts.get(name, 0)
            workload_penalty = min(0.5, held_locks * 0.25)

            # 3. Reputation Multiplier (0.5 to 1.2)
            rep = getattr(bot, "reputation_score", 1.0)
            stats = getattr(bot, "task_stats", {})
            total_runs = stats.get("total_runs", 0)
            completed = stats.get("completed", 0)

            if total_runs > 0:
                success_rate = completed / total_runs
                rep_multiplier = round(max(0.5, min(1.2, rep * (0.8 + 0.4 * success_rate))), 2)
            else:
                rep_multiplier = rep

            # 4. Final Composite Bid Score
            raw_score = (cap_score * 0.6 + (1.0 - workload_penalty) * 0.4) * rep_multiplier
            final_score = round(max(0.01, min(1.0, raw_score)), 3)

            rationale = (
                f"Capability match: {int(cap_score*100)}%, "
                f"Workload penalty: {workload_penalty:.2f} ({held_locks} locks), "
                f"Reputation multiplier: {rep_multiplier}."
            )

            bid = TaskBid(
                bid_id=f"BID-{uuid.uuid4().hex[:8].upper()}",
                task_id=task_id,
                bot_name=name,
                bid_score=final_score,
                capability_score=cap_score,
                workload_penalty=workload_penalty,
                reputation_multiplier=rep_multiplier,
                rationale=rationale,
            )
            bids.append(bid)

        # Sort descending by bid_score
        bids.sort(key=lambda b: -b.bid_score)
        return bids

    def evaluate_and_award(
        self,
        task_id: str,
        *,
        required_capabilities: list[str] | None = None,
        candidate_bots: list[str] | None = None,
    ) -> tuple[TaskBid | None, TaskContract | None]:
        """Solicit bids and automatically award the task contract to the winning bot."""
        bids = self.solicit_bids(
            task_id,
            required_capabilities=required_capabilities,
            candidate_bots=candidate_bots,
        )
        if not bids:
            logger.warning("No eligible bids received for task %s", task_id)
            return None, None

        winning_bid = bids[0]
        gk = get_contract_gatekeeper(self.project_id)
        contract = gk.get_contract(task_id)

        if contract:
            contract.assignee_bot = winning_bid.bot_name
            contract.status = "in_progress"
            gk._save()
        else:
            contract = gk.create_contract(
                task_id=task_id,
                title=f"Task {task_id}",
                assignee_bot=winning_bid.bot_name,
            )

        get_event_bus(self.project_id).emit(
            "task_assigned",
            winning_bid.bot_name,
            {
                "task_id": task_id,
                "bid_score": winning_bid.bid_score,
                "rationale": winning_bid.rationale,
            },
        )

        logger.info(
            "Awarded task %s to @%s (score: %.3f)",
            task_id, winning_bid.bot_name, winning_bid.bid_score
        )
        return winning_bid, contract


def get_auction_engine(project_id: str) -> AuctionEngine:
    return AuctionEngine(project_id)
