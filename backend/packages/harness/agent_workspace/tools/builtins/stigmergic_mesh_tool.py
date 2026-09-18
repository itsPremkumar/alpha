"""Built-in Stigmergic Event Mesh and Swarm Pheromone Memory Tools.

Exposes ``emit_stigmergic_event`` and ``query_stigmergic_traces`` to the agent.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from agent_workspace.blackboard.stigmergic_event_mesh import (
    DEFAULT_HALF_LIFE_SEC,
    StigmergicEventMesh,
    default_mesh_path,
    get_stigmergic_mesh,
)


def _resolve_mesh(workspace_root: str, in_memory: bool) -> StigmergicEventMesh:
    """Resolve the mesh instance backing a tool invocation.

    Args:
        workspace_root: Workspace whose on-disk mesh should be used.
        in_memory: When True, force a process-local ephemeral mesh.

    Returns:
        The shared :class:`StigmergicEventMesh` instance.
    """
    if in_memory or not workspace_root:
        return get_stigmergic_mesh()
    return get_stigmergic_mesh(default_mesh_path(workspace_root))


@tool("emit_stigmergic_event", parse_docstring=True)
def emit_stigmergic_event(
    agent_id: str,
    entity: str,
    pheromone: str,
    action: str = "deposit",
    strength: float = 1.0,
    half_life_sec: float = DEFAULT_HALF_LIFE_SEC,
    trace_id: str = "",
    metadata_json: str = "{}",
    workspace_root: str = "",
    in_memory: bool = False,
) -> dict[str, Any]:
    """Deposit or release a stigmergic pheromone signal on a code entity.

    Stigmergic coordination lets a swarm of agents coordinate without direct
    messaging: agents leave decaying traces on shared entities and let trace
    strength steer collective behaviour. Use ``UNDER_SURGICAL_REFACTOR`` to
    claim a file before editing it, ``DEFECT_SUSPECT`` to flag a likely bug,
    ``CANARY_VERIFIED_STABLE`` after a verified canary run, and
    ``CONVERGENCE_LOCKED`` to freeze an entity that has converged.

    Args:
        agent_id: Identifier of the emitting agent or subagent.
        entity: Code entity key, typically a repository-relative file path.
        pheromone: Signal type: ``DEFECT_SUSPECT``,
            ``UNDER_SURGICAL_REFACTOR``, ``CANARY_VERIFIED_STABLE``,
            ``CONVERGENCE_LOCKED``, ``COVERAGE_GAP``, ``DEPENDENCY_HOTSPOT``.
        action: ``deposit`` (overwrite), ``reinforce`` (add to decayed value),
            ``release`` (remove) or ``purge`` (remove all deposits).
        strength: Base signal strength before exponential decay.
        half_life_sec: Half-life in seconds used for exponential evaporation.
        trace_id: Optional correlation identifier for a swarm trajectory.
        metadata_json: Optional JSON object with arbitrary annotations.
        workspace_root: Workspace owning the durable mesh. Empty means
            process-local in-memory mesh when ``in_memory`` is True.
        in_memory: Force an ephemeral in-memory mesh instead of the durable one.

    Returns:
        dict: ``{"success": bool, "entity": str, "pheromone": str, ...}``.
        On failure ``success`` is False and ``error`` describes the reason.
    """
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
    except (ValueError, TypeError):
        metadata = {}

    try:
        mesh = _resolve_mesh(workspace_root, in_memory)
        return mesh.emit(
            agent_id=agent_id,
            entity=entity,
            pheromone=pheromone,
            action=action,
            strength=strength,
            half_life_sec=half_life_sec,
            trace_id=trace_id,
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "entity": entity,
            "pheromone": pheromone,
        }


@tool("query_stigmergic_traces", parse_docstring=True)
def query_stigmergic_traces(
    mode: str = "field",
    entity: str = "",
    pheromone: str = "",
    agent_id: str = "",
    limit: int = 50,
    workspace_root: str = "",
    in_memory: bool = False,
    evaporate: bool = True,
) -> dict[str, Any]:
    """Read the shared stigmergic pheromone field of the agent swarm.

    Args:
        mode: ``field`` returns decayed signal strengths per entity,
            ``traces`` returns raw chronological events, ``locks`` returns
            entities currently claimed by an agent, ``priorities`` returns a
            work-priority ranking, and ``stats`` returns mesh statistics.
        entity: Optional entity filter (exact match).
        pheromone: Optional pheromone type filter.
        agent_id: Optional emitting-agent filter (``traces`` mode only).
        limit: Maximum number of entries to return.
        workspace_root: Workspace owning the durable mesh.
        in_memory: Force an ephemeral in-memory mesh instead of the durable one.
        evaporate: Prune fully evaporated signals before reading.

    Returns:
        dict: ``{"success": bool, "mode": str, "count": int, "results": list}``.
        On failure ``success`` is False and ``error`` describes the reason.
    """
    try:
        mesh = _resolve_mesh(workspace_root, in_memory)
        if evaporate:
            mesh.evaporate()
        return mesh.query(
            mode=mode,
            entity=entity,
            pheromone=pheromone,
            agent_id=agent_id,
            limit=limit,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "mode": mode,
            "count": 0,
            "results": [],
        }
