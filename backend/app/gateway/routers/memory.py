"""Memory API router for retrieving and managing global memory data."""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.gateway.internal_auth import get_trusted_internal_owner_user_id
from agent_workspace.agents.memory import MemoryConflictError, MemoryCorruptionError, MemoryManager, get_memory_manager
from agent_workspace.config.memory_config import get_memory_config
from agent_workspace.config.paths import make_safe_user_id
from agent_workspace.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api", tags=["memory"])


def _resolve_memory_user_id(request: Request) -> str:
    """Resolve the memory owner for this request.

    Honors the trusted internal owner header that channel workers attach when
    acting for a connection owner, so an IM ``/memory`` command reads the bound
    owner's memory instead of the synthetic internal user. The header is only
    honored after ``AuthMiddleware`` validated the internal token (see
    ``get_trusted_internal_owner_user_id``). Browser/API callers are never
    internal, so this falls back to the normal contextvar-based effective user.

    The trusted owner header carries the *raw* owner id, so sanitize it through
    ``make_safe_user_id`` (the same normalization the channel file pipeline applies
    via ``_safe_user_id_for_run``/``prepare_user_dir_for_raw_id``). This keeps the
    memory bucket aligned with the owner's file/upload bucket and avoids a 500 when
    the raw id contains characters ``_validate_user_id`` would reject.
    """
    raw_owner = get_trusted_internal_owner_user_id(request)
    if raw_owner:
        return make_safe_user_id(raw_owner)
    return get_effective_user_id()


class ContextSection(BaseModel):
    """Model for context sections (user and history)."""

    summary: str = Field(default="", description="Summary content")
    updatedAt: str = Field(default="", description="Last update timestamp")


class UserContext(BaseModel):
    """Model for user context."""

    workContext: ContextSection = Field(default_factory=ContextSection)
    personalContext: ContextSection = Field(default_factory=ContextSection)
    topOfMind: ContextSection = Field(default_factory=ContextSection)


class HistoryContext(BaseModel):
    """Model for history context."""

    recentMonths: ContextSection = Field(default_factory=ContextSection)
    earlierContext: ContextSection = Field(default_factory=ContextSection)
    longTermBackground: ContextSection = Field(default_factory=ContextSection)


class Fact(BaseModel):
    """Model for a memory fact."""

    id: str = Field(..., description="Unique identifier for the fact")
    content: str = Field(..., description="Fact content")
    category: str = Field(default="context", description="Fact category")
    categoryExtension: str | None = Field(default=None, description="Extension category when category is 'other'")
    topics: list[str] | None = Field(default=None, description="Retrieval-oriented topic labels")
    confidence: float = Field(default=0.5, description="Confidence score (0-1)")
    createdAt: str = Field(default="", description="Creation timestamp")
    source: str = Field(default="unknown", description="Legacy source string; structured metadata remains internal to storage")
    sourceError: str | None = Field(default=None, description="Optional description of the prior mistake or wrong approach")
    schemaVersion: int | None = Field(default=None, description="Per-fact schema version")
    status: str | None = Field(default=None, description="Fact lifecycle status")
    scope: dict[str, str | None] | None = Field(default=None, description="Canonical user/agent scope")
    revision: int | None = Field(default=None, description="Fact optimistic revision")
    updatedAt: str | None = Field(default=None, description="Last fact update timestamp")
    consolidatedAt: str | None = None
    consolidatedFrom: list[str] | None = None

    @field_validator("source", mode="before")
    @classmethod
    def _legacy_source_string(cls, value: Any) -> str:
        """Keep the HTTP contract stable while Markdown stores rich metadata."""
        if isinstance(value, str):
            return value
        if not isinstance(value, dict):
            return "unknown"
        source_type = value.get("type")
        thread_id = value.get("threadId")
        if source_type == "conversation" and isinstance(thread_id, str) and thread_id:
            return thread_id
        if isinstance(source_type, str) and source_type:
            return source_type
        if isinstance(thread_id, str) and thread_id:
            return thread_id
        return "unknown"


