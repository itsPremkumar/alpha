"""Memory tools for tool-driven memory mode.

Exposes memory_search, memory_add, memory_update, memory_delete as
LangChain @tool functions the model can call directly.

When memory.mode == "tool", these tools are registered on the agent. Most
backends omit MemoryMiddleware so the model drives persistence; a backend that
sets ``requires_passive_writes_in_tool_mode`` retains conversation writes while
the tools provide query-aware recall.

Backend-agnostic: every tool goes through the ``MemoryManager`` ABC
(:func:`get_memory_manager`) -- ``search``/``get_memory`` are tier-2 methods;
``create_fact``/``update_fact``/``delete_fact`` are tier-3 hooks with a default
``raise NotImplementedError`` (unsupported -> the tool catches it and returns a
JSON ``error`` instead of crashing). So tool mode works for any backend that
overrides those ops (DeerMem does; noop inherits the raises -> errors).
"""

import json
import logging
from datetime import datetime

from langchain.tools import tool

from alpha.agents.memory.manager import get_memory_manager
from alpha.runtime.user_context import resolve_runtime_user_id
from alpha.tools.types import Runtime

logger = logging.getLogger(__name__)


def _resolve_scope(runtime: Runtime | None = None) -> tuple[str | None, str]:
    """Resolve agent_name and user_id for tool handler scope.

    Tool execution receives user and agent metadata through LangGraph runtime
    context.  Prefer that channel over ContextVar fallback so persistence stays
    scoped correctly across request/task boundaries.
    """
    context = getattr(runtime, "context", None)
    agent_name = None
    if isinstance(context, dict) and context.get("agent_name"):
        agent_name = str(context["agent_name"])
    return agent_name, resolve_runtime_user_id(runtime)


def _memory_content_key(content: str) -> str:
    return content.strip().casefold()


def _parse_as_of(value: str | None) -> float | None:
    """Parse an ``as_of`` cutoff into unix seconds — parsed or refused, never guessed.

    ``None``/blank means "not provided". A pure number is unix seconds (float
    accepted). Any other value must be an ISO-8601 timestamp with a date part
    (``Z`` suffix accepted); everything else raises :class:`ValueError` so the
    caller can refuse the request instead of silently ignoring the parameter.
    """

    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    # An ISO-8601 timestamp always carries a date part; refusing anything
    # without one keeps bare times ("07:40") from silently becoming
    # "today at that time".
    if "-" not in text:
        raise ValueError(f"invalid as_of timestamp: {value!r}")
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError as exc:
        raise ValueError(f"invalid as_of timestamp: {value!r}") from exc


@tool("memory_search", parse_docstring=True)
def memory_search_tool(
    runtime: Runtime,
    query: str,
    category: str | None = None,
    limit: int = 10,
    as_of: str | None = None,
) -> str:
    """Search existing facts by natural language query.

    Use this when you need to check what you already know about the user
    - their preferences, past corrections, context, or any stored facts.

    Args:
        query: Natural language query to match against fact content.
            Case-insensitive substring matching.
        category: Optional category filter (e.g. "preference", "correction",
            "context"). Only facts with this exact category are returned.
        limit: Maximum results to return (default 10).
        as_of: Optional temporal cutoff (ISO-8601 timestamp like
            "2026-10-01T00:00:00Z", or unix seconds). Asks "what did the
            memory system know at that time?" — candidates dated after the
            cutoff are excluded. Requires the fused recall surface
            (memory.fusion.enabled); refused, not ignored, otherwise.

    Returns:
        JSON string with "results" (list of fact objects) and "count".
        Each fact has id, content, category, confidence, createdAt, and source.
        With ``as_of``, the payload also discloses the parsed cutoff.
    """
    agent_name, user_id = _resolve_scope(runtime)
    try:
        cutoff: float | None = None
        if as_of is not None:
            try:
                cutoff = _parse_as_of(as_of)
            except ValueError:
                return json.dumps(
                    {"error": (f'invalid as_of: {as_of!r} — use an ISO-8601 timestamp (e.g. "2026-10-01T00:00:00Z") or unix seconds')},
                    ensure_ascii=False,
                )
        fused = _active_memory_fusion(query, user_id=user_id, agent_name=agent_name, limit=limit, as_of=cutoff)
        if fused is not None:
            return json.dumps(fused, ensure_ascii=False)
        if cutoff is not None:
            # The fused surface is the only path with temporal semantics; an
            # unsupported parameter must be refused loudly, never dropped.
            return json.dumps(
                {"error": ("as_of requires the fused recall surface (memory.fusion.enabled); the single-backend path has no temporal projection, so the request is refused rather than answered with present-day data")},
                ensure_ascii=False,
            )
        results = get_memory_manager().search(
            query,
            top_k=limit,
            user_id=user_id,
            agent_name=agent_name,
            category=category,
        )
        return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("memory_search_tool failed")
        return json.dumps({"error": str(exc)})


