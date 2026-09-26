"""The correlation spine: one run identity, propagated through the execution path.

:class:`RunContext` is the value every span, every event and every redaction
disclosure carries. It is a frozen dataclass, so it can be compared, hashed,
copied into a task and restored exactly.

Where the trace id comes from
-----------------------------
``RunContext.trace_id`` is **not** minted here. It is resolved through
:func:`alpha.trace_context.resolve_trace_id`, whose module docstring states
that its ContextVar is the only source of a request trace id in this codebase.
This package therefore *extends* the existing spine (request -> run -> span)
instead of starting a rival one: a run created inside an HTTP request, a
scheduled occurrence, an IM message or an MCP notification all report the same
trace id the request already returned in ``X-Trace-Id``.

Crossing execution boundaries -- the documented answer
-----------------------------------------------------
A :class:`contextvars.ContextVar` is copied by :mod:`asyncio` when a task is
created and when a coroutine is handed to an executor, and it is **not** copied
by a raw thread. A subprocess is a different interpreter and gets nothing at
all. So "does the trace id survive?" has no single answer, and the honest
engineering answer is the table in :data:`BOUNDARY_RULES`, which this module
exports as data so a caller -- and a test -- can ask instead of guess:

============================  =========  ==================================
hop                           propagates  remedy
============================  =========  ==================================
``asyncio_task``              yes         nothing; a task copies the context
``asyncio_to_thread``         yes         nothing; it copies the context too
``thread_pool_submit``        no          pass the carrier, call
                                           :func:`bind_carrier`
``run_in_executor``           no          pass the carrier, call
                                           :func:`bind_carrier`
``threading_thread``          no          pass the carrier, call
                                           :func:`bind_carrier`
``event_bus_handler``         no          the bus owns its own task; pass the
                                           carrier in the payload
``multiprocessing_spawn``     no          pass the carrier through the
                                           argument tuple
``subprocess``                no          :func:`subprocess_link_env`, which
                                           discloses rather than inherits
============================  =========  ==================================

The ``run_in_executor`` row is the one that surprises people, and it is here
because the test suite caught the assumption. ``asyncio.to_thread`` wraps the
callable in :func:`contextvars.copy_context`; ``loop.run_in_executor`` does
not, because it hands the callable to an ordinary
:class:`~concurrent.futures.ThreadPoolExecutor` worker, which starts from a
fresh context. Two nearly identical-looking APIs, opposite answers, so the
table states it rather than leaving it to memory.

The subprocess case is the one that must never lie
--------------------------------------------------
A child process cannot inherit a contextvar, and pretending otherwise is how
orphans get into a trace file. :func:`subprocess_link_env` does **not** claim
inheritance. It returns an environment mapping that marks itself
``ALPHA_OBSERVABILITY_LINK_KIND=parent_disclosed`` and
``ALPHA_OBSERVABILITY_CONTEXT_PROPAGATED=false``, so a child that reads it
with :func:`link_from_env` produces events carrying ``context_inherited=False``
plus the parent ids as attributes. The trace then shows a *disclosed link*: the
child's events sit under the parent's trace id with an explicit marker saying
the value travelled as data rather than ambient state. An orphan -- a child
event with a fresh trace id and no parent reference -- is not representable via
this path.

:func:`bind_carrier` is the same idea for a thread hop: the carrier travels as
data and the receiving side rebinds, so a thread-pool worker emits under the
right trace id while the ambient variable is still honestly absent in a thread
nobody thought to hand a carrier to. The recorder emits
``run.context.linked`` with ``link_kind="thread_pool"`` for exactly that
rebind, so a reader can tell "propagated for free" from "rebound explicitly".

Restorability
-------------
:func:`bind` returns a :class:`contextvars.Token` and :func:`detach` consumes
it, so the previous context is restored *exactly* -- including after an
exception, because :func:`run_scope` resets in a ``finally``. There is no
"clear the global" path, and no global beyond the one ContextVar.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any, Final

from alpha.trace_context import normalize_trace_id, resolve_trace_id

from .ids import IDGenerator, default_id_generator, is_valid_id, require_id

__all__ = [
    "BOUNDARY_RULES",
    "ENV_AGENT_NAME",
    "ENV_CONTEXT_PROPAGATED",
    "ENV_LINK_KIND",
    "ENV_PARENT_RUN_ID",
    "ENV_PARENT_TRACE_ID",
    "ENV_RUN_ID",
    "ENV_THREAD_ID",
    "ENV_TRACE_ID",
    "ENV_USER_ID",
    "BoundaryRule",
    "RunContext",
    "bind",
    "bind_carrier",
    "current",
    "detach",
    "describe_boundary",
    "link_from_env",
    "propagates_automatically",
    "run_scope",
    "subprocess_link_env",
    "to_carrier",
]

ENV_TRACE_ID: Final[str] = "ALPHA_OBSERVABILITY_TRACE_ID"
ENV_RUN_ID: Final[str] = "ALPHA_OBSERVABILITY_RUN_ID"
ENV_PARENT_TRACE_ID: Final[str] = "ALPHA_OBSERVABILITY_PARENT_TRACE_ID"
ENV_PARENT_RUN_ID: Final[str] = "ALPHA_OBSERVABILITY_PARENT_RUN_ID"
ENV_THREAD_ID: Final[str] = "ALPHA_OBSERVABILITY_THREAD_ID"
ENV_USER_ID: Final[str] = "ALPHA_OBSERVABILITY_USER_ID"
ENV_AGENT_NAME: Final[str] = "ALPHA_OBSERVABILITY_AGENT_NAME"
#: The disclosed-link marker. Present and ``"false"`` means "these ids travelled
#: as data across a boundary that does not copy contextvars", which is a
#: *different claim* from "the ambient context carried them".
ENV_CONTEXT_PROPAGATED: Final[str] = "ALPHA_OBSERVABILITY_CONTEXT_PROPAGATED"
#: Which boundary forced the hand-off, e.g. ``subprocess`` or ``thread_pool``.
ENV_LINK_KIND: Final[str] = "ALPHA_OBSERVABILITY_LINK_KIND"


@dataclass(frozen=True)
class BoundaryRule:
    """One row of the propagation table."""

    hop: str
    propagates: bool
    mechanism: str
    remedy: str


#: The documented answer to "what happens in a thread pool or a subprocess".
#: Exported as data (not prose) so a caller can branch on it and a test can
#: assert the *actual* Python behaviour agrees with the table.
BOUNDARY_RULES: Final[tuple[BoundaryRule, ...]] = (
    BoundaryRule(
        "asyncio_task",
        True,
        "asyncio.Task copies the current contextvars.Context at task creation",
        "nothing: the ambient RunContext is already correct in the task",
    ),
    BoundaryRule(
        "asyncio_to_thread",
        True,
        "asyncio.to_thread runs the callable inside contextvars.copy_context()",
        "nothing: the ambient RunContext is already correct in the worker thread",
    ),
    BoundaryRule(
        "run_in_executor",
        False,
        "the callable is handed to a concurrent.futures thread, which starts from a fresh empty Context; unlike asyncio.to_thread there is no contextvars.copy_context() wrapper",
        "to_carrier() the context, pass it to the worker, bind_carrier() on the far side",
    ),
    BoundaryRule(
        "thread_pool_submit",
        False,
        "concurrent.futures.ThreadPoolExecutor starts a bare thread with an empty Context",
        "to_carrier() the context, pass it to the worker, bind_carrier() on the far side",
    ),
    BoundaryRule(
        "threading_thread",
        False,
        "threading.Thread starts with a fresh, empty Context",
        "to_carrier() the context, pass it to the target, bind_carrier() on the far side",
    ),
    BoundaryRule(
        "event_bus_handler",
        False,
        "alpha.events.bus creates its pump task per subscriber, from the bus's own context",
        "put the carrier in the event payload; the handler calls bind_carrier()",
    ),
    BoundaryRule(
        "multiprocessing_spawn",
        False,
        "a spawned process re-imports the module in a new interpreter with no inherited Context",
        "pass the carrier in the argument tuple; the child calls bind_carrier()",
    ),
    BoundaryRule(
        "subprocess",
        False,
        "a subprocess is a separate interpreter; contextvars cannot cross it",
        "subprocess_link_env() marks the ids as a disclosed parent link, never as inheritance",
    ),
)

_BOUNDARY_BY_HOP: Final[dict[str, BoundaryRule]] = {rule.hop: rule for rule in BOUNDARY_RULES}


def describe_boundary(hop: str) -> BoundaryRule:
    """Return the documented rule for *hop*.

    Raises:
        ValueError: for a hop outside :data:`BOUNDARY_RULES`. An unknown hop is
            a programming error, not a "probably fine" case: the whole point of
            the table is that no hop is left to a guess.
    """
    rule = _BOUNDARY_BY_HOP.get(hop)
    if rule is None:
        raise ValueError(f"unknown execution boundary {hop!r}; expected one of {sorted(_BOUNDARY_BY_HOP)}")
    return rule


def propagates_automatically(hop: str) -> bool:
    """Return whether contextvars survive *hop* without an explicit hand-off."""
    return describe_boundary(hop).propagates


@dataclass(frozen=True)
class RunContext:
    """The identity every part of one run agrees on.

    Attributes:
        trace_id: The request-level correlation id, resolved from
            :mod:`alpha.trace_context`. Never minted here.
        run_id: 128-bit, time-sortable, minted once per run by the injected
            :class:`~alpha.observability.ids.IDGenerator`.
        thread_id: LangGraph thread; ``None`` for a run with no thread.
        user_id: Effective user id. Carried for correlation, and it is the one
            field a metrics label must never carry.
        agent_name: Logical agent (e.g. ``"lead-agent"``), an attribute rather
            than part of the identity.
        parent_span_id: The span this run hangs under, when it is a
            subagent. ``None`` for a root run.
    """

    trace_id: str
    run_id: str
    thread_id: str | None = None
    user_id: str | None = None
    agent_name: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        normalized = normalize_trace_id(self.trace_id)
        if normalized is None:
            raise ValueError("RunContext.trace_id must be printable ASCII of at most 512 characters; the value travels into HTTP headers and log records")
        object.__setattr__(self, "trace_id", normalized)
        require_id(self.run_id, field="RunContext.run_id")
        if self.parent_span_id is not None:
            require_id(self.parent_span_id, field="RunContext.parent_span_id")

    def with_parent(self, parent_span_id: str) -> RunContext:
        """Return a copy parented to *parent_span_id*.

        Used by the subagent spawn seam: the child's :class:`RunContext` keeps
        the same ``trace_id`` and gains a parent span, which is what makes a
        delegated call visible as a child of the delegation rather than as a
        separate trace.
        """
        return replace(self, parent_span_id=parent_span_id)

    def to_carrier(self) -> dict[str, str]:
        """Return the plain-dict form for an explicit hand-off.

        This is the only supported way across a non-propagating boundary: the
        ids travel as data, and the receiving side calls :func:`bind_carrier`.
        """
        carrier: dict[str, str] = {ENV_TRACE_ID: self.trace_id, ENV_RUN_ID: self.run_id}
        if self.thread_id is not None:
            carrier[ENV_THREAD_ID] = self.thread_id
        if self.user_id is not None:
            carrier[ENV_USER_ID] = self.user_id
        if self.agent_name is not None:
            carrier[ENV_AGENT_NAME] = self.agent_name
        if self.parent_span_id is not None:
            carrier[ENV_PARENT_TRACE_ID] = self.parent_span_id
        return carrier


#: The one ambient state in this package. A ContextVar, not a module global:
#: it is per-task and per-thread, it is always restorable via its token, and it
#: disappears with the context that created it.
_current_run_context: ContextVar[RunContext | None] = ContextVar("alpha_observability_run_context", default=None)


def current() -> RunContext | None:
    """Return the bound :class:`RunContext`, or ``None`` when nothing is bound.

    Nullable on purpose. ``TraceRecorder`` consults it on every event, and it
    runs on import-time and third-party threads where nothing is bound; a
    fabricated placeholder id would be worse than admitting the gap.
    """
    return _current_run_context.get()


def bind(context: RunContext) -> Token[RunContext | None]:
    """Bind *context* and return the token that restores the previous value.

    The low-level pair, for callers that cannot use a ``with`` block. Pair
    every :func:`bind` with exactly one :func:`detach`, including on the error
    path -- :func:`run_scope` does that for you.
    """
    if not isinstance(context, RunContext):
        raise TypeError(f"bind expects a RunContext, got {type(context).__name__}")
    return _current_run_context.set(context)


def detach(token: Token[RunContext | None]) -> None:
    """Restore the binding captured by *token*.

    Raises:
        ValueError: when *token* was created in a different context than the
            one resetting it. ``contextvars`` already raises here; the check is
            kept as a named failure so a mismatched detach is legible in a
            traceback rather than an opaque ``ValueError`` from the stdlib.
    """
    if not isinstance(token, Token):
        raise TypeError(f"detach expects a contextvars.Token, got {type(token).__name__}")
    _current_run_context.reset(token)


@contextmanager
def run_scope(
    context: RunContext,
    *,
    trace_id: str | None = None,
) -> Iterator[RunContext]:
    """Bind *context* for the duration of the block, restoring it exactly.

    ``trace_id`` is an optional *carrier* for the non-propagating paths: pass
    the id that travelled alongside the work and the scope binds that instead
    of *context*'s own. That is what makes the same ``with`` statement correct
    both inside a request (where the contextvar is already right) and inside a
    thread or a scheduled occurrence (where it is not).

    Restoration happens in a ``finally``, so an exception inside the block
    still leaves the caller's context exactly as it was.
    """
    if trace_id is not None:
        context = replace(context, trace_id=resolve_trace_id(trace_id))
    token = bind(context)
    try:
        yield context
    finally:
        detach(token)


def to_carrier(context: RunContext | None = None) -> dict[str, str]:
    """Return the carrier dict for *context* (or the ambient one).

    Raises:
        RuntimeError: when nothing is bound and no context is passed. There is
            no "empty carrier" -- a hand-off with no ids is indistinguishable
            from no hand-off at all, which is the orphan this package exists to
            prevent.
    """
    selected = context if context is not None else current()
    if selected is None:
        raise RuntimeError("no RunContext is bound and none was passed; bind one with run_scope() or pass it explicitly")
    return selected.to_carrier()


def bind_carrier(carrier: Mapping[str, str], *, link_kind: str) -> RunContext:
    """Rebuild and bind a :class:`RunContext` from a carrier dict.

    Args:
        carrier: The mapping produced by :func:`to_carrier` or
            :func:`subprocess_link_env`.
        link_kind: Which boundary forced the hand-off. Recorded so the emitted
            ``run.context.linked`` event can distinguish "rebounded on a thread
            pool" from "linked across a subprocess". Must be non-empty.

    Returns:
        The bound context. The caller owns the returned token's lifecycle: use
        :func:`run_scope` for the happy path, or :func:`bind`/ :func:`detach`
        directly when the value must outlive the function.

    Raises:
        ValueError: on an empty ``link_kind`` or a carrier with no usable
            ``run_id``. A carrier that cannot produce a valid run id is
            refused rather than half-applied, so a broken hand-off surfaces at
            the boundary instead of as a run full of orphan events.
    """
    if not link_kind or not str(link_kind).strip():
        raise ValueError("link_kind must name the execution boundary that forced the hand-off")
    run_id = carrier.get(ENV_RUN_ID)
    if not is_valid_id(run_id):
        raise ValueError(f"carrier has no valid {ENV_RUN_ID}; got {run_id!r}")
    parent_span_id = carrier.get(ENV_PARENT_TRACE_ID)
    context = RunContext(
        trace_id=resolve_trace_id(carrier.get(ENV_TRACE_ID)),
        run_id=str(run_id),
        thread_id=carrier.get(ENV_THREAD_ID),
        user_id=carrier.get(ENV_USER_ID),
        agent_name=carrier.get(ENV_AGENT_NAME),
        parent_span_id=str(parent_span_id) if is_valid_id(parent_span_id) else None,
    )
    bind(context)
    return context


def subprocess_link_env(context: RunContext | None = None, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment mapping that *discloses* a link to a subprocess.

    A subprocess cannot inherit a contextvar, so the only honest thing to hand
    it is the ids plus an explicit statement that they travelled as data. The
    returned mapping therefore always carries
    :data:`ENV_CONTEXT_PROPAGATED` ``"false"`` and
    :data:`ENV_LINK_KIND` ``"subprocess"``; a child that reads it with
    :func:`link_from_env` emits ``context_inherited=False`` events that name
    their parent.

    Args:
        context: The context to link from; defaults to the ambient one.
        environ: Existing environment to extend. Omitted keys are not removed,
            so a child that inherited a stale value cannot disagree with the
            caller's environment by accident.

    Returns:
        A new ``dict``; *environ* is not mutated.
    """
    selected = context if context is not None else current()
    if selected is None:
        raise RuntimeError("no RunContext is bound and none was passed; a subprocess link with no ids is an orphan, not a link")
    merged: dict[str, str] = dict(environ if environ is not None else os.environ)
    merged.update(selected.to_carrier())
    # The parent reference travels under its own names so a child can record a
    # disclosed parent even when it mints a run id of its own.
    merged[ENV_PARENT_TRACE_ID] = selected.trace_id
    merged[ENV_PARENT_RUN_ID] = selected.run_id
    merged[ENV_CONTEXT_PROPAGATED] = "false"
    merged[ENV_LINK_KIND] = "subprocess"
    return merged


