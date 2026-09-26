"""Capability dispatch: the one place a task is matched to the agent that can do it.

The three components this wires together
---------------------------------------
``capability_tags`` (:mod:`alpha.capabilities.eligibility`) decided eligibility,
:func:`alpha.bots.work_discovery.match_bot_for_task` produced the human-readable
ranking, and :class:`alpha.swarm.cnp_auction.ContractNetAuctionEngine` ran the
capability auction. All three existed. **None of them was on a dispatch path**:
``capability_tags`` was consulted for leader election only, ``match_bot_for_task``
had no production caller at all, and the auction engine was imported by its own
test and nothing else. That is why a mismatched agent got work it could not do —
the selection logic was not merely weak, it was absent.

This module is that dispatch path. The order of operations is deliberate:

1. **Authority** - the leader may only DIRECT capabilities on its explicit
   allowlist (:mod:`alpha.bots.alpha_leader`). A refusal here is a refusal, not a
   reroute, and it never satisfies another gate.
2. **Bounds** - depth, fan-out, hops, cycle and budget are checked against the
   inherited :class:`~alpha.bots.delegation.DelegationContext`
   (:mod:`alpha.bots.delegation`). A runaway tree is stopped here, before a
   target is even named.
3. **Capability match (HARD)** - :func:`alpha.capabilities.eligibility.eligible_candidates`
   removes every agent that does not cover the required tags. This is a filter,
   not a score: an agent that does not match is unreachable, however senior.
4. **Declared fit** - :func:`alpha.bots.work_discovery.match_bot_for_task` ranks
   the *surviving* candidates and supplies the reason fragments ("Skills matched:
   sql", "Department match (engineering)").
5. **Auction** - a real :class:`~alpha.swarm.cnp_auction.ContractNetAuctionEngine`
   conducts the contract net over the surviving candidates. Workers that share no
   tag do not bid at all, so the auction cannot re-introduce a mismatched agent
   that the filter removed. The award picks the best of the capable by
   relevance, spare capacity and cost.
6. **Record** - the decision and its reason are appended to the global handoff
   ledger as ``(from, to, reason, attempt)``.

Why the auction runs over the filtered pool rather than replacing the filter
---------------------------------------------------------------------------
Both stages answer different questions. The filter answers *"may this agent do
this work at all"*; the auction answers *"of those that may, who should do it
now"*. Letting the auction bid for ineligible agents would reintroduce exactly
the failure this module exists to remove.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.bots.alpha_leader import ALPHA_LEADER_NAME, leader_may_direct
from alpha.bots.delegation import (
    DEFAULT_LIMITS,
    REFUSAL_AUTHORITY,
    REFUSAL_CYCLE,
    REFUSAL_NO_ELIGIBLE_AGENT,
    DelegationContext,
    DelegationTree,
    GuardVerdict,
)
from alpha.bots.failure_reasons import HANDOFF_REQUESTED
from alpha.bots.registry import BotRegistry, get_bot_registry
from alpha.bots.work_discovery import match_bot_for_task
from alpha.capabilities.eligibility import (
    eligible_candidates,
    infer_capability_tags,
    normalize_tags,
    profile_capability_tags,
    required_capability_tags,
)
from alpha.swarm.cnp_auction import ContractNetAuctionEngine, SwarmWorkerAgent, TaskAnnouncement

logger = logging.getLogger(__name__)

#: Ledger domain for leader-initiated capability dispatch. Shares the bot domain
#: so a leader dispatch, a bot handoff and a bot failure are one ordered log.
DISPATCH_DOMAIN = "bot"

#: The recorded method string. Stamped on every decision so a reader can tell a
#: capability dispatch from a legacy by-name dispatch at a glance.
DISPATCH_METHOD = "capability-match+contract-net-auction"

#: Recorded when a task declares no capability tags. Stated explicitly so an
#: unconstrained dispatch is never mistaken for a capability match.
REASON_UNCONSTRAINED = "no capability constraint: selection decided by auction over every live agent"

#: Default token budget handed to a dispatched task. The auction's cost term is
#: scaled by this, so it is a real input to the award, not decoration.
DEFAULT_TASK_TOKEN_BUDGET = 40_000
DEFAULT_DEADLINE_SECONDS = 900.0

#: The topic the auction's blackboard entries are written under.
DISPATCH_TOPIC = "alpha.leader.dispatch"


class DispatchOutcome(StrEnum):
    """Terminal states of one dispatch attempt."""

    DISPATCHED = "dispatched"
    REFUSED = "refused"

    @property
    def ok(self) -> bool:
        return self is DispatchOutcome.DISPATCHED


@dataclass
class DispatchDecision:
    """The leader's answer to "who does this task, and why?".

    ``reason`` is the same string written to the ledger, so the record and the
    explanation can never drift apart.
    """

    task_id: str
    outcome: DispatchOutcome
    target: str | None
    reason: str
    method: str = DISPATCH_METHOD
    issuer: str = ALPHA_LEADER_NAME
    required_capability_tags: tuple[str, ...] = ()
    matched_capability_tags: tuple[str, ...] = ()
    missing_capability_tags: tuple[str, ...] = ()
    candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    award: dict[str, Any] | None = None
    bids: list[dict[str, Any]] = field(default_factory=list)
    guard: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    max_attempts: int = 3
    ledger_seq: int | None = None
    refusal_code: str = ""
    depth: int = 0
    hops: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome is DispatchOutcome.DISPATCHED and bool(self.target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "outcome": str(self.outcome),
            "ok": self.ok,
            "target": self.target,
            "reason": self.reason,
            "method": self.method,
            "issuer": self.issuer,
            "required_capability_tags": list(self.required_capability_tags),
            "matched_capability_tags": list(self.matched_capability_tags),
            "missing_capability_tags": list(self.missing_capability_tags),
            "candidates": [dict(item) for item in self.candidates],
            "rejected": [dict(item) for item in self.rejected],
            "award": dict(self.award) if self.award else None,
            "bids": [dict(item) for item in self.bids],
            "guard": dict(self.guard),
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "ledger_seq": self.ledger_seq,
            "refusal_code": self.refusal_code,
            "depth": self.depth,
            "hops": self.hops,
        }

    def summary(self) -> str:
        """One human-readable line, for logs and the dispatch response."""
        if not self.ok:
            return f"dispatch refused ({self.refusal_code or 'no_eligible_agent'}): {self.reason}"
        return f"dispatched to @{self.target} — {self.reason}"


def _refusal(
    task_id: str,
    code: str,
    reason: str,
    *,
    issuer: str,
    guard: GuardVerdict | None = None,
    rejected: list[dict[str, Any]] | None = None,
    required: Iterable[str] = (),
    attempt: int = 1,
    max_attempts: int = 3,
    context: DelegationContext | None = None,
    record: bool = True,
) -> DispatchDecision:
    """Build, record and return a refusal.

    Refusals are recorded in the ledger too: a dispatch that was stopped by a
    ceiling or by the authority boundary is exactly the event a human needs to
    see when asking "why did nothing happen?".
    """
    decision = DispatchDecision(
        task_id=task_id,
        outcome=DispatchOutcome.REFUSED,
        target=None,
        reason=reason,
        issuer=issuer,
        required_capability_tags=tuple(sorted(normalize_tags(required))),
        rejected=list(rejected or []),
        refusal_code=code,
        guard=guard.to_dict() if guard is not None else {"allowed": False, "code": code, "detail": reason},
        attempt=attempt,
        max_attempts=max_attempts,
        depth=context.depth if context is not None else 0,
        hops=context.hops if context is not None else 0,
    )
    if record:
        seq = _record_ledger(
            task_id=task_id,
            from_ref=issuer,
            to_ref="(none)",
            reason=code,
            attempt=attempt,
            max_attempts=max_attempts,
            details={"detail": reason, "refused": True, "method": DISPATCH_METHOD},
        )
        decision.ledger_seq = seq
    return decision


def _record_ledger(
    *,
    task_id: str,
    from_ref: str,
    to_ref: str,
    reason: str,
    attempt: int,
    max_attempts: int,
    details: dict[str, Any] | None = None,
) -> int | None:
    """Append one hop to the global handoff ledger. Fail-soft, returns the seq.

    Uses the existing global ledger (:mod:`alpha.runtime.escalation`) rather than
    a private log, so a leader dispatch, a bot handoff, a swarm reassignment and
    a batch escalation all land in ONE ordered, queryable history.
    """
    try:
        from alpha.runtime.escalation import record_handoff

        entry = record_handoff(
            DISPATCH_DOMAIN,
            task_id,
            from_ref,
            to_ref,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details=dict(details or {}),
        )
        return entry.seq if entry is not None else None
    except Exception:
        logger.warning("Failed to record dispatch %s -> %s in the ledger", from_ref, to_ref, exc_info=True)
        return None


def _load(bot: Any) -> float:
    """Current load in [0, 1] for a bot, for the auction's capacity term.

    Reads the liveness monitor so a loaded agent loses the auction to an idle
    peer that is equally capable. Any failure degrades to "unknown load = 0.5"
    rather than to "idle", which would let a broken monitor hand every task to
    the same agent.
    """
    try:
        from alpha.bots.health import get_health_monitor

        liveness = get_health_monitor().evaluate_liveness(bot)
        state = str(liveness.get("liveness", "")).strip().lower()
    except Exception:
        return 0.5
    return {
        "active": 0.7,
        "idle": 0.0,
        "healthy": 0.3,
        "sleeping": 0.0,
        "stale": 0.6,
        "stalled": 0.9,
    }.get(state, 0.5)


def _reputation(bot: Any) -> float:
    """Measured reputation in [0, 1]; ``None`` becomes the disclosed neutral 0.5."""
    raw = getattr(bot, "reputation_score", None)
    if raw is None:
        return 0.5
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5


class CapabilityDispatcher:
    """Leader-side dispatcher: bounded, capability-matched, auctioned, recorded.

    Constructed per dispatch tree (it holds the bounded context), or once per
    process with :meth:`for_root` for a fresh root context.
    """

    def __init__(
        self,
        context: DelegationContext,
        *,
        registry: BotRegistry | None = None,
        engine_factory: Callable[[], ContractNetAuctionEngine] | None = None,
        tree: DelegationTree | None = None,
    ) -> None:
        self._context = context
        self._registry = registry
        # A FRESH auction engine per dispatch. The engine keeps a `workers`
        # registry that only ever grows, so sharing one across dispatches would
        # let a previous attempt's workers keep bidding — which is precisely how
        # a reassignment ends up handing the work back to the agent that just
        # failed. A per-dispatch engine also makes each award a function of that
        # dispatch's candidate pool alone.
        self._engine_factory = engine_factory or ContractNetAuctionEngine
        self._engine: ContractNetAuctionEngine | None = None
        self._tree = tree if tree is not None else DelegationTree(context, recorder=self._record_hop)
        # Distinct auction identity per call; the engine's award table is keyed by
        # announcement id and is idempotent, so a repeated id would return the
        # previous call's award.
        self._auction_seq = itertools.count(1)
        self._lock = threading.RLock()

    @property
    def context(self) -> DelegationContext:
        return self._context

    @property
    def last_engine(self) -> ContractNetAuctionEngine | None:
        """The engine that ran the most recent auction (None before the first)."""
        return self._engine

    @property
    def tree(self) -> DelegationTree:
        """The bounded subtree this node owns.

        Exposed so a caller reports the AGGREGATE of everything dispatched under
        this node rather than the fate of its last dispatch: a task is not
        complete while any descendant is still running or unresolved.
        """
        return self._tree

    def _record_hop(self, hop: dict[str, Any]) -> None:
        """Ledger hook handed to the tree, so every admitted hop is recorded."""
        _record_ledger(
            task_id=self._context.task_id,
            from_ref=str(hop.get("from") or self._context.root),
            to_ref=str(hop.get("to") or "(none)"),
            reason=str(hop.get("reason") or HANDOFF_REQUESTED),
            attempt=int(hop.get("attempt") or 1),
            max_attempts=0,
            details={
                "method": DISPATCH_METHOD,
                "child_id": hop.get("child_id"),
                "depth": hop.get("depth"),
                "lineage": hop.get("lineage"),
                "status": hop.get("status"),
            },
        )

    def for_child(self, agent: str) -> CapabilityDispatcher:
        """A dispatcher for ``agent``, inheriting this node's (smaller) budget."""
        return CapabilityDispatcher(
            self._context.for_child(agent),
            registry=self._registry,
            engine_factory=self._engine_factory,
        )

    @property
    def registry(self) -> BotRegistry:
        return self._registry or get_bot_registry()

    # ------------------------------------------------------------------
    # The dispatch path
    # ------------------------------------------------------------------
    def dispatch(
        self,
        task_id: str,
        objective: str,
        *,
        required_capability_tags: Sequence[str] | None = None,
        proposed_owners: Sequence[str] | None = None,
        exclude: Sequence[str] | None = None,
        candidate_names: Sequence[str] | None = None,
        attempt: int = 1,
        max_attempts: int = 3,
        token_budget: int = DEFAULT_TASK_TOKEN_BUDGET,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        enforce_authority: bool = True,
    ) -> DispatchDecision:
        """Select the target for ``task_id`` and record why.

        The single production entry point for "a task is being assigned". Every
        refusal is a returned decision, never an exception, so a caller cannot
        accidentally treat a bounded stop as a dispatch.
        """
        registry = self.registry
        issuer = self._context.node_id or ALPHA_LEADER_NAME

        # 1. Required capability tags, in strict precedence order:
        #      a. supplied by the caller;
        #      b. derived from the TASK TEXT (the only non-circular source — a
        #         requirement taken from the proposed owner would make that owner
        #         eligible by construction and the check would be a no-op);
        #      c. failing both, the proposed owner's declared surface, recorded
        #         as a fallback so it is never mistaken for a capability match.
        if required_capability_tags:
            required = normalize_tags(required_capability_tags)
            requirement_reason = "required tags supplied by the caller"
        else:
            inferred = infer_capability_tags(objective)
            if inferred:
                required = inferred
                requirement_reason = f"required tags inferred from the task text: {sorted(inferred)}"
            else:
                required, requirement_reason = _derive_requirement(proposed_owners or [], registry=registry)

        # 2. Authority: the leader may only DIRECT what its allowlist permits.
        if enforce_authority:
            allowed, why, _blocking = leader_may_direct(sorted(required))
            if not allowed:
                return _refusal(
                    task_id,
                    REFUSAL_AUTHORITY,
                    f"leader authority boundary: {why}",
                    issuer=issuer,
                    required=required,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    context=self._context,
                )
            requirement_reason = f"{requirement_reason}; {why}"

        # 3. Bounds: depth / fan-out / hops / budget, before any target is named.
        guard = self._context.check()
        if not guard.allowed:
            return _refusal(
                task_id,
                guard.code,
                f"delegation bound: {guard.detail}",
                issuer=issuer,
                guard=guard,
                required=required,
                attempt=attempt,
                max_attempts=max_attempts,
                context=self._context,
            )

        # 4. Candidate pool, then the HARD capability filter. The lineage is NOT
        #    applied as an exclusion here: the cycle guard is a question about
        #    the CAPABILITY-eligible pool, and folding the lineage into the
        #    filter would report a ping-pong as "nobody is capable".
        profiles = registry.list_bots(include_archived=False)
        if candidate_names:
            wanted = {str(item).strip().lower() for item in candidate_names}
            profiles = [p for p in profiles if p.name.lower() in wanted]
        eligibility = eligible_candidates(profiles, required, exclude=list(exclude or []))
        if not eligibility.eligible:
            detail = "; ".join(f"{item['bot']}: {item['detail']}" for item in eligibility.rejected[:8]) or "no live agent on the roster"
            return _refusal(
                task_id,
                REFUSAL_NO_ELIGIBLE_AGENT,
                f"no agent declares the required capability {sorted(required) or '(none)'}. Rejected: {detail}",
                issuer=issuer,
                rejected=eligibility.rejected,
                required=required,
                attempt=attempt,
                max_attempts=max_attempts,
                context=self._context,
            )

        # 5. Cycle guard, over the capability SURVIVORS. ``check_cycle`` is the
        #    ONLY decision point here — an earlier version re-implemented the
        #    lineage test inline, which left the guard itself un-load-bearing
        #    (mutating it changed nothing, because the inline copy still refused).
        #    This is the ping-pong case: every agent that could do this work
        #    already holds it, so handing it on would loop.
        survivors: list[str] = []
        cycle: GuardVerdict | None = None
        for name in eligibility.eligible:
            verdict = self._context.check_cycle(name)
            if verdict.allowed:
                survivors.append(name)
            elif cycle is None:
                cycle = verdict
        if not survivors:
            cyclic = list(eligibility.eligible)
            return _refusal(
                task_id,
                REFUSAL_CYCLE,
                f"delegation bound: every capability-eligible agent {cyclic} already holds this task in its lineage "
                f"{' -> '.join(self._context.lineage)}; handing it on would loop",
                issuer=issuer,
                guard=cycle or GuardVerdict(False, REFUSAL_CYCLE, "no candidate outside the delegation lineage"),
                rejected=eligibility.rejected,
                required=required,
                attempt=attempt,
                max_attempts=max_attempts,
                context=self._context,
            )
        # Agents in the lineage stay out of the auction, and stay in the record.
        for name in eligibility.eligible:
            if name not in survivors:
                eligibility.rejected.append(
                    {
                        "bot": name,
                        "code": REFUSAL_CYCLE,
                        "detail": f"already holds this task in lineage {' -> '.join(self._context.lineage)}",
                    }
                )
        eligibility.eligible = survivors

        by_name = {p.name.lower(): p for p in profiles}
        eligible_profiles = [by_name[name] for name in eligibility.eligible if name in by_name]
        loads = {profile.name.lower(): _load(profile) for profile in eligible_profiles}

        # 6. Declared fit, over the SURVIVING candidates only.
        #    ``match_bot_for_task`` has no production caller anywhere else in the
        #    tree; this is where it earns its keep.
        ranked = match_bot_for_task(
            objective,
            required_skills=sorted(required) or None,
            registry=registry,
            limit=len(eligible_profiles),
        )
        ranked_by_name = {str(item.get("bot_name", "")).lower(): item for item in ranked}

        # 7. The real contract-net auction over the surviving candidates.
        workers = [
            SwarmWorkerAgent(
                agent_id=profile.name.lower(),
                capabilities=sorted(profile_capability_tags(profile)),
                base_load=loads[profile.name.lower()],
                reputation=_reputation(profile),
            )
            for profile in eligible_profiles
        ]
        engine = self._engine_factory()
        self._engine = engine
        engine.register_workers_batch(workers)
        announcement = TaskAnnouncement(
            # Auction identity: root task, the dispatched unit, the attempt, and
            # a per-call sequence number. See ``_auction_seq`` for why the last
            # one is load-bearing.
            task_id=f"{self._context.task_id}:{task_id}:a{max(1, int(attempt))}#{next(self._auction_seq)}",
            topic=DISPATCH_TOPIC,
            description=objective[:4000],
            domain_tags=sorted(required),
            token_budget=max(1, min(int(token_budget), max(1, self._context.tokens_remaining))),
            deadline_seconds=max(1.0, float(deadline_seconds)),
        )
        # The engine scores bids internally and does not retain them, so score
        # the SAME bids here with the engine's own bidder/scorer to record what
        # actually competed. Deterministic: identical workers, announcement and
        # weights, so these are the engine's numbers, not a reconstruction.
        bids: list[Any] = []
        for worker in workers:
            bid = worker.calculate_bid(announcement)
            if bid is None:
                continue
            engine.score_bid(bid, announcement.token_budget)
            bids.append(bid)
        award = engine.conduct_auction(announcement)
        if award is None:
            return _refusal(
                task_id,
                REFUSAL_NO_ELIGIBLE_AGENT,
                f"capability-eligible agents {eligibility.eligible} all declined to bid",
                issuer=issuer,
                rejected=eligibility.rejected,
                required=required,
                attempt=attempt,
                max_attempts=max_attempts,
                context=self._context,
            )
        target = award.contractor_agent_id
        target_profile = by_name.get(target)
        matched = sorted(required & profile_capability_tags(target_profile)) if target_profile is not None else sorted(required)

        # 8. Record the choice AND the reason, in the same call.
        reason = _build_reason(
            issuer=issuer,
            target=target,
            required=required,
            matched=matched,
            constraint_reason=requirement_reason,
            fit_reasons=list(ranked_by_name.get(target, {}).get("reasons", [])),
            fit_score=ranked_by_name.get(target, {}).get("match_score"),
            award=award,
            eligible=eligibility.eligible,
        )
        decision = DispatchDecision(
            task_id=task_id,
            outcome=DispatchOutcome.DISPATCHED,
            target=target,
            reason=reason,
            issuer=issuer,
            required_capability_tags=tuple(sorted(required)),
            matched_capability_tags=tuple(matched),
            missing_capability_tags=(),
            candidates=[
                {
                    "bot_name": name,
                    "offered_capability_tags": sorted(profile_capability_tags(by_name[name])),
                    "match_score": ranked_by_name.get(name, {}).get("match_score"),
                    "load": loads.get(name, 0.5),
                    "reputation": _reputation(by_name[name]),
                    "reasons": list(ranked_by_name.get(name, {}).get("reasons", [])),
                }
                for name in eligibility.eligible
                if name in by_name
            ],
            rejected=list(eligibility.rejected),
            award=award.to_dict(),
            bids=[bid.to_dict() for bid in bids],
            guard=guard.to_dict(),
            attempt=attempt,
            max_attempts=max_attempts,
            depth=self._context.depth,
            hops=self._context.hops,
        )
        decision.ledger_seq = _record_ledger(
            task_id=task_id,
            from_ref=issuer,
            to_ref=target,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details={
                "method": DISPATCH_METHOD,
                "required_capability_tags": sorted(required),
                "matched_capability_tags": matched,
                "eligible": eligibility.eligible,
                "rejected": eligibility.rejected,
                "award": award.to_dict(),
                "objective": objective[:500],
                "depth": self._context.depth,
                "hops": self._context.hops,
                "lineage": list(self._context.lineage),
                "limits": self._context.limits.to_dict(),
            },
        )
        return decision

    def dispatch_with_handoff(
        self,
        task_id: str,
        objective: str,
        *,
        required_capability_tags: Sequence[str] | None = None,
        proposed_owners: Sequence[str] | None = None,
        attempt: int = 1,
        max_attempts: int = 3,
        **kwargs: Any,
    ) -> tuple[DispatchDecision, Any]:
        """Dispatch, then execute the real bot handoff to the selected target.

        The handoff is the *existing* transfer primitive
        (:func:`alpha.bots.handoff.execute_handoff`); this only decides WHO, and
        tells the primitive not to double-record the ledger entry the decision
        already wrote.
        """
        decision = self.dispatch(
            task_id,
            objective,
            required_capability_tags=required_capability_tags,
            proposed_owners=proposed_owners,
            attempt=attempt,
            max_attempts=max_attempts,
            **kwargs,
        )
        if not decision.ok or decision.target is None:
            return decision, None
        from alpha.bots.handoff import execute_handoff

        package = execute_handoff(
            task_id=task_id,
            from_bot=decision.issuer,
            to_bot=decision.target,
            objective=objective,
            context_summary=decision.reason,
            attempt=attempt,
            max_attempts=max_attempts,
            reason=decision.reason,
            record_ledger=False,  # already recorded, with the selection reason
            registry=self.registry,
        )
        return decision, package


