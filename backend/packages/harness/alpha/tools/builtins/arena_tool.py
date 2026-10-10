"""The model-facing arena tool.

Actions on the ``arena`` tool implement the full lifecycle:
plan a run's cost, create a draft, start/resume it, read its
status/pairings/winner/card, and stop it. The tool is
lead-agent-only (denied to sub-agents like ``ralph_loop``)
and declares governance so the operator's ceiling wins.

Every action that reads or writes a run is owner-scoped: the
caller's identity comes from the request context, and the
service enforces it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from langchain.tools import tool

from alpha.arena.config import ArenaConfig, arena_config
from alpha.arena.service import ArenaConfirmationRequired, ArenaRunError, ArenaService
from alpha.arena.store import ArenaStoreError
from alpha.config import get_app_config
from alpha.runtime.user_context import get_effective_user_id
from alpha.utils.assembly_io import run_assembly

#: The arena tool name. Declared here so tests and the
#: manifest generator can reference it without importing
#: the tool function (which binds it to the langchain
#: decorator).
ARENA_TOOL_NAME = "arena"


@tool(ARENA_TOOL_NAME, parse_docstring=False)
async def arena_tool(
    runtime,
    action: Literal[
        "estimate",
        "create",
        "start",
        "resume",
        "status",
        "pairings",
        "winner",
        "card",
        "runs",
        "stop",
        "delete",
        "profiles",
    ],
    *,
    task: str = "",
    agents: int | None = None,
    wave: int | None = None,
    seed: str | int | None = None,
    baseline: str = "",
    max_subagent_calls: int | None = None,
    max_tokens: int | None = None,
    max_wall_seconds: float | None = None,
    confirm: bool = False,
    run_id: str = "",
    agent_id: str = "",
    profile: str = "",
    mode: str = "",
) -> str:
    """Run a single-elimination tournament of sub-agents.

    Each competitor solves the same task with a different
    strategy card (reasoning mode + workflow + strategy),
    then they attack, defend and revise each other in a
    bracket until one solution survives. A judge scores
    both revised solutions on a fixed rubric; the rubric
    arithmetic decides the winner, not the judge's prose.

    The engine is durable (one JSON state file, atomic
    writes, cross-process lock), resumable (a run survives
    a Gateway restart and continues from the last
    completed wave), and budget-gated (every run projects
    its exact call count before anything is spent; large
    runs need an explicit ``confirm=True``).

    Actions:

    * ``estimate`` - project the call count and estimated
      token cost for a run with the given parameters.
    * ``create`` - deal cards and persist a draft run.
      Returns the ``run_id``.
    * ``start`` - drive a draft to completion. If the
      projected call count exceeds the confirmation
      ceiling, ``confirm=True`` is required.
    * ``resume`` - continue a run that stopped mid-bracket
      (crashed, budget-exhausted, paused). Terminal runs
      cannot be resumed.
    * ``status`` - a bounded summary of a run's state.
    * ``pairings`` - the current round's pairings with
      each match's progress.
    * ``winner`` - the champion, its card, and the final
      check result.
    * ``card`` - the strategy card dealt to one
      competitor.
    * ``runs`` - list your arena runs (newest first).
    * ``stop`` - stop a running run.
    * ``delete`` - delete a run and its work files.
    * ``profiles`` - the declared budget profiles
      (quick/standard/deep) and the run modes, naming
      which modes this engine actually implements.

    Parameters beyond the run itself:

    * ``profile`` - ``quick`` / ``standard`` / ``deep``
      to pin a cost profile, or ``auto`` to let the
      router choose from the task's own signals (the
      reply always names the reason). An explicit
      ``agents``/``wave`` still wins over a profile.
    * ``mode`` - ``decide`` (the bracket, default) or
      ``plan`` (project cost only: ``start`` refuses a
      plan-mode run because it spends nothing).

    Owner-scoped: every read/write is checked against the
    caller's identity from the request context.
    """
    # Resolve config from the loaded AppConfig. Reading the config file
    # stats the filesystem, so it never happens on the event loop.
    app_config = getattr(runtime, "app_config", None)
    config = arena_config(app_config)
    if not config.enabled:
        return "Arena is disabled in config.yaml (arena.enabled: false)."

    # Read-only actions need no sub-agent runner, so they pay for no
    # tool assembly. Only the actions that actually execute jobs bind
    # one, and that assembly runs off-loop (it blocks on MCP).
    service = ArenaService(config=config)

    owner_id = str(get_effective_user_id(runtime) or "")
    if not owner_id:
        return "No effective user identity; cannot access arena runs."

    async def _executing_service() -> ArenaService:
        resolved_config = app_config if app_config is not None else await asyncio.to_thread(get_app_config)
        tools = await _resolve_subagent_tools(runtime, resolved_config)
        return _build_service(runtime, config, resolved_config, tools)

    try:
        if action == "estimate":
            return _format_estimate(
                service.estimate(
                    agents=agents,
                    wave=wave,
                    baseline=bool(baseline),
                    task=task,
                    profile=profile or None,
                    mode=mode or None,
                )
            )
        if action == "profiles":
            return _format_profiles(service.profiles())
        if action == "create":
            state = service.create(
                owner_id=owner_id,
                thread_id=runtime.config.get("configurable", {}).get("thread_id") if hasattr(runtime, "config") else None,
                task=task,
                agents=agents,
                wave=wave,
                seed=seed,
                baseline=baseline or None,
                max_subagent_calls=max_subagent_calls,
                max_tokens=max_tokens,
                max_wall_seconds=max_wall_seconds,
                profile=profile or None,
                mode=mode or None,
            )
            return (
                f"Created arena run {state['run_id']} with {state['agents_n']} competitors "
                f"(profile {state.get('profile', 'custom')}: {state.get('route_reason', '')}, "
                f"mode {state.get('mode', 'decide')}). "
                f"Use action='start' with run_id='{state['run_id']}' to run it."
            )
        if action == "start":
            if not run_id:
                return "start requires run_id"
            try:
                # Gate first: a refusal must cost no tool assembly.
                service.check_start(run_id, owner_id, confirm=confirm)
                state = await (await _executing_service()).start(run_id, owner_id, confirm=confirm)
            except ArenaConfirmationRequired as error:
                return str(error)
            return _format_status(state)
        if action == "resume":
            if not run_id:
                return "resume requires run_id"
            # Gate first: refusing a terminal run must cost no tool assembly.
            service.check_resume(run_id, owner_id)
            state = await (await _executing_service()).resume(run_id, owner_id)
            return _format_status(state)
        if action == "status":
            if not run_id:
                return "status requires run_id"
            return _format_status(service.status(run_id, owner_id))
        if action == "pairings":
            if not run_id:
                return "pairings requires run_id"
            return _format_pairings(service.pairings(run_id, owner_id))
        if action == "winner":
            if not run_id:
                return "winner requires run_id"
            return _format_winner(service.winner(run_id, owner_id))
        if action == "card":
            if not run_id or not agent_id:
                return "card requires run_id and agent_id"
            return _format_card(service.card(run_id, owner_id, agent_id))
        if action == "runs":
            runs = service.list_runs(owner_id)
            return _format_runs(runs)
        if action == "stop":
            if not run_id:
                return "stop requires run_id"
            service.stop(run_id, owner_id)
            return f"Stopped arena run {run_id}."
        if action == "delete":
            if not run_id:
                return "delete requires run_id"
            service.delete(run_id, owner_id)
            return f"Deleted arena run {run_id}."
        return f"unknown arena action '{action}'"
    except ArenaRunError as error:
        return f"Arena run error: {error}"
    except ArenaStoreError as error:
        return f"Arena store error: {error}"
    except ValueError as error:
        return f"Arena error: {error}"
    except KeyError as error:
        return f"Arena not found: {error}"


def _build_service(runtime, config: ArenaConfig, app_config, tools: list) -> ArenaService:
    """Bind a runner factory to this request's context.

    ``app_config`` and ``tools`` are resolved by the caller, async and
    off-loop, so neither this factory nor anything it is asked for can
    block the event loop. The service itself never imports ``app.*``.
    """
    from alpha.subagents.config import SubagentConfig
    from alpha.subagents.executor import SubagentExecutor

    configurable = runtime.config.get("configurable", {}) if hasattr(runtime, "config") else {}

    def factory(sub_config: SubagentConfig) -> SubagentExecutor:
        return SubagentExecutor(
            config=sub_config,
            tools=tools,
            app_config=app_config,
            thread_id=configurable.get("thread_id"),
            trace_id=configurable.get("trace_id"),
            user_id=str(get_effective_user_id(runtime) or ""),
        )

    return ArenaService(config=config, runner_factory=factory)


async def _resolve_subagent_tools(runtime) -> list:
    """Assemble the toolset a sub-agent inherits, minus the sub-agent
    tools themselves (no nested arenas).

    Assembly blocks on MCP discovery and config reads, so it runs
    through ``run_assembly`` (the shared off-loop assembly seam) —
    never inline on the event loop, and never through
    ``loop.run_until_complete``, which raises on an already-running
    loop and ``asyncio.run`` after it.
    """
    from alpha.tools import get_available_tools

    configurable = runtime.config.get("configurable", {}) if hasattr(runtime, "config") else {}
    return await run_assembly(
        get_available_tools,
        model_name=configurable.get("model_name"),
        groups=runtime.config.get("tool_groups") if hasattr(runtime, "config") else None,
        include_mcp=True,
        # Competitors are sub-agents: they get no delegation tools.
        subagent_enabled=False,
    )


def _format_estimate(estimate: dict[str, Any]) -> str:
    plan = estimate["plan"]
    lines = [
        f"Arena cost projection for {plan['agents']} agents:",
        f"  Profile: {estimate.get('profile', 'custom')} ({estimate.get('route_reason', 'configured defaults')})",
        f"  Mode: {estimate.get('mode', 'decide')}",
        f"  Rounds: {plan['rounds']}",
        f"  Total sub-agent calls: {plan['total_calls']}",
        f"  Waves at size {plan['wave_size']}: {plan['total_waves']}",
        f"  Estimated tokens: {estimate['estimated_tokens']:,}",
        f"  Confirmation required: {estimate['confirmation_required']}",
        "",
        "Per-round breakdown:",
    ]
    for row in plan["rows"]:
        lines.append(f"  Round {row['round']}: {row['alive']} alive, {row['matches']} matches, {'1 bye' if row['bye'] else 'no bye'}, {row['calls']} calls, {row['waves']} waves")
    return "\n".join(lines)


def _format_status(summary: dict[str, Any]) -> str:
    lines = [
        f"Run {summary['run_id']} — {summary['status']} ({summary['phase']})",
        f"  Profile: {summary.get('profile', 'custom')} ({summary.get('route_reason', '')}), mode: {summary.get('mode', 'decide')}",
        f"  Agents: {summary['agents_n']} (wave {summary['wave']})",
        f"  Cards dealt: {summary['cards_dealt']}, Solutions: {summary['solutions']}",
        f"  Rounds complete: {summary['rounds_complete']}/{len(summary['rounds'])}",
    ]
    if summary.get("champion"):
        lines.append(f"  Champion: {summary['champion']} ({summary.get('champion_card', {}).get('reasoning', {}).get('name', '?')})")
    budget = summary.get("budget", {})
    lines.append(f"  Budget: {budget.get('measured_calls', 0)}/{budget.get('max_subagent_calls', '∞')} calls, {budget.get('measured_tokens', 0):,}/{budget.get('max_tokens', '∞'):,} tokens")
    if summary.get("failed"):
        lines.append(f"  Failed agents: {', '.join(summary['failed'].keys())}")
    if summary.get("recalled"):
        lines.append(f"  Recalled strategies: {', '.join(summary['recalled'][:2])}{'…' if len(summary['recalled']) > 2 else ''}")
    return "\n".join(lines)


def _format_pairings(data: dict[str, Any]) -> str:
    if data["round"] == 0:
        return "Run has not started."
    lines = [f"Round {data['round']} ({data['status']})"]
    for match in data["matches"]:
        status_parts = []
        if match["winner"]:
            status_parts.append(f"winner: {match['winner']}")
        else:
            if match["attacks_recorded"][match["a"]] > 0 or match["attacks_recorded"][match["b"]] > 0:
                status_parts.append("attacks in")
            if match["revised"][match["a"]] or match["revised"][match["b"]]:
                status_parts.append("revisions in")
        line = f"  {match['id']}: {match['a']} ({match['a_card']}) vs {match['b']} ({match['b_card']}) — {', '.join(status_parts) or 'pending'}"
        unanswered = {side: indexes for side, indexes in (match.get("unanswered_attacks") or {}).items() if indexes}
        if unanswered:
            line += f" — unanswered attacks: {unanswered}"
        if match.get("repairs"):
            line += f" — dispositions recorded: {len(match['repairs'])}"
        lines.append(line)
    if data["byes"]:
        lines.append(f"  Byes: {', '.join(data['byes'])}")
    return "\n".join(lines)


def _format_winner(data: dict[str, Any]) -> str:
    lines = [
        f"Champion: {data['champion']}",
        f"  Card: {data['card'].get('reasoning', {}).get('name')} + {data['card'].get('workflow', {}).get('name')} + {data['card'].get('strategy', {}).get('name')}",
        f"  Final check: {data['final'].get('passed') if data['final'] else 'not run'}",
    ]
    if data["final"] and data["final"].get("reason"):
        lines.append(f"  Reason: {data['final']['reason']}")
    return "\n".join(lines)


def _format_card(card: dict[str, Any]) -> str:
    reasoning = card.get("reasoning", {})
    workflow = card.get("workflow", {})
    strategy = card.get("strategy", {})
    return f"Reasoning: {reasoning.get('name')}\n  {reasoning.get('how')}\n\nWorkflow: {workflow.get('name')}\n  {workflow.get('how')}\n\nStrategy: {strategy.get('name')}\n  {strategy.get('how')}"


def _format_profiles(data: dict[str, Any]) -> str:
    lines = ["Arena budget profiles (pass one as 'profile', or 'auto' to route):"]
    for spec in data["profiles"]:
        lines.append(f"  {spec['profile']}: {spec['agents']} competitors, wave {spec['wave']}, {spec['repair_cycles']} repair cycle(s), final check {'on' if spec['final_check'] else 'off'}")
        lines.append(f"    {spec['description']}")
    implemented = set(data.get("implemented_modes", []))
    lines.append("Run modes:")
    for mode in data["modes"]:
        suffix = "" if mode["mode"] in implemented else " (declared, not implemented - refused by name)"
        lines.append(f"  {mode['mode']}{suffix} - {mode['description']}")
    return "\n".join(lines)


def _format_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "No arena runs."
    lines = ["Arena runs (newest first):"]
    for run in runs[:10]:
        lines.append(f"  {run['run_id']} — {run['status']} — {run['agents_n']} agents — {run['task'][:80]}{'…' if len(run['task']) > 80 else ''}")
    if len(runs) > 10:
        lines.append(f"  … and {len(runs) - 10} more")
    return "\n".join(lines)


__all__ = [
    "ARENA_TOOL_NAME",
    "arena_tool",
]
