"""The ``/mission`` command family — the chat entry point for durable mission memory.

Registered through the same process-wide :class:`SlashCommandRegistry` as every
other command, so ``/mission`` from chat, ``POST /api/commands/execute``, and the
model-facing ``execute_slash_command`` tool are one command with one
implementation — the same rule :mod:`alpha.apex.commands` follows and states.

This is the *human* surface for the mission-memory substrate in
:mod:`alpha.runtime.missions`: set a long-horizon objective in chat, then walk
away and read a one-line resume brief on return. The model-facing
``mission_memory`` tool remains the surface the *agent* records into; this command
delegates to the same durable, owner+thread-scoped stack, so the two cannot drift.

**Handlers are synchronous and nothing here raises.** The registry dispatches
synchronously, so an ``async def`` handler would be rendered as a coroutine repr;
and a raising handler is flattened to a bare ``status="error"`` that loses the
structured ``data``. Every failure is returned with the reason in both ``output``
and ``data``.

**The owner is server-resolved, never client-asserted.** The command ``context``
arrives from an HTTP body or an IM payload — client-controlled — so this module
does **not** read an owner out of it. The owner comes from
:func:`alpha.runtime.user_context.resolve_runtime_user_id` (the auth middleware's
request ContextVar, which ``asyncio.to_thread`` copies into the command worker);
only the *thread/scope key* is taken from the context. That keeps one user's
mission from ever being read or written by another, while still letting a caller
address its own conversations.
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.commands.registry import CommandCategory, CommandExecutionResult, command_registry
from alpha.runtime.missions import (
    MissionManager,
    MissionStack,
    decide_mission,
    render_anchor,
    render_resume_brief,
)
from alpha.runtime.user_context import resolve_runtime_user_id

logger = logging.getLogger(__name__)

__all__ = ["bound_commands", "register_mission_commands"]

#: Whichever conversation identifier the dispatch context carries becomes the
#: mission's thread key, so two conversations never share a mission.
_SCOPE_KEYS = ("session_id", "thread_id", "conversation_id", "chat_id", "run_id")


def _scope_of(context: dict[str, Any] | None) -> str:
    context = context or {}
    for key in _SCOPE_KEYS:
        value = context.get(key)
        if value:
            return str(value)
    return "default"


def _owner_of(_context: dict[str, Any] | None) -> str:
    """Server-resolved owner. The context is ignored on purpose (see module doc)."""
    return resolve_runtime_user_id(None)


def _manager() -> MissionManager:
    from alpha.config.paths import get_paths

    return MissionManager(get_paths())


def _brief_output(stack: MissionStack) -> str:
    brief = render_resume_brief(stack)
    anchor = render_anchor(stack, max_chars=1200)
    return (brief + ("\n\n" + anchor if anchor else "")).strip()


def handle_mission(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/mission`` — show this conversation's mission and where it is (read-only)."""
    owner = _owner_of(context)
    thread = _scope_of(context)
    try:
        stack = _manager().load(owner, thread)
    except ValueError as exc:
        return CommandExecutionResult(status="error", command="/mission", output=f"Could not read the mission: {exc}", data={"error": str(exc), "readable": False})
    if not stack.is_active():
        return CommandExecutionResult(
            status="success",
            command="/mission",
            output="No mission on this conversation yet. Start one with `/mission set <objective>` — an objective small enough to finish and verify.",
            data={"active": False, "owner": owner, "thread": thread, "progress": stack.progress()},
            autonomous_directives=["Start a mission with /mission set <objective>"],
        )
    progress = stack.progress()
    decision = decide_mission(stack).to_dict()
    return CommandExecutionResult(
        status="success",
        command="/mission",
        output=_brief_output(stack),
        data={"active": True, "owner": owner, "thread": thread, "progress": progress, "decision": decision},
    )


def handle_mission_set(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/mission set <objective>`` — start or refresh this conversation's objective."""
    objective = (args or "").strip()
    if not objective:
        return CommandExecutionResult(status="error", command="/mission set", output="An objective is required: `/mission set <objective>`.", data={"error": "objective_required", "changed": False})
    owner = _owner_of(context)
    thread = _scope_of(context)
    manager = _manager()
    try:
        stack = manager.load(owner, thread)
        changed = stack.spec_objective.strip() != objective
        if changed:
            # Setting a fresh objective clears any parked state: a new objective
            # is a fresh start, not a continuation of a blocked run.
            stack = stack.with_spec(objective=objective).unblock()
            manager.save(owner, thread, stack)
    except ValueError as exc:
        return CommandExecutionResult(status="error", command="/mission set", output=f"Could not record the objective: {exc}", data={"error": str(exc), "changed": False})
    return CommandExecutionResult(
        status="success",
        command="/mission set",
        output=("Objective recorded for this conversation.\n\n" if changed else "That objective is already set for this conversation.\n\n") + _brief_output(stack),
        data={"active": True, "owner": owner, "thread": thread, "objective": objective, "changed": changed, "progress": stack.progress()},
        autonomous_directives=["Add milestones with the mission_memory tool (action=set_plan)", "Check progress any time with /mission"],
    )


_MISSION_HANDLERS: dict[str, Any] = {
    "/mission": handle_mission,
    "/mission set": handle_mission_set,
}

_DESCRIPTIONS: dict[str, str] = {
    "/mission": "Shows this conversation's mission, progress and resume brief",
    "/mission set": "Starts or refreshes this conversation's mission objective",
}

_USAGE: dict[str, str] = {
    "/mission": "/mission",
    "/mission set": "/mission set <objective>",
}

_BOUND: list[str] = []


def bound_commands() -> list[str]:
    """Exactly what this module registered. Used by the parity test."""
    return list(_BOUND)


def register_mission_commands() -> list[str]:
    """Bind every ``/mission`` handler onto the process-wide registry.

    Idempotent: re-binding replaces the handler rather than stacking a second
    one, so a module reload cannot double-register.
    """
    from alpha.commands.registry import SlashCommandDef

    published = {c for c in _MISSION_HANDLERS if command_registry.get(c) is not None}
    bound: list[str] = []
    for command, handler in _MISSION_HANDLERS.items():
        cmd_def = command_registry.get(command)
        if cmd_def is None:
            # Synthesised only when the catalog does not publish the row; the
            # catalog is the preferred home, so this is a safety net, not the path.
            cmd_def = SlashCommandDef(command=command, category=CommandCategory.MISSION, description=_DESCRIPTIONS.get(command, command), usage=_USAGE.get(command, f"{command} [args]"), is_core=True)
        command_registry.register(cmd_def, handler=handler)
        bound.append(command)

    _BOUND[:] = bound
    unpublished = sorted(set(_MISSION_HANDLERS) - published)
    logger.info("Bound %d mission handlers to SlashCommandRegistry%s", len(bound), f"; unpublished: {unpublished}" if unpublished else "")
    return bound


register_mission_commands()
