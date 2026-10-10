"""Swarm ledger: per-member state tracking and aggregation for the SWARM node.

The SWARM node kind is what makes swarm a first-class paradigm in the DWE
graph model — not a MAP fan-out with swarm branding. A swarm differs from a
static fan-out in three load-bearing ways:

1. **Members carry their own lifecycle.** Each member has an independent status,
   retry count, cost accumulator and failure reason — the swarm node can report
   exactly which member failed and why, not just "the fan-out failed".

2. **Aggregation is policy-driven.** ``FIRST_SUCCESS`` stops after the first
   member succeeds; ``QUORUM`` waits for a configurable fraction; ``ALL`` waits
   for everyone; ``ANY`` takes the first finished member's outcome. The policy
   is a property of the swarm, not an emergent behaviour of the graph edges.

3. **The cost ledger is real.** Token and cost usage per member are accumulated
   from the actual runner results, never estimated or defaulted to zero.

Two invariants keep the verdict honest under concurrent member execution:

- **The verdict is frozen once decided.** A late member finishing after the
  policy was satisfied updates the *totals* (its spend was real) but never
  flips ``completed``/``succeeded`` — otherwise a swarm recorded as failed
  would retroactively "succeed" because a straggler came back.
- **Every mutation is locked.** Members run on a bounded pool, so the ledger
  serialises its own state transitions; a torn read of two members' statuses
  is how an aggregation counts a member twice.

A swarm that cannot aggregate (every member failed, or the policy cannot be
satisfied within the member budget) reports failure with the full ledger — not
a bare "failed" that hides which member broke.
"""

from __future__ import annotations

import threading
import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AggregationPolicy(StrEnum):
    """How a SWARM node decides it has enough member results to complete."""

    #: Succeeds as soon as one member succeeds; remaining members are skipped.
    FIRST_SUCCESS = "first_success"
    #: Succeeds when the number of successful members reaches ``quorum``.
    QUORUM = "quorum"
    #: Succeeds when every member has finished successfully.
    ALL = "all"
    #: Takes the FIRST finished member's outcome, success or failure.
    ANY = "any"


class SwarmMemberStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SwarmMember(BaseModel):
    """One worker inside a swarm: its identity, state and measured cost."""

    member_id: str
    prompt: str = ""
    status: SwarmMemberStatus = SwarmMemberStatus.PENDING
    output: str | None = None
    evidence: list[str] = Field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 1
    failure_reason: str | None = None
    tokens_used: int = 0
    cost_usd: float | None = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return end - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SwarmState(BaseModel):
    """Aggregate swarm state: member list + terminal decision."""

    members: list[SwarmMember] = Field(default_factory=list)
    aggregation: AggregationPolicy = AggregationPolicy.ALL
    quorum: int = 1
    total_tokens: int = 0
    total_cost_usd: float | None = None
    completed: bool = False
    succeeded: bool = False

    def member(self, member_id: str) -> SwarmMember | None:
        for member in self.members:
            if member.member_id == member_id:
                return member
        return None

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for member in self.members:
            key = member.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts


