"""Built-in tools for direct agent-to-agent messaging and roster observation."""

from __future__ import annotations

from langchain.tools import tool

from alpha.subagents.messaging import DeliveryMode, get_agent_roster
from alpha.tools.types import Runtime


def _resolve_thread_id(runtime: Runtime | None) -> str:
    if runtime and hasattr(runtime, "context") and isinstance(runtime.context, dict):
        tid = runtime.context.get("thread_id")
        if tid:
            return str(tid)
    return "default_thread"


@tool("agent_observe", parse_docstring=True)
def agent_observe_tool(
    runtime: Runtime | None = None,
) -> str:
    """Inspect the active family roster to discover running peer agents and their statuses.

    Returns the list of registered agents, their assigned roles, and whether they
    are currently idle, busy, or completed.
    """
    thread_id = _resolve_thread_id(runtime)
    roster = get_agent_roster(thread_id)
    agents = roster.list_agents()

    if not agents:
        return "No other agents currently registered in this thread."

    out = [f"=== Active Agent Family Roster (Thread: {thread_id}) ==="]
    for a in agents:
        out.append(f"- **{a.name}** ({a.role}) — Status: `{a.status}` [ID: {a.agent_id}]")
    return "\n".join(out)


@tool("agent_message", parse_docstring=True)
def agent_message_tool(
    receiver_name: str,
    content: str,
    sender_name: str = "caller",
    mode: DeliveryMode = "auto",
    runtime: Runtime | None = None,
) -> str:
    """Send a direct message to another active agent in the family roster.

    Enables parallel subagents (e.g. coder, tester, reviewer) to coordinate directly
    without routing through the user or waiting for full turns to finish.

    Args:
        receiver_name: Name of target agent (or 'all' to broadcast to all peers).
        content: The message text or instruction to deliver.
        sender_name: Name of the sending agent. Defaults to 'caller'.
        mode: Delivery mode ('auto', 'steer', or 'follow_up'). Defaults to 'auto'.
            - 'auto': steer busy target, deliver immediately if idle.
            - 'steer': inject message directly into active target execution.
            - 'follow_up': queue message until target's current step finishes.
    """
    thread_id = _resolve_thread_id(runtime)
    roster = get_agent_roster(thread_id)

    res = roster.send_message(
        sender_name=sender_name,
        receiver_name=receiver_name,
        content=content,
        mode=mode,
    )

    if res.get("status") == "error":
        return f"Error: {res.get('error')}. Available: {res.get('available_agents')}"

    receipts = res.get("receipts", [])
    receipt_str = ", ".join(f"{r['receiver']}: {r['delivery_status']}" for r in receipts)
    return f"Message sent successfully. Receipts: [{receipt_str}]"
