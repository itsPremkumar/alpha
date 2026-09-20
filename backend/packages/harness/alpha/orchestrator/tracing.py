"""14. End-to-end trace propagation (run -> subagent -> memory).

Alpha already issues X-Trace-Id unconditionally (trace_context.py) and
records agent_workspace_trace_id on runs/checkpoints/Langfuse. What's missing is
explicit propagation into subagent delegation + background memory writes,
so logs correlate across hops. These helpers are pure dict/context ops —
no transport change, safe to call from any worker.
"""

from __future__ import annotations

from typing import Any


def bind_trace_for_subagent(parent_context: dict[str, Any] | None, trace_id: str) -> dict[str, Any]:
    """Return child run context carrying the parent trace id (overwrites)."""
    ctx = dict(parent_context or {})
    ctx["agent_workspace_trace_id"] = trace_id
    # Never trust caller-supplied trace inside metadata/config.context either.
    return ctx


def propagate_trace_to_memory(memory_payload: dict[str, Any] | None, trace_id: str) -> dict[str, Any]:
    """Attach trace id to a background memory-update payload for log correlation."""
    payload = dict(memory_payload or {})
    payload["agent_workspace_trace_id"] = trace_id
    return payload


def trace_header(trace_id: str) -> dict[str, str]:
    return {"X-Trace-Id": trace_id}
