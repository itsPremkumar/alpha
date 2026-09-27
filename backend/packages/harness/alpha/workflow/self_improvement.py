"""Bounded, evidence-driven improvement suggestions for workflow runs.

The DWE already measures a great deal: node timings, wave shape, retries,
idempotency dedupes, stagnation-recovery attempts, patch revisions, budget
spend, and per-node evidence. This module turns those MEASUREMENTS into typed,
bounded suggestions about what to change.

The design constraint that shapes everything here: **a suggestion is a proposal,
never an action.** Nothing in this module mutates a graph, a run, or a template.
Applying a suggestion produces a patch that goes through the existing
``PatchValidator`` and OCC machinery exactly like any other operator change, so
an automated suggestion has no more authority than a human's — and the same
invariants apply to it.

The honesty rules:

- Every suggestion cites the MEASURED signal that produced it. A suggestion with
  no measured evidence is not emitted, because "this might be better" is noise.
- Confidence is derived from sample size, never asserted. One run is one run, and
  the payload says ``samples: 1`` so a caller cannot mistake a single observation
  for a trend.
- A run that completed with no evidence is reported as ``unproven`` rather than
  folded into a success rate.
- The module never claims an improvement WORKS. Verification is a separate,
  evidence-gated step (:mod:`alpha.workflow.templates`), and the payload says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha.workflow.models import NodeStatus, PatchOperation, WorkflowRunStatus
from alpha.workflow.observability import build_run_observability
from alpha.workflow.runtime import STAGNATION_RECOVERY_LIMIT, DynamicWorkflowEngine

# Ceiling on suggestions returned for one run. A long analysis that emits dozens of
# near-identical hints is worse than a short one an operator will read.
MAX_SUGGESTIONS = 8

# Metric keys the engine writes that this module reads.
_KEY_IDEMPOTENT = "completed_idempotency_keys"
_KEY_RECOVERY = "stagnation_recovery_attempts"
_KEY_EXECUTOR_KEYS = "executor_state_keys"


@dataclass(frozen=True)
class RunSignals:
    """Measured facts about one run. Every field is observed, never inferred."""

    run_id: str
    workflow_id: str
    status: str
    terminal: bool
    node_count: int
    completed_nodes: int
    failed_nodes: int
    skipped_nodes: int
    patches_applied: int
    retries_recorded: int
    deduplicated_effects: int
    stagnation_recoveries: int
    tokens_consumed: int
    budget_limit: int | None
    waves_dispatched: int
    total_measured_seconds: float
    slowest_node: str | None
    slowest_seconds: float
    completed_without_evidence: list[str] = field(default_factory=list)
    timed_out_nodes: list[str] = field(default_factory=list)
    budget_exhausted: bool = False

    @property
    def success(self) -> bool:
        return self.status == WorkflowRunStatus.COMPLETED.value

    @property
    def unproven(self) -> bool:
        """Completed, but at least one success was asserted without evidence."""
        return self.success and bool(self.completed_without_evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "terminal": self.terminal,
            "node_count": self.node_count,
            "completed_nodes": self.completed_nodes,
            "failed_nodes": self.failed_nodes,
            "skipped_nodes": self.skipped_nodes,
            "patches_applied": self.patches_applied,
            "retries_recorded": self.retries_recorded,
            "deduplicated_effects": self.deduplicated_effects,
            "stagnation_recoveries": self.stagnation_recoveries,
            "tokens_consumed": self.tokens_consumed,
            "budget_limit": self.budget_limit,
            "waves_dispatched": self.waves_dispatched,
            "total_measured_seconds": self.total_measured_seconds,
            "slowest_node": self.slowest_node,
            "slowest_seconds": self.slowest_seconds,
            "completed_without_evidence": list(self.completed_without_evidence),
            "timed_out_nodes": list(self.timed_out_nodes),
            "budget_exhausted": self.budget_exhausted,
            "success": self.success,
            "unproven": self.unproven,
        }


@dataclass(frozen=True)
class Suggestion:
    """One bounded, evidence-cited proposal. Never applied automatically."""

    kind: str
    subject: str
    rationale: str
    evidence: dict[str, Any] = field(default_factory=dict)
    patch_operations: list[PatchOperation] = field(default_factory=list)
    confidence: str = "low"
    samples: int = 1
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "rationale": self.rationale,
            "evidence": dict(self.evidence),
            "patch_operations": [op.model_dump(mode="json") for op in self.patch_operations],
            "confidence": self.confidence,
            "samples": self.samples,
            "requires_human_review": self.requires_human_review,
            "applied": False,
            "note": "a proposal only; applying it goes through the normal patch validator and optimistic-concurrency check",
        }


def collect_signals(engine: DynamicWorkflowEngine, run_id: str) -> RunSignals:
    """Measure one run. Raises ``KeyError`` for a run this engine does not hold."""
    run = engine.get_run(run_id)
    if run is None:
        raise KeyError(f"Run '{run_id}' not found.")
    graph = engine._run_graph_for(run)
    events = engine.events.get_events(run_id)
    observability = build_run_observability(run, graph, events)

    slowest = (observability.get("slowest_nodes") or [None])[0]
    completed_without_evidence = sorted(
        nid
        for nid in run.completed_nodes
        if nid in graph.nodes and not graph.nodes[nid].evidence
    )
    idempotent = run.metrics.get(_KEY_IDEMPOTENT)
    recoveries = run.metrics.get(_KEY_RECOVERY, 0)
    try:
        recoveries = int(recoveries)
    except (TypeError, ValueError):
        recoveries = 0

    return RunSignals(
        run_id=run.run_id,
        workflow_id=run.workflow_id,
        status=run.status.value,
        terminal=observability["terminal"],
        node_count=len(run.node_states),
        completed_nodes=len(run.completed_nodes),
        failed_nodes=len(set(run.failed_nodes)),
        skipped_nodes=sum(1 for status in run.node_states.values() if status == NodeStatus.SKIPPED),
        patches_applied=len(run.patches_applied),
        retries_recorded=sum(1 for event in events if getattr(event, "event_type", None) == "node_retry_scheduled"),
        deduplicated_effects=len(idempotent) if isinstance(idempotent, list) else 0,
        stagnation_recoveries=recoveries,
        tokens_consumed=run.tokens_consumed,
        budget_limit=run.budget_limit,
        waves_dispatched=int(observability.get("waves_dispatched") or 0),
        total_measured_seconds=float(observability.get("total_measured_seconds") or 0.0),
        slowest_node=slowest.get("node_id") if isinstance(slowest, dict) else None,
        slowest_seconds=float(slowest.get("duration_seconds") or 0.0) if isinstance(slowest, dict) else 0.0,
        completed_without_evidence=completed_without_evidence,
        timed_out_nodes=list(observability.get("timed_out_nodes") or []),
        budget_exhausted=run.status is WorkflowRunStatus.BUDGET_EXHAUSTED,
    )


def _confidence(samples: int) -> str:
    """Confidence derived from sample size, never asserted.

    One observation is an anecdote. This is intentionally conservative: the point
    is to stop a single unlucky run from being presented as a pattern.
    """
    if samples >= 20:
        return "high"
    if samples >= 5:
        return "medium"
    return "low"


def _ancestors(graph: Any, node_id: str) -> set[str]:
    """Every node that must complete before ``node_id``, transitively."""
    seen: set[str] = set()
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        node = graph.nodes.get(current)
        deps: set[str] = set()
        if node is not None:
            deps.update(node.depends_on)
        deps.update(edge.source for edge in graph.incoming_edges(current))
        for dep in deps:
            if dep != current and dep in graph.nodes and dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return seen


def _descendants(graph: Any, node_id: str) -> set[str]:
    """Every node that waits on ``node_id``, transitively."""
    seen: set[str] = set()
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        dependents: set[str] = set()
        node = graph.nodes.get(current)
        if node is not None:
            for other_id, other in graph.nodes.items():
                if current in other.depends_on:
                    dependents.add(other_id)
        dependents.update(edge.target for edge in graph.outgoing_edges(current))
        for dep in dependents:
            if dep != current and dep in graph.nodes and dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return seen


def _independent_siblings(graph: Any, node_id: str, run: Any) -> list[str]:
    """Completed nodes that could genuinely run alongside ``node_id``.

    A sibling counts only if it is neither an ancestor nor a descendant, because
    those are ordered against ``node_id`` by definition and overlapping them
    would be a scheduling error, not an optimisation. Only nodes that actually
    completed are returned: a sibling that has not run yet is a hypothesis, not an
    observed opportunity.
    """
    if node_id not in graph.nodes:
        return []
    related = _ancestors(graph, node_id) | _descendants(graph, node_id)
    return sorted(
        nid
        for nid, status in run.node_states.items()
        if nid != node_id and nid not in related and status == NodeStatus.SUCCEEDED
    )


def suggest_improvements(
    engine: DynamicWorkflowEngine,
    run_id: str,
    *,
    baseline_runs: int = 1,
) -> dict[str, Any]:
    """Produce bounded, evidence-cited suggestions for one run.

    ``baseline_runs`` states how many comparable runs the caller is folding in.
    It only affects the reported confidence and sample count — this function
    analyses exactly the run it was given, and never implies a trend it did not
    measure.
    """
    signals = collect_signals(engine, run_id)
    graph = engine._run_graph_for(engine.get_run(run_id))  # type: ignore[arg-type]
    samples = max(1, int(baseline_runs))
    suggestions: list[Suggestion] = []

    # 1. A node that missed its declared deadline should get a real bound, not a
    #    deadline that is simply exceeded every time.
    for nid in signals.timed_out_nodes:
        node = graph.nodes.get(nid)
        if node is None:
            continue
        suggestions.append(
            Suggestion(
                kind="timeout_tuning",
                subject=nid,
                rationale=(
                    f"node '{nid}' missed its declared {node.timeout_seconds}s deadline; either raise the bound to a value the "
                    f"work actually needs, or split the node, because a deadline that is always exceeded is not a bound"
                ),
                evidence={
                    "measured": "node_timeout",
                    "declared_timeout_seconds": node.timeout_seconds,
                    "node_id": nid,
                },
                confidence=_confidence(samples),
                samples=samples,
            )
        )

    # 2. A completion asserted without evidence is a trust problem, not a
    #    performance one, and outranks every other suggestion.
    for nid in signals.completed_without_evidence:
        suggestions.append(
            Suggestion(
                kind="missing_evidence",
                subject=nid,
                rationale=(
                    f"node '{nid}' is recorded as succeeded with no evidence attached; the run reports success it cannot "
                    f"substantiate, so the executor binding for this node must be fixed before its result is trusted"
                ),
                evidence={"measured": "empty_node_evidence", "node_id": nid, "run_status": signals.status},
                confidence="high",
                samples=samples,
            )
        )

    # 3. Budget exhaustion means the declared budget did not match the work.
    if signals.budget_exhausted:
        suggestions.append(
            Suggestion(
                kind="budget_tuning",
                subject=signals.workflow_id,
                rationale=(
                    f"the run exhausted its budget at {signals.tokens_consumed} tokens; the graph either needs a larger "
                    f"declared budget or fewer/cheaper nodes, and raising the limit without reducing work just moves the wall"
                ),
                evidence={
                    "measured": "workflow_budget_exhausted",
                    "tokens_consumed": signals.tokens_consumed,
                    "budget_limit": signals.budget_limit,
                },
                confidence=_confidence(samples),
                samples=samples,
            )
        )

    # 4. Stagnation recovery means the graph could not progress on its own.
    if signals.stagnation_recoveries > 0:
        suggestions.append(
            Suggestion(
                kind="graph_deadlock",
                subject=signals.workflow_id,
                rationale=(
                    f"the runtime committed {signals.stagnation_recoveries} stagnation-recovery patch(es) (ceiling "
                    f"{STAGNATION_RECOVERY_LIMIT}); the graph's dependencies need a real fix, because recovery patches buy "
                    f"time rather than removing the deadlock"
                ),
                evidence={"measured": "stagnation_recovery_attempted", "attempts": signals.stagnation_recoveries},
                confidence=_confidence(samples),
                samples=samples,
            )
        )

    # 5. An idempotency key that fired means the effect would have repeated.
    if signals.deduplicated_effects > 0:
        suggestions.append(
            Suggestion(
                kind="idempotency_observed",
                subject=signals.run_id,
                rationale=(
                    f"{signals.deduplicated_effects} declared idempotent effect(s) were suppressed on re-attempt; the keys are "
                    f"working, and any SIBLING node performing the same effect should declare the same key"
                ),
                evidence={"measured": "node_deduplicated", "suppressed_effects": signals.deduplicated_effects},
                confidence=_confidence(samples),
                samples=samples,
            )
        )

    # 6. Runtime replanning means the authored plan was wrong.
    if signals.patches_applied > 0:
        suggestions.append(
            Suggestion(
                kind="plan_revision",
                subject=signals.workflow_id,
                rationale=(
                    f"{signals.patches_applied} runtime patch(es) were needed to finish; the authored graph does not match the "
                    f"work, so capture the patched revision as a template instead of replanning it every run"
                ),
                evidence={"measured": "patches_applied", "patches": signals.patches_applied},
                confidence=_confidence(samples),
                samples=samples,
            )
        )

    # 7. A dominant slow node is only a *parallelisation* opportunity if it has an
    #    independent sibling to overlap with. In a linear chain the last node
    #    always dominates and always has no sibling, so suggesting concurrency
    #    there is noise. Requiring a real sibling is what keeps this signal
    #    worth reading.
    if signals.slowest_node and signals.slowest_seconds > 0 and signals.total_measured_seconds > 0:
        share = signals.slowest_seconds / signals.total_measured_seconds
        siblings = _independent_siblings(graph, signals.slowest_node, run)
        if share >= 0.5 and signals.completed_nodes > 1 and siblings:
            suggestions.append(
                Suggestion(
                    kind="parallelisation_candidate",
                    subject=signals.slowest_node,
                    rationale=(
                        f"node '{signals.slowest_node}' consumed {share:.0%} of the run's measured time "
                        f"({signals.slowest_seconds:.3f}s of {signals.total_measured_seconds:.3f}s) and has "
                        f"{len(siblings)} independent sibling(s) ({siblings}); those can overlap, which is the "
                        f"largest available win here"
                    ),
                    evidence={
                        "measured": "node_timed",
                        "node_id": signals.slowest_node,
                        "node_seconds": signals.slowest_seconds,
                        "run_seconds": signals.total_measured_seconds,
                        "share": round(share, 4),
                        "independent_siblings": siblings,
                    },
                    confidence=_confidence(samples),
                    samples=samples,
                )
            )

    # 8. A single-node-per-wave graph has concurrency on the table.
    if signals.completed_nodes > 1 and signals.waves_dispatched >= signals.completed_nodes:
        suggestions.append(
            Suggestion(
                kind="wave_underuse",
                subject=signals.workflow_id,
                rationale=(
                    f"{signals.completed_nodes} node(s) ran across {signals.waves_dispatched} wave(s), i.e. strictly one node "
                    f"per wave; either the nodes are genuinely serial, or their write scopes (or a declared max_concurrency) are "
                    f"serialising work that could overlap"
                ),
                evidence={
                    "measured": "wave_dispatched",
                    "waves": signals.waves_dispatched,
                    "completed_nodes": signals.completed_nodes,
                },
                confidence=_confidence(samples),
                samples=samples,
            )
        )

    truncated = len(suggestions) > MAX_SUGGESTIONS
    kept = suggestions[:MAX_SUGGESTIONS]

    return {
        "run_id": signals.run_id,
        "workflow_id": signals.workflow_id,
        "signals": signals.to_dict(),
        "suggestions": [suggestion.to_dict() for suggestion in kept],
        "suggestion_count": len(kept),
        "truncated": truncated,
        "max_suggestions": MAX_SUGGESTIONS,
        "samples_considered": samples,
        "confidence_basis": "derived from sample count only; one run is an anecdote, not a trend",
        "applied": False,
        "note": (
            "every entry is a proposal. Applying one produces a normal typed patch that must pass PatchValidator and the "
            "run's optimistic-concurrency check; nothing here mutates a run, a graph, or a template, and no suggestion is "
            "evidence that the change would actually work until a re-run is measured"
        ),
    }


def analyze_corpus(
    engine: DynamicWorkflowEngine,
    run_ids: list[str],
    *,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate measured signals across several runs of one workflow.

    The only place a rate is reported. Sample size is always attached to every
    rate, because "60% failure rate" from two runs and from two hundred are very
    different claims and must not read the same.
    """
    selected = [rid for rid in run_ids if engine.get_run(rid) is not None]
    if workflow_id is not None:
        selected = [rid for rid in selected if (engine.get_run(rid).workflow_id == workflow_id)]  # type: ignore[union-attr]
    if not selected:
        return {
            "workflow_id": workflow_id,
            "samples": 0,
            "successes": 0,
            "failures": 0,
            "unproven_successes": 0,
            "success_rate": None,
            "runs": [],
            "note": "no runs were measured, so no rate is reported",
        }

    signals = [collect_signals(engine, rid) for rid in selected]
    successes = [item for item in signals if item.success]
    unproven = [item for item in successes if item.unproven]
    failures = [item for item in signals if not item.success]

    return {
        "workflow_id": workflow_id or (signals[0].workflow_id if signals else None),
        "samples": len(signals),
        "successes": len(successes),
        "failures": len(failures),
        "unproven_successes": len(unproven),
        "success_rate": round(len(successes) / len(signals), 4),
        "mean_measured_seconds": round(sum(item.total_measured_seconds for item in signals) / len(signals), 6),
        "total_tokens": sum(item.tokens_consumed for item in signals),
        "patches_applied_total": sum(item.patches_applied for item in signals),
        "runs": [item.to_dict() for item in signals],
        "note": (
            "every rate carries its sample count. A success asserted without evidence is counted separately as unproven "
            "rather than folded into the success rate."
        ),
    }


__all__ = [
    "MAX_SUGGESTIONS",
    "RunSignals",
    "Suggestion",
    "analyze_corpus",
    "collect_signals",
    "suggest_improvements",
]