def _active_memory_fusion(
    query: str,
    *,
    user_id: str,
    agent_name: str | None,
    limit: int,
    as_of: float | None = None,
) -> dict[str, object] | None:
    """Fused multi-source retrieval, or ``None`` when the operator has not enabled it.

    ``memory.fusion.enabled`` is the single activation switch (see
    ``alpha.memory.active_controller.active_memory_enabled``). With it off this
    returns ``None`` and the tool keeps its original single-backend behaviour
    byte for byte, so an existing deployment changes nothing.

    When on, the result carries the honest per-source posture beside the
    records: a source that could not be read is reported as such and is never
    rendered as "no memories matched". The controller abstains rather than
    padding the answer, so an empty ``results`` list means "nothing cleared the
    relevance floor", not "the search failed".

    ``as_of`` rides through to the controller's central temporal projection;
    when set, the parsed cutoff is disclosed beside the answer so a caller can
    tell a time-traveling read from a present-day one.
    """
    from alpha.memory.active_controller import active_memory_enabled, get_active_memory_controller

    if not active_memory_enabled():
        return None

    controller = get_active_memory_controller(user_id=user_id, agent_name=agent_name)
    working_set = controller.retrieve(query, as_of=as_of)
    payload: dict[str, object] = {
        "mode": working_set.mode,
        "results": [
            {
                "id": item.memory_id,
                "content": item.content,
                "memory_type": item.memory_type,
                # Ranking only. Deliberately not named "confidence": a good
                # lexical match is not a probability that the text is correct.
                "relevance": item.relevance,
                "evidence_state": item.evidence_state,
                "source": item.source,
                "source_refs": list(item.source_refs),
            }
            for item in working_set.selected[:limit]
        ],
        "count": len(working_set.selected[:limit]),
        "abstained": working_set.abstained,
        "abstain_reason": working_set.abstain_reason,
        "degraded_sources": list(working_set.degraded_sources),
        "token_estimate": working_set.token_estimate,
    }
    if as_of is not None:
        payload["as_of"] = as_of
    return payload


