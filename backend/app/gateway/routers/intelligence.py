"""Read-only intelligence introspection surface (``/api/intelligence``).

Contract, and why it is shaped this way
---------------------------------------
Every route here **reads real runtime state and writes nothing.** That is not a
limitation being apologised for; it is the point. The dangerous version of this
API is one that can promote, prune, or snapshot from an HTTP call, because an
operator surface that mutates learned state has no gate between "an operator
looked" and "Alpha permanently changed what it is".

Consequently:

* **No ``POST`` that mutates intelligence state exists in this router.** The
  learning actions the design brief describes (``dry-run``, ``evaluate``,
  ``snapshot``, ``rollback``) are available as *library* calls in
  :mod:`alpha.intelligence`, which is where the mode gate and the journal
  actually live. Wiring them to HTTP without an authz decision and a CSRF
  review would be adding a mutation surface nobody asked for.
* Every payload reports availability explicitly. A subsystem that cannot answer
  is ``{"available": false, "reason": "..."}``, not a fabricated value.
* Expensive projections (``/state`` aggregates everything) run in
  ``asyncio.to_thread`` so they never block the event loop — the same rule the
  evolution router follows for its identity/ledger reads.

Route-order note
----------------
``/experts/{expert_id}`` is a single-segment catch-all in intent, so the
collection routes (``/experts``, ``/experts/{id}/lineage``) are declared in a way
that cannot be swallowed: ``/experts`` is an exact match, and every
``/experts/{id}...`` route carries a second segment. This mirrors the ordering
rule the skills and dynamic-workflow routers already document.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])

#: Default page size for journal-style listings. Bounded so a single request
#: cannot ask the Gateway to serialise an unbounded journal.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


async def _read(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking projection in a worker thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# Self-knowledge
# ---------------------------------------------------------------------------


@router.get("/state", summary="Full self-knowledge snapshot of real runtime state")
async def intelligence_state() -> dict[str, Any]:
    """Everything Alpha can currently report about itself.

    ``mode`` and ``config`` come first in the payload so a caller asking "is
    learning on?" does not have to walk the whole document.
    """
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().snapshot)


@router.get("/mode", summary="Effective learning mode and what it permits")
async def intelligence_mode() -> dict[str, Any]:
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().mode)


@router.get("/capabilities", summary="Tools, skills, MCP servers and models, from live state")
async def intelligence_capabilities() -> dict[str, Any]:
    """Capability inventory. Models are read from config, never probed live."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    service = get_self_knowledge()

    async def gather() -> dict[str, Any]:
        return {
            "tools": await _read(service.tools),
            "skills": await _read(service.skills),
            "mcp_servers": await _read(service.mcp_servers),
            "models": await _read(service.models),
        }

    return await gather()


# ---------------------------------------------------------------------------
# Experts
# ---------------------------------------------------------------------------


