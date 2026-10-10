"""Agent middleware adapter connecting the Alpha Mod Kernel to LangGraph tool & model execution."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from alpha.mods.kernel import ModKernel, get_mod_kernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome

logger = logging.getLogger(__name__)


async def run_mod_command(kernel: ModKernel, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Run a mod-registered command against ``kernel``, or report that none owns it.

    This is the path Claude Code's ``$.command.register`` needs: the handler
    runs immediately, with no model turn and no tokens. An unknown command
    returns ``None`` so a caller (the Gateway bridge, the CLI, a chat
    ``/command``) can fall through to the ordinary command registry rather than
    being told an error.
    """
    from alpha.mods.commands import MAX_OUTPUT_CHARS, run_command

    resolved = kernel.commands.resolve(command)
    if resolved is None:
        return None
    command_def, handler = resolved
    try:
        output = await run_command(handler, dict(payload or {}))
    except Exception as exc:
        logger.error("Mod command '/%s' raised: %s", command_def.name, exc, exc_info=True)
        return {
            "command": command_def.name,
            "mod_name": command_def.mod_name,
            "status": "error",
            "output": f"Mod command '/{command_def.name}' failed: {exc}",
        }
    text = str(output)[:MAX_OUTPUT_CHARS]
    return {
        "command": command_def.name,
        "mod_name": command_def.mod_name,
        "status": "success",
        "output": text,
        "requires_approval": command_def.requires_approval,
    }


def list_mod_commands(kernel: ModKernel) -> list[dict[str, Any]]:
    """Project every mod-registered command on ``kernel`` for an operator surface."""
    return [c.to_dict() for c in kernel.commands.list_commands()]


class ModKernelMiddleware(AgentMiddleware[AgentState]):
    """Bridges the Alpha Mod Kernel into the LangGraph AgentMiddleware pipeline.

    Intercepts tool calls (``tool.requested``), model calls (``model.requested``
    *before* the provider sees them, and ``turn.complete`` after), enforcing
    deterministic mod pipeline outcomes (DENY, DEFER, REWRITE, ANSWER).
    """

    def __init__(self, kernel: ModKernel | None = None):
        self._kernel = kernel or get_mod_kernel()

    def release_policy_parameters(self) -> dict[str, object]:
        return {
            "source": "alpha_mod_kernel",
            "mods_count": len(self._kernel.list_mods()),
            "fail_closed": True,
        }

    async def run_mod_command(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Run a mod-registered command, or report that none owns it.

        Delegates to the module-level :func:`run_mod_command`, which is the
        shared implementation the Gateway bridge and CLI also call.
        """
        return await run_mod_command(self._kernel, command, payload)

    def list_mod_commands(self) -> list[dict[str, Any]]:
        return list_mod_commands(self._kernel)

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

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
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

        res = await self._kernel.dispatch(event)

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

        new_args = res.event.payload.get("tool_args")
        if isinstance(new_args, dict):
            request.args = new_args

        tool_result = await handler(request)

        # Dispatch tool.completed event to kernel
        try:
            content_str = getattr(tool_result, "content", str(tool_result))
            status_str = getattr(tool_result, "status", "success")
            completed_ev = AlphaEvent(
                name="tool.completed",
                payload={
                    "tool_name": tool_name,
                    "tool_args": dict(request.args) if hasattr(request, "args") and isinstance(request.args, dict) else {},
                    "content": content_str,
                    "status": status_str,
                    "tool_call_id": tool_call_id,
                },
                correlation=correlation,
                source="runtime:tool_node",
            )
            await self._kernel.dispatch(completed_ev)
        except Exception as dispatch_err:
            logger.debug("Failed dispatching tool.completed event: %s", dispatch_err)

        return tool_result

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        correlation = CorrelationContext.create(
            run_id=str(getattr(request, "run_id", None) or uuid.uuid4().hex),
        )

        # The pre-model dispatch. Claude Code's mods can rewrite a prompt
        # *before* it reaches the model, which is the only point at which a
        # rewrite is still a rewrite of intent rather than a repair of output.
        # The kernel owns this decision; this adapter only applies the verdict.
        messages = list(getattr(request, "messages", None) or [])
        pre_event = AlphaEvent(
            name="model.requested",
            payload={
                "messages": messages,
                "model": getattr(request, "model", None),
                "system_prompt": getattr(request, "system_prompt", None),
                "tool_count": len(getattr(request, "tools", None) or []),
            },
            correlation=correlation,
            source="runtime:model",
        )

        try:
            pre_result = await self._kernel.dispatch(pre_event)
        except Exception as exc:
            logger.warning("Mod kernel could not evaluate model.requested: %s", exc)
            pre_result = None

        if pre_result is not None and pre_result.outcome == EventOutcome.DENY:
            logger.warning("Model call refused by mod kernel: %s", pre_result.reason)
            raise RuntimeError(f"Model call refused by mod policy: {pre_result.reason}")

        rewritten_messages = None
        if pre_result is not None and pre_result.outcome == EventOutcome.REWRITE:
            candidate = pre_result.event.payload.get("messages") if pre_result.event else None
            if isinstance(candidate, list) and candidate:
                rewritten_messages = candidate
                logger.info("Model request rewritten by mod kernel: %s", pre_result.reason)

        if rewritten_messages is not None:
            request.messages = rewritten_messages

        response = await handler(request)

        # After model responds, verify completion claim
        messages = getattr(response, "messages", [])
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage):
                content = str(getattr(last_msg, "content", ""))
                event = AlphaEvent(
                    name="turn.complete",
                    payload={"message": content, "messages": messages},
                    correlation=correlation,
                    source="runtime:model",
                )
                res = await self._kernel.dispatch(event)
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

        return response
