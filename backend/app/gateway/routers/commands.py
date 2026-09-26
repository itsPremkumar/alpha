"""Gateway API Router for Master Slash Commands.

Exposes endpoints for:
- Querying the global Master Slash Command registry (418+ commands across 28 families)
- Category listing and counts
- Substring and capability search across commands and descriptions
- Direct command dispatch and intent resolution for the UI/Agents
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from alpha.commands import (
    CommandCategory,
    LifecyclePhase,
    autonomous_command_engine,
    command_registry,
)
from alpha.commands.registry import APPROVAL_CONTEXT_KEY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/commands", tags=["commands"])

#: Ceiling on one slash-command dispatch over HTTP. A command can make a real
#: model turn or touch the filesystem, and ``asyncio.to_thread`` cannot cancel
#: the worker, so without a ceiling one request can pin a thread and the
#: endpoint never answers. Overrunning it returns an explicit ``timeout`` result
#: instead of hanging the caller.
DEFAULT_EXECUTE_TIMEOUT_SECONDS = 120.0

#: ``status`` alone is not a verdict: a catalog row with no bound handler answers
#: ``status="success"`` (the documented directive-accepted placeholder). The
#: response therefore also carries a ``verdict`` that says whether anything ran.
_VERDICTS: dict[str, str] = {
    "success": "succeeded",
    "ok": "succeeded",
    "error": "failed",
    "not_found": "unknown_command",
    "approval_required": "blocked_needs_approval",
    "timeout": "failed_timed_out",
}


def _execute_timeout_seconds() -> float:
    raw = os.environ.get("ALPHA_SLASH_COMMAND_TIMEOUT_SECONDS", "")
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_EXECUTE_TIMEOUT_SECONDS
    return parsed if parsed > 0 else DEFAULT_EXECUTE_TIMEOUT_SECONDS


def _verdict_for(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "").lower()
    verdict = _VERDICTS.get(status, "failed")
    if verdict == "succeeded" and not command_registry.has_handler(str(result.get("command") or "")):
        return "not_executed_placeholder"
    if status == "not_found":
        data = result.get("data") or {}
        if not data.get("unknown_subcommand") and not str(result.get("output") or "").startswith("Unknown slash command"):
            return "failed_target_not_found"
    return verdict


class CommandExecuteRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Slash command line to execute, e.g. '/goal status' or '/plan'")
    context: dict[str, Any] | None = Field(default=None, description="Optional execution context such as thread_id, agent_id, or options")


class AutoTriggerRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Natural language prompt or task to analyze")
    phase: str | None = Field(default=None, description="Optional current phase hint")
    auto_execute: bool = Field(default=True, description="Whether to immediately execute matched command")
    context: dict[str, Any] | None = Field(default=None, description="Optional execution context")


class PhaseTransitionRequest(BaseModel):
    phase: str = Field(..., description="Target lifecycle phase ('planning', 'research', 'swarm', 'coding', 'verification', 'self_heal', 'reflection', 'schedule')")
    details: str = Field(default="", description="Optional transition payload or error trace")
    context: dict[str, Any] | None = Field(default=None, description="Optional execution context")


@router.get("")
async def list_commands(
    category: str | None = Query(default=None, description="Filter by command category (e.g. 'core', 'mission', 'swarm')"),
    core_only: bool = Query(default=False, description="Filter only core commands"),
) -> dict[str, Any]:
    """Lists all registered slash commands with optional category or core filter."""
    cat_enum: CommandCategory | None = None
    if category:
        try:
            cat_enum = CommandCategory(category.lower())
        except ValueError:
            cat_enum = None

    commands = await asyncio.to_thread(command_registry.list_commands, category=cat_enum, only_core=core_only)
    return {
        "total": len(commands),
        "category": category,
        "core_only": core_only,
        # A row is only invokable if a handler is bound; the rest answer with the
        # directive-accepted placeholder. Saying which is which here stops the
        # discovery plane from advertising 410 rows as capabilities.
        "executable": sum(1 for c in commands if command_registry.has_handler(c.command)),
        "commands": [{**c.to_dict(), "has_handler": command_registry.has_handler(c.command)} for c in commands],
    }


@router.get("/categories")
async def get_categories() -> dict[str, Any]:
    """Returns all 28 command categories and their command counts."""
    categories = await asyncio.to_thread(command_registry.get_categories)
    return {
        "categories": categories,
        "total_categories": len(categories),
    }


@router.get("/search")
async def search_commands(
    q: str = Query(..., min_length=1, description="Search query string"),
) -> dict[str, Any]:
    """Searches commands by name, description, or category."""
    results = await asyncio.to_thread(command_registry.search, query=q)
    return {
        "query": q,
        "total": len(results),
        "commands": [{**c.to_dict(), "has_handler": command_registry.has_handler(c.command)} for c in results],
    }


@router.post("/execute")
async def execute_command(payload: CommandExecuteRequest) -> dict[str, Any]:
    """Dispatches a slash command intent to the runtime."""
    context = dict(payload.context or {})
    # An approval grant is an explicit, positive act by a human; refuse to
    # infer it from anything else in the context.
    if APPROVAL_CONTEXT_KEY in context and context[APPROVAL_CONTEXT_KEY] is not True:
        context.pop(APPROVAL_CONTEXT_KEY, None)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                command_registry.execute,
                command_line=payload.command,
                context=context,
                timeout_seconds=_execute_timeout_seconds(),
            ),
            timeout=_execute_timeout_seconds(),
        )
    except TimeoutError:
        return {
            "status": "timeout",
            "command": payload.command.split()[0] if payload.command.split() else "",
            "output": (
                f"Command did not answer within {_execute_timeout_seconds():g}s and was abandoned; "
                f"its outcome is unknown. Retry with a narrower command or raise "
                f"ALPHA_SLASH_COMMAND_TIMEOUT_SECONDS."
            ),
            "data": {"executed": False, "timed_out": True},
            "autonomous_directives": [],
            "verdict": "failed_timed_out",
        }
    body = result.to_dict()
    body["verdict"] = _verdict_for(body)
    return body


@router.post("/auto-trigger")
async def auto_trigger_command(payload: AutoTriggerRequest) -> dict[str, Any]:
    """Automatically identifies the required slash command and starts executing it at the correct time."""
    phase_enum = None
    if payload.phase:
        try:
            phase_enum = LifecyclePhase(payload.phase.lower())
        except ValueError:
            pass

    detection = await asyncio.to_thread(
        autonomous_command_engine.identify_and_trigger,
        prompt=payload.prompt,
        phase_hint=phase_enum,
        auto_execute=payload.auto_execute,
        context=payload.context,
    )
    return detection.to_dict()


@router.post("/phase-transition")
async def trigger_phase_transition(payload: PhaseTransitionRequest) -> dict[str, Any]:
    """Executes phase-specific autonomous slash commands at exact lifecycle events (e.g. on error, post-code edit)."""
    try:
        phase_enum = LifecyclePhase(payload.phase.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid phase: {payload.phase}")

    detection = await asyncio.to_thread(
        autonomous_command_engine.trigger_phase_transition,
        phase=phase_enum,
        details=payload.details,
        context=payload.context,
    )
    return detection.to_dict()


@router.get("/lifecycle-rules")
async def get_lifecycle_rules() -> dict[str, Any]:
    """Returns all active autonomous command trigger rules and their lifecycle phases."""
    rules = [
        {
            "rule_id": r.rule_id,
            "phase": r.phase.value,
            "target_command": r.target_command,
            "description": r.description,
            "keywords": r.keywords,
            "priority": r.priority,
        }
        for r in autonomous_command_engine.rules
    ]
    return {"total_rules": len(rules), "rules": rules}