class MemoryResponse(BaseModel):
    """Response model for memory data."""

    version: str = Field(default="1.0", description="Memory schema version")
    revision: int | None = Field(default=None, description="Manifest revision")
    lastUpdated: str = Field(default="", description="Last update timestamp")
    user: UserContext = Field(default_factory=UserContext)
    history: HistoryContext = Field(default_factory=HistoryContext)
    facts: list[Fact] = Field(default_factory=list)


def _map_memory_fact_value_error(exc: ValueError) -> HTTPException:
    """Convert updater validation errors into stable API responses."""
    if exc.args and exc.args[0] == "confidence":
        detail = "Invalid confidence value; must be between 0 and 1."
    elif exc.args and exc.args[0] == "agent_name":
        detail = "An agent name is required for fact operations; user-global memory stores summaries only."
    elif exc.args and exc.args[0] == "Duplicate fact":
        return HTTPException(status_code=409, detail="A fact with the same content already exists.")
    else:
        detail = "Memory fact content cannot be empty."
    return HTTPException(status_code=400, detail=detail)


def _map_memory_manager_error(exc: MemoryConflictError | MemoryCorruptionError) -> HTTPException:
    """Map backend-neutral manager errors without importing a storage plugin."""
    if isinstance(exc, MemoryConflictError):
        return HTTPException(status_code=409, detail="Memory changed concurrently; reload and retry.")
    return HTTPException(status_code=500, detail="Stored memory data is corrupted.")


def _unsupported_501(manager: object, label: str) -> HTTPException:
    """501 for an unsupported memory operation.

    Tier-3 hooks (``reload_memory`` / ``create_fact`` / ``delete_fact`` /
    ``update_fact``) and tier-2 management ops (``get_memory`` / ``clear_memory``
    / ``import_memory``) all default to ``raise NotImplementedError``; backends
    that support them override, unsupported ones inherit the raise. Before the
    contract change these were ``@abstractmethod`` (every backend implemented
    them, so the endpoints could never raise); now a minimal backend (only
    ``add`` + ``get_context``) inherits the raise, so endpoints invoke the
    method directly and catch ``NotImplementedError`` -> this 501. There is no
    global ``NotImplementedError`` handler, so an uncaught raise is a raw 500.
    """
    return HTTPException(
        status_code=501,
        detail=f"Operation '{label}' not supported by memory backend '{type(manager).__name__}'.",
    )


async def _get_memory_or_501(manager: MemoryManager, user_id: str, label: str) -> dict[str, Any]:
    """Read the full memory doc; 501 if the backend doesn't expose one.

    ``get_memory`` is tier-2 (default ``raise NotImplementedError``); a minimal
    backend doesn't expose a full doc. The standalone read endpoints (GET
    /memory, /memory/export, /memory/status) and the /memory/reload fallback all
    route reads through here so an unsupported backend gets a clean 501 instead
    of a raw 500. ``label`` is the operation name in the 501 detail (the
    endpoint's verb, e.g. "get memory" / "export memory" / "reload memory").
    """
    try:
        return await asyncio.to_thread(manager.get_memory, user_id=user_id)
    except NotImplementedError:
        raise _unsupported_501(manager, label) from None
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc


class FactCreateRequest(BaseModel):
    """Request model for creating a memory fact."""

    content: str = Field(..., min_length=1, description="Fact content")
    category: str = Field(default="context", description="Fact category")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score (0-1)")


class FactPatchRequest(BaseModel):
    """PATCH request model that preserves existing values for omitted fields."""

    content: str | None = Field(default=None, min_length=1, description="Fact content")
    category: str | None = Field(default=None, description="Fact category")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="Confidence score (0-1)")


