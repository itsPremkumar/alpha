"""``/goal`` and ``/subgoal``: the standing goal's operator surface.

These bindings are what make the loop *reachable* rather than merely present.
``alpha.commands.registry.SlashCommandRegistry`` is the process-wide dispatch
table, and the gateway calls it from a live route
(``backend/app/gateway/routers/commands.py`` -> ``command_registry.execute``), so
a handler registered here is a real runtime path, not a test-only affordance.

Two rules shaped which commands this module claims:

* **Only claim commands that are currently unhandled.** A catalog row with no
  handler resolves to ``SlashCommandRegistry._dispatch``'s stub, which returns
  ``"Directive <cmd> accepted"`` and does nothing - the single most expensive
  kind of dead surface. This module fills those holes rather than shadowing
  working handlers (``/goal create``, ``/goal status`` and ``/goal decompose``
  already belong to ``backend_handlers`` and are left alone).
* **Never require approval to observe.** ``/goal status`` and ``/goal gate
  list`` are read-only and must stay usable while a loop is mid-run; only
  destructive verbs (clear, gate remove) do anything irreversible, and they say
  so.

Every handler returns a :class:`CommandExecutionResult`; a handler that raises
is turned into a failed command by the registry, so these stay total.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any

from alpha.mission.goalloop import (
    CompletionContract,
    GateReport,
    GateRunner,
    GoalLoopEngine,
    GoalState,
    GoalStatus,
    GoalStore,
    QualityGate,
    TurnAction,
    build_draft_prompt,
    draft_contract,
    get_goal_store,
    parse_goal_text,
)

logger = logging.getLogger(__name__)

#: Commands this module binds that the catalog does not already cover. Keys are
#: the exact command strings the registry matches on.
NEW_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/goal", "Sets the standing goal and starts its first turn", "/goal <objective>"),
    ("/goal show", "Prints the active goal's completion contract", "/goal show"),
    ("/goal step", "Runs one turn boundary now: gates, then judge, then continuation", "/goal step [response text]"),
    ("/goal verify", "Runs the gates and the judge against the goal right now", "/goal verify"),
    ("/goal draft", "Drafts a completion contract from a plain objective, then sets it", "/goal draft <objective>"),
    ("/goal clear", "Drops the standing goal entirely", "/goal clear"),
    ("/goal gate add", "Adds a quality gate that must exit 0 before done", "/goal gate add <command>"),
    ("/goal gate list", "Lists the goal's gates and their last state", "/goal gate list"),
    ("/goal gate remove", "Removes the Nth gate (1-based)", "/goal gate remove <N>"),
    ("/goal gate clear", "Removes every gate", "/goal gate clear"),
    ("/subgoal", "Appends an acceptance criterion to the active goal", "/subgoal <text>"),
    ("/subgoal remove", "Removes the Nth subgoal (1-based)", "/subgoal remove <N>"),
    ("/subgoal clear", "Drops every subgoal but keeps the goal", "/subgoal clear"),
)

#: Filled at bind time so tests can assert exactly which rows this module owns.
_BOUND: list[str] = []


def bound_commands() -> list[str]:
    """The command strings this module registered handlers for."""
    return list(_BOUND)


# --------------------------------------------------------------- accessors


def _store() -> GoalStore:
    return get_goal_store()


def _session_of(context: dict[str, Any] | None) -> str:
    """Resolve the session key from the dispatch context.

    The registry hands handlers an opaque context dict; whichever of the common
    conversation identifiers it carries becomes the goal's key, so two
    conversations never share a goal.
    """
    context = context or {}
    for key in ("session_id", "thread_id", "conversation_id", "chat_id", "run_id"):
        value = context.get(key)
        if value:
            return str(value)
    return "default"


def _load(context: dict[str, Any] | None) -> GoalState | None:
    return _store().load(_session_of(context))


def _result(command: str, output: str, *, status: str = "success", **data: Any) -> Any:
    from alpha.commands.registry import CommandExecutionResult

    return CommandExecutionResult(status=status, command=command, output=output, data=data)


def _no_goal(command: str) -> Any:
    return _result(command, "No active goal. Set one with /goal <objective>.", has_goal=False)


def _status_block(state: GoalState) -> str:
    lines = [
        f"goal: {state.goal_id}",
        f"status: {state.status.value}",
        f"objective: {state.headline}",
        f"turns: {state.turns_used}/{state.max_turns} (budget remaining: {state.budget_remaining})",
    ]
    if state.last_verdict:
        lines.append(f"last verdict: {state.last_verdict} - {state.last_reason}")
    if state.pause_reason:
        lines.append(f"pause reason: {state.pause_reason}")
    if not state.contract.is_empty:
        lines.append("contract:")
        lines.append(state.contract.render())
    if state.subgoals:
        lines.append("subgoals:")
        lines.extend(f"  {index}. {item}" for index, item in enumerate(state.subgoals, start=1))
    if state.gates:
        lines.append("gates:")
        lines.extend(f"  {index}. {gate.name}" for index, gate in enumerate(state.gates, start=1))
    return "\n".join(lines)


# ----------------------------------------------------------------- handlers


def _refuse_near_miss(args: str) -> str | None:
    """Reproduce the registry's near-miss refusal for the ``/goal`` family.

    ``/goal`` genuinely takes free text, so the registry's blanket "a token
    after a no-argument row is a mistyped subcommand" guard cannot apply to it
    as written - and applying it to any trailing token would make ``/goal <prose>``
    unusable, because difflib fuzzily matches ordinary first words to
    subcommand names often enough to be useless ("fix" ~ "refine").

    Dropping the guard would weaken a real protection though: ``/goal creat``
    must not silently become a goal called "creat". So it is re-applied at its
    actual width - a *bare* token that fuzzily matches a ``/goal`` subcommand
    and carries no further words is a mistyped subcommand, and is refused with
    the registry's own wording. Anything with more text is prose and is set as
    the objective.
    """
    from alpha.commands.registry import command_registry

    tokens = (args or "").strip().split()
    if len(tokens) != 1:
        return None
    wanted = tokens[0].lower()
    siblings = [c.split(" ", 1)[1] for c in command_registry.subcommands_of("/goal") if " " in c]
    close = difflib.get_close_matches(wanted, siblings, n=3, cutoff=0.6)
    if not close:
        return None
    listed = close + [s for s in siblings if s not in close]
    hint = f" Did you mean: {', '.join(close)}?"
    return (
        f"Unknown subcommand '{wanted}' for /goal; nothing was executed.{hint}\n"
        f"Known subcommands of /goal: {', '.join(listed)}\n"
        "To set a goal whose text starts with those words, add more text so it is "
        "unambiguously an objective."
    )


def handle_goal(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/goal <text>`` - set (or replace) the standing goal.

    Setting a new goal REPLACES the old one and clears its subgoals, which is
    the documented behaviour: the subgoals belonged to the objective they were
    added to, and carrying them onto a different objective would silently
    change what "done" means.
    """
    from alpha.commands.registry import CommandExecutionResult

    session = _session_of(context)
    text = (args or "").strip()
    if not text:
        state = _load(context)
        if state is None:
            return _no_goal("/goal")
        # Nested under "goal", never splatted: GoalState.to_dict() carries its
        # own `status` key (the goal's status), which would silently overwrite
        # the CommandExecutionResult status and make a successful command
        # report itself as "active"/"paused".
        return _result("/goal", _status_block(state), has_goal=True, goal=state.to_dict())

    refusal = _refuse_near_miss(text)
    if refusal is not None:
        return _result("/goal", refusal, status="not_found", executed=False)

    parsed = parse_goal_text(text)
    if not parsed.objective:
        return _result("/goal", "That goal had no objective text, only contract fields.", status="error", executed=False)

    store = _store()
    state = GoalState(session_id=session, objective=parsed.objective, contract=parsed.contract)
    store.save(state)

    lines = [f"Goal set ({state.max_turns}-turn budget): {state.headline}"]
    if parsed.has_contract:
        lines.append("Completion contract:")
        lines.append(parsed.contract.render())
    lines.append("Use /goal step to run a turn boundary, /subgoal to add criteria, /goal gate add <command> for a hard check.")
    return CommandExecutionResult(
        status="success",
        command="/goal",
        output="\n".join(lines),
        data={"goal_id": state.goal_id, "session_id": session, "goal": state.to_dict()},
    )