@router.get("/experts", summary="Every expert: declared catalog entries and learned records")
async def list_experts(
    include_terminal: bool = Query(default=False, description="Include PRUNED records, which are retained for lineage but not routable."),
) -> dict[str, Any]:
    """Declared experts (``alpha.experts``) and learned fabric records, labelled by source."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    projection = await _read(get_self_knowledge().experts)
    if projection.get("available") and not include_terminal:
        experts = projection.get("data") or {}
        if isinstance(experts, dict):
            experts["experts"] = [entry for entry in experts.get("experts", []) if entry.get("status") != "PRUNED"]
    return projection


@router.get("/experts/weak", summary="Learned experts with a measured low success rate")
async def weak_experts(
    max_success_rate: float = Query(default=0.34, ge=0.0, le=1.0),
    min_usage: int = Query(default=1, ge=0),
) -> dict[str, Any]:
    """Weak experts, with unobserved experts reported separately.

    An expert with no observations is *unmeasured*, not weak. Folding the two
    together would prune novelty on the strength of having never been tried.
    """
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().weak_experts, max_success_rate=max_success_rate, min_usage=min_usage)


@router.get("/experts/graph", summary="Capability lineage graph: nodes and edges")
async def expert_graph() -> dict[str, Any]:
    """Node/edge projection with generation, status, usage and success rate."""
    from alpha.intelligence.expert_fabric import ExpertFabricError, get_expert_fabric

    try:
        return await _read(get_expert_fabric().capability_graph)
    except ExpertFabricError as exc:
        # A corrupt fabric is an operator problem, not a 500 with a traceback.
        raise HTTPException(status_code=503, detail=f"expert fabric unavailable: {exc}") from exc


@router.get("/experts/{expert_id}", summary="One expert with metrics and provenance")
async def get_expert(expert_id: str) -> dict[str, Any]:
    from alpha.intelligence.expert_fabric import ExpertFabricError, get_expert_fabric

    try:
        record = await _read(get_expert_fabric().require, expert_id)
    except ExpertFabricError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return record.to_dict()


@router.get("/experts/{expert_id}/lineage", summary="Ancestry, descendants and creation reason")
async def get_expert_lineage(expert_id: str) -> dict[str, Any]:
    from alpha.intelligence.expert_fabric import ExpertFabricError, get_expert_fabric

    try:
        return await _read(get_expert_fabric().lineage, expert_id)
    except ExpertFabricError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/experts/{expert_id}/prune-eligibility", summary="Clause-by-clause prune eligibility")
async def get_expert_prune_eligibility(expert_id: str) -> dict[str, Any]:
    """Every prune clause evaluated and reported, including the ones that passed.

    A boolean would hide "it is too young" behind ``False``; this returns the
    reason the expert was kept.
    """
    from alpha.intelligence.config import intelligence_config
    from alpha.intelligence.expert_fabric import ExpertFabricError, get_expert_fabric

    grace = intelligence_config().experts.grace_period_seconds

    try:
        decision = await _read(get_expert_fabric().evaluate_prune, expert_id, grace_period_seconds=grace)
    except ExpertFabricError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return decision.to_dict()


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


@router.get("/learning", summary="What Alpha is learning right now, and its plasticity dial")
async def learning_status() -> dict[str, Any]:
    from alpha.intelligence.self_knowledge import get_self_knowledge

    service = get_self_knowledge()

    async def gather() -> dict[str, Any]:
        return {
            "now": await _read(service.learning_now),
            "plasticity": await _read(service.plasticity),
            "mode": await _read(service.mode),
        }

    return await gather()


@router.get("/journal", summary="Learning journal tail plus chain integrity")
async def learning_journal(limit: int = Query(default=_DEFAULT_LIMIT, ge=0, le=_MAX_LIMIT)) -> dict[str, Any]:
    """The tail of the hash-linked learning journal, plus ``verify_chain`` output.

    ``integrity.ok == false`` with a ``broken_at`` index is how a caller learns a
    historical line was edited or removed.
    """
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().journal, limit=limit)


@router.get("/replay", summary="Replay reservoir occupancy, strata, and oldest-item age")
async def replay_status() -> dict[str, Any]:
    """Reservoir state. ``oldest_age_seconds`` is the direct measurement of whether
    the reservoir is actually retaining old knowledge or just filling with new work."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().replay)


@router.get("/regressions", summary="Standing regression suite coverage")
async def regressions() -> dict[str, Any]:
    """Suite coverage by capability category. Runs no cases; this is the inventory."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().regression)


@router.get("/snapshots", summary="Stored intelligence snapshots, newest first")
async def snapshots(limit: int = Query(default=10, ge=0, le=_MAX_LIMIT)) -> dict[str, Any]:
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().snapshots, limit=limit)


@router.get("/paging", summary="Paging configuration and residency")
async def paging() -> dict[str, Any]:
    """Paging tiers. ``resident_now`` is ``None`` when no manager is active, rather
    than an empty list that would read as "nothing is resident"."""
    from alpha.intelligence.self_knowledge import get_self_knowledge

    return await _read(get_self_knowledge().paging)


@router.get("/difficulty", summary="Estimate task difficulty and return the bounded compute plan")
async def difficulty(
    declared_complexity: float | None = Query(default=None, ge=0.0, le=1.0),
    novelty: float | None = Query(default=None, ge=0.0, le=1.0),
    uncertainty: float | None = Query(default=None, ge=0.0, le=1.0),
    dependency_count: int | None = Query(default=None, ge=0),
    tool_breadth: float | None = Query(default=None, ge=0.0, le=1.0),
    verification_difficulty: float | None = Query(default=None, ge=0.0, le=1.0),
    historical_failure_rate: float | None = Query(default=None, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Estimate difficulty from whichever signals are supplied.

    Omitted signals are **excluded and renormalised**, not defaulted to zero, and
    the returned ``coverage`` says what fraction of signal weight was actually
    measured. Supplying nothing yields ``coverage: 0.0`` — an unmeasured task is
    not an easy task.
    """
    from alpha.intelligence.difficulty import DifficultyEstimator, DifficultySignals

    signals = DifficultySignals(
        declared_complexity=declared_complexity,
        novelty=novelty,
        uncertainty=uncertainty,
        dependency_count=dependency_count,
        tool_breadth=tool_breadth,
        verification_difficulty=verification_difficulty,
        historical_failure_rate=historical_failure_rate,
    )
    estimate = DifficultyEstimator().estimate(signals, with_plan=True)
    return estimate.to_dict()