class MemoryConfigResponse(BaseModel):
    """Response model for memory configuration."""

    enabled: bool = Field(..., description="Whether the memory mechanism is enabled (call-site gate).")
    mode: Literal["middleware", "tool"] = Field(..., description="Memory operation mode: 'middleware' (passive per-turn LLM summarization) or 'tool' (model calls memory tools directly). Mechanism-level, applies to any backend.")
    injection_enabled: bool = Field(..., description="Whether memory is injected into the system prompt (call-site gate).")
    shutdown_flush_timeout_seconds: float = Field(..., description="Hard budget (s) to drain pending memory updates on Gateway graceful shutdown; must fit inside the pod's K8s terminationGracePeriodSeconds.")
    manager_class: str = Field(..., description="Active memory backend selector (backend name or dotted path).")
    backend_config: dict = Field(..., description="Backend-private config (self-interpreted by the backend).")


class MemoryStatusResponse(BaseModel):
    """Response model for memory status."""

    config: MemoryConfigResponse
    data: MemoryResponse


@router.get(
    "/memory",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Get Memory Data",
    description="Retrieve the current global memory data including user context, history, and facts.",
)
async def get_memory(http_request: Request) -> MemoryResponse:
    """Get the current global memory data.

    Returns:
        The current memory data with user context, history, and facts.

    Example Response:
        ```json
        {
            "version": "1.0",
            "lastUpdated": "2024-01-15T10:30:00Z",
            "user": {
                "workContext": {"summary": "Working on Alpha project", "updatedAt": "..."},
                "personalContext": {"summary": "Prefers concise responses", "updatedAt": "..."},
                "topOfMind": {"summary": "Building memory API", "updatedAt": "..."}
            },
            "history": {
                "recentMonths": {"summary": "Recent development activities", "updatedAt": "..."},
                "earlierContext": {"summary": "", "updatedAt": ""},
                "longTermBackground": {"summary": "", "updatedAt": ""}
            },
            "facts": [
                {
                    "id": "fact_abc123",
                    "content": "User prefers TypeScript over JavaScript",
                    "category": "preference",
                    "confidence": 0.9,
                    "createdAt": "2024-01-15T10:30:00Z",
                    "source": "thread_xyz"
                }
            ]
        }
        ```
    """
    manager = await asyncio.to_thread(get_memory_manager)
    memory_data = await _get_memory_or_501(manager, _resolve_memory_user_id(http_request), "get memory")
    return MemoryResponse(**memory_data)


@router.post(
    "/memory/reload",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Reload Memory Data",
    description="Reload memory data from the storage file, refreshing the in-memory cache.",
)
async def reload_memory(http_request: Request) -> MemoryResponse:
    """Reload memory data from file.

    This forces a reload of the memory data from the storage file,
    useful when the file has been modified externally.

    Returns:
        The reloaded memory data.
    """
    user_id = _resolve_memory_user_id(http_request)
    manager = await asyncio.to_thread(get_memory_manager)
    try:
        memory_data = await asyncio.to_thread(manager.reload_memory, user_id=user_id)
    except NotImplementedError:
        # Non-DeerMem backends have no reload concept; fall back to get_memory
        # (read-only refresh, so degrading is safe and still useful -- vs fact
        # CRUD writes, which fail loud at 501 since silently no-op'ing a write
        # would hide data loss). If get_memory is also unsupported (a minimal
        # backend with no full doc), surface 501 rather than a raw 500: reads
        # degrade only when there is a doc to degrade to.
        memory_data = await _get_memory_or_501(manager, user_id, "reload memory")
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc
    return MemoryResponse(**memory_data)


@router.delete(
    "/memory",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Clear All Memory Data",
    description="Delete all saved memory data and reset the memory structure to an empty state.",
)
async def clear_memory(http_request: Request) -> MemoryResponse:
    """Clear all persisted memory data."""
    manager = await asyncio.to_thread(get_memory_manager)
    try:
        memory_data = await asyncio.to_thread(manager.clear_memory, user_id=_resolve_memory_user_id(http_request))
    except NotImplementedError:
        raise _unsupported_501(manager, "clear memory") from None
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to clear memory data.") from exc

    return MemoryResponse(**memory_data)