def _derive_requirement(
    proposed_owners: Sequence[str],
    *,
    registry: BotRegistry,
) -> tuple[frozenset[str], str]:
    """Registry-scoped wrapper over the shared requirement derivation.

    Resolves the proposed owners' declared surfaces first so the shared rule in
    :mod:`alpha.capabilities.eligibility` never has to reach for a global
    registry, and so a caller with its own roster gets its own answer.
    """
    offered: dict[str, frozenset[str]] = {}
    for owner in proposed_owners:
        key = str(owner or "").strip().lower()
        if not key:
            continue
        profile = registry.get_bot(key)
        if profile is not None:
            offered[key] = profile_capability_tags(profile)
    return required_capability_tags(proposed_owners, offered_by=offered)


def _build_reason(
    *,
    issuer: str,
    target: str,
    required: frozenset[str],
    matched: list[str],
    constraint_reason: str,
    fit_reasons: list[str],
    fit_score: Any,
    award: Any,
    eligible: list[str],
) -> str:
    """Compose the one-sentence reason that is both returned and recorded.

    The same string goes into the dispatch decision and the ledger entry, so the
    explanation a reader sees later cannot drift from the decision that was
    actually taken.
    """
    if required:
        capability_clause = f"@{target} declares the required capability {matched} ({len(eligible)} eligible agent(s): {', '.join(eligible)})"
    else:
        capability_clause = f"no capability constraint; @{target} won the auction over {', '.join(eligible)}"
    fit_clause = f"; declared fit: {'; '.join(fit_reasons)}" if fit_reasons else ""
    score_clause = f" (match_score={fit_score})" if fit_score is not None else ""
    return (
        f"{capability_clause}{score_clause}{fit_clause}; "
        f"auction award score={round(getattr(award, 'winning_bid_score', 0.0), 3)}; "
        f"requirement: {constraint_reason}"
    )