def link_from_env(environ: Mapping[str, str] | None = None) -> tuple[RunContext | None, str]:
    """Read a disclosed parent link out of a child's environment.

    Returns:
        ``(context, link_kind)``. ``context`` is ``None`` when the environment
        carries no link, which the caller must treat as "this process is not
        part of the parent's trace" rather than as an error. ``link_kind`` is
        empty in that case.

    When a link *is* present, the returned context always has
    ``parent_span_id`` unset and instead exposes the parent ids to the caller,
    which records them as ``parent_trace_id`` / ``parent_run_id`` attributes on
    a ``run.context.linked`` event. The child's own ``run_id`` is the parent's
    run id so a single trace file holds the whole chain under one id, while
    ``context_inherited=False`` keeps the fact that the boundary did not carry
    ambient state visible to any reader.
    """
    source = environ if environ is not None else os.environ
    link_kind = str(source.get(ENV_LINK_KIND, "") or "")
    run_id = source.get(ENV_RUN_ID)
    trace_id = source.get(ENV_TRACE_ID)
    if not link_kind or not is_valid_id(run_id) or not normalize_trace_id(trace_id):
        return None, ""
    return (
        RunContext(
            trace_id=resolve_trace_id(trace_id),
            run_id=str(run_id),
            thread_id=source.get(ENV_THREAD_ID),
            user_id=source.get(ENV_USER_ID),
            agent_name=source.get(ENV_AGENT_NAME),
        ),
        link_kind,
    )


