"""SWARM node execution: member dispatch, policy aggregation, and ledger wiring.

The SWARM node is handled by the runtime's structural-node path, like PARALLEL
and RACE. It differs from PARALLEL in three load-bearing ways:

- **Members are dynamic.** A swarm reads its member list from node config at
  execution time; members can be added or removed between runs without changing
  the graph structure.
- **Aggregation is policy-driven.** FIRST_SUCCESS, QUORUM, ALL, ANY — chosen
  per swarm, not implied by graph edges.
- **The cost ledger is real.** Per-member token/cost usage is accumulated from
  actual runner results, never estimated.

Members run on a **bounded pool** (``max_concurrency``, default 4): the swarm
topology is the point of the node, so dispatching members one at a time would
make the construct a sequential loop wearing swarm's name. The bound is clamped
to the member count — a swarm is a fan-out, not a thread bomb.

When no runner is bound, members fail with the same honest reason any runnable
node fails with — the swarm never fabricates member results. The runner result
contract mirrors the engine's own: ``status == "completed"`` succeeds, anything
else fails with the returned reason.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from alpha.workflow.models import WorkflowNode, WorkflowRun
from alpha.workflow.swarm.ledger import AggregationPolicy, SwarmLedger, SwarmMember

# A runner is called with (member_id, prompt) and returns a dict with at least
# ``status`` plus optionally ``output``, ``evidence``, ``tokens_used``,
# ``cost_usd`` — the same shape the engine's own node runner returns.
SwarmMemberRunner = Callable[[str, str], dict[str, Any]]

#: Hard ceiling on concurrent members. A typo'd ``max_concurrency`` must not
#: turn a swarm into a thread bomb against the shared Gateway process.
MAX_SWARM_CONCURRENCY = 32


@dataclass
class SwarmConfig:
    """Resolved swarm node configuration."""

    members: list[dict[str, Any]] = field(default_factory=list)
    aggregation: AggregationPolicy = AggregationPolicy.ALL
    quorum: int = 1
    max_concurrency: int = 4

    @classmethod
    def from_node(cls, node: WorkflowNode) -> SwarmConfig:
        """Parse a SWARM node's config into a SwarmConfig.

        An invalid ``aggregation`` value falls back to ALL (the conservative
        "wait for everyone" reading) rather than raising mid-run — the node
        still fails honestly if that policy cannot be met.
        """
        raw = node.config.get("members") or []
        members = [dict(m) for m in raw if isinstance(m, dict)]

        agg_raw = str(node.config.get("aggregation") or "all").lower()
        try:
            aggregation = AggregationPolicy(agg_raw)
        except ValueError:
            aggregation = AggregationPolicy.ALL

        try:
            quorum = int(node.config.get("quorum") or 1)
        except (TypeError, ValueError):
            quorum = 1
        try:
            max_conc = int(node.config.get("max_concurrency") or 4)
        except (TypeError, ValueError):
            max_conc = 4
        return cls(
            members=members,
            aggregation=aggregation,
            quorum=quorum,
            max_concurrency=max(1, min(max_conc, MAX_SWARM_CONCURRENCY)),
        )


@dataclass
class SwarmOutcome:
    """Result of executing a SWARM node."""

    succeeded: bool
    outputs: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    ledger: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def _run_one_member(
    member: SwarmMember,
    ledger: SwarmLedger,
    member_runner: SwarmMemberRunner | None,
) -> None:
    """Execute a single member through its runner, retrying up to max_attempts.

    Every failure path lands in the ledger with a reason — a runner exception,
    a non-completed status, and an exhausted attempt budget are all distinct
    disclosures, not one opaque "failed".
    """
    if ledger.should_stop():
        ledger.skip_member(member.member_id, "swarm completed before this member was needed")
        return

    if member_runner is None:
        ledger.fail_member(
            member.member_id,
            "no swarm member runner is bound — the swarm cannot execute without a host",
        )
        return

    ledger.start_member(member.member_id)
    last_error = ""
    for attempt in range(1, member.max_attempts + 1):
        member.attempts = attempt
        try:
            result = member_runner(member.member_id, member.prompt)
        except Exception as exc:  # noqa: BLE001 — a runner failure is a member failure
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        if not isinstance(result, dict):
            last_error = f"runner returned {type(result).__name__}, expected a dict"
            continue

        status = str(result.get("status") or "failed")
        if status == "completed":
            cost = result.get("cost_usd")
            ledger.complete_member(
                member.member_id,
                output=str(result.get("output") or ""),
                evidence=[str(e) for e in (result.get("evidence") or [])],
                tokens_used=int(result.get("tokens_used") or 0),
                cost_usd=float(cost) if cost is not None else None,
            )
            return
        last_error = str(result.get("output") or result.get("reason") or f"status={status}")

    ledger.fail_member(member.member_id, f"exhausted {member.max_attempts} attempt(s): {last_error}")


def execute_swarm_node(
    node: WorkflowNode,
    run: WorkflowRun,
    config: SwarmConfig,
    member_runner: SwarmMemberRunner | None,
    *,
    now: float | None = None,
) -> SwarmOutcome:
    """Execute a swarm node's members and aggregate per the policy.

    Members are dispatched on a bounded pool when ``max_concurrency > 1`` and
    the member list justifies it; the ledger serialises every state transition,
    so the pool never tears the aggregation read.

    This is a synchronous helper — the runtime calls it from the structural-
    node handler, which already runs inside the engine's step boundary.
    """
    del run, now  # the ledger owns member state; both args are the call seam
    if not config.members:
        return SwarmOutcome(
            succeeded=False,
            reason="swarm node has no members configured",
        )

    ledger = SwarmLedger(aggregation=config.aggregation, quorum=config.quorum)
    for index, spec in enumerate(config.members):
        member_id = str(spec.get("id") or f"member-{index}")
        prompt = str(spec.get("prompt") or spec.get("task") or "")
        try:
            max_attempts = max(1, int(spec.get("max_attempts") or 1))
        except (TypeError, ValueError):
            max_attempts = 1
        ledger.add_member(SwarmMember(member_id=member_id, prompt=prompt, max_attempts=max_attempts))

    members = ledger.members()
    workers = min(config.max_concurrency, len(members))
    if workers > 1 and len(members) > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="swarm") as pool:
            futures = [pool.submit(_run_one_member, member, ledger, member_runner) for member in members]
            for future in futures:
                future.result()  # propagate only executor-level errors
    else:
        for member in members:
            _run_one_member(member, ledger, member_runner)

    state = ledger.state
    counts = state.counts()
    outputs = [m.output for m in state.members if m.status.value == "succeeded" and m.output]
    evidence: list[str] = []
    for member in state.members:
        evidence.extend(member.evidence)
        if member.status.value == "failed" and member.failure_reason:
            evidence.append(f"member {member.member_id} failed: {member.failure_reason}")

    reason = ""
    if not state.succeeded:
        reason = (
            f"swarm aggregation {config.aggregation.value} not satisfied: "
            f"{counts.get('succeeded', 0)} succeeded, "
            f"{counts.get('failed', 0)} failed, "
            f"{counts.get('skipped', 0)} skipped, "
            f"{counts.get('pending', 0) + counts.get('running', 0)} incomplete"
        )

    return SwarmOutcome(
        succeeded=state.succeeded,
        outputs=outputs,
        evidence=evidence,
        ledger=state.model_dump(mode="json"),
        reason=reason,
    )


__all__ = [
    "MAX_SWARM_CONCURRENCY",
    "SwarmConfig",
    "SwarmMemberRunner",
    "SwarmOutcome",
    "execute_swarm_node",
]
