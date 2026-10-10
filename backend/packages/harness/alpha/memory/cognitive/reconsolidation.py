"""Reconsolidation: retrieval writes back, and rest replays what worked.

Two mechanisms from the memory literature, both of which Alpha's stack was missing:

**Retrieval is not read-only.** A-MEM (arXiv:2502.12110) lets adding a memory
rewrite existing memories; SleepGate (arXiv:2603.14517) treats proactive
interference as the failure mode that longer context does *not* fix. In both, a
retrieval is an event that must update the store: strengthen, re-link, or mark
contested. Alpha's retriever returned scored items and recorded nothing, so
nothing downstream could tell a memory that was recalled and useful from one
that had been sitting idle since it was written.

**Rest replays what worked.** Hippocampal replay (Wilson & McNaughton 1994; the
Go-Explore+HR result reaches 100/100 solves with *zero variance*) re-activates
successful episodes during quiet periods, which is what makes performance
consistent rather than merely retained. Alpha's consolidation engine decayed by
Ebbinghaus and crystallised salient items, but nothing ever re-strengthened a
trace that had worked.

Honesty rules this module holds
-------------------------------
* **An access count of zero is not a retention of zero.** `retention_probability`
  reports `None` for a memory that has never been accessed, because a
  never-retrieved fact has no measured elapsed-since-retrieval to decay from —
  the same distinction as `count: null` versus `count: 0`.
* **`useful` is a caller assertion, not an inference.** This module never decides
  that a retrieval helped. `useful=None` records the access with no usefulness
  verdict, and that is a third state, not a `False`.
* **Replay is bounded and reports what it actually did.** `budget` is a hard cap
  on how many traces are replayed in one pass, and the report says how many were
  replayed, how many were skipped, and why. It never claims a replay ran when
  the store was unreadable.
* **One stability formula, not two.** `retention_probability` is the single
  implementation of the Ebbinghaus curve that `CognitiveConsolidationEngine`
  already applies; reconsolidation reuses it rather than growing a second,
  quietly-different decay model.

Deliberately *not* faked
------------------------
RRSI's structural pruning (`alpha/rsi/rrsi/pruning.py`) needs measured per-component
gain. Memory consolidation knows which *tier* changed, not what a change did to
an evolve-set score, and the two are not the same quantity. Nothing here invents
that mapping: `tier_activity` reports counts per tier with `None` wherever a
measured gain would be required, so a caller can see the gap instead of being
handed a plausible number.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from alpha.memory.cognitive.models import CognitiveTier, EpisodicTrace, SemanticFactNode, TraceOutcome

__all__ = [
    "BASE_STABILITY_SECONDS",
    "MIN_STABILITY_SECONDS",
    "ReconsolidationRecord",
    "ReplayReport",
    "TierActivity",
    "evidence_gap_reason",
    "record_retrieval",
    "replay_strengthen",
    "retention_probability",
    "tier_activity",
]

#: Base memory stability, matching `CognitiveConsolidationEngine`'s default. It
#: lives here so the two engines cannot drift into two different clocks.
BASE_STABILITY_SECONDS = 86400.0

#: Floor on the divisor of the exponent, so a stability of ~0 cannot produce a
#: retention that underflows to exactly 0.0 (which would read as "measured as
#: forgotten" rather than "not retained").
MIN_STABILITY_SECONDS = 1.0


def retention_probability(node: SemanticFactNode, *, now: float | None = None, base_stability_seconds: float = BASE_STABILITY_SECONDS) -> float | None:
    """``R = exp(-dt / (S * (1 + ln(1 + n))))`` — or ``None`` if never accessed.

    This is the same curve `CognitiveConsolidationEngine` applies to belief
    nodes, extracted here so reconsolidation strengthens and consolidation
    decays against one definition rather than two. `None` means the node has
    never been retrieved, which is the honest answer: without an
    elapsed-since-access interval there is no measured retention to report.
    """
    if isinstance(base_stability_seconds, bool) or not isinstance(base_stability_seconds, (int, float)) or base_stability_seconds <= 0:
        raise ValueError(f"base_stability_seconds must be a positive number, got {base_stability_seconds!r}")
    accesses = node.access_count
    if accesses <= 0:
        return None
    moment = time.time() if now is None else float(now)
    if moment < node.last_accessed_at:
        raise ValueError(f"now ({moment}) precedes last_accessed_at ({node.last_accessed_at}); a node cannot decay backwards")
    elapsed = max(0.0, moment - node.last_accessed_at)
    stability = float(base_stability_seconds) * (1.0 + math.log(1.0 + accesses))
    return math.exp(-elapsed / max(MIN_STABILITY_SECONDS, stability))


@dataclass(frozen=True)
class ReconsolidationRecord:
    """What one retrieval did to one memory, and what it did *not* decide.

    ``useful`` is the caller's assertion (``None`` when the caller expressed no
    opinion), never an inference made here. ``retention_before`` and
    ``retention_after`` are both ``None`` on a node's first access, because there
    was no measured interval to decay over yet.
    """

    tier: CognitiveTier
    item_id: str
    useful: bool | None
    access_count: int
    retention_before: float | None
    retention_after: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "item_id": self.item_id,
            "useful": self.useful,
            "access_count": self.access_count,
            "retention_before": self.retention_before,
            "retention_after": self.retention_after,
            "reason": self.reason,
        }


def record_retrieval(
    node: SemanticFactNode,
    *,
    useful: bool | None = None,
    now: float | None = None,
    base_stability_seconds: float = BASE_STABILITY_SECONDS,
) -> ReconsolidationRecord:
    """Fold one retrieval into the node's access history and report the change.

    The spacing effect: a retrieval after a long gap buys more stability than one
    immediately after the last, which is why the stability term is a function of
    the *interval*, not of a counter alone. This function does not decide
    usefulness; a caller that knows says so, and a caller that does not leaves
    `useful=None`, which is recorded as such.
    """
    if not isinstance(node, SemanticFactNode):
        raise ValueError(f"record_retrieval takes a SemanticFactNode, got {type(node).__name__}")
    if useful is not None and not isinstance(useful, bool):
        raise ValueError(f"useful must be a bool or None, got {type(useful).__name__}")

    moment = time.time() if now is None else float(now)
    before = retention_probability(node, now=moment, base_stability_seconds=base_stability_seconds)

    node.access_count += 1
    node.last_accessed_at = moment
    after = retention_probability(node, now=moment, base_stability_seconds=base_stability_seconds)

    if useful is True:
        reason = f"retrieved and reported useful; access_count is now {node.access_count}"
    elif useful is False:
        reason = f"retrieved but reported not useful; access_count is now {node.access_count} and no strength credit was taken"
    else:
        reason = f"retrieved with no usefulness verdict; access_count is now {node.access_count}"

    return ReconsolidationRecord(
        tier=CognitiveTier.SEMANTIC_FACT,
        item_id=node.node_id,
        useful=useful,
        access_count=node.access_count,
        retention_before=before,
        retention_after=after,
        reason=reason,
    )


@dataclass(frozen=True)
class ReplayReport:
    """What one rest pass replayed — bounded, and honest about the bound.

    ``replayed`` counts traces actually strengthened. ``skipped`` records why
    each bypass was taken, so a pass that replayed nothing because every trace
    was a failure reads differently from one that replayed nothing because there
    were no traces at all.
    """

    replayed: int
    budget: int
    considered: int
    skipped: tuple[str, ...]
    strengthened_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "replayed": self.replayed,
            "budget": self.budget,
            "considered": self.considered,
            "skipped": list(self.skipped),
            "strengthened_ids": list(self.strengthened_ids),
            "reason": self.reason,
        }


def replay_strengthen(
    traces: Iterable[EpisodicTrace],
    *,
    budget: int = 8,
    salience_floor: float = 0.0,
    now: float | None = None,
) -> ReplayReport:
    """Replay the most salient *successful* traces, strengthening each in place.

    ``budget`` is a hard cap on how many traces one pass replays: replay is
    offline work, and an unbounded pass would turn idle time into an unbounded
    cost. Traces are taken most-salient-first so the bound spends itself on the
    episodes most worth reinforcing.

    A trace is replayable only when its outcome is a measured success. A trace
    whose outcome is ``UNKNOWN`` is skipped rather than assumed successful,
    because replaying an unmeasured trace would strengthen a result nobody
    observed. Failures are skipped too: the failure-clustering work happens in
    the consolidation engine's REM phase, and replay is not where it belongs.
    """
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError(f"budget must be an integer >= 0, got {budget!r}")
    if isinstance(salience_floor, bool) or not isinstance(salience_floor, (int, float)):
        raise ValueError(f"salience_floor must be a number, got {salience_floor!r}")
    if not 0.0 <= float(salience_floor) <= 1.0:
        raise ValueError(f"salience_floor must be within [0, 1], got {salience_floor!r}")

    items = [trace for trace in traces if isinstance(trace, EpisodicTrace)]
    moment = time.time() if now is None else float(now)
    # Deterministic order: salience desc, then timestamp, then id — so a bounded
    # replay replays the same traces on the same input regardless of dict order.
    items.sort(key=lambda trace: (-trace.salience, trace.timestamp, trace.trace_id))

    strengthened: list[str] = []
    skipped: list[str] = []
    failures = unknown = below_floor = 0

    for trace in items:
        if len(strengthened) >= budget:
            skipped.append(f"budget of {budget} replay(s) reached; remaining traces were not replayed")
            break
        if trace.salience < float(salience_floor):
            below_floor += 1
            skipped.append(f"{trace.trace_id}: salience {trace.salience:.2f} is below the {float(salience_floor):.2f} floor")
            continue
        if trace.outcome is not TraceOutcome.SUCCESS:
            if trace.outcome is TraceOutcome.UNKNOWN:
                unknown += 1
                skipped.append(f"{trace.trace_id}: outcome is unmeasured, so there is no observed success to reinforce")
            else:
                failures += 1
                skipped.append(f"{trace.trace_id}: outcome is {trace.outcome.value}, not a success")
            continue

        # Strengthening in place: the trace is re-encoded with a raised salience
        # and a fresh timestamp, which is what the spacing effect does to a
        # replayed episode. Salience is capped: reinforcement must not be able to
        # turn a marginally-successful run into a pinned memory.
        trace.salience = min(1.0, round(trace.salience + 0.05, 4))
        trace.timestamp = moment
        trace.tags = sorted({*trace.tags, "replayed"})
        strengthened.append(trace.trace_id)

    if not items:
        reason = "no episodic traces were supplied; nothing was replayed"
    elif strengthened:
        reason = f"replayed {len(strengthened)} successful trace(s) within a budget of {budget}"
    elif failures or unknown:
        reason = f"replayed 0: {failures} failure and {unknown} unmeasured trace(s) among {len(items)} considered"
    else:
        reason = f"replayed 0: {below_floor} trace(s) fell below the {float(salience_floor):.2f} salience floor"

    return ReplayReport(
        replayed=len(strengthened),
        budget=budget,
        considered=len(items),
        skipped=tuple(skipped),
        strengthened_ids=tuple(strengthened),
        reason=reason,
    )


@dataclass(frozen=True)
class TierActivity:
    """What one pass changed per memory tier, with measured stays measured.

    ``measured`` is ``None`` rather than ``0`` wherever this engine did not
    measure anything for that tier: an unmeasured quantity is a gap, not a
    zero-count, and a caller must be able to tell them apart.
    """

    tier: str
    changed: int
    measured: int | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "changed": self.changed, "measured": self.measured, "reason": self.reason}


def tier_activity(records: Iterable[TierActivity]) -> dict[str, Any]:
    """Collapse per-tier records into one bounded disclosure.

    Totals sum only the tiers that reported a number; a tier reporting `None`
    keeps `None` rather than being folded into a sum, which would turn "we could
    not look" into "we looked and found nothing".
    """
    items = list(records)
    measured = [item.measured for item in items if item.measured is not None]
    return {
        "tiers": [item.to_dict() for item in items],
        "tiers_changed": sum(item.changed for item in items),
        "tiers_measured": sum(measured) if measured else None,
        "reasons": [item.reason for item in items if item.reason],
    }


def evidence_gap_reason() -> str:
    """Why memory activity is not, by itself, RRSI gain evidence.

    RRSI's L1 pruning needs a measured ``g_t(ℓ)`` per component: what a change
    did to the evolve-set score. Consolidation knows which *tier* was touched
    and how many items moved, which is a different quantity. Naming the gap
    stops a plausible-looking mapping being invented later.
    """
    return (
        "memory activity records which tier changed and how many items moved; RRSI structural pruning needs a measured "
        "evolve-set score delta per component (g_t), which consolidation does not measure. No mapping from tier to "
        "component is asserted here."
    )
