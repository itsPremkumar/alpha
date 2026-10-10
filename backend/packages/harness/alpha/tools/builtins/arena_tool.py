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

from typing import Literal

from langchain.tools import tool

from alpha.arena.config import ArenaConfig, arena_config
from alpha.arena.service import ArenaConfirmationRequired, ArenaRunError, ArenaService
from alpha.runtime.user_context import get_effective_user_id

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

    Owner-scoped: every read/write is checked against the
    caller's identity from the request context.
    """
    # Resolve config from the loaded AppConfig
    app_config = getattr(runtime, "app_config", None)
    config = arena_config(app_config)
    if not config.enabled:
        return "Arena is disabled in config.yaml (arena.enabled: false)."

    # Build the service with a runner factory bound to this
    # run's context. The factory builds a SubagentExecutor
    # with the parent's tools/sandbox/thread/identity.
    # The service never imports app.*; the factory is
    # supplied by the caller's runtime.
    service = _build_service(runtime, config)

    owner_id = str(get_effective_user_id(runtime) or "")
    if not owner_id:
        return "No effective user identity; cannot access arena runs."

    try:
        if action == "estimate":
            return _format_estimate(service.estimate(agents=agents, wave=wave, baseline=bool(baseline)))
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
            )
            return f"Created arena run {state['run_id']} with {state['agents_n']} competitors. Use action='start' with run_id='{state['run_id']}' to run it."
        if action == "start":
            if not run_id:
                return "start requires run_id"
            try:
                state = await service.start(run_id, owner_id, confirm=confirm)
            except ArenaConfirmationRequired as error:
                return str(error)
            return _format_status(state)
        if action == "resume":
            if not run_id:
                return "resume requires run_id"
            state = await service.resume(run_id, owner_id)
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


def _build_service(runtime, config: ArenaConfig) -> ArenaService:
    """Bind a runner factory to the current request's context."""
    from alpha.tools import get_available_tools
    from alpha.subagents.config import SubagentConfig
    from alpha.subagents.executor import SubagentExecutor
    from alpha.config import get_app_config

    app_config = getattr(runtime, "app_config", None) or get_app_config()

    def factory(sub_config: SubagentConfig) -> SubagentExecutor:
        return SubagentExecutor(
            config=sub_config,
            tools=_get_tools_for_subagent(runtime, app_config),
            app_config=app_config,
            thread_id=runtime.config.get("configurable", {}).get("thread_id") if hasattr(runtime, "config") else None,
            trace_id=runtime.config.get("configurable", {}).get("trace_id") if hasattr(runtime, "config") else None,
            user_id=str(get_effective_user_id(runtime) or ""),
        )

    return ArenaService(config=config, runner_factory=factory)


def _get_tools_for_subagent(runtime, app_config) -> list:
    """Resolve the toolset a sub-agent inherits, minus the
    sub-agent tools themselves (no nested arenas)."""
    import asyncio

    parent_tool_groups = runtime.config.get("tool_groups") if hasattr(runtime, "config") else None

    async def _assemble():
        return await asyncio.to_thread(
            get_available_tools,
            model_name=runtime.config.get("configurable", {}).get("model_name") if hasattr(runtime, "config") else None,
            groups=parent_tool_groups,
            include_mcp=True,
            subagent_enabled=False,
        )

    # The tool is called from the lead agent's turn, so we're
    # on the event loop. Tool assembly blocks on MCP, so it
    # must run off-loop.
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(_assemble())
    except RuntimeError:
        # No loop: synchronous call (e.g. tests)
        return asyncio.run(_assemble())


def _format_estimate(estimate: dict[str, Any]) -> str:
    plan = estimate["plan"]
    lines = [
        f"Arena cost projection for {plan['agents']} agents:",
        f"  Rounds: {plan['rounds']}",
        f"  Total sub-agent calls: {plan['total_calls']}",
        f"  Waves at size {plan['wave_size']}: {plan['total_waves']}",
        f"  Estimated tokens: {estimate['estimated_tokens']:,}",
        f"  Confirmation required: {estimate['confirmation_required']}",
        "",
        "Per-round breakdown:",
    ]
    for row in plan["rows"]:
        lines.append(
            f"  Round {row['round']}: {row['alive']} alive, {row['matches']} matches, "
            f"{'1 bye' if row['bye'] else 'no bye'}, {row['calls']} calls, {row['waves']} waves"
        )
    return "\n".join(lines)


def _format_status(summary: dict[str, Any]) -> str:
    lines = [
        f"Run {summary['run_id']} — {summary['status']} ({summary['phase']})",
        f"  Agents: {summary['agents_n']} (wave {summary['wave']})",
        f"  Cards dealt: {summary['cards_dealt']}, Solutions: {summary['solutions']}",
        f"  Rounds complete: {summary['rounds_complete']}/{len(summary['rounds'])}",
    ]
    if summary.get("champion"):
        lines.append(f"  Champion: {summary['champion']} ({summary.get('champion_card', {}).get('reasoning', {}).get('name', '?')})")
    budget = summary.get("budget", {})
    lines.append(f"  Budget: {budget.get('measured_calls', 0)}/{budget.get('max_subagent_calls', '∞')} calls, "
                 f"{budget.get('measured_tokens', 0):,}/{budget.get('max_tokens', '∞'):,} tokens")
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
        lines.append(
            f"  {match['id']}: {match['a']} ({match['a_card']}) vs "
            f"{match['b']} ({match['b_card']}) — {', '.join(status_parts) or 'pending'}"
        )
    if data["byes"]:
        lines.append(f"  Byes: {', '.join(data['byes'])}")
    return "\n".join(lines)


def _format_winner(data: dict[str, Any]) -> str:
    lines = [
        f"Champion: {data['champion']}",
        f"  Card: {data['card'].get('reasoning', {}).get('name')} + "
        f"{data['card'].get('workflow', {}).get('name')} + "
        f"{data['card'].get('strategy', {}).get('name')}",
        f"  Final check: {data['final'].get('passed') if data['final'] else 'not run'}",
    ]
    if data["final"] and data["final"].get("reason"):
        lines.append(f"  Reason: {data['final']['reason']}")
    return "\n".join(lines)


def _format_card(card: dict[str, Any]) -> str:
    reasoning = card.get("reasoning", {})
    workflow = card.get("workflow", {})
    strategy = card.get("strategy", {})
    return (
        f"Reasoning: {reasoning.get('name')}\n  {reasoning.get('how')}\n\n"
        f"Workflow: {workflow.get('name')}\n  {workflow.get('how')}\n\n"
        f"Strategy: {strategy.get('name')}\n  {strategy.get('how')}"
    )


def _format_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "No arena runs."
    lines = ["Arena runs (newest first):"]
    for run in runs[:10]:
        lines.append(
            f"  {run['run_id']} — {run['status']} — {run['agents_n']} agents — "
            f"{run['task'][:80]}{'…' if len(run['task']) > 80 else ''}"
        )
    if len(runs) > 10:
        lines.append(f"  … and {len(runs) - 10} more")
    return "\n".join(lines)


from alpha.arena.store import ArenaStoreError

__all__ = [
    "ARENA_TOOL_NAME",
    "arena_tool",
]