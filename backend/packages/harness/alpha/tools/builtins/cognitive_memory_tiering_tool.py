"""Built-in Cognitive Memory Consolidation and Tiered Storage Tools.

Exposes ``consolidate_cognitive_memory`` and ``recall_agent_memory``.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.memory.cognitive_memory_tiering import (
    EpisodeOutcome,
    get_cognitive_memory_system,
)


@tool("consolidate_cognitive_memory", parse_docstring=True)
def consolidate_cognitive_memory(
    action: str = "consolidate",
    content: str = "",
    task: str = "",
    tool_calls_json: str = "[]",
    outcome: str = EpisodeOutcome.UNKNOWN.value,
    statement: str = "",
    category: str = "manual",
    confidence: float = 0.75,
    metadata_json: str = "{}",
    path: str = "",
) -> dict[str, Any]:
    """Manage the three-tier cognitive memory system and run the consolidation daemon.

    Working memory is an ephemeral capacity-bounded scratchpad for intermediate
    plan steps; episodic memory is an append-only chronological log of task
    trajectories; semantic memory holds distilled heuristics and invariant
    rules. The autonomous consolidation daemon compresses accumulated episodic
    traces into semantic rules once a context threshold or an idle-cycle
    threshold is reached.

    Args:
        action: ``consolidate`` runs the daemon (pass ``force=True`` semantics
            by using ``force_consolidate``), ``force_consolidate`` runs it
            unconditionally, ``remember_working`` stores a scratchpad step,
            ``remember_episode`` appends a trajectory episode,
            ``remember_rule`` injects a distilled rule, ``status`` reports tier
            occupancy, ``save`` persists a JSON snapshot and ``load`` restores
            one, ``clear`` resets a tier (use ``content`` to name the tier:
            ``working``, ``episodic``, ``semantic`` or ``all``).
        content: Text payload for the write actions (or tier name for ``clear``).
        task: Task description attached to an episode.
        tool_calls_json: JSON list of tool call records, each with a ``tool``
            (or ``name``) key, used to distill trajectory patterns.
        outcome: Episode outcome: ``success``, ``failure``, ``partial`` or
            ``unknown``.
        statement: Rule statement for ``remember_rule``.
        category: Rule category for ``remember_rule``.
        confidence: Rule confidence in ``[0, 1]`` for ``remember_rule``.
        metadata_json: Optional JSON object of extra metadata.
        path: Filesystem path used by ``save`` and ``load``.

    Returns:
        dict: ``{"success": bool, ...}``. Consolidation returns the daemon
        report; ``status`` returns tier occupancy. On failure ``success`` is
        False and ``error`` describes the reason.
    """
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
        if not isinstance(metadata, dict):
            metadata = {}
    except (ValueError, TypeError):
        metadata = {}

    try:
        tool_calls = json.loads(tool_calls_json) if tool_calls_json else []
        if not isinstance(tool_calls, list):
            tool_calls = []
    except (ValueError, TypeError):
        tool_calls = []

    try:
        system = get_cognitive_memory_system()
        normalized_action = (action or "consolidate").strip().lower()

        if normalized_action in {"consolidate", "force_consolidate"}:
            report = system.consolidate(force=normalized_action == "force_consolidate")
            payload = report.to_dict()
            payload["success"] = True
            return payload

        if normalized_action == "remember_working":
            item = system.remember_working(content, metadata=metadata)
            return {"success": True, "action": normalized_action, "memory": item.to_dict()}

        if normalized_action == "remember_episode":
            episode = system.remember_episode(
                content=content,
                task=task,
                tool_calls=tool_calls,
                outcome=outcome,
                metadata=metadata,
            )
            return {"success": True, "action": normalized_action, "memory": episode.to_dict()}

        if normalized_action == "remember_rule":
            rule = system.remember_rule(
                statement=statement or content,
                category=category,
                confidence=confidence,
            )
            return {"success": True, "action": normalized_action, "rule": rule.to_dict()}

        if normalized_action == "status":
            return {"success": True, "action": normalized_action, "status": system.status()}

        if normalized_action == "save":
            if not path:
                return {
                    "success": False,
                    "action": normalized_action,
                    "error": "path is required for action='save'",
                }
            return {"success": True, "action": normalized_action, "path": system.save(path)}

        if normalized_action == "load":
            if not path:
                return {
                    "success": False,
                    "action": normalized_action,
                    "error": "path is required for action='load'",
                }
            return {"success": True, "action": normalized_action, "restored": system.load(path)}

        if normalized_action == "clear":
            tier = (content or "all").strip().lower()
            cleared = 0
            if tier in {"working", "all"}:
                cleared += system.working.clear()
            if tier in {"episodic", "all"}:
                cleared += system.episodic.clear()
            if tier in {"semantic", "all"}:
                cleared += system.semantic.clear()
            return {"success": True, "action": normalized_action, "tier": tier, "cleared": cleared}

        return {
            "success": False,
            "action": normalized_action,
            "error": f"unknown action '{action}'",
        }
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {"success": False, "action": action, "error": f"{type(exc).__name__}: {exc}"}


@tool("recall_agent_memory", parse_docstring=True)
def recall_agent_memory(
    query: str,
    tiers: str = "working,episodic,semantic",
    limit: int = 10,
    min_score: float = 0.0,
) -> dict[str, Any]:
    """Recall memories across working, episodic and semantic tiers with hybrid ranking.

    Retrieval blends Okapi BM25 lexical relevance with cosine vector similarity
    and applies exponential relevance decay, so recent and frequently accessed
    memories outrank stale ones.

    Args:
        query: Natural language query describing what to recall.
        tiers: Comma-separated tiers to search: ``working``, ``episodic``,
            ``semantic``. Defaults to all three.
        limit: Maximum number of hits to return.
        min_score: Minimum hybrid score for a hit to be included.

    Returns:
        dict: ``{"success": bool, "query": str, "count": int, "results": list}``
        where each result carries ``score``, ``lexical_score``,
        ``semantic_score``, ``decay``, ``tier`` and ``content``.
    """
    try:
        tier_list = [item.strip() for item in str(tiers).split(",") if item.strip()]
        system = get_cognitive_memory_system()
        return system.recall(
            query=query,
            tiers=tier_list or None,
            limit=limit,
            min_score=min_score,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "query": query,
            "error": f"{type(exc).__name__}: {exc}",
            "count": 0,
            "results": [],
        }