@router.post(
    "/memory/facts",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Create Memory Fact",
    description="Create a single saved memory fact manually.",
)
async def create_memory_fact_endpoint(request: FactCreateRequest, http_request: Request) -> MemoryResponse:
    """Create a single fact manually."""
    manager = await asyncio.to_thread(get_memory_manager)
    try:
        memory_data, fact_id = await asyncio.to_thread(
            manager.create_fact,
            content=request.content,
            category=request.category,
            confidence=request.confidence,
            user_id=_resolve_memory_user_id(http_request),
        )
    except NotImplementedError:
        raise _unsupported_501(manager, "create fact") from None
    except ValueError as exc:
        raise _map_memory_fact_value_error(exc) from exc
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to create memory fact.") from exc

    if fact_id is None:
        # The configured max_facts policy evicted the new fact; it was not stored.
        raise HTTPException(status_code=409, detail="Fact was not stored because the configured memory.max_facts capacity policy evicted it")
    return MemoryResponse(**memory_data)


@router.delete(
    "/memory/facts/{fact_id}",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Delete Memory Fact",
    description="Delete a single saved memory fact by its fact id.",
)
async def delete_memory_fact_endpoint(fact_id: str, http_request: Request) -> MemoryResponse:
    """Delete a single fact from memory by fact id."""
    manager = await asyncio.to_thread(get_memory_manager)
    try:
        memory_data = await asyncio.to_thread(manager.delete_fact, fact_id, user_id=_resolve_memory_user_id(http_request))
    except NotImplementedError:
        raise _unsupported_501(manager, "delete fact") from None
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Memory fact '{fact_id}' not found.") from exc
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to delete memory fact.") from exc

    return MemoryResponse(**memory_data)


@router.patch(
    "/memory/facts/{fact_id}",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Patch Memory Fact",
    description="Partially update a single saved memory fact by its fact id while preserving omitted fields.",
)
async def update_memory_fact_endpoint(fact_id: str, request: FactPatchRequest, http_request: Request) -> MemoryResponse:
    """Partially update a single fact manually."""
    manager = await asyncio.to_thread(get_memory_manager)
    try:
        memory_data = await asyncio.to_thread(
            manager.update_fact,
            fact_id=fact_id,
            content=request.content,
            category=request.category,
            confidence=request.confidence,
            user_id=_resolve_memory_user_id(http_request),
        )
    except NotImplementedError:
        raise _unsupported_501(manager, "update fact") from None
    except ValueError as exc:
        raise _map_memory_fact_value_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Memory fact '{fact_id}' not found.") from exc
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to update memory fact.") from exc

    return MemoryResponse(**memory_data)


@router.get(
    "/memory/export",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Export Memory Data",
    description="Export the current global memory data as JSON for backup or transfer.",
)
async def export_memory(http_request: Request) -> MemoryResponse:
    """Export the current memory data."""
    manager = await asyncio.to_thread(get_memory_manager)
    memory_data = await _get_memory_or_501(manager, _resolve_memory_user_id(http_request), "export memory")
    return MemoryResponse(**memory_data)


@router.post(
    "/memory/import",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Import Memory Data",
    description="Import and overwrite the current global memory data from a JSON payload.",
)
async def import_memory(request: MemoryResponse, http_request: Request) -> MemoryResponse:
    """Import and persist memory data."""
    manager = await asyncio.to_thread(get_memory_manager)
    try:
        memory_data = await asyncio.to_thread(
            manager.import_memory,
            request.model_dump(exclude_none=True),
            user_id=_resolve_memory_user_id(http_request),
        )
    except NotImplementedError:
        raise _unsupported_501(manager, "import memory") from None
    except (MemoryConflictError, MemoryCorruptionError) as exc:
        raise _map_memory_manager_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to import memory data.") from exc

    return MemoryResponse(**memory_data)


