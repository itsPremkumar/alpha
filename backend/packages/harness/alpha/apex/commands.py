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
from alpha.apex.store import ApexSessionState
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


def _format_budget(value: int | None) -> str:
    return "unlimited" if value is None else f"{value:,}"


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
        lines.append(f"Invariant sites present: {summary['live']}/{summary['declared']} (module/symbol probe only; runtime enforcement not verified)")
        data["invariants"] = {
            "declared": summary["declared"],
            "live": summary["live"],
            "all_live": summary["all_live"],
            "probe_scope": summary["probe_scope"],
            "runtime_enforcement_verified": summary["runtime_enforcement_verified"],
        }
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
        (
            f"Operational caps: {_format_budget(contract.budget.max_active_agents)} agents, {_format_budget(contract.budget.max_parallel_tasks)} parallel, "
            f"depth {_format_budget(contract.budget.max_delegation_depth)}, {_format_budget(contract.budget.max_replans)} replans, "
            f"{_format_budget(contract.budget.max_retries_per_failure_class)} retries per failure class; spend ceilings: "
            f"{_format_budget(contract.budget.max_tool_calls)} tool calls, {_format_budget(contract.budget.max_total_tokens)} tokens, "
            f"{_format_budget(contract.budget.max_runtime_minutes)} min"
        ),
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


# --------------------------------------------------------------------------- #
# Session control — pause / resume / stop / steer / take-over / approve /
# reject / replan / verify. Every one of these resolves the *active session
# for the conversation* rather than a session id, because a chat command
# names a conversation, not a row.
# --------------------------------------------------------------------------- #


def _session_store() -> Any:
    from alpha.apex.store import get_apex_store

    return get_apex_store()


def _goal_store() -> Any:
    from alpha.apex.goals import get_goal_store

    return get_goal_store()


def _active_session(scope: str) -> Any:
    """The live session bound to this conversation, or ``None``."""
    return _session_store().active_for_scope(scope)


def _session_for_command(scope: str) -> Any:
    """The session a control command targets.

    The live session when one exists; otherwise the newest
    terminal one, so a completed mission is refused by name
    ("session X is terminal") rather than answered as though
    the conversation never had a session at all.
    """
    store = _session_store()
    session = store.active_for_scope(scope)
    if session is None:
        session = store.latest_for_scope(scope)
    return session


def _no_session(scope: str) -> CommandExecutionResult:
    return CommandExecutionResult(
        status="error",
        command="/apex",
        output=(f"No active APEX session in this conversation (scope: {scope}).\nCreate one with POST /api/apex/sessions, passing this conversation's thread id."),
        data={"session": None, "scope_key": scope},
    )


def _terminal_refusal(session: Any, verb: str) -> CommandExecutionResult:
    return CommandExecutionResult(
        status="error",
        command="/apex",
        output=f"Cannot {verb}: session {session.session_id} is terminal ('{session.state.value}').",
        data={"session_id": session.session_id, "state": session.state.value},
    )


def _approval_gate_refusal(session: Any, verb: str) -> CommandExecutionResult:
    """Refuse to move a parked session from the control verbs.

    The approval gate owns ``BLOCKED``: only an operator verdict (or a
    replan) may move it. Without this, ``pause`` followed by ``resume``
    would un-park it in two commands — the gate with a side door.

    The refusal also says the park already stops the work, so an operator
    arriving with stop intent learns the session decides nothing rather
    than being sent to un-park it first.
    """
    return CommandExecutionResult(
        status="error",
        command="/apex",
        output=(
            f"Cannot {verb}: session {session.session_id} is parked awaiting approval"
            f" ({session.blocked_reason or 'blocked'}). It already decides nothing, and the control"
            " verbs do not move a blocked session — decide the approval with /apex approve or"
            " /apex reject, or move it with /apex replan."
        ),
        data={
            "session_id": session.session_id,
            "state": session.state.value,
            "blocked_reason": session.blocked_reason,
        },
    )


def handle_apex_pause(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex pause`` — park this conversation's session."""
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "pause")
    if session.state is ApexSessionState.BLOCKED:
        return _approval_gate_refusal(session, "pause")
    if session.state.value == "paused":
        return CommandExecutionResult(
            status="success",
            command="/apex pause",
            output="APEX is already paused for this session.",
            data={"applied": False, "state": "paused", "session_id": session.session_id},
        )
    updated = _session_store().set_state(session.session_id, ApexSessionState.PAUSED, reason="operator pause")
    if updated is None:
        return CommandExecutionResult(
            status="error",
            command="/apex pause",
            output="Pause was refused by the session state machine.",
            data={"applied": False, "session_id": session.session_id},
        )
    return CommandExecutionResult(
        status="success",
        command="/apex pause",
        output=(f"APEX paused for this session ({session.session_id}). The executive will decide nothing further until /apex resume. Work already admitted to a run is owned by RunManager and is not interrupted by a pause."),
        data={"applied": True, "state": "paused", "session_id": session.session_id},
    )


