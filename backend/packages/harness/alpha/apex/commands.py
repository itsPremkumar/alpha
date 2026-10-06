"""The ``/apex`` command family — spec §3.

Registered through the same process-wide ``SlashCommandRegistry`` as every other
Alpha command, so ``/apex on`` from chat, ``POST /api/commands/execute`` over
HTTP, and the model-facing ``execute_slash_command`` tool are **one** command
with one implementation. That is spec §6's requirement that a command be
callable by a user, by APEX, and by an authorized agent without separate logic
paths — and it is why these handlers call :mod:`alpha.apex.mode` rather than
doing their own persistence.

**Why this module exists separately from ``backend_handlers.py``.** That file is
already 1,100+ lines of unrelated subsystems. A self-registering module beside
the ``alpha/apex`` code it serves follows the ``alpha.mission.goalloop.bindings``
precedent, keeps the APEX surface findable in one place, and makes the command
count auditable: ``bound_commands()`` returns exactly what was registered.

**Handlers are synchronous.** The registry dispatches synchronously, so an
``async def`` handler would be handed back as a coroutine object and rendered as
its repr. Nothing here awaits.

**Nothing here raises.** A handler that raises becomes ``status="error"`` at the
registry's safety net, losing the structured ``data``. Every failure here is
returned instead, with the reason in both ``output`` and ``data``.
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.apex.contract import AutonomyProfile
from alpha.apex.mode import get_apex_mode_store, set_mode
from alpha.commands.registry import CommandExecutionResult, command_registry

logger = logging.getLogger(__name__)

__all__ = ["bound_commands", "register_apex_commands"]

#: Mirrors the Gateway's ``_session_of``: whichever conversation identifier the
#: dispatch context carries becomes the scope. Two conversations must never
#: share an autonomy toggle.
_SCOPE_KEYS = ("apex_scope", "session_id", "thread_id", "conversation_id", "chat_id", "run_id")

#: The profile used when ``/apex on`` names none. ``assist`` rather than
#: ``autonomous`` deliberately: a bare "on" should not be the most permissive
#: setting available. Raising it is an explicit act.
DEFAULT_ON_PROFILE = AutonomyProfile.ASSIST.value


def _scope_of(context: dict[str, Any] | None) -> str:
    context = context or {}
    for key in _SCOPE_KEYS:
        value = context.get(key)
        if value:
            return str(value)
    return "default"


def _owner_of(context: dict[str, Any] | None) -> str:
    context = context or {}
    return str(context.get("user_id") or context.get("actor") or "")


def _mode_payload(scope_key: str) -> dict[str, Any]:
    """The record plus the contract it authorises, for ``data``.

    Nested under ``mode`` on purpose: a splat of the record would carry its own
    ``profile``/``enabled`` keys into ``data`` where a consumer could read them
    as the command's result rather than the mode's.
    """
    record = get_apex_mode_store().for_scope(scope_key)
    return {
        "scope_key": scope_key,
        "enabled": record.enabled,
        "profile": record.profile,
        "contract_digest": record.contract().digest(),
        "enabled_at": record.enabled_at,
        "updated_at": record.updated_at,
        **({"load_note": record.load_note} if record.load_note else {}),
    }


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def handle_apex_on(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex on [profile]`` — enable APEX for this session."""
    raw = (args or "").strip()
    requested = raw.split()[0] if raw else DEFAULT_ON_PROFILE
    scope = _scope_of(context)

    try:
        outcome = set_mode(scope, True, profile=requested, owner=_owner_of(context))
    except ValueError as exc:
        return CommandExecutionResult(
            status="error",
            command="/apex on",
            output=f"APEX was not enabled: {exc}\nUsage: /apex on [off|assist|autonomous|apex_max]",
            data={"error": str(exc), "enabled": False, "changed": False, "mode": _mode_payload(scope)},
        )
    except Exception as exc:  # noqa: BLE001 - a handler must report, not raise
        return CommandExecutionResult(
            status="error",
            command="/apex on",
            output=f"APEX was not enabled: {type(exc).__name__}: {exc}",
            data={"error": f"{type(exc).__name__}: {exc}", "enabled": False, "changed": False},
        )

    mode = _mode_payload(scope)
    if not outcome["changed"]:
        return CommandExecutionResult(
            status="success",
            command="/apex on",
            output=f"APEX is already on for this session at profile '{mode['profile']}'.",
            data={"enabled": True, "changed": False, "mode": mode, "reason": outcome.get("reason", "")},
        )
    return CommandExecutionResult(
        status="success",
        command="/apex on",
        output=(
            f"APEX autopilot is ON for this session (profile: {mode['profile']}, "
            f"contract {mode['contract_digest']}).\n"
            "APEX decides the strategy; it does not run work itself — existing engines execute, "
            "and a mission completes only on measured acceptance evidence."
        ),
        data={"enabled": True, "changed": True, "mode": mode, "durable": outcome.get("durable", False)},
        autonomous_directives=["Compose a goal with /apex goals", "Inspect the contract with /apex policy"],
    )