@tool("memory_add", parse_docstring=True)
def memory_add_tool(
    runtime: Runtime,
    content: str,
    category: str = "context",
    confidence: float = 0.7,
) -> str:
    """Store a new fact about the user or conversation context.

    Use this when the user shares something worth remembering for future
    conversations - preferences, corrections, personal details, work context.
    The fact persists across sessions and will be available via memory_search
    and automatic context injection.

    Args:
        content: The fact text to remember. Be specific and factual.
        category: Category label for organization (default "context").
            e.g. "preference", "correction", "behavior", "personal".
        confidence: How certain you are about this fact, 0.0-1.0
            (default 0.7). Use higher values for explicit user statements,
            lower for inferences.

    Returns:
        JSON string with "fact_id" and "status": "added".
        On duplicate content, returns "error" with explanation.
    """
    agent_name, user_id = _resolve_scope(runtime)
    try:
        normalized_content = content.strip()
        if not normalized_content:
            return json.dumps({"error": "empty content"})
        content_key = _memory_content_key(normalized_content)
        manager = get_memory_manager()
        existing_facts = manager.get_memory(agent_name=agent_name, user_id=user_id).get("facts", [])
        # Fast-path duplicate rejection to spare a write attempt in the common
        # case. The authoritative check lives in the backend's create critical
        # section (DeerMem re-checks against a fresh snapshot on every
        # revision-conflict retry in create_memory_fact), so concurrent tool
        # calls for the same user cannot both store the same content.
        if any(_memory_content_key(str(fact.get("content", ""))) == content_key for fact in existing_facts):
            return json.dumps({"error": "Duplicate fact"})

        # create_fact returns (memory_data, fact_id) -- use the id directly rather
        # than re-deriving it by content matching (which would couple the tool to
        # the backend's content normalization and could misreport a storage cap).
        # Unsupported backends raise NotImplementedError (tier-3 default) -> JSON error.
        try:
            _memory_data, fact_id = manager.create_fact(
                normalized_content,
                category=category,
                confidence=confidence,
                agent_name=agent_name,
                user_id=user_id,
            )
        except NotImplementedError:
            return json.dumps({"error": f"memory backend {type(manager).__name__} does not support create_fact"})
        if fact_id is None:
            # The configured max_facts policy evicted the new fact; report the
            # capacity result instead of a dangling id.
            return json.dumps({"error": "Fact was not stored because the configured memory.max_facts capacity policy evicted it"})
        return json.dumps({"fact_id": fact_id, "status": "added"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("memory_add_tool failed")
        return json.dumps({"error": str(exc)})


# Tool mode exposes explicit CRUD, not the passive staleness-review path.
# The staleness age/category/removal-count guardrails protect automatic
# middleware cleanup; tool-mode operators opt into model-directed updates
# and deletes. The docs call out this difference for configuration review.


@tool("memory_update", parse_docstring=True)
def memory_update_tool(
    runtime: Runtime,
    fact_id: str,
    content: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
) -> str:
    """Update an existing fact. Only provided fields are changed; omitted
    fields stay as-is.

    Use this when a stored fact is outdated, incorrect, or needs refinement.
    First use memory_search to find the fact_id, then update it.

    Args:
        fact_id: Fact ID from memory_search results (required).
        content: New fact text (unchanged if omitted).
        category: New category (unchanged if omitted).
        confidence: New confidence score 0.0-1.0 (unchanged if omitted).

    Returns:
        JSON string with "fact_id" and "status": "updated".
        On invalid fact_id, returns "error" with explanation.
    """
    agent_name, user_id = _resolve_scope(runtime)
    try:
        manager = get_memory_manager()
        try:
            manager.update_fact(
                fact_id,
                content=content,
                category=category,
                confidence=confidence,
                agent_name=agent_name,
                user_id=user_id,
            )
        except NotImplementedError:
            return json.dumps({"error": f"memory backend {type(manager).__name__} does not support update_fact"})
        return json.dumps({"fact_id": fact_id, "status": "updated"})
    except KeyError:
        return json.dumps({"error": f"Fact not found: {fact_id}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("memory_update_tool failed")
        return json.dumps({"error": str(exc)})


@tool("memory_delete", parse_docstring=True)
def memory_delete_tool(runtime: Runtime, fact_id: str) -> str:
    """Delete a fact by its ID.

    Use this when a fact is no longer accurate or relevant. First use
    memory_search to find the fact_id, then delete it.

    Args:
        fact_id: Fact ID to delete (from memory_search results).

    Returns:
        JSON string with "fact_id" and "status": "deleted".
        On invalid fact_id, returns "error" with explanation.
    """
    agent_name, user_id = _resolve_scope(runtime)
    try:
        manager = get_memory_manager()
        try:
            manager.delete_fact(fact_id, agent_name=agent_name, user_id=user_id)
        except NotImplementedError:
            return json.dumps({"error": f"memory backend {type(manager).__name__} does not support delete_fact"})
        return json.dumps({"fact_id": fact_id, "status": "deleted"})
    except KeyError:
        return json.dumps({"error": f"Fact not found: {fact_id}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        logger.exception("memory_delete_tool failed")
        return json.dumps({"error": str(exc)})


def get_memory_tools() -> list:
    """Return all memory tools for agent registration.

    Called by agent factory when memory.mode == "tool".
    """
    return [
        memory_search_tool,
        memory_add_tool,
        memory_update_tool,
        memory_delete_tool,
    ]