def handle_apex_resume(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex resume`` — release a paused session."""
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "resume")
    if session.state is ApexSessionState.BLOCKED:
        return _approval_gate_refusal(session, "resume")
    if session.state.value != "paused":
        return CommandExecutionResult(
            status="success",
            command="/apex resume",
            output=f"APEX is not paused for this session (state: {session.state.value}).",
            data={"applied": False, "state": session.state.value, "session_id": session.session_id},
        )
    updated = _session_store().set_state(session.session_id, ApexSessionState.ACTIVE, reason="operator resume")
    if updated is None:
        return CommandExecutionResult(
            status="error",
            command="/apex resume",
            output="Resume was refused by the session state machine.",
            data={"applied": False, "session_id": session.session_id},
        )
    return CommandExecutionResult(
        status="success",
        command="/apex resume",
        output=f"APEX resumed for this session ({session.session_id}).",
        data={"applied": True, "state": "active", "session_id": session.session_id},
    )


def handle_apex_stop(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex stop`` — stop this mission's work.

    Stops *this mission*: the session is parked so the executive decides
    nothing further for it. Two boundaries are stated rather than crossed:
    in-flight runs are owned by ``RunManager`` (APEX is a control plane,
    not a second lifecycle owner), and the whole-fleet emergency stop is
    ``alpha.runtime.control``'s ESTOP — deliberately not an APEX command,
    because spec §24 requires the emergency stop outside LLM control.
    """
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "stop")
    if session.state is ApexSessionState.BLOCKED:
        return _approval_gate_refusal(session, "stop")
    if session.state.value == "paused":
        return CommandExecutionResult(
            status="success",
            command="/apex stop",
            output="APEX is already stopped for this session.",
            data={"applied": False, "state": "paused", "session_id": session.session_id},
        )
    updated = _session_store().set_state(session.session_id, ApexSessionState.PAUSED, reason="operator stop")
    if updated is None:
        return CommandExecutionResult(
            status="error",
            command="/apex stop",
            output="Stop was refused by the session state machine.",
            data={"applied": False, "session_id": session.session_id},
        )
    return CommandExecutionResult(
        status="success",
        command="/apex stop",
        output=(
            f"APEX stopped for this session ({session.session_id}). The executive will decide "
            "nothing further for this mission.\n"
            "In-flight runs belong to RunManager and are not interrupted here; the whole-fleet "
            "emergency stop is the separate ESTOP, which no APEX command can engage."
        ),
        data={"applied": True, "state": "paused", "session_id": session.session_id},
    )


def handle_apex_steer(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex steer <instruction>`` — record a mission constraint."""
    instruction = (args or "").strip()
    if not instruction:
        return CommandExecutionResult(
            status="error",
            command="/apex steer",
            output="Usage: /apex steer <instruction>\nExample: /apex steer prefer readability over cleverness",
            data={"error": "an instruction is required"},
        )
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    constraint = _session_store().record_constraint(session.session_id, instruction, source="user")
    if constraint is None:
        current = _session_store().get(session.session_id)
        reason = "session is terminal" if current and current.is_terminal else "constraint refused"
        return CommandExecutionResult(
            status="error",
            command="/apex steer",
            output=f"Steering was not recorded: {reason}.",
            data={"error": reason, "session_id": session.session_id},
        )
    return CommandExecutionResult(
        status="success",
        command="/apex steer",
        output=f"Constraint recorded on session {session.session_id} ({constraint.constraint_id}).",
        data={"constraint": constraint.to_dict(), "session_id": session.session_id},
    )


def handle_apex_take_over(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex take-over`` — the operator drives; APEX stands down.

    Take-over is a pause *plus* a recorded, high-priority constraint:
    the park stops the executive, and the constraint is what the next
    cycle reads when the operator releases it, so "a human drove this"
    is answerable from the record rather than from memory.
    """
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "take over")
    if session.state is ApexSessionState.BLOCKED:
        return _approval_gate_refusal(session, "take over")
    if session.state.value != "paused":
        _session_store().set_state(session.session_id, ApexSessionState.PAUSED, reason="operator take-over")
    _session_store().record_constraint(
        session.session_id,
        "Operator took over manual control; APEX stands down until released",
        source="operator",
        priority="high",
    )
    return CommandExecutionResult(
        status="success",
        command="/apex take-over",
        output=(f"Operator take-over: APEX is parked for this session ({session.session_id}) and a high-priority constraint records it. Resume with /apex resume."),
        data={"applied": True, "state": "paused", "session_id": session.session_id},
    )