def handle_goal_show(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/goal show`` - the contract, so an operator can see the bar they set."""
    state = _load(context)
    if state is None:
        return _no_goal("/goal show")
    if state.contract.is_empty:
        return _result(
            "/goal show",
            "No completion contract is set. Add field lines to /goal, e.g.\n"
            "  verification: <the command that proves it>\n"
            "  boundaries: <what is in scope>",
            has_goal=True,
            has_contract=False,
        )
    return _result("/goal show", state.contract.render(), has_goal=True, has_contract=True)


def handle_goal_draft(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/goal draft <objective>`` - expand a one-liner into a contract.

    Drafting is best-effort by construction: if no drafter is available the
    plain goal is still set, because refusing to set a goal because a second
    model is down would be a worse failure than a vaguer contract.

    Synchronous on purpose - the registry's dispatch is synchronous, so an
    ``async def`` handler would be handed back to the caller as a coroutine
    object and rendered as its repr.
    """
    from alpha.mission.goalloop.judge import make_model_judge

    objective = (args or "").strip()
    if not objective:
        return _result("/goal draft", "Usage: /goal draft <objective>", status="error")

    def _drafter(raw_objective: str) -> Any:
        return _draft_fields(make_model_judge())(raw_objective)

    contract, error = _run_coroutine(draft_contract(objective, _drafter))
    session = _session_of(context)
    store = _store()
    state = GoalState(
        session_id=session,
        objective=objective,
        contract=contract or CompletionContract(),
    )
    store.save(state)
    lines = [f"Goal set ({state.max_turns}-turn budget): {state.headline}"]
    if contract is not None:
        lines.append("Drafted completion contract:")
        lines.append(contract.render())
    else:
        lines.append(f"Contract drafting unavailable ({error}); set a plain free-form goal.")
    return _result("/goal draft", "\n".join(lines), has_goal=True, drafted=contract is not None, goal=state.to_dict())


def _draft_fields(model_fn: Any) -> Any:
    """Adapt a chat model into the contract-drafting callable."""

    async def _draft(objective: str) -> Any:
        import json

        from langchain_core.messages import HumanMessage, SystemMessage

        response = await model_fn.ainvoke(
            [
                SystemMessage(content="You expand objectives into completion contracts. Reply with one JSON object only."),
                HumanMessage(content=build_draft_prompt(objective)),
            ],
        )
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            content = str(content)
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(content[start : end + 1])

    return _draft


def handle_goal_step(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/goal step [text]`` - run one turn boundary synchronously.

    This is the callable seam: gates first, then the judge, then a decision. It
    is what an operator (or a host runtime) calls at a turn boundary when the
    automatic after-turn hook is not installed.
    """
    state = _load(context)
    if state is None:
        return _no_goal("/goal step")
    if state.status is not GoalStatus.ACTIVE:
        return _result(
            "/goal step",
            f"Goal is {state.status.value}; nothing to continue. {state.pause_reason}\nUse /goal resume to continue.",
            has_goal=True,
            action=TurnAction.IDLE.value,
        )

    response_text = (args or "").strip() or "(no response text supplied; judging the previous turn's state)"
    engine = _engine_for(state)
    decision = _run_decision(engine, state, response_text, user_initiated=False)
    return _result(
        "/goal step",
        _decision_text(decision),
        has_goal=True,
        action=decision.action.value,
        continuation=decision.continuation,
        goal=state.to_dict(),
    )


def handle_goal_verify(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/goal verify`` - gates plus judge, reported, without continuing."""
    state = _load(context)
    if state is None:
        return _no_goal("/goal verify")
    runner = GateRunner()
    report = GateReport(results=runner.run_all(state.gates))
    if not report.all_passed:
        return _result(
            "/goal verify",
            f"Gates are RED, so the goal cannot be judged done.\n{report.render_failures()}",
            has_goal=True,
            gates_passed=False,
            gates=report.to_dict(),
        )
    return _result(
        "/goal verify",
        "All gates pass. Run /goal step to have the judge rule on the goal.",
        has_goal=True,
        gates_passed=True,
        gates=report.to_dict(),
    )


def handle_goal_clear(args: str, context: dict[str, Any] | None = None) -> Any:
    state = _load(context)
    if state is None:
        return _no_goal("/goal clear")
    goal_id = state.goal_id
    # Delete rather than tombstone: a cleared goal must read as "no goal" on the
    # next dispatch, not as a lingering record of a goal nobody is pursuing.
    _store().delete(state.session_id)
    return _result("/goal clear", f"Goal {goal_id} cleared.", has_goal=False, goal_id=goal_id)


def handle_goal_gate(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/goal gate add|list|remove|clear``."""
    from alpha.commands.registry import CommandResolution, command_registry  # noqa: F401

    parts = (args or "").strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    state = _load(context)
    if state is None:
        return _no_goal("/goal gate")

    if sub == "add":
        if not rest:
            return _result("/goal gate add", "Usage: /goal gate add <command>", status="error")
        gate = state.add_gate(QualityGate(command=rest))
        _store().save(state)
        return _result(
            "/goal gate add",
            f"Gate #{len(state.gates)} added: {gate.command}\n"
            "It must exit 0 before this goal can be judged done; until then the judge is not called.",
            has_goal=True,
            gate=gate.to_dict(),
        )

    if sub in {"list", ""}:
        if not state.gates:
            return _result("/goal gate list", "No gates on this goal.", has_goal=True, gates=[])
        rows = [
            f"{index}. {gate.command} (max {gate.max_attempts} attempts, {gate.timeout_seconds:g}s each)"
            for index, gate in enumerate(state.gates, start=1)
        ]
        return _result("/goal gate list", "\n".join(rows), has_goal=True, gates=[g.to_dict() for g in state.gates])

    if sub == "remove":
        try:
            index = int(rest)
        except ValueError:
            return _result("/goal gate remove", f"'{rest}' is not a gate number.", status="error")
        try:
            removed = state.remove_gate(index)
        except IndexError as exc:
            return _result("/goal gate remove", str(exc), status="error")
        _store().save(state)
        return _result("/goal gate remove", f"Removed gate #{index}: {removed.command}", has_goal=True)

    if sub == "clear":
        count = state.clear_gates()
        _store().save(state)
        return _result("/goal gate clear", f"Removed {count} gate(s).", has_goal=True, removed=count)

    known = "add, list, remove, clear"
    return _result(
        "/goal gate",
        f"Unknown gate subcommand '{sub}'. Known: {known}.",
        status="error",
        known=known,
        executed=False,
    )


def handle_subgoal(args: str, context: dict[str, Any] | None = None) -> Any:
    """``/subgoal <text>`` - append a criterion without resetting the loop."""
    state = _load(context)
    if state is None:
        return _no_goal("/subgoal")
    text = (args or "").strip()
    if not text:
        if not state.subgoals:
            return _result("/subgoal", "No subgoals. Add one with /subgoal <text>.", has_goal=True, subgoals=[])
        rows = "\n".join(f"{index}. {item}" for index, item in enumerate(state.subgoals, start=1))
        return _result("/subgoal", rows, has_goal=True, subgoals=list(state.subgoals))

    parts = text.split(maxsplit=1)
    if parts[0].lower() in {"remove", "clear"} and len(parts) > 1:
        return _subgoal_remove(state, parts[0].lower(), parts[1].strip())
    if parts[0].lower() == "clear" and len(parts) == 1:
        count = state.clear_subgoals()
        _store().save(state)
        return _result("/subgoal clear", f"Removed {count} subgoal(s); the goal itself is untouched.", has_goal=True)

    added = state.add_subgoal(text)
    _store().save(state)
    return _result(
        "/subgoal",
        f"Subgoal #{len(state.subgoals)} added: {added}\n"
        "The loop was not reset; the goal is now complete only when the original objective "
        "AND every subgoal are satisfied.",
        has_goal=True,
        subgoals=list(state.subgoals),
    )


def _subgoal_remove(state: GoalState, verb: str, rest: str) -> Any:
    if verb == "clear":
        count = state.clear_subgoals()
        _store().save(state)
        return _result("/subgoal clear", f"Removed {count} subgoal(s); the goal itself is untouched.", has_goal=True)
    try:
        index = int(rest)
    except ValueError:
        return _result("/subgoal remove", f"'{rest}' is not a subgoal number.", status="error")
    try:
        removed = state.remove_subgoal(index)
    except IndexError as exc:
        return _result("/subgoal remove", str(exc), status="error")
    _store().save(state)
    return _result("/subgoal remove", f"Removed subgoal #{index}: {removed}", has_goal=True, subgoals=list(state.subgoals))


# ------------------------------------------------------------------ helpers


def _engine_for(state: GoalState) -> GoalLoopEngine:
    """Build an engine bound to this goal's store.

    The judge is left unconfigured unless a caller installs one, which means
    the fail-open path is the default: with no judge the loop continues and
    never falsely completes. That default is the safe one.
    """
    store = _store()
    return GoalLoopEngine(save=store.save)


def _run_decision(
    engine: GoalLoopEngine,
    state: GoalState,
    response_text: str,
    *,
    user_initiated: bool,
) -> Any:
    return _run_coroutine(engine.on_turn_end(state, response_text, user_initiated=user_initiated))


def _run_coroutine(coro: Any) -> Any:
    """Resolve *coro* from synchronous command dispatch.

    Runs it directly when no loop is active, and on a worker thread when one is
    - blocking an already-running loop would deadlock the very runtime that is
    trying to drive the goal.
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def _runner() -> None:
        box["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    return box.get("value")


def _decision_text(decision: Any) -> str:
    lines = [f"decision: {decision.action.value}", f"reason: {decision.reason}"]
    if decision.continuation:
        lines.append("continuation message (plain user role, appended verbatim):")
        lines.append(decision.continuation)
    if decision.gate_report is not None and not decision.gate_report.all_passed:
        lines.append("gates: RED")
    return "\n".join(lines)


# ------------------------------------------------------------------ binding

_HANDLERS = {
    "/goal": handle_goal,
    "/goal show": handle_goal_show,
    "/goal step": handle_goal_step,
    "/goal verify": handle_goal_verify,
    "/goal draft": handle_goal_draft,
    "/goal clear": handle_goal_clear,
    "/goal gate": handle_goal_gate,
    "/goal gate add": handle_goal_gate,
    "/goal gate list": handle_goal_gate,
    "/goal gate remove": handle_goal_gate,
    "/goal gate clear": handle_goal_gate,
    "/subgoal": handle_subgoal,
    "/subgoal remove": handle_subgoal,
    "/subgoal clear": handle_subgoal,
}


def bind_goal_commands() -> list[str]:
    """Bind every handler above onto the process-wide command registry.

    Idempotent: re-binding replaces the handler rather than stacking a second
    one, so a module reload cannot double-register.
    """
    from alpha.commands.registry import CommandCategory, SlashCommandDef, command_registry

    bound: list[str] = []
    for command, handler in _HANDLERS.items():
        cmd_def = command_registry.get(command)
        if command == "/goal":
            # The catalog row publishes usage "/goal", which makes the registry
            # treat any trailing token as a mistyped subcommand. This command
            # really does take free text, so it is re-registered with the usage
            # that says so, and the near-miss guard it displaces is re-applied
            # inside handle_goal (see _refuse_near_miss).
            cmd_def = SlashCommandDef(
                command="/goal",
                category=CommandCategory.MISSION,
                description="Sets the standing goal and starts its first turn",
                usage="/goal <objective>",
                is_core=True,
            )
        elif cmd_def is None:
            usage = next((usage for name, _, usage in NEW_COMMANDS if name == command), f"{command} [args]")
            description = next((desc for name, desc, _ in NEW_COMMANDS if name == command), command)
            cmd_def = SlashCommandDef(
                command=command,
                category=CommandCategory.MISSION,
                description=description,
                usage=usage,
                is_core=True,
            )
        command_registry.register(cmd_def, handler=handler)
        bound.append(command)

    _BOUND[:] = bound
    logger.info("Bound %d standing-goal handlers to SlashCommandRegistry.", len(bound))
    return bound


bind_goal_commands()