class SwarmLedger:
    """Tracks member lifecycle and enforces the aggregation policy.

    The ledger is the single source of truth for "is this swarm done, and did
    it succeed". It never fabricates a verdict: a swarm with no successful
    members under ``FIRST_SUCCESS`` is a failure, not a timeout, and a swarm
    that reaches ``ALL`` with mixed outcomes succeeds only if every member did.
    """

    def __init__(self, *, aggregation: AggregationPolicy = AggregationPolicy.ALL, quorum: int = 1) -> None:
        self.aggregation = aggregation
        self.quorum = max(1, quorum)
        self._state = SwarmState(aggregation=aggregation, quorum=self.quorum)
        self._lock = threading.RLock()

    @property
    def state(self) -> SwarmState:
        """The live state object. Callers must not mutate it directly."""
        return self._state

    def members(self) -> list[SwarmMember]:
        """A stable snapshot of the member list (members are never removed)."""
        with self._lock:
            return list(self._state.members)

    def add_member(self, member: SwarmMember) -> None:
        with self._lock:
            self._state.members.append(member)

    def start_member(self, member_id: str) -> None:
        with self._lock:
            member = self._state.member(member_id)
            if member is None:
                return
            member.status = SwarmMemberStatus.RUNNING
            member.started_at = time.time()

    def complete_member(
        self,
        member_id: str,
        *,
        output: str | None = None,
        evidence: list[str] | None = None,
        tokens_used: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        with self._lock:
            member = self._state.member(member_id)
            if member is None:
                return
            member.status = SwarmMemberStatus.SUCCEEDED
            member.output = output
            member.evidence = list(evidence or [])
            member.tokens_used = max(0, int(tokens_used or 0))
            member.cost_usd = cost_usd
            member.finished_at = time.time()
            self._recompute()

    def fail_member(self, member_id: str, reason: str) -> None:
        with self._lock:
            member = self._state.member(member_id)
            if member is None:
                return
            member.status = SwarmMemberStatus.FAILED
            member.failure_reason = reason
            member.finished_at = time.time()
            self._recompute()

    def skip_member(self, member_id: str, reason: str) -> None:
        with self._lock:
            member = self._state.member(member_id)
            if member is None:
                return
            if member.status in (SwarmMemberStatus.SUCCEEDED, SwarmMemberStatus.FAILED):
                return  # already finished: its outcome is recorded, not erased
            member.status = SwarmMemberStatus.SKIPPED
            member.failure_reason = reason
            member.finished_at = time.time()
            self._recompute()

    def should_stop(self) -> bool:
        """True when the aggregation policy says no more members are needed."""
        with self._lock:
            return self._state.completed

    def totals(self) -> tuple[int, float | None]:
        """Measured (tokens, cost) across every member, cost ``None`` if unpriced."""
        with self._lock:
            return self._state.total_tokens, self._state.total_cost_usd

    def _recompute(self) -> None:
        """Re-derive totals and, while undecided, the policy verdict.

        Called with the lock held. Totals always reflect every finished
        member; the verdict is computed only until the swarm completes, so a
        straggler cannot rewrite a decision the caller already acted on.
        """
        counts = self._state.counts()
        succeeded = counts.get("succeeded", 0)
        failed = counts.get("failed", 0)
        pending = counts.get("pending", 0) + counts.get("running", 0)
        total = len(self._state.members)

        # Accumulate totals from member data (never estimated).
        self._state.total_tokens = sum(member.tokens_used for member in self._state.members)
        costs = [member.cost_usd for member in self._state.members if member.cost_usd is not None]
        self._state.total_cost_usd = sum(costs) if costs else None

        if self._state.completed:
            return  # totals updated above; the verdict is frozen

        policy = self.aggregation
        if policy is AggregationPolicy.FIRST_SUCCESS:
            if succeeded >= 1:
                self._state.completed = True
                self._state.succeeded = True
                self._skip_pending()
            elif pending == 0 and total > 0:
                self._state.completed = True
                self._state.succeeded = False
        elif policy is AggregationPolicy.QUORUM:
            if succeeded >= self.quorum:
                self._state.completed = True
                self._state.succeeded = True
                self._skip_pending()
            elif pending == 0 and total > 0:
                self._state.completed = True
                self._state.succeeded = False
        elif policy is AggregationPolicy.ALL:
            if pending == 0 and total > 0:
                self._state.completed = True
                self._state.succeeded = succeeded == total
        elif policy is AggregationPolicy.ANY:
            # ANY takes the FIRST finished member's outcome, whatever it is —
            # waiting for the rest would make it a slow ALL.
            if succeeded + failed >= 1:
                self._state.completed = True
                self._state.succeeded = succeeded >= 1
                self._skip_pending()

    def _skip_pending(self) -> None:
        """Mark not-yet-started members as skipped. RUNNING members finish.

        A member already mid-flight is deliberately left alone: marking it
        skipped while its worker runs would be a status the ledger contradicts
        a moment later. Its result still lands, and the frozen verdict decides.
        """
        now = time.time()
        for member in self._state.members:
            if member.status is SwarmMemberStatus.PENDING:
                member.status = SwarmMemberStatus.SKIPPED
                member.failure_reason = "swarm completed before this member was needed"
                member.finished_at = now
