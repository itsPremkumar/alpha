"""Middleware to enforce subagent tool-call limits."""

import logging
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from alpha.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
from alpha.config.subagents_config import (
    DEFAULT_MAX_TOTAL_SUBAGENTS_PER_RUN,
    MAX_CONCURRENT_SUBAGENT_CALLS,
    MAX_TOTAL_SUBAGENTS_PER_RUN,
    MIN_CONCURRENT_SUBAGENT_CALLS,
    MIN_TOTAL_SUBAGENTS_PER_RUN,
    clamp_subagent_concurrency,
    clamp_total_subagents_per_run,
)
from alpha.subagents.executor import MAX_CONCURRENT_SUBAGENTS

logger = logging.getLogger(__name__)

# Valid range for max_concurrent_subagents
MIN_SUBAGENT_LIMIT = MIN_CONCURRENT_SUBAGENT_CALLS
MAX_SUBAGENT_LIMIT = MAX_CONCURRENT_SUBAGENT_CALLS
DEFAULT_MAX_TOTAL_SUBAGENTS = DEFAULT_MAX_TOTAL_SUBAGENTS_PER_RUN
MIN_SUBAGENT_TOTAL_LIMIT = MIN_TOTAL_SUBAGENTS_PER_RUN
MAX_SUBAGENT_TOTAL_LIMIT = MAX_TOTAL_SUBAGENTS_PER_RUN

_TOTAL_LIMIT_STOP_MSG = (
    "[SUBAGENT LIMIT REACHED] The subagent delegation limit for this run has been reached. "
    "Continue using the subagent results already collected, execute remaining simple work "
    "directly, or summarize the remaining work instead of launching more subagents."
)

_APEX_WITHHELD_MSG = "[APEX DELEGATION LIMIT] This session's APEX policy withholds delegated subagent calls entirely. Execute the remaining work directly or summarize it instead of launching subagents."


def _clamp_subagent_limit(value: int) -> int:
    """Clamp subagent limit to the hard safety range [1, 64]."""
    return clamp_subagent_concurrency(value)


def _clamp_total_subagent_limit(value: int) -> int:
    """Clamp total subagent limit to a bounded positive range."""
    return clamp_total_subagents_per_run(value)


def _append_text(content: Any, text: str) -> Any:
    if content is None:
        return text
    if isinstance(content, str):
        if content:
            return f"{content}\n\n{text}"
        return text
    if isinstance(content, list):
        return [*content, {"type": "text", "text": f"\n\n{text}"}]
    return f"{content}\n\n{text}"