def handle_apex_approve(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex approve`` — decide the pending approval for this session."""
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "approve")
    store = _session_store()
    pending = store.pending_approval(session.session_id)
    if pending is None:
        state_note = " The session is parked, but no approval is pending — it was parked outside the approval gate, so /apex replan or /apex stop is the honest next step." if session.state.value == "blocked" else ""
        return CommandExecutionResult(
            status="error",
            command="/apex approve",
            output=f"Nothing is pending approval for this session.{state_note}",
            data={"approval": None, "session_id": session.session_id},
        )
    outcome = store.decide_approval(
        pending.approval_id,
        verdict="approved",
        operator=_owner_of(context) or "operator",
        note="approved via /apex approve",
    )
    if outcome is None:
        return CommandExecutionResult(
            status="error",
            command="/apex approve",
            output=f"Approval {pending.approval_id} was already decided; a second verdict cannot overwrite it.",
            data={"approval_id": pending.approval_id, "decided": False},
        )
    record, resumed = outcome
    resume_note = " The session resumed." if resumed is not None else "\nNote: the session did not resume — it is not parked at BLOCKED, so the approval is recorded only."
    return CommandExecutionResult(
        status="success",
        command="/apex approve",
        output=f"Approved {record.approval_id}: {record.note or '(no note)'}.{resume_note}",
        data={"approval": record.to_dict(), "resumed": resumed is not None, "session_id": session.session_id},
    )


def handle_apex_reject(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex reject`` — refuse the pending approval; the park stands."""
    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "reject")
    store = _session_store()
    pending = store.pending_approval(session.session_id)
    if pending is None:
        return CommandExecutionResult(
            status="error",
            command="/apex reject",
            output="Nothing is pending approval for this session.",
            data={"approval": None, "session_id": session.session_id},
        )
    outcome = store.decide_approval(
        pending.approval_id,
        verdict="rejected",
        operator=_owner_of(context) or "operator",
        note="rejected via /apex reject",
    )
    if outcome is None:
        return CommandExecutionResult(
            status="error",
            command="/apex reject",
            output=f"Approval {pending.approval_id} was already decided; a second verdict cannot overwrite it.",
            data={"approval_id": pending.approval_id, "decided": False},
        )
    record, _resumed = outcome
    return CommandExecutionResult(
        status="success",
        command="/apex reject",
        output=(f"Rejected {record.approval_id}. The session stays parked at '{session.state.value}'; the recorded blocker was: " + (record.note or "(none)")),
        data={"approval": record.to_dict(), "resumed": False, "session_id": session.session_id},
    )