@router.get(
    "/memory/config",
    response_model=MemoryConfigResponse,
    summary="Get Memory Configuration",
    description="Retrieve the current memory system configuration.",
)
async def get_memory_config_endpoint() -> MemoryConfigResponse:
    """Get the memory system configuration.

    Returns:
        The current memory configuration. The response is backend-agnostic:
        ``enabled`` / ``injection_enabled`` / ``mode`` are mechanism-level
        fields that apply to any backend (``mode`` selects middleware vs tool
        operation), and ``backend_config`` is an opaque dict the active
        backend (``manager_class``) self-interprets. DeerMem's knobs
        (``storage_path``, ``max_facts``, ``debounce_seconds``, ...) live under
        ``backend_config`` -- they are NOT top-level, because a non-DeerMem
        backend has its own (different) knobs.

    Example Response:
        ```json
        {
            "enabled": true,
            "injection_enabled": true,
            "shutdown_flush_timeout_seconds": 30.0,
            "mode": "middleware",
            "manager_class": "deermem",
            "backend_config": {
                "storage_path": "/.../.agent-workspace",
                "debounce_seconds": 30,
                "max_facts": 100,
                "fact_confidence_threshold": 0.7,
                "max_injection_tokens": 2000,
                "token_counting": "tiktoken"
            }
        }
        ```
    """
    config = get_memory_config()
    return MemoryConfigResponse(
        enabled=config.enabled,
        mode=config.mode,
        injection_enabled=config.injection_enabled,
        shutdown_flush_timeout_seconds=config.shutdown_flush_timeout_seconds,
        manager_class=config.manager_class,
        backend_config=config.backend_config,
    )


@router.get(
    "/memory/status",
    response_model=MemoryStatusResponse,
    response_model_exclude_none=True,
    summary="Get Memory Status",
    description="Retrieve both memory configuration and current data in a single request.",
)
async def get_memory_status(http_request: Request) -> MemoryStatusResponse:
    """Get the memory system status including configuration and data.

    Returns:
        Combined memory configuration and current data.
    """
    config = get_memory_config()
    manager = await asyncio.to_thread(get_memory_manager)
    memory_data = await _get_memory_or_501(manager, _resolve_memory_user_id(http_request), "get memory status")

    return MemoryStatusResponse(
        config=MemoryConfigResponse(
            enabled=config.enabled,
            mode=config.mode,
            injection_enabled=config.injection_enabled,
            shutdown_flush_timeout_seconds=config.shutdown_flush_timeout_seconds,
            manager_class=config.manager_class,
            backend_config=config.backend_config,
        ),
        data=MemoryResponse(**memory_data),
    )


# =============================================================================
# Multi-Tier Cognitive Memory Endpoints
# =============================================================================


class CognitiveRecallRequest(BaseModel):
    """Request payload for context-aware hybrid retrieval."""

    query: str = Field(..., min_length=1, description="Search query string")
    limit: int = Field(default=10, ge=1, le=50)
    bm25_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    vector_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    temporal_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    graph_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    min_score: float = Field(default=0.05, ge=0.0, le=1.0)
    tier_filter: list[str] | None = None
    as_of_timestamp: float | None = None


class WorkingMemoryCreateRequest(BaseModel):
    """Request payload to push an item into working memory scratchpad."""

    content: str = Field(..., min_length=1)
    context_tag: str = Field(default="scratch")
    attention_score: float = Field(default=1.0, ge=0.0, le=1.0)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    task_id: str = Field(default="default")
    metadata: dict[str, Any] = Field(default_factory=dict)


