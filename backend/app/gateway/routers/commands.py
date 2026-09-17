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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from agent_workspace.commands import (
    CommandCategory,
    LifecyclePhase,
    autonomous_command_engine,
    command_registry,
)
from fastapi import HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/commands", tags=["commands"])


class CommandExecuteRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Slash command line to execute, e.g. '/goal status' or '/plan'")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Optional execution context such as thread_id, agent_id, or options")


class AutoTriggerRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Natural language prompt or task to analyze")
    phase: Optional[str] = Field(default=None, description="Optional current phase hint")
    auto_execute: bool = Field(default=True, description="Whether to immediately execute matched command")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Optional execution context")


class PhaseTransitionRequest(BaseModel):
    phase: str = Field(..., description="Target lifecycle phase ('planning', 'research', 'swarm', 'coding', 'verification', 'self_heal', 'reflection', 'schedule')")
    details: str = Field(default="", description="Optional transition payload or error trace")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Optional execution context")


@router.get("")
async def list_commands(
    category: Optional[str] = Query(default=None, description="Filter by command category (e.g. 'core', 'mission', 'swarm')"),
    core_only: bool = Query(default=False, description="Filter only core commands"),
) -> Dict[str, Any]:
    """Lists all registered slash commands with optional category or core filter."""
    cat_enum: Optional[CommandCategory] = None
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
        "commands": [c.to_dict() for c in commands],
    }


@router.get("/categories")
async def get_categories() -> Dict[str, Any]:
    """Returns all 28 command categories and their command counts."""
    categories = await asyncio.to_thread(command_registry.get_categories)
    return {
        "categories": categories,
        "total_categories": len(categories),
    }


@router.get("/search")
async def search_commands(
    q: str = Query(..., min_length=1, description="Search query string"),
) -> Dict[str, Any]:
    """Searches commands by name, description, or category."""
    results = await asyncio.to_thread(command_registry.search, query=q)
    return {
        "query": q,
        "total": len(results),
        "commands": [c.to_dict() for c in results],
    }


@router.post("/execute")
async def execute_command(payload: CommandExecuteRequest) -> Dict[str, Any]:
    """Dispatches a slash command intent to the runtime."""
    result = await asyncio.to_thread(
        command_registry.execute,
        command_line=payload.command,
        context=payload.context,
    )
    return result.to_dict()


@router.post("/auto-trigger")
async def auto_trigger_command(payload: AutoTriggerRequest) -> Dict[str, Any]:
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
async def trigger_phase_transition(payload: PhaseTransitionRequest) -> Dict[str, Any]:
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
async def get_lifecycle_rules() -> Dict[str, Any]:
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