def handle_apex_off(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex off`` — disable APEX for this session, keeping the profile."""
    scope = _scope_of(context)
    try:
        outcome = set_mode(scope, False, owner=_owner_of(context))
    except Exception as exc:  # noqa: BLE001
        return CommandExecutionResult(
            status="error",
            command="/apex off",
            output=f"APEX was not disabled: {type(exc).__name__}: {exc}",
            data={"error": f"{type(exc).__name__}: {exc}", "changed": False},
        )

    mode = _mode_payload(scope)
    if not outcome["changed"]:
        return CommandExecutionResult(
            status="success",
            command="/apex off",
            output="APEX is already off for this session.",
            data={"enabled": False, "changed": False, "mode": mode, "reason": outcome.get("reason", "")},
        )
    return CommandExecutionResult(
        status="success",
        command="/apex off",
        output=(f"APEX autopilot is OFF for this session. Mission state is preserved; re-enabling restores profile '{mode['profile']}'."),
        data={"enabled": False, "changed": True, "mode": mode, "durable": outcome.get("durable", False)},
    )


def handle_apex_status(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex status`` — on/off, profile, contract, loop, invariants."""
    scope = _scope_of(context)
    mode = _mode_payload(scope)

    lines = [
        f"APEX autopilot: {'ON' if mode['enabled'] else 'OFF'}",
        f"Scope: {scope}",
        f"Profile: {mode['profile']}",
        f"Contract: {mode['contract_digest']}",
    ]
    data: dict[str, Any] = {"read_only": True, "mode": mode}

    try:
        from alpha.apex.invariants import invariant_summary

        summary = invariant_summary()
        lines.append(f"Invariants: {summary['live']}/{summary['declared']} enforcement sites live")
        data["invariants"] = {"declared": summary["declared"], "live": summary["live"], "all_live": summary["all_live"]}
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Invariants: not probed ({type(exc).__name__})")
        data["invariants"] = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    try:
        from alpha.runtime.control import read_state

        fleet = read_state()
        lines.append(f"Fleet control: {fleet.mode.value}")
        data["fleet"] = {"mode": fleet.mode.value, "reason": fleet.reason}
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Fleet control: unreadable ({type(exc).__name__}) — treated as refusing work")
        data["fleet"] = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    if not mode["enabled"]:
        lines.append("")
        lines.append("Turn it on with /apex on [assist|autonomous|apex_max]")

    return CommandExecutionResult(status="success", command="/apex status", output="\n".join(lines), data=data)


def handle_apex_policy(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex policy [profile]`` — what the contract grants and what it refuses."""
    raw = (args or "").strip()
    scope = _scope_of(context)
    record = get_apex_mode_store().for_scope(scope)
    target = raw.split()[0] if raw else record.profile

    try:
        from alpha.apex.contract import profile_for

        contract = profile_for(target, mission_id=scope)
    except ValueError as exc:
        return CommandExecutionResult(
            status="error",
            command="/apex policy",
            output=f"{exc}\nUsage: /apex policy [off|assist|autonomous|apex_max]",
            data={"error": str(exc), "read_only": True},
        )

    granted = sorted(k for k, v in contract.authority.items() if v)
    protected = ", ".join(f"{k}={v}" for k, v in sorted(contract.protected_actions.items()))
    # "(in force)" must mean *this* profile is the one the session is running,
    # not merely that the profile happens to grant authority. `contract.enabled`
    # answers the latter, and every non-OFF profile answers it True -- so
    # `/apex policy assist` during an apex_max session would claim to be active.
    in_force = record.enabled and record.profile == contract.profile.value
    lines = [
        f"APEX policy - profile '{contract.profile.value}' ({'in force' if in_force else 'not in force'})",
        f"Contract: {contract.digest()}",
        f"Budgets: {contract.budget.max_active_agents} agents, {contract.budget.max_parallel_tasks} parallel, {contract.budget.max_tool_calls} tool calls, {contract.budget.max_runtime_minutes} min",
        "Emergency stop: always on (not configurable)",
        f"Authority granted ({len(granted)}): {', '.join(granted)}",
        f"Protected actions: {protected}",
        f"Delegated to: {', '.join(sorted(contract.policy_sites.to_dict()))}",
    ]
    if not in_force and record.enabled:
        lines.insert(1, f"(this session is running profile '{record.profile}')")
    elif not record.enabled:
        lines.insert(1, "(APEX is off for this session, so this grants nothing right now)")

    from alpha.apex.contract import PolicyAttribution

    live = set(PolicyAttribution.live_sites())
    missing = sorted(set(contract.policy_sites.to_dict()) - live)
    if missing:
        lines.append(f"WARNING: delegated policy kernels not importable: {', '.join(missing)}")

    return CommandExecutionResult(
        status="success",
        command="/apex policy",
        output="\n".join(lines),
        data={"read_only": True, "contract": contract.to_dict(), "policy_sites_missing": missing},
    )


def handle_apex(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex`` with no argument — routes to status, the safe default.

    Deliberately not "enable". ``/apex`` on its own must never grant autonomy
    from a mistyped or truncated line.
    """
    return handle_apex_status(args, context=context)


#: (command, handler). Colon spellings are registered beside the space forms
#: because the registry normalises ``:`` to a space but the catalog publishes
#: both, and a name with a handler but no published row fails
#: ``test_discovery_plane_parity``.
_APEX_HANDLERS: dict[str, Any] = {
    "/apex": handle_apex,
    "/apex on": handle_apex_on,
    "/apex off": handle_apex_off,
    "/apex status": handle_apex_status,
    "/apex policy": handle_apex_policy,
}

_BOUND: list[str] = []


def bound_commands() -> list[str]:
    """Exactly what this module registered. Used by the parity test."""
    return list(_BOUND)


def register_apex_commands() -> list[str]:
    """Bind every ``/apex`` handler onto the process-wide registry.

    Idempotent: re-binding replaces the handler rather than stacking a second
    one, so a module reload cannot double-register.
    """
    from alpha.commands.registry import CommandCategory, SlashCommandDef

    published = {c for c in _APEX_HANDLERS if command_registry.get(c) is not None}
    bound: list[str] = []
    for command, handler in _APEX_HANDLERS.items():
        cmd_def = command_registry.get(command)
        if cmd_def is None:
            # Synthesised only when the catalog does not publish the row. The
            # catalog is the preferred home; this keeps the family functional if
            # a row is ever missing rather than leaving a dead command.
            cmd_def = SlashCommandDef(
                command=command,
                category=CommandCategory.MISSION,
                description=_DESCRIPTIONS.get(command, command),
                usage=_USAGE.get(command, f"{command} [args]"),
                is_core=True,
            )
        command_registry.register(cmd_def, handler=handler)
        bound.append(command)

    _BOUND[:] = bound
    unpublished = sorted(set(_APEX_HANDLERS) - published)
    logger.info("Bound %d APEX handlers to SlashCommandRegistry%s", len(bound), f"; unpublished: {unpublished}" if unpublished else "")
    return bound


_DESCRIPTIONS: dict[str, str] = {
    "/apex": "Shows the APEX autopilot state for this session",
    "/apex on": "Enables APEX autopilot for this session at a named profile",
    "/apex off": "Disables APEX autopilot for this session, preserving mission state",
    "/apex status": "Shows APEX on/off, profile, contract, invariants and fleet control",
    "/apex policy": "Shows what the APEX contract grants, budgets, and refuses",
}

_USAGE: dict[str, str] = {
    "/apex": "/apex",
    "/apex on": "/apex on [assist|autonomous|apex_max]",
    "/apex off": "/apex off",
    "/apex status": "/apex status",
    "/apex policy": "/apex policy [off|assist|autonomous|apex_max]",
}


register_apex_commands()