class EpisodicTraceCreateRequest(BaseModel):
    """Request payload to log an immediate episodic action trace."""

    action: str = Field(..., min_length=1)
    observation: str = Field(..., min_length=1)
    outcome: str = Field(default="success")
    error_context: str | None = None
    session_id: str = Field(default="default")
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class SemanticBeliefCreateRequest(BaseModel):
    """Request payload to add a semantic belief node."""

    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object_val: str = Field(..., min_length=1)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    salience: float = Field(default=0.7, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class ProceduralSkillCreateRequest(BaseModel):
    """Request payload to register a reusable procedural skill."""

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    trigger_pattern: str = Field(..., min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    code_snippet: str = Field(default="")
    postconditions: list[str] = Field(default_factory=list)


def _cognitive_system_for_request(request: Request):
    from contextlib import contextmanager

    from agent_workspace.memory.cognitive import get_cognitive_memory_system
    from agent_workspace.runtime.user_context import require_current_user

    @contextmanager
    def operation():
        raw_owner = get_trusted_internal_owner_user_id(request)
        if raw_owner:
            user_id = make_safe_user_id(raw_owner)
        else:
            if getattr(getattr(request.state, "user", None), "system_role", None) == "internal":
                raise HTTPException(status_code=401, detail="Cognitive memory requires an owner.")
            try:
                require_current_user()
            except RuntimeError as exc:
                raise HTTPException(status_code=401, detail="Cognitive memory requires an owner.") from exc
            user_id = _resolve_memory_user_id(request)
        system = get_cognitive_memory_system(user_id=user_id)
        with system.operation():
            yield system

    return operation()


@router.get(
    "/memory/cognitive/overview",
    summary="Get Multi-Tier Cognitive Memory Overview",
    description="Retrieve status, stats, and density metrics across all cognitive tiers.",
)
def get_cognitive_overview(http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        return system.overview()


@router.post(
    "/memory/cognitive/recall",
    summary="Context-Aware Hybrid Memory Recall",
    description="Query memory fusing BM25 lexical, vector similarity, graph traversal, and temporal decay.",
)
def recall_cognitive_memory(req: CognitiveRecallRequest, http_request: Request) -> list[dict[str, Any]]:
    from agent_workspace.memory.cognitive import HybridRecallQuery

    with _cognitive_system_for_request(http_request) as system:
        query_obj = HybridRecallQuery(**req.model_dump())
        return [r.to_dict() for r in system.recall(query_obj)]


@router.get(
    "/memory/cognitive/working",
    summary="List Active Working Memory Items",
)
def list_working_memory(http_request: Request, task_id: str | None = None) -> list[dict[str, Any]]:
    with _cognitive_system_for_request(http_request) as system:
        items = system.working_mem.list_active(task_id=task_id, min_attention=0.0)
        return [it.to_dict() for it in items]


@router.post(
    "/memory/cognitive/working",
    summary="Add Working Memory Scratchpad Item",
)
def add_working_memory(req: WorkingMemoryCreateRequest, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        item = system.working_mem.add(**req.model_dump())
        return item.to_dict()


@router.delete(
    "/memory/cognitive/working",
    summary="Clear Working Memory Items",
)
def clear_working_memory(http_request: Request, task_id: str | None = None) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        cleared = system.working_mem.clear(task_id=task_id)
        return {"status": "cleared", "count": cleared}


@router.get(
    "/memory/cognitive/episodic",
    summary="List Episodic Traces or Episodes",
)
def list_episodic_memory(http_request: Request, mode: str = "trace", session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with _cognitive_system_for_request(http_request) as system:
        if mode == "episode":
            eps = system.episodic_mem.list_episodes(session_id=session_id, limit=limit)
            return [e.to_dict() for e in eps]
        traces = system.episodic_mem.list_traces(session_id=session_id, limit=limit)
        return [t.to_dict() for t in traces]


@router.post(
    "/memory/cognitive/episodic",
    summary="Record Episodic Trace",
)
def record_episodic_trace(req: EpisodicTraceCreateRequest, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        trace = system.episodic_mem.record_trace(**req.model_dump())
        system.save_to_disk()
        return trace.to_dict()


@router.delete(
    "/memory/cognitive/episodic/{trace_id}",
    summary="Delete Episodic Trace",
)
def delete_episodic_trace(trace_id: str, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        deleted = system.episodic_mem.delete_trace(trace_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Episodic trace '{trace_id}' not found.")
        system.save_to_disk()
        return {"status": "deleted", "trace_id": trace_id}


@router.get(
    "/memory/cognitive/semantic",
    summary="List Semantic Belief Graph Nodes and Edges",
)
def list_semantic_graph(http_request: Request, status: str | None = None, subject: str | None = None, limit: int = 100) -> dict[str, Any]:
    from agent_workspace.memory.cognitive import BeliefStatus

    with _cognitive_system_for_request(http_request) as system:
        st_enum = None
        if status:
            try:
                st_enum = BeliefStatus(status.lower())
            except ValueError:
                pass
        nodes = system.semantic_graph.list_nodes(status=st_enum, subject=subject, limit=limit)
        return {
            "metrics": system.semantic_graph.density_metrics(),
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in list(system.semantic_graph._edges.values())[:limit]],
        }


@router.post(
    "/memory/cognitive/semantic",
    summary="Add Semantic Belief",
)
def add_semantic_belief(req: SemanticBeliefCreateRequest, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        node = system.semantic_graph.add_belief(**req.model_dump())
        system.save_to_disk()
        return node.to_dict()


@router.delete(
    "/memory/cognitive/semantic/{node_id}",
    summary="Delete Semantic Belief Node",
)
def delete_semantic_belief(node_id: str, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        deleted = system.semantic_graph.delete_node(node_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Semantic belief node '{node_id}' not found.")
        system.save_to_disk()
        return {"status": "deleted", "node_id": node_id}


@router.get(
    "/memory/cognitive/procedural",
    summary="List Procedural Skills",
)
def list_procedural_skills(http_request: Request, limit: int = 50) -> list[dict[str, Any]]:
    with _cognitive_system_for_request(http_request) as system:
        skills = system.procedural_mem.list_skills(limit=limit)
        return [s.to_dict() for s in skills]


@router.post(
    "/memory/cognitive/procedural",
    summary="Register Procedural Skill",
)
def register_procedural_skill(req: ProceduralSkillCreateRequest, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        skill = system.procedural_mem.register_skill(**req.model_dump())
        system.save_to_disk()
        return skill.to_dict()


@router.delete(
    "/memory/cognitive/procedural/{skill_id}",
    summary="Delete Procedural Skill",
)
def delete_procedural_skill(skill_id: str, http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        deleted = system.procedural_mem.delete_skill(skill_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Procedural skill '{skill_id}' not found.")
        system.save_to_disk()
        return {"status": "deleted", "skill_id": skill_id}


@router.post(
    "/memory/cognitive/consolidate",
    summary="Trigger 3-Phase Sleep/Dream Consolidation Cycle",
)
def trigger_consolidation(http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        report = system.consolidate()
        return report.to_dict()


@router.post(
    "/memory/cognitive/reconcile",
    summary="Trigger Epistemic Belief Conflict Reconciliation",
)
def trigger_belief_reconciliation(http_request: Request) -> dict[str, Any]:
    with _cognitive_system_for_request(http_request) as system:
        conflicts_detected = system.semantic_graph.detect_conflicts()
        reconciled = system.semantic_graph.reconcile_conflicts()
        system.save_to_disk()
        return {
            "reconciled_count": reconciled,
            "conflicts_detected_count": len(conflicts_detected),
            "details": [{"n1": c[0].statement, "n2": c[1].statement, "reason": c[2]} for c in conflicts_detected],
        }
