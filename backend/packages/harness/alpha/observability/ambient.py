"""Ambient writer binding and one-line emit helpers -- the instrumentation seam.

The problem this solves
----------------------
:class:`~alpha.observability.writer.BehaviourTraceWriter` is an injected object,
which is correct and which this package will not give up: a process-wide writer
is global mutable state that two tests cannot isolate and a config reload cannot
reason about. But a deep call site -- ``rank_candidates`` in
:mod:`alpha.tools.selection`, ``HandoffLedger.append`` in
:mod:`alpha.runtime.escalation`` -- cannot receive a writer without every one of
its callers changing, which is how instrumentation never lands.

So the run boundary binds the writer, exactly the way it binds the
:class:`~alpha.observability.context.RunContext`, and a call site reaches it
through a ``ContextVar``. The seam is deliberately *narrow*:

* :func:`current_writer` returns ``None`` when nothing is bound, so there is no
  mutable default and no hidden writer;
* :func:`emit` and the per-layer helpers return ``None`` immediately in that case,
  so an instrumented call site is **one added line** and its cost with tracing
  off is one function call and one ``is None`` test;
* the writer's own ``traced`` check is the second line of defence, so a writer
  that *is* bound but disabled still costs nothing.

A ``ContextVar`` and not a module global, for the reason
:mod:`alpha.observability.context` already states: it is per-task, it is copied
by :mod:`asyncio` on task creation, and it disappears with the context that
created it. Two concurrent runs in one process see two different writers, and a
raw thread sees ``None`` -- which is a counted, disclosed absence rather than an
event attributed to the wrong run.

The helpers are thin on purpose
------------------------------
Each helper is a null check and a delegation. They add no defaults, no coercion
and no policy, so a reader comparing a call site to
:meth:`BehaviourTraceWriter.tool_selection` sees the same arguments. Anything
smarter here would be a second place where the contract is decided.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from .context import RunContext
from .contract import TraceEnvelope
from .writer import BehaviourTraceWriter

__all__ = [
    "bind_writer",
    "current_writer",
    "detach_writer",
    "emit",
    "emit_budget_check",
    "emit_error_observed",
    "emit_guardrail_denied",
    "emit_handoff",
    "emit_model_call",
    "emit_node_transition",
    "emit_search_query",
    "emit_search_results",
    "emit_skill_selection",
    "emit_subagent_spawn",
    "emit_tool_selection",
    "writer_scope",
]

#: The one ambient state this module owns. A ``ContextVar`` for the reasons in
#: the module docstring, and ``None`` (not a disabled writer) for the reason that
#: a mutable default is exactly the thing the spine forbids.
_current_writer: ContextVar[BehaviourTraceWriter | None] = ContextVar("alpha_observability_behaviour_writer", default=None)


def current_writer() -> BehaviourTraceWriter | None:
    """Return the bound writer, or ``None`` when tracing is not in scope.

    ``None`` is a first-class answer, not a failure. A call site that runs
    outside a traced run -- a CLI, a scheduler tick, a unit test -- gets ``None``
    and does nothing, which is the correct outcome and costs one test.
    """
    return _current_writer.get()


def bind_writer(writer: BehaviourTraceWriter | None) -> Token[BehaviourTraceWriter | None]:
    """Bind *writer* and return the token that restores the previous value.

    Pair every :func:`bind_writer` with exactly one :func:`detach_writer`,
    including on the error path. :func:`writer_scope` does that for you.
    """
    if writer is not None and not isinstance(writer, BehaviourTraceWriter):
        raise TypeError(f"bind_writer expects a BehaviourTraceWriter or None, got {type(writer).__name__}")
    return _current_writer.set(writer)


def detach_writer(token: Token[BehaviourTraceWriter | None]) -> None:
    """Restore the binding captured by *token*.

    Raises:
        ValueError: when *token* was created in a different context than the one
            resetting it. ``contextvars`` already raises; the wrapper is kept so
            an unbound-writer bug reads as a named failure.
    """
    if not isinstance(token, Token):
        raise TypeError(f"detach_writer expects a contextvars.Token, got {type(token).__name__}")
    _current_writer.reset(token)


@contextmanager
def writer_scope(writer: BehaviourTraceWriter | None) -> Iterator[BehaviourTraceWriter | None]:
    """Bind *writer* for the duration of the block, restoring it exactly.

    Restoration happens in a ``finally``, so an exception inside the block still
    leaves the caller's context exactly as it was.
    """
    token = bind_writer(writer)
    try:
        yield writer
    finally:
        detach_writer(token)


# ---------------------------------------------------------------------------
# One-line emit helpers
# ---------------------------------------------------------------------------


def emit(
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    context: RunContext | None = None,
    **kwargs: Any,
) -> TraceEnvelope | None:
    """Emit *event_type* on the ambient writer, or do nothing.

    The generic seam, for a layer whose payload does not justify a named helper.
    Returns the envelope, or ``None`` when no writer is bound or the event was
    rejected -- the same contract as
    :meth:`BehaviourTraceWriter.emit`, with one extra ``None`` case in front of it.
    """
    writer = current_writer()
    if writer is None:
        return None
    return writer.emit(event_type, payload, context=context, **kwargs)


def emit_node_transition(
    *,
    from_node: str,
    to_node: str,
    reason: str,
    context: RunContext | None = None,
    **kwargs: Any,
) -> TraceEnvelope | None:
    """Layer 1. Delegate to :meth:`BehaviourTraceWriter.node_transition`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.node_transition(from_node=from_node, to_node=to_node, reason=reason, context=context, **kwargs)


def emit_model_call(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 2. Delegate to :meth:`BehaviourTraceWriter.model_call`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.model_call(context=context, **kwargs)


def emit_tool_selection(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 3. Delegate to :meth:`BehaviourTraceWriter.tool_selection`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.tool_selection(context=context, **kwargs)


def emit_skill_selection(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 4. Delegate to :meth:`BehaviourTraceWriter.skill_selection`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.skill_selection(context=context, **kwargs)


def emit_subagent_spawn(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 6. Delegate to :meth:`BehaviourTraceWriter.subagent_spawn`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.subagent_spawn(context=context, **kwargs)


def emit_search_query(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 8. Delegate to :meth:`BehaviourTraceWriter.search_query`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.search_query(context=context, **kwargs)


def emit_search_results(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 8. Delegate to :meth:`BehaviourTraceWriter.search_results`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.search_results(context=context, **kwargs)


def emit_budget_check(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 12. Delegate to :meth:`BehaviourTraceWriter.budget_check`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.budget_check(context=context, **kwargs)


def emit_error_observed(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 13. Delegate to :meth:`BehaviourTraceWriter.error_observed`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.error_observed(context=context, **kwargs)


def emit_handoff(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 14. Delegate to :meth:`BehaviourTraceWriter.handoff`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.handoff(context=context, **kwargs)


def emit_guardrail_denied(*, context: RunContext | None = None, **kwargs: Any) -> TraceEnvelope | None:
    """Layer 16. Delegate to :meth:`BehaviourTraceWriter.guardrail_denied`."""
    writer = current_writer()
    if writer is None:
        return None
    return writer.guardrail_denied(context=context, **kwargs)
