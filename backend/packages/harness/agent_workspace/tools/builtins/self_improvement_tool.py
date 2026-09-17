"""Bounded self-improvement delegation loop (DeepSeek-Harness-style tool-ralph).

``tool-ralph`` in DeepSeek Harness loops a workflow until a completion promise
holds, bounded by ``maxRounds``. This tool is the DeerFlow equivalent for
delegated work: it runs a task through a subagent, checks the deterministic
acceptance verdict for the completion promise, and retries — feeding each
round's shortfall back in — until the promise holds or ``max_rounds`` is
exhausted.

Each round reuses the exact delegation machinery of :func:`task_tool`
(:class:`SubagentExecutor`, parent tool-group inheritance, skill-allowlist
merge, identity propagation, receipt/acceptance evaluation), so rounds behave
like ordinary delegations. The loop itself adds only: the retry prompt, the
completion rule, and honest exhaustion framing (mirroring the goal
stand-down language — an unmet promise is reported with gaps, never
papered over).

Cost warning (also in the model-facing docstring): every round is a full
subagent execution. Keep ``max_rounds`` small; the default is 3, the hard
cap is 8. Subagents never receive this tool (``disallowed_tools`` default),
so rounds cannot nest.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command

from agent_workspace.agents.middlewares.receipt_verification import verify_receipt_citations
from agent_workspace.authz.principal import normalize_authz_attributes
from agent_workspace.config import get_app_config
from agent_workspace.extensions import resolve_run_extensions
from agent_workspace.runtime.user_context import resolve_runtime_user_id
from agent_workspace.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from agent_workspace.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from agent_workspace.subagents.acceptance_checks import check_acceptance_criteria, render_acceptance_section
from agent_workspace.subagents.config import resolve_subagent_model_name
from agent_workspace.subagents.executor import (
    SubagentResult,
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from agent_workspace.subagents.status_contract import (
    SubagentStatusValue,
    SubagentStopReasonValue,
    format_subagent_result_message,
    make_subagent_additional_kwargs,
)
from agent_workspace.tools.builtins.task_tool import _get_runtime_app_config
from agent_workspace.tools.types import Runtime
from agent_workspace.trace_context import AGENT_WORKSPACE_TRACE_METADATA_KEY, resolve_trace_id
from agent_workspace.utils.assembly_io import run_assembly
from agent_workspace.utils.custom_events import aemit_custom_event

logger = logging.getLogger(__name__)

RALPH_DEFAULT_MAX_ROUNDS = 3
RALPH_HARD_MAX_ROUNDS = 8
_RALPH_POLL_INTERVAL_SECONDS = 5.0


def _round_prompt(task: str, promise: str, round_no: int, previous_shortfall: str | None) -> str:
    """Build the round prompt. Later rounds carry only the shortfall, never a full retry."""
    base = f"{task}\n\nCompletion promise (the run is done only when this holds): {promise}\nAddress the promise point by point in your final report."
    if round_no > 1 and previous_shortfall:
        return f"{base}\n\nPrevious attempt fell short:\n{previous_shortfall}\nFix ONLY what is missing; reuse unaffected outputs. Do not repeat unchanged work."
    return base


def _ralph_complete(status: SubagentStatus, verdict: dict | None) -> tuple[bool, str]:
    """Completion rule for a finished round.

    Returns ``(complete, reason)``. A round completes the loop when the run
    finished AND nothing checkable is proven unmet: an all-hold verdict, an
    unchecked (non-canonical promise) verdict, or a missing verdict (checker
    outage — fail open like ``task_tool``, which flows back unchecked).
    Checked-and-failing leaves always retry while rounds remain.
    """
    if status is not SubagentStatus.COMPLETED:
        return False, f"round status is {status.value}"
    if verdict is None:
        return True, "completed (acceptance unchecked)"
    leaves = verdict.get("leaves") or []
    failing = [leaf.get("criterion", "?") for leaf in leaves if leaf.get("checked") and not leaf.get("holds")]
    if failing:
        return False, f"unmet criteria: {'; '.join(failing)}"
    if verdict.get("all_hold"):
        return True, "completion promise holds"
    return True, "completed (promise not deterministically checkable)"


def _shortfall_summary(result: SubagentResult | None, verdict: dict | None, reason: str) -> str:
    """Compact shortfall context for the next round (bounded, no full-report echo)."""
    parts = [reason]
    if verdict is not None:
        try:
            parts.append(render_acceptance_section(verdict).strip())
        except Exception:
            logger.debug("Failed to render acceptance section for ralph shortfall", exc_info=True)
    if result is not None and result.result:
        parts.append((result.result or "")[:2000])
    return "\n".join(part for part in parts if part)[:3000]


def _ralph_result_command(
    *,
    tool_call_id: str,
    status: SubagentStatusValue,
    result: str | None = None,
    error: str | None = None,
    stop_reason: SubagentStopReasonValue | None = None,
    model_name: str | None = None,
    usage: dict[str, int] | None = None,
    tool_receipts: list[dict] | None = None,
    receipt_verdict: dict | None = None,
    acceptance_verdict: dict | None = None,
) -> Command:
    """Mirror ``task_tool._task_result_command`` with the ralph tool identity."""
    content, metadata_error = format_subagent_result_message(status, result=result, error=error, stop_reason=stop_reason)
    if acceptance_verdict is not None:
        content = f"{content}\n\n{render_acceptance_section(acceptance_verdict)}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name="ralph_loop",
                    additional_kwargs=make_subagent_additional_kwargs(
                        status,
                        result=result,
                        error=metadata_error,
                        stop_reason=stop_reason,
                        model_name=model_name,
                        token_usage=usage,
                        tool_receipts=tool_receipts,
                        receipt_verdict=receipt_verdict,
                        acceptance_verdict=acceptance_verdict,
                    ),
                )
            ]
        }
    )


async def _await_round_terminal(execution_id: str, max_polls: int) -> Any | None:
    """Poll the background registry until the round is terminal. None = vanished."""
    polls = 0
    while polls < max_polls:
        result = get_background_task_result(execution_id)
        if result is None:
            return None
        if result.status.is_terminal:
            return result
        polls += 1
        await asyncio.sleep(_RALPH_POLL_INTERVAL_SECONDS)
    return get_background_task_result(execution_id)


@tool("ralph_loop", parse_docstring=True)
async def ralph_loop_tool(
    runtime: Runtime,
    task: str,
    completion_promise: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    *,
    max_rounds: int = RALPH_DEFAULT_MAX_ROUNDS,
    subagent_type: str = "general-purpose",
    description: str = "",
) -> str | Command:
    """Run a task through bounded self-improvement rounds until a promise holds.

    Use when an outcome is objectively checkable and a first attempt often
    falls short: each round delegates to a subagent, the deterministic
    acceptance checklist is evaluated, and only proven shortfalls feed the
    next round. Prefer the ordinary `task` tool for open-ended work with no
    crisp done-condition.

    Costs to include in the decision: EVERY round is a full subagent
    execution (model calls + context). Default 3 rounds, hard cap 8 — a
    promise that needs more rounds is a task that needs replanning, not more
    retries. Rounds never nest: subagents do not receive this tool.

    Reading the result: `completed` means the promise holds (or held nothing
    checkable); `failed` after N rounds means the promise is still unmet —
    the last report and the unmet criteria are attached, so repair only what
    is missing instead of restarting.

    Args:
        task: The task description for the subagent. Be specific about what needs to be done.
        completion_promise: The done-condition, preferably in canonical
            acceptance form (`file:<path> exists|non-empty`,
            `file_written:<path>`, `tests_passed:<command>`) so it is checked
            deterministically. Non-canonical promises complete on a clean run.
        max_rounds: Maximum delegation rounds (1-8, default 3).
        subagent_type: The subagent type per round. Unknown types fail listing the available types.
        description: Optional short (3-5 word) description for logging/display.
    """
    from agent_workspace.tools import get_available_tools

    if not (task or "").strip():
        return _ralph_result_command(tool_call_id=tool_call_id, status="failed", error="ralph_loop requires a non-empty task.")
    if not (completion_promise or "").strip():
        return _ralph_result_command(tool_call_id=tool_call_id, status="failed", error="ralph_loop requires a non-empty completion_promise.")
    if not isinstance(max_rounds, int) or not 1 <= max_rounds <= RALPH_HARD_MAX_ROUNDS:
        return _ralph_result_command(
            tool_call_id=tool_call_id,
            status="failed",
            error=f"max_rounds must be an integer in 1..{RALPH_HARD_MAX_ROUNDS} (got {max_rounds!r}).",
        )

    runtime_app_config = _get_runtime_app_config(runtime)
    metadata: dict = runtime.config.get("metadata", {}) if runtime is not None and getattr(runtime, "config", None) else {}
    allowed_subagents = metadata.get("allowed_subagents")
    if allowed_subagents is None:
        available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()
    else:
        available_subagent_names = get_available_subagent_names(app_config=runtime_app_config, allowed_subagents=allowed_subagents) if runtime_app_config is not None else get_available_subagent_names(allowed_subagents=allowed_subagents)

    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return _ralph_result_command(tool_call_id=tool_call_id, status="failed", error=LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE)

    config = get_subagent_config(subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(subagent_type)
    if config is None or subagent_type not in available_subagent_names:
        available = ", ".join(available_subagent_names) if available_subagent_names else ("none permitted by caller policy" if allowed_subagents is not None else "none")
        return _ralph_result_command(tool_call_id=tool_call_id, status="failed", error=f"Unknown subagent type '{subagent_type}'. Available: {available}")

    # Parent-context plumbing mirrors task_tool so rounds behave like ordinary
    # delegations (sandbox, uploads, identity, skill allowlist, model chain).
    sandbox_state = None
    thread_data = None
    uploaded_files = None
    upload_state_available = False
    thread_id = None
    parent_model = None
    trace_id = None
    user_id = None
    agent_workspace_trace_id = None
    parent_context: dict = {}
    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        parent_uploaded_files = runtime.state.get("uploaded_files")
        if isinstance(parent_uploaded_files, list) and all(
            isinstance(entry, dict) and isinstance(entry.get("filename"), str) and bool(entry["filename"]) and Path(entry["filename"]).name == entry["filename"] for entry in parent_uploaded_files
        ):
            uploaded_files = parent_uploaded_files
            upload_state_available = True
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")
        parent_model = metadata.get("model_name")
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]
    user_id = resolve_runtime_user_id(runtime)
    if runtime is not None and isinstance(runtime.context, dict):
        parent_context = runtime.context
        user_role = parent_context.get("user_role")
        oauth_provider = parent_context.get("oauth_provider")
        oauth_id = parent_context.get("oauth_id")
        run_id = parent_context.get("run_id")
        channel_user_id = parent_context.get("channel_user_id")
        is_internal = parent_context.get("is_internal") is True
        authz_attributes = normalize_authz_attributes(parent_context.get("authz_attributes"))
        run_extensions = resolve_run_extensions(parent_context)
        agent_workspace_trace_id = resolve_trace_id(parent_context.get(AGENT_WORKSPACE_TRACE_METADATA_KEY))
    else:
        user_role = oauth_provider = oauth_id = run_id = channel_user_id = None
        is_internal = False
        authz_attributes = normalize_authz_attributes(None)
        run_extensions = resolve_run_extensions({})
        agent_workspace_trace_id = resolve_trace_id(None)

    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        # Inherit the parent's skill allowlist like task_tool so rounds
        # respect the same skill visibility (operator text stays downgraded:
        # the executor appends skills as session items, never system text).
        config = replace(config, skills=list(parent_available_skills))
    parent_tool_groups = metadata.get("tool_groups")
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
        "include_upload_tool": upload_state_available,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = await run_assembly(get_available_tools, **available_tools_kwargs)

    writer = get_stream_writer()
    await aemit_custom_event(
        {"type": "ralph_started", "task_id": tool_call_id, "description": description or task[:120], "max_rounds": max_rounds, "model_name": effective_model},
        writer=writer,
    )

    max_polls = (config.timeout_seconds + 60) // 5
    previous_shortfall: str | None = None
    last_result: SubagentResult | None = None
    last_verdict: dict | None = None
    last_reason = ""
    rounds_used = 0
    for round_no in range(1, max_rounds + 1):
        rounds_used = round_no
        round_prompt = _round_prompt(task, completion_promise, round_no, previous_shortfall)
        executor = SubagentExecutor(
            config=config,
            tools=tools,
            parent_model=parent_model,
            sandbox_state=sandbox_state,
            thread_data=thread_data,
            uploaded_files=uploaded_files,
            thread_id=thread_id,
            trace_id=f"{trace_id}-r{round_no}" if trace_id else None,
            user_id=user_id,
            user_role=user_role,
            oauth_provider=oauth_provider,
            oauth_id=oauth_id,
            run_id=run_id,
            channel_user_id=channel_user_id,
            is_internal=is_internal,
            authz_attributes=authz_attributes,
            agent_workspace_trace_id=agent_workspace_trace_id,
            acceptance_criteria=[completion_promise],
            app_config=resolved_app_config,
            extensions=run_extensions,
        )
        round_task_id = f"{tool_call_id}-ralph-{round_no}"
        execution_id = executor.execute_async(round_prompt, task_id=round_task_id)
        logger.info(f"[trace={trace_id}] Ralph {tool_call_id} round {round_no}/{max_rounds} started (execution_id={execution_id})")
        try:
            result = await _await_round_terminal(execution_id, max_polls)
        except asyncio.CancelledError:
            request_cancel_background_task(execution_id)
            raise
        if result is None:
            last_result, last_verdict, last_reason = None, None, "round vanished from background tasks"
            await aemit_custom_event({"type": "ralph_round_end", "task_id": tool_call_id, "round": round_no, "max_rounds": max_rounds, "status": "vanished"}, writer=writer)
            cleanup_background_task(execution_id)
            continue
        verdict = None
        try:
            verdict = await asyncio.to_thread(
                check_acceptance_criteria,
                [completion_promise],
                runtime=runtime,
                thread_data=thread_data,
                bash_executions=getattr(result, "bash_executions", None),
            )
        except Exception:
            logger.warning(f"[trace={trace_id}] Ralph {tool_call_id} round {round_no} acceptance check failed; round flows back unchecked", exc_info=True)
        complete, reason = _ralph_complete(result.status, verdict)
        await aemit_custom_event(
            {"type": "ralph_round_end", "task_id": tool_call_id, "round": round_no, "max_rounds": max_rounds, "status": result.status.value, "complete": complete, "reason": reason},
            writer=writer,
        )
        logger.info(f"[trace={trace_id}] Ralph {tool_call_id} round {round_no}/{max_rounds}: {result.status.value} — {reason}")
        cleanup_background_task(execution_id)
        last_result, last_verdict, last_reason = result, verdict, reason
        if complete:
            await aemit_custom_event({"type": "ralph_completed", "task_id": tool_call_id, "rounds_used": round_no, "reason": reason}, writer=writer)
            receipts = getattr(result, "tool_receipts", None)
            receipt_verdict = verify_receipt_citations(result.result or "", receipts) if receipts is not None else None
            return _ralph_result_command(
                tool_call_id=tool_call_id,
                status="completed",
                result=f"Completion promise holds after {round_no} round(s): {reason}\n\n{(result.result or '').strip()}",
                stop_reason=result.stop_reason,
                model_name=effective_model,
                tool_receipts=receipts,
                receipt_verdict=receipt_verdict,
                acceptance_verdict=verdict,
            )
        previous_shortfall = _shortfall_summary(result, verdict, reason)

    await aemit_custom_event({"type": "ralph_exhausted", "task_id": tool_call_id, "rounds_used": rounds_used, "reason": last_reason}, writer=writer)
    last_report = (last_result.result or "").strip() if last_result is not None else ""
    return _ralph_result_command(
        tool_call_id=tool_call_id,
        status="failed",
        error=f"Completion promise still unmet after {rounds_used} round(s): {last_reason}. Last report retained below — repair only what is missing instead of restarting.\n\n{last_report}",
        acceptance_verdict=last_verdict,
    )


# Enterprise naming aliases
self_improvement_loop_tool = ralph_loop_tool

__all__ = [
    "RALPH_DEFAULT_MAX_ROUNDS",
    "RALPH_HARD_MAX_ROUNDS",
    "ralph_loop_tool",
    "self_improvement_loop_tool",
]
