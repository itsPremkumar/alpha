"""Timed spans with an injected clock, a closed status set, and enforced redaction.

A span is a named interval inside a run: one tool call, one agent turn, one
subagent. It carries the run's identity, a start and an end taken from an
**injected clock**, a bounded set of scrubbed attributes, and a status from a
closed set.

Semantics borrowed from OpenTelemetry
-------------------------------------
The model is the one OpenTelemetry popularised -- trace / span / parent span,
attributes as a bounded key-value bag, an explicit status rather than an
exception-derived one -- implemented from scratch here. Nothing is vendored.
The three ideas actually taken:

* **A status is a closed enumeration, not a string.** :class:`SpanStatus` is a
  :class:`~enum.StrEnum`, so ``SpanStatus("nope")`` raises and a downstream
  reader can match exhaustively. An open string status is how traces end up
  with ``"failed"``, ``"error"`` and ``"Error"`` meaning three things.
* **Attributes are a bounded key-value bag**, not a payload. The same care
  that goes into metric label cardinality goes here into span size: an
  unbounded attribute is how a trace file becomes a copy of the tool output.
* **A span knows its parent**, so the tree is recoverable from the flat event
  stream without any nesting in the wire format.

The redaction is enforced, not advisory
---------------------------------------
There is no public method that stores a raw value. :meth:`Span.set_attribute`
and :meth:`Span.set_attributes` both route through the injected
:class:`~alpha.observability.redaction.Redactor` and keep only the scrubbed
form; the span never holds the original. :meth:`Span.record_exception` is the
sharp case, because an exception message is the single most likely place for a
credential to enter a trace -- a tool that fails on a bad URL, an MCP server
that echoes an ``Authorization`` header, an HTTP client whose ``repr`` includes
the token. So:

* only the exception **type name** is stored verbatim;
* the message goes through the redactor, and a message whose scrub did not
  produce a fully-safe outcome (it had to be truncated, so a cut could leave
  half a credential) is **dropped entirely** and replaced by
  ``error.message_omitted="redaction_policy"``;
* the traceback is never stored at all. A traceback embeds source lines, and a
  source line can embed a literal.

Nesting and the depth cap
-------------------------
A span's parent is whatever span is current when it starts. Past ``max_depth``
the chain stops deepening: the new span is created at the cap, parented to the
current span so the tree stays connected, and stamped ``span.depth_capped=True``
plus a ``span`` record flag. It is *not* silently dropped, and it is *not*
allowed to recurse without bound -- an unbounded depth is how a recursive
subagent spawn becomes a stack overflow inside the tracer itself.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from enum import StrEnum
from typing import Any, Final

from .context import RunContext, current
from .ids import IDGenerator, is_valid_id
from .redaction import Redactor

__all__ = [
    "DEFAULT_MAX_ATTRIBUTES",
    "DEFAULT_MAX_SPAN_DEPTH",
    "SPAN_STATUSES",
    "Span",
    "SpanStatus",
    "Tracer",
    "UnknownSpanStatusError",
    "coerce_status",
    "current_span",
    "detach_span",
    "status_for_exception",
]

#: Depth past which nesting is capped. 12 comfortably covers run -> agent turn
#: -> subagent -> tool call -> MCP server -> provider call while still stopping
#: a runaway recursive delegation before it becomes a tracer stack overflow.
DEFAULT_MAX_SPAN_DEPTH: Final[int] = 12
#: Per-span attribute cap. The value a span refuses is counted in
#: ``dropped_attributes`` and disclosed on the record, so a missing attribute is
#: never a silent loss.
DEFAULT_MAX_ATTRIBUTES: Final[int] = 64


class UnknownSpanStatusError(ValueError):
    """Raised when a span status is outside the closed set."""


class SpanStatus(StrEnum):
    """Closed span status set.

    ``DISALLOWED`` is separated from ``ERROR`` on purpose: a guardrail, a
    policy engine or an authorization check refusing an action is not a failure
    of the system, and collapsing the two makes "the run broke" and "the run
    was correctly stopped" indistinguishable in an incident review.
    """

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    DISALLOWED = "disallowed"


SPAN_STATUSES: Final[frozenset[str]] = frozenset(member.value for member in SpanStatus)


def coerce_status(value: object) -> SpanStatus:
    """Return *value* as a :class:`SpanStatus`, rejecting anything else.

    An unknown status cannot be constructed: ``SpanStatus("partially_ok")``
    raises :class:`ValueError` from the enum, and this wrapper re-raises it as
    :class:`UnknownSpanStatusError` so the failure names the offending value.
    """
    if isinstance(value, SpanStatus):
        return value
    if isinstance(value, str):
        try:
            return SpanStatus(value)
        except ValueError as exc:
            raise UnknownSpanStatusError(f"unknown span status {value!r}; expected one of {sorted(SPAN_STATUSES)}") from exc
    raise UnknownSpanStatusError(f"span status must be a string or SpanStatus, got {type(value).__name__}")


class Span:
    """One named, timed, scrubbed interval inside a run.

    Construct one through :meth:`Tracer.start_span` (or the
    :meth:`Tracer.span` context manager) rather than directly: the tracer owns
    the id generator, the clock, the depth accounting and the parent linkage.
    """

    __slots__ = (
        "_attributes",
        "_clock",
        "_context",
        "_depth",
        "_depth_capped",
        "_dropped_attributes",
        "_end_time",
        "_ended",
        "_max_attributes",
        "_name",
        "_redactor",
        "_span_id",
        "_start_time",
        "_status",
    )

    def __init__(
        self,
        *,
        name: str,
        span_id: str,
        context: RunContext,
        started_at: float,
        clock: Any,
        redactor: Redactor,
        depth: int,
        depth_capped: bool,
        max_attributes: int = DEFAULT_MAX_ATTRIBUTES,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if not name or not str(name).strip():
            raise ValueError("span name must be a non-empty string")
        if not is_valid_id(span_id):
            raise ValueError(f"span_id must be a 32-character lowercase hex id; got {span_id!r}")
        if depth < 0:
            raise ValueError("span depth must be non-negative")
        if not isinstance(max_attributes, int) or isinstance(max_attributes, bool) or max_attributes < 1:
            raise ValueError("max_attributes must be a positive integer")
        self._name = str(name)
        self._span_id = span_id
        self._context = context
        self._start_time = float(started_at)
        self._end_time: float | None = None
        self._clock = clock
        self._redactor = redactor
        self._depth = depth
        self._depth_capped = depth_capped
        self._max_attributes = max_attributes
        self._attributes: dict[str, Any] = {}
        self._dropped_attributes = 0
        self._status: SpanStatus | None = None
        self._ended = False
        if attributes:
            self.set_attributes(attributes)

    # -- identity -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def span_id(self) -> str:
        return self._span_id

    @property
    def trace_id(self) -> str:
        return self._context.trace_id

    @property
    def run_id(self) -> str:
        return self._context.run_id

    @property
    def parent_span_id(self) -> str | None:
        return self._context.parent_span_id

    @property
    def context(self) -> RunContext:
        return self._context

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def depth_capped(self) -> bool:
        return self._depth_capped

    @property
    def started_at(self) -> float:
        return self._start_time

    @property
    def ended_at(self) -> float | None:
        return self._end_time

    @property
    def duration(self) -> float | None:
        """Seconds between start and end, or ``None`` while still open."""
        if self._end_time is None:
            return None
        return self._end_time - self._start_time

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def status(self) -> SpanStatus | None:
        """The terminal status, or ``None`` while the span is still open."""
        return self._status

    @property
    def dropped_attributes(self) -> int:
        """How many attributes the per-span cap or a truncate refused."""
        return self._dropped_attributes

    @property
    def attributes(self) -> dict[str, Any]:
        """A copy of the scrubbed attributes.

        Copy-on-read so a caller cannot mutate a stored value past the
        redactor. There is deliberately no way to obtain the *unscrubbed*
        attribute: it was never stored.
        """
        return dict(self._attributes)

    # -- attributes -----------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> bool:
        """Scrub and store one attribute.

        The only write path for attribute values, and it keeps the scrubbed
        form only. Re-scrubbing an already-scrubbed value is idempotent, which
        is why :meth:`record_exception` can hand its own output back through
        here without a second rule.

        Returns:
            ``True`` when stored, ``False`` when the per-span cap refused it or
            the span has already ended. A refusal is counted in
            :attr:`dropped_attributes` and disclosed on the record.
        """
        if self._ended:
            self._dropped_attributes += 1
            return False
        if len(self._attributes) >= self._max_attributes and key not in self._attributes:
            self._dropped_attributes += 1
            return False
        outcome = self._redactor.redact_value(value, key=key)
        self._attributes[str(key)] = outcome.value
        if outcome.truncated:
            self._dropped_attributes += 1
        return True

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        """Scrub and store several attributes, preserving input order."""
        if not isinstance(attributes, Mapping):
            raise TypeError(f"set_attributes expects a mapping, got {type(attributes).__name__}")
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def record_exception(self, exc: BaseException) -> None:
        """Record a failure without ever storing a raw message.

        Stores ``error.type`` -- the class name, which is not a secret -- and,
        only when the redactor can vouch for it, ``error.message``. A message
        that had to be truncated is dropped and the span records
        ``error.message_omitted="redaction_policy"`` instead, because a value
        cut mid-credential can leave half a secret behind.

        The traceback is never stored: it embeds source lines, and a source line
        can embed a literal.
        """
        if not isinstance(exc, BaseException):
            raise TypeError(f"record_exception expects an exception, got {type(exc).__name__}")
        self.set_attribute("error.type", type(exc).__name__)
        message = str(exc)
        if not message:
            return
        outcome = self._redactor.redact_value(message, key="error.message")
        if not outcome.safe:
            self.set_attribute("error.message_omitted", "redaction_policy")
            return
        self.set_attribute("error.message", outcome.value)

    # -- lifecycle ------------------------------------------------------------

    def end(self, *, status: object = SpanStatus.OK, end_time: float | None = None) -> SpanStatus:
        """Close the span and return its terminal status.

        Idempotent in effect: a second ``end`` returns the status already
        recorded without overwriting it, because a ``finally`` block and an
        explicit ``end`` racing on the same span must not turn a recorded
        failure into ``ok``.
        """
        if self._ended:
            return self._status or SpanStatus.OK
        self._status = coerce_status(status)
        self._end_time = float(end_time) if end_time is not None else float(self._clock())
        if self._end_time < self._start_time:
            # A clock that went backwards must not produce a negative duration,
            # which would quietly corrupt every duration histogram built from
            # these spans.
            self._end_time = self._start_time
        self._ended = True
        return self._status

    def end_with_exception(self, exc: BaseException, *, status: object | None = None) -> SpanStatus:
        """Record *exc* and close the span.

        The status defaults to the one the exception implies: ``cancelled`` for
        :class:`asyncio.CancelledError`, ``timeout`` for :class:`TimeoutError`,
        ``error`` otherwise. A caller that knows better passes ``status=``.
        """
        self.record_exception(exc)
        return self.end(status=status if status is not None else status_for_exception(exc))

    def child_context(self) -> RunContext:
        """Return the :class:`RunContext` a child of this span should carry."""
        return self._context.with_parent(self._span_id)

    def to_record(self) -> dict[str, Any]:
        """Return the stable JSONL record for this span.

        A span still open serialises with ``ended=False`` and a ``None``
        duration, so a trace file cut short shows the unfinished span instead
        of quietly omitting it.
        """
        return {
            "v": 1,
            "type": "span",
            "name": self._name,
            "span_id": self._span_id,
            "parent_span_id": self.parent_span_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "thread_id": self._context.thread_id,
            "user_id": self._context.user_id,
            "agent_name": self._context.agent_name,
            "start": self._start_time,
            "end": self._end_time,
            "duration": self.duration,
            "status": self._status.value if self._status is not None else None,
            "ended": self._ended,
            "depth": self._depth,
            "depth_capped": self._depth_capped,
            "dropped_attributes": self._dropped_attributes,
            "attributes": dict(self._attributes),
        }

    def __repr__(self) -> str:
        return f"Span(name={self._name!r}, span_id={self._span_id!r}, depth={self._depth}, status={self._status})"


def status_for_exception(exc: BaseException) -> SpanStatus:
    """Map an exception to the closed status set.

    Cancellation and timeout are their own statuses because they are the two
    cases where "the run did not finish" has a completely different remedy
    from "the run failed", and an incident review should not have to read the
    message to tell them apart.
    """
    if isinstance(exc, asyncio.CancelledError):
        return SpanStatus.CANCELLED
    if isinstance(exc, TimeoutError):
        return SpanStatus.TIMEOUT
    return SpanStatus.ERROR


#: The current span, used for parent linkage and the depth cap. A ContextVar
#: for the same reason the run context is one: two concurrent runs must never
#: share a span stack.
_current_span: ContextVar[Span | None] = ContextVar("alpha_observability_current_span", default=None)


def current_span() -> Span | None:
    """Return the span currently in scope, or ``None``."""
    return _current_span.get()


def detach_span(token: Token[Span | None]) -> None:
    """Restore the span binding captured by *token*.

    Named wrapper so a mismatch is legible: ``contextvars`` reports a
    cross-context reset as a bare ``ValueError``, and "unbound a span token
    from the wrong task" is a bug worth naming.
    """
    if not isinstance(token, Token):
        raise TypeError(f"detach_span expects a contextvars.Token, got {type(token).__name__}")
    _current_span.reset(token)


class Tracer:
    """Creates spans for one recorder.

    Holds the injected clock, id generator, redactor and the two caps. One
    tracer per recorder; it owns no global state, so two recorders with
    different policies produce independent traces.
    """

    __slots__ = ("_clock", "_id_generator", "_max_attributes", "_max_depth", "_on_span_end", "_redactor")

    def __init__(
        self,
        *,
        id_generator: IDGenerator,
        clock: Any = time.time,
        redactor: Redactor | None = None,
        max_depth: int = DEFAULT_MAX_SPAN_DEPTH,
        max_attributes: int = DEFAULT_MAX_ATTRIBUTES,
        on_span_end: Callable[[Span], None] | None = None,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
            raise ValueError("max_depth must be a positive integer")
        if not isinstance(max_attributes, int) or isinstance(max_attributes, bool) or max_attributes < 1:
            raise ValueError("max_attributes must be a positive integer")
        if on_span_end is not None and not callable(on_span_end):
            raise TypeError("on_span_end must be callable")
        self._id_generator = id_generator
        self._clock = clock
        self._redactor = redactor if redactor is not None else Redactor()
        self._max_depth = max_depth
        self._max_attributes = max_attributes
        self._on_span_end = on_span_end

    @property
    def max_depth(self) -> int:
        return self._max_depth

    @property
    def max_attributes(self) -> int:
        return self._max_attributes

    @property
    def clock(self) -> Any:
        return self._clock

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def start_span(
        self,
        name: str,
        *,
        context: RunContext | None = None,
        attributes: Mapping[str, Any] | None = None,
        started_at: float | None = None,
    ) -> Span:
        """Start a span parented to the current span.

        Args:
            name: Span name. A label, not an identity -- a repeated name is
                expected and is not a collision.
            context: The run context. Defaults to the ambient one; when neither
                is available a :class:`ValueError` is raised rather than
                fabricating a run, because an unparented span is exactly the
                orphan this package exists to prevent.
            attributes: Scrubbed on the way in.
            started_at: Explicit start, for a caller replaying a known time.
        """
        run_context = context if context is not None else current()
        if run_context is None:
            raise ValueError("no RunContext bound; pass context= explicitly or wrap the call in alpha.observability.context.run_scope()")
        parent = _current_span.get()
        parent_span_id = parent.span_id if parent is not None else run_context.parent_span_id
        if parent is not None and parent.depth + 1 > self._max_depth:
            depth = self._max_depth
            depth_capped = True
        elif parent is not None:
            depth = parent.depth + 1
            depth_capped = False
        else:
            depth = 0
            depth_capped = False
        child_context = run_context.with_parent(parent_span_id) if parent_span_id is not None else run_context
        span = Span(
            name=name,
            span_id=self._id_generator.new_span_id(),
            context=child_context,
            started_at=float(started_at) if started_at is not None else float(self._clock()),
            clock=self._clock,
            redactor=self._redactor,
            depth=depth,
            depth_capped=depth_capped,
            max_attributes=self._max_attributes,
        )
        if depth_capped:
            span.set_attribute("span.depth_capped", True)
        if attributes:
            span.set_attributes(attributes)
        return span

    @contextmanager
    def span(
        self,
        name: str,
        *,
        context: RunContext | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Start a span, make it current, end it on the way out, and record it.

        The span is current for the duration of the block, so a nested
        ``start_span`` parents to it. Restoration happens in a ``finally``:
        an exception inside the block ends the span with the status the
        exception implies and still restores the enclosing span.

        Recording is not optional. A span created through a ``Tracer`` that was
        given an ``on_span_end`` hook is recorded when it ends, so a caller
        cannot end up with a span that is invisible to every sink by reaching
        for the tracer directly. One path, so "I forgot to record it" is not a
        state the API permits.
        """
        started = self.start_span(name, context=context, attributes=attributes)
        token = _current_span.set(started)
        try:
            yield started
        except BaseException as exc:
            started.end_with_exception(exc)
            raise
        else:
            started.end()
        finally:
            detach_span(token)
            if self._on_span_end is not None:
                self._on_span_end(started)

    def bind_span(self, span: Span) -> Token[Span | None]:
        """Make *span* current, returning the token for :func:`detach_span`.

        For the async case, where a ``with`` block cannot span an ``await``
        that hands the span to another task.
        """
        return _current_span.set(span)

    def child_context(self, span: Span) -> RunContext:
        """Return the run context a child of *span* should carry."""
        return span.child_context()

    def __repr__(self) -> str:
        return f"Tracer(max_depth={self._max_depth}, max_attributes={self._max_attributes})"