def handle_apex_replan(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex replan`` — move this session's goals back to replanning.

    Spec §37's strategy escalation: a stop is not a dead end. Every
    non-terminal goal in the session is asked to enter REPLANNING; a
    goal whose transition table refuses (e.g. one already terminal, or
    one in a state the table does not serve) is named in the output
    rather than silently skipped, so the operator sees exactly what
    was and was not replanned.
    """
    from alpha.apex.goals import GoalState, IllegalGoalTransition

    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "replan")
    goals = _goal_store().list(session_id=session.session_id)
    if not goals:
        return CommandExecutionResult(
            status="success",
            command="/apex replan",
            output=f"No goals exist for this session ({session.session_id}); nothing to replan.",
            data={"replanned": [], "refused": [], "session_id": session.session_id},
        )
    moved: list[str] = []
    refused: list[str] = []
    for goal in goals:
        if goal.is_terminal:
            refused.append(f"{goal.goal_id} (terminal: {goal.state.value})")
            continue
        try:
            _goal_store().transition(goal.goal_id, GoalState.REPLANNING, reason="operator replan")
            moved.append(goal.goal_id)
        except IllegalGoalTransition as exc:
            refused.append(f"{goal.goal_id} ({exc})")
    _session_store().emit(
        session.session_id,
        "session.replan_requested",
        replanned=len(moved),
        refused=len(refused),
        operator=_owner_of(context) or "operator",
    )
    lines = [f"Replan requested for session {session.session_id}:"]
    if moved:
        lines.append(f"  replanned ({len(moved)}): " + ", ".join(moved))
    if refused:
        lines.append(f"  not replanned ({len(refused)}): " + "; ".join(refused))
    return CommandExecutionResult(
        status="success",
        command="/apex replan",
        output="\n".join(lines),
        data={"replanned": moved, "refused": refused, "session_id": session.session_id},
    )


def handle_apex_verify(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """``/apex verify`` — verify this session's goals against their criteria.

    Verification-first (spec §16): each non-terminal goal either enters
    VERIFYING (criteria still unmeasured), completes through the
    acceptance gate (every criterion measured and holding), or is
    refused with the criteria that failed. A refusal is reported per
    goal, never folded into a success.
    """
    from alpha.apex.goals import IllegalGoalTransition

    scope = _scope_of(context)
    session = _session_for_command(scope)
    if session is None:
        return _no_session(scope)
    if session.is_terminal:
        return _terminal_refusal(session, "verify")
    goals = _goal_store().list(session_id=session.session_id)
    if not goals:
        return CommandExecutionResult(
            status="success",
            command="/apex verify",
            output=f"No goals exist for this session ({session.session_id}); nothing to verify.",
            data={"completed": [], "verifying": [], "refused": [], "session_id": session.session_id},
        )
    completed: list[str] = []
    verifying: list[str] = []
    refused: list[str] = []
    for goal in goals:
        if goal.is_terminal:
            refused.append(f"{goal.goal_id} (terminal: {goal.state.value})")
            continue
        try:
            updated = _goal_store().verify(goal.goal_id)
            if updated.state.value == "completed":
                completed.append(goal.goal_id)
            else:
                verifying.append(f"{goal.goal_id} ({updated.state.value})")
        except IllegalGoalTransition as exc:
            refused.append(f"{goal.goal_id} ({exc})")
    lines = [f"Verification for session {session.session_id}:"]
    if completed:
        lines.append(f"  completed ({len(completed)}): " + ", ".join(completed))
    if verifying:
        lines.append(f"  verifying ({len(verifying)}): " + "; ".join(verifying))
    if refused:
        lines.append(f"  refused ({len(refused)}): " + "; ".join(refused))
    if not completed and not refused and not verifying:
        lines.append("  no non-terminal goals to verify")
    return CommandExecutionResult(
        status="success",
        command="/apex verify",
        output="\n".join(lines),
        data={
            "completed": completed,
            "verifying": verifying,
            "refused": refused,
            "session_id": session.session_id,
        },
    )


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
    "/apex pause": handle_apex_pause,
    "/apex resume": handle_apex_resume,
    "/apex stop": handle_apex_stop,
    "/apex steer": handle_apex_steer,
    "/apex take-over": handle_apex_take_over,
    "/apex approve": handle_apex_approve,
    "/apex reject": handle_apex_reject,
    "/apex replan": handle_apex_replan,
    "/apex verify": handle_apex_verify,
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
    "/apex pause": "Parks this conversation's APEX session; the executive decides nothing further",
    "/apex resume": "Releases a paused APEX session",
    "/apex stop": "Stops this mission's APEX work (in-flight runs belong to RunManager)",
    "/apex steer": "Records a mission constraint on this conversation's APEX session",
    "/apex take-over": "Parks APEX and records that the operator drives manually",
    "/apex approve": "Approves the pending approval and resumes the parked session",
    "/apex reject": "Rejects the pending approval; the session stays parked",
    "/apex replan": "Moves this session's non-terminal goals back to replanning",
    "/apex verify": "Verifies this session's goals against their success criteria",
}

_USAGE: dict[str, str] = {
    "/apex": "/apex",
    "/apex on": "/apex on [assist|autonomous|apex_max]",
    "/apex off": "/apex off",
    "/apex status": "/apex status",
    "/apex policy": "/apex policy [off|assist|autonomous|apex_max]",
    "/apex pause": "/apex pause",
    "/apex resume": "/apex resume",
    "/apex stop": "/apex stop",
    "/apex steer": "/apex steer <instruction>",
    "/apex take-over": "/apex take-over",
    "/apex approve": "/apex approve",
    "/apex reject": "/apex reject",
    "/apex replan": "/apex replan",
    "/apex verify": "/apex verify",
}


register_apex_commands()