def _delegation_id(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    entry_id = entry.get("id")
    return str(entry_id) if entry_id else None


def _delegation_run_id(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    run_id = entry.get("run_id")
    return str(run_id) if run_id else None


def _runtime_run_id(runtime: Runtime | None) -> str | None:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return None
    run_id = context.get("run_id")
    return str(run_id) if run_id else None


def _apex_task_call_limit(runtime: Runtime | None, *, fallback: int) -> int | None:
    """Resolve a trusted APEX session's per-turn delegation ceiling.

    ``None`` means this is an ordinary run. An APEX marker is Gateway-owned;
    once present, unreadable or mismatched policy fails closed with zero child
    calls. APEX counts delegated children against its active-agent ceiling;
    the task-call cap also remains subject to engine capacity.
    """
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return None
    try:
        from alpha.apex.contract import APEX_RUNTIME_SESSION_KEY
    except Exception:  # noqa: BLE001 - detect a broken APEX import without opening delegation
        return 0 if context.get("__alpha_apex_session_id") else None
    session_id = context.get(APEX_RUNTIME_SESSION_KEY)
    if not session_id:
        return None
    try:
        from alpha.apex.contract import contract_from_snapshot
        from alpha.apex.mode import DEFAULT_SCOPE, get_apex_mode_store
        from alpha.apex.store import ApexSessionState, get_apex_store

        store = get_apex_store()
        modes = get_apex_mode_store()
        if store.is_degraded or modes.is_degraded:
            return 0
        session = store.get(str(session_id))
        if session is None or session.owner != str(context.get("user_id") or "") or session.state is not ApexSessionState.ACTIVE or session.contract_snapshot is None:
            return 0
        scope = session.thread_id or session.owner or DEFAULT_SCOPE
        mode = modes.for_scope(scope)
        if not mode.enabled or mode.profile != session.profile:
            return 0
        contract = contract_from_snapshot(session.contract_snapshot, expected_digest=session.contract_digest)
        if contract.profile.value != session.profile:
            return 0
        # The ordinary task tool creates depth-1 children and deliberately
        # disables nested delegation in those children. APEX may narrow that
        # capability to depth zero, which must withhold the first child call;
        # larger values do not enable recursion that this executor does not
        # expose.
        if contract.budget.max_delegation_depth is not None and int(contract.budget.max_delegation_depth) < 1:
            return 0
        limits = [fallback]
        if contract.budget.max_active_agents is not None:
            limits.append(max(0, int(contract.budget.max_active_agents)))
        if contract.budget.max_parallel_tasks is not None:
            limits.append(max(0, int(contract.budget.max_parallel_tasks)))
        return min(limits)
    except Exception:  # noqa: BLE001 - a marked APEX run must fail closed
        logger.exception("Could not resolve APEX subagent limits; delegation is withheld")
        return 0


def _count_prior_delegations(delegations: object, *, run_id: str | None) -> int:
    if not isinstance(delegations, list):
        return 0
    ids = set()
    for entry in delegations:
        if run_id is not None and _delegation_run_id(entry) != run_id:
            continue
        delegation_id = _delegation_id(entry)
        if delegation_id is not None:
            ids.add(delegation_id)
    return len(ids)


class SubagentLimitMiddleware(AgentMiddleware[AgentState]):
    """Truncates excess 'task' tool calls from a single model response/run.

    When an LLM generates more than max_concurrent parallel task tool calls
    in one response, this middleware keeps only the first max_concurrent and
    discards the rest. It also enforces a total per-run cap using entries in
    the durable delegation ledger tagged with the current run_id, so repeated
    planning checkpoints in one run cannot keep launching more legal-sized
    batches indefinitely. This is more reliable than prompt-based limits.

    An APEX-marked run (Gateway-stamped session id in runtime context) is
    additionally capped by the session's frozen ``max_parallel_tasks`` /
    ``max_active_agents`` budget. Three distinct withholdings, each with its own
    visible message, are distinguished so the model is never told a cap bound
    it when it did not: the per-run total is exhausted (all calls dropped),
    the session's policy withholds delegation entirely (all calls dropped),
    or the session merely narrows this turn's parallelism. In the first two
    cases ``stop_reason="subagent_limit_capped"`` is written into runtime
    context so the run worker reports a capped completion rather than a clean
    success. The third case is an ordinary narrower turn and is not a cap the
    run itself hit.

    Args:
        max_concurrent: Maximum number of concurrent subagent calls allowed.
            Defaults to MAX_CONCURRENT_SUBAGENTS (3). Callers pass the value
            already clamped to the configured process execution capacity.
        max_total: Maximum number of subagent calls allowed across the run.
            Defaults to 6. Clamped to [1, 50].
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SUBAGENTS, max_total: int = DEFAULT_MAX_TOTAL_SUBAGENTS):
        super().__init__()
        self.max_concurrent = _clamp_subagent_limit(max_concurrent)
        self.max_total = _clamp_total_subagent_limit(max_total)

    def release_policy_parameters(self) -> dict[str, object]:
        return {
            "max_concurrent": self.max_concurrent,
            "max_total": self.max_total,
        }

    def _truncate_task_calls(self, state: AgentState, runtime: Runtime | None = None) -> dict | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None

        # Count task tool calls
        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        if not task_indices:
            return None

        run_id = _runtime_run_id(runtime)
        if run_id is None:
            logger.warning("Subagent limit middleware received no run_id; counting all thread delegations as prior usage. Pass run_id in runtime context to enforce the total cap per run.")
        prior_delegation_count = _count_prior_delegations(state.get("delegations"), run_id=run_id)
        remaining_total = max(0, self.max_total - prior_delegation_count)
        apex_limit = _apex_task_call_limit(runtime, fallback=self.max_concurrent)
        concurrent_limit = self.max_concurrent if apex_limit is None else min(self.max_concurrent, apex_limit)
        allowed_task_calls = min(concurrent_limit, remaining_total)

        if len(task_indices) <= allowed_task_calls:
            return None

        # Build set of indices to drop (excess task calls beyond the limit)
        indices_to_drop = set(task_indices[allowed_task_calls:])
        truncated_tool_calls = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]
        dropped_count = len(indices_to_drop)
        logger.warning(
            "Truncated %s excess task tool call(s) from model response (concurrent limit: %s; total limit: %s; prior delegations: %s)",
            dropped_count,
            self.max_concurrent,
            self.max_total,
            prior_delegation_count,
        )

        # Stamp stop_reason whenever delegation was withheld, not only when the
        # per-run total is exhausted, so the worker surfaces this capped
        # completion alongside loop_capped / token_capped / safety_capped
        # (#4176). Without this, an APEX-refused run — every task call withheld
        # by policy — ends as an ordinary clean success.
        apex_withheld_all = apex_limit is not None and apex_limit <= 0
        if (remaining_total == 0 or apex_withheld_all) and isinstance(getattr(runtime, "context", None), dict):
            runtime.context["stop_reason"] = "subagent_limit_capped"

        # Replace the AIMessage with truncated tool_calls (same id triggers replacement)
        if remaining_total == 0:
            content = _append_text(last_msg.content, _TOTAL_LIMIT_STOP_MSG)
        elif apex_withheld_all:
            content = _append_text(last_msg.content, _APEX_WITHHELD_MSG)
        elif apex_limit is not None and apex_limit < self.max_concurrent:
            note = f"[APEX DELEGATION LIMIT] This session permits up to {apex_limit} parallel task call(s) in this turn. Continue with the work already assigned or plan another bounded batch after those results return."
            content = _append_text(last_msg.content, note)
        else:
            content = None
        updated_msg = clone_ai_message_with_tool_calls(last_msg, truncated_tool_calls, content=content)
        return {"messages": [updated_msg]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._truncate_task_calls(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._truncate_task_calls(state, runtime)