def get_leader_dispatcher(
    task_id: str,
    *,
    registry: BotRegistry | None = None,
    limits=DEFAULT_LIMITS,
    root: str = ALPHA_LEADER_NAME,
    token_budget: int | None = None,
    cost_budget_usd: float | None = None,
) -> CapabilityDispatcher:
    """A dispatcher rooted at the default leader, with a fresh bounded context."""
    from alpha.bots.delegation import root_context

    return CapabilityDispatcher(
        root_context(root, task_id, limits=limits, token_budget=token_budget, cost_budget_usd=cost_budget_usd),
        registry=registry,
    )


def dispatch_task(
    task_id: str,
    objective: str,
    *,
    required_capability_tags: Sequence[str] | None = None,
    proposed_owners: Sequence[str] | None = None,
    registry: BotRegistry | None = None,
    limits=DEFAULT_LIMITS,
    context: DelegationContext | None = None,
    attempt: int = 1,
    max_attempts: int = 3,
    exclude: Sequence[str] | None = None,
) -> DispatchDecision:
    """Convenience wrapper: one leader-initiated dispatch, no object ceremony."""
    dispatcher = (
        CapabilityDispatcher(context, registry=registry)
        if context is not None
        else get_leader_dispatcher(task_id, registry=registry, limits=limits)
    )
    return dispatcher.dispatch(
        task_id,
        objective,
        required_capability_tags=required_capability_tags,
        proposed_owners=proposed_owners,
        exclude=exclude,
        attempt=attempt,
        max_attempts=max_attempts,
    )


__all__ = [
    "DEFAULT_DEADLINE_SECONDS",
    "DEFAULT_TASK_TOKEN_BUDGET",
    "DISPATCH_DOMAIN",
    "DISPATCH_METHOD",
    "DISPATCH_TOPIC",
    "REASON_UNCONSTRAINED",
    "CapabilityDispatcher",
    "DispatchDecision",
    "DispatchOutcome",
    "dispatch_task",
    "get_leader_dispatcher",
]
