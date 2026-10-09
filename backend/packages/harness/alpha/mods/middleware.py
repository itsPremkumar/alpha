"""Agent middleware adapter connecting the Alpha Mod Kernel to LangGraph tool & model execution."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from alpha.mods.kernel import ModKernel, get_mod_kernel, sync_dispatch
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome, EventResult

logger = logging.getLogger(__name__)


class _ToolCallPlan(NamedTuple):
    """The event both the sync and async tool hooks dispatch, plus its identifiers.

    Built once so the two hooks cannot construct different `tool.requested`
    payloads for the same request — the enforcement verdict is only comparable
    across the two paths if they are asking the kernel the same question.
    """

    event: AlphaEvent
    correlation: CorrelationContext
    tool_name: str
    tool_call_id: str


class ModKernelMiddleware(AgentMiddleware[AgentState]):
    """Bridges the Alpha Mod Kernel into the LangGraph AgentMiddleware pipeline.

    Intercepts tool calls (`tool.requested`) and model completions (`turn.complete`),
    enforcing deterministic mod pipeline outcomes (DENY, DEFER, REWRITE, ANSWER).
    """

    def __init__(self, kernel: ModKernel | None = None):
        self._kernel = kernel or get_mod_kernel()

    def release_policy_parameters(self) -> dict[str, object]:
        return {
            "source": "alpha_mod_kernel",
            "mods_count": len(self._kernel.list_mods()),
            "fail_closed": True,
        }

    def _extract_correlation(self, request: ToolCallRequest) -> CorrelationContext:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None) if runtime is not None else None
        corr_dict = context.get("correlation", {}) if isinstance(context, dict) else {}

        run_id = getattr(runtime, "run_id", None) or corr_dict.get("run_id") or uuid.uuid4().hex
        trace_id = getattr(runtime, "trace_id", None) or corr_dict.get("trace_id") or uuid.uuid4().hex

        return CorrelationContext(
            trace_id=str(trace_id),
            run_id=str(run_id),
            tool_call_id=getattr(request, "id", None) or uuid.uuid4().hex,
            task_id=corr_dict.get("task_id"),
            agent_id=corr_dict.get("agent_id"),
        )

    def _plan_tool_call(self, request: ToolCallRequest) -> _ToolCallPlan:
        """Build the `tool.requested` event both hooks dispatch, plus its identifiers."""
        tool_name = getattr(request, "name", "unknown")
        tool_args = getattr(request, "args", {})
        tool_call_id = getattr(request, "id", str(uuid.uuid4()))
        correlation = self._extract_correlation(request)

        event = AlphaEvent(
            name="tool.requested",
            payload={
                "tool_name": tool_name,
                "tool_args": dict(tool_args) if isinstance(tool_args, dict) else {},
            },
            correlation=correlation,
            source="runtime:tool_node",
        )
        return _ToolCallPlan(event=event, correlation=correlation, tool_name=tool_name, tool_call_id=tool_call_id)

    def _verdict_message(self, plan: _ToolCallPlan, res: EventResult) -> ToolMessage | None:
        """A terminal kernel verdict as a ToolMessage, or None to let the tool run.

        Shared by the sync and async hooks on purpose: a DENY must read
        identically on both paths, so the translation lives in one place rather
        than being maintained twice until the two disagree.
        """
        tool_name = plan.tool_name
        tool_call_id = plan.tool_call_id

        if res.outcome == EventOutcome.DENY:
            logger.warning("Tool execution of '%s' DENIED by mod kernel: %s", tool_name, res.reason)
            return ToolMessage(
                content=f"Error: Tool execution denied by security/safety policy: {res.reason}",
                tool_call_id=tool_call_id,
                status="error",
            )

        if res.outcome == EventOutcome.DEFER:
            logger.info("Tool execution of '%s' DEFERRED for approval: %s", tool_name, res.reason)
            hold = res.response_payload if isinstance(res.response_payload, dict) else {}
            hold_id = str(hold.get("hold_id") or "")
            risk_level = str(hold.get("risk_level") or "")
            hold_reference = "".join(part for part in (f" Hold ID: {hold_id}." if hold_id else "", f" Risk: {risk_level}." if risk_level else ""))
            return ToolMessage(
                content=f"Action held for operator approval: {res.reason}{hold_reference} The tool was not executed.",
                tool_call_id=tool_call_id,
                status="error",
            )

        if res.outcome == EventOutcome.ANSWER:
            logger.info("Tool execution of '%s' ANSWERED directly by mod: %s", tool_name, res.reason)
            content = res.response_payload.get("output", "") if isinstance(res.response_payload, dict) else str(res.response_payload or "")
            return ToolMessage(
                content=content or "Action answered directly by mod.",
                tool_call_id=tool_call_id,
                status="success",
            )

        if res.outcome == EventOutcome.RETRY:
            logger.error("Tool execution of '%s' requested RETRY without a retry scheduler: %s", tool_name, res.reason)
            return ToolMessage(
                content="Error: Policy requested a retry, but no retry scheduler is available; the action was not executed.",
                tool_call_id=tool_call_id,
                status="error",
            )

        if res.outcome == EventOutcome.ESCALATE:
            logger.warning("Tool execution of '%s' ESCALATED by mod: %s", tool_name, res.reason)
            return ToolMessage(
                content=f"Escalation required; tool '{tool_name}' was not executed: {res.reason}",
                tool_call_id=tool_call_id,
                status="error",
            )

        if res.outcome not in (EventOutcome.CONTINUE, EventOutcome.OBSERVE, EventOutcome.REWRITE):
            logger.error("Tool execution of '%s' refused for unsupported mod outcome %s", tool_name, res.outcome)
            return ToolMessage(
                content=f"Error: Tool execution refused because policy returned unsupported outcome {res.outcome}.",
                tool_call_id=tool_call_id,
                status="error",
            )
        return None

    @staticmethod
    def _apply_rewritten_args(request: ToolCallRequest, res: EventResult) -> None:
        """Adopt a REWRITE's tool_args before the real handler runs."""
        new_args = res.event.payload.get("tool_args")
        if isinstance(new_args, dict):
            request.args = new_args

    @staticmethod
    def _completed_event(plan: _ToolCallPlan, request: ToolCallRequest, tool_result: Any) -> AlphaEvent:
        return AlphaEvent(
            name="tool.completed",
            payload={
                "tool_name": plan.tool_name,
                "tool_args": dict(request.args) if hasattr(request, "args") and isinstance(request.args, dict) else {},
                "content": getattr(tool_result, "content", str(tool_result)),
                "status": getattr(tool_result, "status", "success"),
                "tool_call_id": plan.tool_call_id,
            },
            correlation=plan.correlation,
            source="runtime:tool_node",
        )

    async def _dispatch_completed(self, plan: _ToolCallPlan, request: ToolCallRequest, tool_result: Any) -> None:
        """Best-effort `tool.completed` dispatch; a journal failure never fails the tool."""
        try:
            await self._kernel.dispatch(self._completed_event(plan, request, tool_result))
        except Exception as dispatch_err:
            logger.debug("Failed dispatching tool.completed event: %s", dispatch_err)

    def _dispatch_completed_sync(self, plan: _ToolCallPlan, request: ToolCallRequest, tool_result: Any) -> None:
        """The same best-effort dispatch driven synchronously."""
        try:
            sync_dispatch(self._kernel, self._completed_event(plan, request, tool_result))
        except Exception as dispatch_err:
            logger.debug("Failed dispatching tool.completed event: %s", dispatch_err)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Synchronous twin of :meth:`awrap_tool_call`.

        ``alpha --json`` drives ``AlphaClient.stream()``, a *synchronous* graph
        invocation: with only the async hook overridden, ``create_agent`` leaves
        this node with no sync callable and the run dies before the first token.

        The enforcement is deliberately NOT skipped here. ``sync_dispatch`` runs
        the very same kernel — already the production sync entry point for five
        other call sites — so a DENY or DEFER blocks the tool on both paths. A
        passthrough would be worse than the crash it replaces: it would turn the
        sync path into a fail-open bypass of every verdict the async path
        applies.
        """
        plan = self._plan_tool_call(request)
        res = sync_dispatch(self._kernel, plan.event)
        verdict = self._verdict_message(plan, res)
        if verdict is not None:
            return verdict
        self._apply_rewritten_args(request, res)
        tool_result = handler(request)
        self._dispatch_completed_sync(plan, request, tool_result)
        return tool_result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        plan = self._plan_tool_call(request)
        res = await self._kernel.dispatch(plan.event)
        verdict = self._verdict_message(plan, res)
        if verdict is not None:
            return verdict
        self._apply_rewritten_args(request, res)
        tool_result = await handler(request)
        await self._dispatch_completed(plan, request, tool_result)
        return tool_result

    @staticmethod
    def _turn_event(response: ModelResponse) -> AlphaEvent | None:
        """The `turn.complete` event for an AI reply, or None when there is nothing to verify."""
        messages = getattr(response, "messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        return AlphaEvent(
            name="turn.complete",
            payload={"message": str(getattr(messages[-1], "content", "")), "messages": messages},
            correlation=CorrelationContext.create(),
            source="runtime:model",
        )

    @staticmethod
    def _apply_turn_verdict(response: ModelResponse, res: EventResult) -> None:
        """Append the verification gate's remediation note, when it asked for one."""
        rewrites = res.metadata.get("mod_rewrites", [])
        remediation = next(
            (item.get("remediation_prompt") for item in reversed(rewrites) if isinstance(item, dict) and item.get("remediation_prompt")),
            None,
        )
        if remediation or res.outcome == EventOutcome.REWRITE:
            remediation = remediation or res.reason
            logger.warning("Model turn completion rewritten by verification gate: %s", remediation)
            # Inject remediation note
            notice = HumanMessage(content=remediation)
            if hasattr(response, "messages") and isinstance(response.messages, list):
                response.messages.append(notice)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Synchronous twin of :meth:`awrap_model_call` — see :meth:`wrap_tool_call`.

        The verification gate runs here as well: skipping it on the sync path
        would let an unverified completion through exactly where the async path
        rewrites it.
        """
        response = handler(request)
        event = self._turn_event(response)
        if event is not None:
            self._apply_turn_verdict(response, sync_dispatch(self._kernel, event))
        return response

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        event = self._turn_event(response)
        if event is not None:
            self._apply_turn_verdict(response, await self._kernel.dispatch(event))
        return response