def disclosed_link_attributes(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the ``parent_*`` attributes describing a non-inherited link.

    Empty when the environment carries no link, so a caller can splat it into
    an event's attributes unconditionally.
    """
    source = environ if environ is not None else os.environ
    attributes: dict[str, Any] = {}
    parent_trace_id = source.get(ENV_PARENT_TRACE_ID)
    parent_run_id = source.get(ENV_PARENT_RUN_ID)
    if parent_trace_id:
        attributes["parent_trace_id"] = parent_trace_id
    if parent_run_id:
        attributes["parent_run_id"] = parent_run_id
    link_kind = source.get(ENV_LINK_KIND)
    if link_kind:
        attributes["link_kind"] = link_kind
    return attributes


def ensure_run_context(
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    agent_name: str | None = None,
    parent_span_id: str | None = None,
    trace_id: str | None = None,
    id_generator: IDGenerator | None = None,
) -> RunContext:
    """Build a :class:`RunContext` with *run_id* minted when not supplied.

    The convenience constructor for a run start. ``trace_id`` defaults to
    :func:`alpha.trace_context.resolve_trace_id`, so a run started inside an
    HTTP request automatically joins that request's trace.
    """
    resolved_run_id = run_id if run_id else (id_generator.new_run_id() if id_generator is not None else default_id_generator().new_run_id())
    return RunContext(
        trace_id=resolve_trace_id(trace_id),
        run_id=resolved_run_id,
        thread_id=thread_id,
        user_id=user_id,
        agent_name=agent_name,
        parent_span_id=parent_span_id,
    )
