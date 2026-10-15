"""Refuse every tool call while the fleet is stopped.

## Why a middleware and not a tool filter

The fleet stop has to hold at the seam where a tool call becomes an effect.
Filtering the tool *catalog* is not that seam: it changes what the model is told
is available, which is a prompt-level courtesy the model can route around, and
it would not stop a call the model already committed to in the same response.

``wrap_tool_call`` is the real seam. It runs after the model has emitted the
call and before the tool executes, so it catches every call regardless of how
it was chosen, and it returns a ``ToolMessage`` rather than raising — an
exception here would surface to the model as an internal error instead of a
refusal, and the model would keep retrying the same call.

## Why it fails closed and returns an error ToolMessage

A stop mechanism that fails open is not a stop mechanism, so an unreadable stop
state refuses the call. The refusal is delivered as an ordinary tool result
with ``status="error"`` so the agent's own loop machinery handles it: the
conversation stays well-formed, the model is told what happened, and the run
ends with a real reason instead of a hang.

## Where it sits

Inside ``ToolReceiptMiddleware`` (it is a short-circuiter, and an unrecorded
refusal would silently gap the ledger) and outside the other tool gates, so a
stopped fleet never spends a guardrail policy call or a sandbox slot on work it
has already refused. Registered on both the lead and the subagent runtime
chains: a delegated child that inherited no stop would make delegation a way
out of the stop.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

#: The marker an operator or a test can match on. Deliberately stable: the
#: surrounding prose is not an API.
FLEET_STOPPED_CODE = "FLEET_STOPPED"

_FLEET_STOP_MESSAGE = (
    "[FLEET_STOPPED] The fleet is under an emergency stop, so no tool can execute right now. Reason: {reason}. Do not retry this call or attempt an equivalent one; report that the fleet is stopped and wait for an operator to clear it."
)


def _refusal_tool_message(request: ToolCallRequest, reason: str) -> ToolMessage:
    tool_name = str(request.tool_call.get("name", "unknown_tool"))
    tool_call_id = str(request.tool_call.get("id", "missing_id"))
    return ToolMessage(
        content=_FLEET_STOP_MESSAGE.format(reason=reason or "unspecified"),
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
    )


def _fleet_stop_verdict() -> tuple[bool, str]:
    """One fleet-stop read that never raises; every failure fails closed.

    Synchronous by design: the sync tool path calls it directly, and the async
    path reaches it through ``asyncio.to_thread`` so the sentinel/control reads
    never run on the event loop. Inline there, the disk read stalls every other
    run in the process, and the strict blocking-IO gate's raise is swallowed by
    the fail-closed handler into an "engaged stop" that refuses every tool call.
    """
    from alpha.runtime.fleet_stop import fleet_stop_reason, is_fleet_stopped

    try:
        stopped = is_fleet_stopped()
        return stopped, (fleet_stop_reason() if stopped else "")
    except Exception:  # noqa: BLE001 - fail closed, never into the tool
        logger.exception("Fleet stop predicate raised; refusing tool call (fail-closed)")
        return True, "fleet stop state unreadable"


class FleetStopMiddleware(AgentMiddleware[AgentState]):
    """Refuse every tool call while any stop mechanism is engaged.

    Consults ``alpha.runtime.fleet_stop``, which is the single definition of
    "is the fleet stopped". It is not re-derived here: a second implementation
    would drift, and the whole finding this closes is that three mechanisms
    existed while no execution path asked.
    """

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        from alpha.runtime.fleet_stop import fleet_stop_reason, is_fleet_stopped

        try:
            stopped = is_fleet_stopped()
            reason = fleet_stop_reason() if stopped else ""
        except Exception:  # noqa: BLE001 - fail closed, never into the tool
            logger.exception("Fleet stop predicate raised; refusing tool call (fail-closed)")
            stopped, reason = True, "fleet stop state unreadable"

        if not stopped:
            return handler(request)

        tool_name = str(request.tool_call.get("name", "unknown_tool"))
        logger.warning("Fleet stopped; refusing tool call '%s': %s", tool_name, reason)
        return _refusal_tool_message(request, reason)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        from alpha.runtime.fleet_stop import fleet_stop_reason, is_fleet_stopped

        try:
            stopped = is_fleet_stopped()
            reason = fleet_stop_reason() if stopped else ""
        except Exception:  # noqa: BLE001 - fail closed, never into the tool
            logger.exception("Fleet stop predicate raised; refusing tool call (fail-closed)")
            stopped, reason = True, "fleet stop state unreadable"

        if not stopped:
            # LangGraph control-flow signals must pass through untouched: an
            # interrupt raised by an inner middleware is not a tool call.
            try:
                return await handler(request)
            except GraphBubbleUp:
                raise

        tool_name = str(request.tool_call.get("name", "unknown_tool"))
        logger.warning("Fleet stopped; refusing tool call '%s': %s", tool_name, reason)
        return _refusal_tool_message(request, reason)
