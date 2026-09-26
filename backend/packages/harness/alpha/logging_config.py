"""Logging setup helpers for Alpha.

Three production-safety properties are installed here, and all three are
applied at the **handler chokepoint** -- the last point at which a
:class:`logging.LogRecord` can still be rewritten, before a formatter renders
it. That is what lets this module change the behaviour of every logger in the
process without touching one of the ~2,800 ``logger.debug(...)`` call sites:

1. **Redaction** (:class:`LogRedactionFilter`). Spans, events and the trace
   recorder already scrub their attributes through
   :class:`~alpha.observability.redaction.Redactor`; **log records did not**.
   The filter now scrubs ``msg``, ``args``, ``exc_info`` and ``stack_info``
   through that same ``Redactor``, so a credential that reaches a log call
   cannot reach a log file, a console, or a support bundle. It is installed on
   every root handler *unconditionally*: ``logging.enhance.enabled`` is a
   correlation/format switch, and a secret must not depend on it.

2. **Correlation** (:class:`LogContextFilter`). ``run_id``, ``span_id``,
   ``agent`` and ``code`` are stamped onto every record, which is the "attach
   context without editing 2,800 call sites" mechanism. Values are resolved in
   this order:

   - an explicit :func:`log_context` binding for the current task (the escape
     hatch for code outside a run, and the only source of ``code``);
   - the ambient observability state that already exists --
     :func:`alpha.observability.context.current` for ``run_id``/``agent`` and
     :func:`alpha.observability.span.current_span` for ``span_id``. These are
     ContextVars, so an ``asyncio`` task or an ``asyncio.to_thread`` hop
     carries them for free and production gets run correlation **with no edits
     outside this module**;
   - :data:`LOG_CONTEXT_MISSING`, a deliberate, greppable "not bound" marker
     rather than a missing attribute.

3. **Bounded cost** (:func:`log_redaction_gate`). The full ``Redactor`` pass
   costs ~1.5 ms per record on a typical line, which is not affordable on
   every log call in the process. :func:`log_redaction_gate` is a cheap
   over-approximation that answers "could this text contain anything the engine
   recognises?" with a handful of :meth:`str.__contains__` calls, and only a
   positive answer pays for the full scrub. It is a **performance** filter, not
   a security filter: a false positive costs a slower log line, a false
   negative would leak, so the anchor table
   :data:`LOG_REDACTION_GATE_ANCHORS` is a strict superset of the literal
   anchors the engine's pattern families require, and
   ``tests/test_log_redaction_gate.py`` pins that superset property over the
   redaction corpus and over realistic log lines.

Policy
------
The log chokepoint uses the ``standard`` redaction policy, not ``strict``. The
difference is the strict bare-blob rule (>= 32 spaceless characters with high
character entropy), and Alpha's own correlation identifiers *are* that shape: a
``run_id`` is a 128-bit time-sortable id and a ``span_id`` is 32 hex
characters, both of which ``redact_text(..., policy="strict")`` replaces with
``[REDACTED:high_entropy_blob]``. Running the strict policy here would erase
exactly the fields this module exists to attach. ``standard`` still covers every
credential family the engine and
:data:`~alpha.observability.redaction.TRACE_EXTRA_PATTERNS` recognise -- the
prefixed-token families (OpenAI, GitHub, AWS, Slack, Stripe, Google, JWT, npm,
Azure), ``Basic``/``Bearer`` headers, connection-string credentials, and every
``keyword=value`` / ``keyword: value`` / ``KEY=value`` assignment form. An
operator who wants the strict bare-blob rule as well can opt in with
:data:`LOG_REDACTION_POLICY_ENV`; the gate is then bypassed entirely, because a
bare blob is by definition a value with no anchor.

Environment overrides
---------------------
Two variables exist so an operator has **one** place to turn diagnostics up
without a config edit and restart cycle per knob:

``AGENT_WORKSPACE_LOG_LEVEL``
    Overrides ``config.yaml`` ``log_level`` for the ``alpha``/``app``
    hierarchies. Applied by
    :func:`alpha.config.app_config.apply_logging_level`.

``AGENT_WORKSPACE_LOG_REDACTION_POLICY``
    ``standard`` (default) or ``strict``. A value outside the closed
    :data:`~alpha.observability.redaction.REDACTION_POLICIES` set is reported
    and ignored, so a typo cannot silently downgrade or disable scrubbing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import traceback
from collections.abc import Iterator, Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Final

from alpha.config.app_config import apply_logging_level
from alpha.observability.redaction import (
    REDACTION_POLICIES,
    STANDARD,
    Redactor,
)
from alpha.trace_context import get_current_trace_id

__all__ = [
    "DEFAULT_LOG_DATE_FORMAT",
    "DEFAULT_LOG_FORMAT",
    "LOG_CONTEXT_FIELDS",
    "LOG_CONTEXT_MISSING",
    "LOG_CONTEXT_TEXT_LOG_FORMAT",
    "LOG_REDACTION_GATE_ANCHORS",
    "LOG_REDACTION_POLICY_ENV",
    "REDACTION_FAIL_CLOSED",
    "TRACE_TEXT_LOG_FORMAT",
    "JsonTraceFormatter",
    "LogContextFilter",
    "LogRedactionFilter",
    "TraceContextFilter",
    "TraceTextFormatter",
    "bind_log_context",
    "build_log_redactor",
    "configure_logging",
    "current_log_context",
    "log_context",
    "log_redaction_gate",
    "remove_log_safety_filters",
    "reset_log_context",
    "resolve_log_redaction_policy",
]

DEFAULT_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
TRACE_TEXT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [trace_id=%(trace_id)s] - %(message)s"
#: The correlation bracket is emitted **before** ``[trace_id=...]`` and the trace
#: bracket keeps its exact historical text, so a grep for ``[trace_id=<id>]``
#: written against the pre-existing enhanced format still matches
#: (``tests/test_logging_config.py`` pins that substring).
LOG_CONTEXT_TEXT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [run_id=%(run_id)s span_id=%(span_id)s agent=%(agent)s code=%(code)s] [trace_id=%(trace_id)s] - %(message)s"

_TRACE_FILTER_NAME = "agent_workspace_trace_context_filter"
_REDACTION_FILTER_NAME = "agent_workspace_log_redaction_filter"
_LOG_CONTEXT_FILTER_NAME = "agent_workspace_log_context_filter"

#: The correlation fields stamped onto every record. ``code`` has no ambient
#: source: an error/stop code is a per-call-site fact, so it is only ever set
#: through :func:`log_context`.
LOG_CONTEXT_FIELDS: Final[tuple[str, ...]] = ("run_id", "span_id", "agent", "code")
#: Rendered for an unbound field. Chosen over "" so "no correlation id" is
#: visible in a log tail instead of being indistinguishable from a field the
#: formatter forgot to render.
LOG_CONTEXT_MISSING: Final[str] = "-"

#: What a message collapses to when redaction itself fails. Redaction must
#: never take the process down, and a filter that cannot decide must not
#: decide to pass the text through.
REDACTION_FAIL_CLOSED: Final[str] = "[REDACTED:redaction_error]"

LOG_REDACTION_POLICY_ENV: Final[str] = "AGENT_WORKSPACE_LOG_REDACTION_POLICY"

#: Cheap over-approximation of "this text may contain a credential".
#:
#: Every entry is a literal that a recognised pattern family *requires*, and the
#: comparison is done against :meth:`str.casefold`, so a case-insensitive engine
#: pattern is matched by its folded literal. One comment per group:
#:
#: - ``openai_key`` / ``stripe_secret_key``: ``sk-`` / ``sk_``
#: - ``github_fine_grained_pat`` / ``github_token_prefix``: ``github_pat_`` and
#:   the five ``gh[pousr]_`` prefixes
#: - ``aws_access_key_id``: ``AKIA`` / ``ASIA``
#: - ``pem_private_key_*`` and ``private[_-]?key``: ``private`` -- the PEM
#:   family literally contains ``PRIVATE KEY``, and ``privatekey`` /
#:   ``private_key`` / ``private-key`` all contain ``private``
#: - ``http_basic_auth_header``: ``basic``
#: - ``bearer_token``: ``bearer``
#: - ``session_cookie_assignment``: ``cookie`` (also covers ``set-cookie``),
#:   ``session`` (also covers ``sessionid``/``session_id``), ``jsessionid``,
#:   ``phpsessid``, ``connect_sid``
#: - ``_KEY_WORDS`` ``api[_-]?key``: ``apikey`` / ``api_key`` / ``api-key``
#:   (``api key`` with a space is *not* an engine match, so it is not an anchor)
#: - ``_KEY_WORDS`` remainder: ``secret``, ``password``, ``passwd``, ``token``,
#:   ``credential``
#: - ``connection_string_credentials``: ``://``
#: - ``slack_bot_or_app_token``: ``xox``
#: - ``google_api_key``: ``AIza``
#: - ``jwt_compact_token``: ``eyJ``
#: - ``npm_access_token``: ``npm_``
#: - ``azure_storage_account_key``: ``AccountKey=``
LOG_REDACTION_GATE_ANCHORS: Final[tuple[str, ...]] = (
    "sk-",
    "sk_",
    "github_pat_",
    "ghp_",
    "gho_",
    "ghs_",
    "ghr_",
    "ghu_",
    "akia",
    "asia",
    "private",
    "basic",
    "bearer",
    "cookie",
    "jsessionid",
    "phpsessid",
    "connect_sid",
    "session",
    "apikey",
    "api_key",
    "api-key",
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "://",
    "xox",
    "aiza",
    "eyj",
    "npm_",
    "accountkey",
)

#: Log text is a whole formatted line, not one span attribute, so the shape
#: table caps that make a 1024-character attribute safe would truncate
#: legitimate log lines. They are widened here instead: the *pattern* coverage
#: is unchanged, only the disclosure bound moves.
_LOG_MAX_VALUE_CHARS: Final[int] = 65536
_LOG_MAX_ITEMS: Final[int] = 4096
_LOG_MAX_DEPTH: Final[int] = 16

_log_context: ContextVar[Mapping[str, str]] = ContextVar("agent_workspace_log_context", default={})


# --------------------------------------------------------------------------- #
# Ambient correlation context
# --------------------------------------------------------------------------- #


def current_log_context() -> dict[str, str]:
    """Return the explicitly bound log context for the current task.

    Only :func:`log_context` / :func:`bind_log_context` bindings appear here;
    the values derived from the observability ContextVars are resolved later, in
    :class:`LogContextFilter`, so a caller inspecting this function sees exactly
    what it set.
    """
    return {field: value for field, value in _log_context.get().items() if field in LOG_CONTEXT_FIELDS}


def bind_log_context(**fields: str) -> Token[Mapping[str, str]]:
    """Merge *fields* into the ambient log context and return the reset token.

    Unknown keys are rejected rather than ignored: a typo in ``run_id`` would
    otherwise produce a log full of ``run_id=-`` with no error anywhere.
    """
    unknown = sorted(set(fields) - set(LOG_CONTEXT_FIELDS))
    if unknown:
        raise ValueError(f"unknown log context field(s) {unknown}; expected a subset of {list(LOG_CONTEXT_FIELDS)}")
    merged = dict(_log_context.get())
    for field, value in fields.items():
        text = "" if value is None else str(value)
        if text:
            merged[field] = text
        else:
            merged.pop(field, None)
    return _log_context.set(merged)


def reset_log_context(token: Token[Mapping[str, str]]) -> None:
    """Restore the binding captured by *token*."""
    _log_context.reset(token)


@contextlib.contextmanager
def log_context(**fields: str) -> Iterator[dict[str, str]]:
    """Scope *fields* to a ``with`` block.

    ``asyncio`` copies the context on task creation, so a child task inherits the
    bound values and cannot leak them back out; an explicit
    :func:`bind_log_context` / :func:`reset_log_context` pair is the right tool
    when that is not the shape of the call site.
    """
    token = bind_log_context(**fields)
    try:
        yield current_log_context()
    finally:
        reset_log_context(token)


def _ambient_run_fields() -> dict[str, str]:
    """Return ``run_id``/``agent``/``span_id`` from the observability ContextVars.

    Imported lazily and fully guarded: logging must keep working in a process
    where the observability package failed to import, and a correlation gap is a
    smaller problem than a broken log call.
    """
    resolved: dict[str, str] = {}
    try:
        from alpha.observability.context import current as current_run_context
        from alpha.observability.span import current_span
    except Exception:  # pragma: no cover - only reachable in a broken install
        return resolved
    try:
        run_context = current_run_context()
    except Exception:  # pragma: no cover - defensive
        run_context = None
    if run_context is not None:
        run_id = getattr(run_context, "run_id", None)
        if isinstance(run_id, str) and run_id:
            resolved["run_id"] = run_id
        agent_name = getattr(run_context, "agent_name", None)
        if isinstance(agent_name, str) and agent_name:
            resolved["agent"] = agent_name
    try:
        span = current_span()
    except Exception:  # pragma: no cover - defensive
        span = None
    if span is not None:
        span_id = getattr(span, "span_id", None)
        if isinstance(span_id, str) and span_id:
            resolved["span_id"] = span_id
    return resolved


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


def log_redaction_gate(text: str) -> bool:
    """Return whether *text* could contain something the engine recognises.

    Cheap on purpose (~30 us on a typical 150-character log line, against
    ~1.5 ms for the full :class:`Redactor` pass) because it runs on every
    emitted record. ``False`` means "no recognised credential family can match
    here"; ``True`` means "scrub it and find out".
    """
    lowered = text.casefold()
    for anchor in LOG_REDACTION_GATE_ANCHORS:
        if anchor in lowered:
            return True
    return False


def resolve_log_redaction_policy(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the log redaction policy from :data:`LOG_REDACTION_POLICY_ENV`.

    Returns :data:`~alpha.observability.redaction.STANDARD` unless the
    environment explicitly asks for :data:`~alpha.observability.redaction.STRICT`.
    An unrecognised value is reported and ignored rather than honoured: a typo
    must not be able to turn scrubbing off.
    """
    source = os.environ if environ is None else environ
    raw = source.get(LOG_REDACTION_POLICY_ENV)
    if raw is None or not raw.strip():
        return STANDARD
    candidate = raw.strip().lower()
    if candidate not in REDACTION_POLICIES:
        logging.getLogger(__name__).warning(
            "%s=%r is not a known redaction policy; keeping %r. Expected one of %s.",
            LOG_REDACTION_POLICY_ENV,
            raw,
            STANDARD,
            sorted(REDACTION_POLICIES),
        )
        return STANDARD
    return candidate


def build_log_redactor(policy: str | None = None) -> Redactor:
    """Return a :class:`Redactor` sized for whole log lines.

    Exposed so a non-``configure_logging`` sink (the interactive ``debug.py``
    session, for instance) scrubs through the exact same object the Gateway
    chokepoint uses instead of a second configuration of its own.
    """
    return Redactor(
        policy or resolve_log_redaction_policy(),
        max_value_chars=_LOG_MAX_VALUE_CHARS,
        max_items=_LOG_MAX_ITEMS,
        max_depth=_LOG_MAX_DEPTH,
    )


_EXCEPTION_FORMATTER: Final[logging.Formatter] = logging.Formatter()


def _format_exception(exc_info: Any) -> str:
    """Render an ``exc_info`` triple to text without pinning an interpreter API.

    ``logging.Formatter.formatException`` is the supported path; the
    ``traceback`` fallback covers interpreters where that signature moved.
    """
    try:
        return _EXCEPTION_FORMATTER.formatException(exc_info)
    except Exception:  # pragma: no cover - interpreter fallback
        return "".join(traceback.format_exception(*exc_info)).rstrip("\n")


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


class LogRedactionFilter(logging.Filter):
    """Scrub a record's payload through the shared :class:`Redactor`.

    Installed on every root handler by :func:`configure_logging`, so coverage
    does not depend on a logger opting in.

    What is scrubbed, and why each part:

    ``msg``
        The literal format string. Safe to rewrite: :meth:`LogRecord.getMessage`
        only does ``str(self.msg)``.
    ``args``
        The interpolated values, which is where credentials usually arrive
        (``logger.error("call failed: %s", response.text)``). A mapping is
        scrubbed per key, so the *key* rule applies too; a tuple/list is scrubbed
        positionally. A value under no key that is not text is passed to the
        redactor anyway, because its shape table is where the "unknown shape is
        redacted" rule lives.
    ``exc_info``
        Pre-rendered into ``record.exc_text``.
        :meth:`logging.Formatter.format` only formats the traceback when
        ``record.exc_text`` is unset, so setting it here substitutes the scrubbed
        rendering without disturbing ``exc_info`` for any other consumer. A
        provider URL with embedded credentials inside a ``requests``/``httpx``
        exception is the common leak, and it is in the traceback, not the
        message.
    ``stack_info``
        Rewritten in place; a ``stack_info`` block can carry locals.

    The filter never raises. A redaction failure replaces the payload with
    :data:`REDACTION_FAIL_CLOSED`: for a logging call, dropping the message is
    recoverable and passing an unscrubbed message through is not.
    """

    name = _REDACTION_FILTER_NAME

    def __init__(self, redactor: Redactor | None = None) -> None:
        # See ``TraceContextFilter.__init__``: the base class would otherwise
        # shadow ``name`` with ``''`` and make this filter unidentifiable.
        super().__init__(name=_REDACTION_FILTER_NAME)
        self._redactor = redactor if redactor is not None else build_log_redactor()
        # The strict bare-blob rule has no anchor to gate on -- it exists
        # precisely for values with no recognizable prefix -- so the gate is only
        # valid for the value-pattern policies.
        self._gate_enabled = self._redactor.policy == STANDARD

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def should_scrub(self, text: str) -> bool:
        """Return whether *text* needs the full pass."""
        return not self._gate_enabled or log_redaction_gate(text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact_record(record)
        except Exception:
            record.msg = REDACTION_FAIL_CLOSED
            record.args = ()
        return True

    def _scrub(self, value: Any, key: object = None) -> Any:
        if isinstance(value, str):
            if not self.should_scrub(value):
                return value
            return self._redactor.redact_value(value, key=key).value
        if isinstance(value, Mapping):
            return {item_key: self._scrub(item, key=item_key) for item_key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self._scrub(item) for item in value)
        if isinstance(value, list):
            return [self._scrub(item) for item in value]
        # Numbers, bytes, None and unknown objects still go through the redactor:
        # the key rule ("a numeric value under api_key is a credential") and the
        # unknown-shape rule both live there, and neither is reachable anywhere
        # else.
        return self._redactor.redact_value(value, key=key).value

    def _redact_record(self, record: logging.LogRecord) -> None:
        message = record.msg
        if isinstance(message, str):
            record.msg = self._scrub(message)
        args = record.args
        if isinstance(args, Mapping):
            record.args = {key: self._scrub(value, key=key) for key, value in args.items()}
        elif isinstance(args, tuple):
            record.args = tuple(self._scrub(item) for item in args)
        elif isinstance(args, list):  # pragma: no cover - logging always uses a tuple
            record.args = [self._scrub(item) for item in args]
        if record.exc_info:
            record.exc_text = self._scrub(_format_exception(record.exc_info))
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)


class TraceContextFilter(logging.Filter):
    """Inject the current request trace id into every log record."""

    name = _TRACE_FILTER_NAME

    def __init__(self) -> None:
        # ``logging.Filter.__init__`` assigns ``self.name = ''``, which shadows
        # the class attribute. Passing the name through makes the *instance*
        # attribute correct, so an id-based lookup (remove, dedupe, introspection)
        # sees this filter and not an anonymous one. The ``isinstance`` fallbacks
        # in the helpers below kept working regardless, which is why this was
        # never noticed.
        super().__init__(name=_TRACE_FILTER_NAME)

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_current_trace_id() or "-"
        return True


class LogContextFilter(logging.Filter):
    """Stamp ``run_id``/``span_id``/``agent``/``code`` onto every log record.

    This is the "no call-site edits" half of the correlation story: the values
    come from ContextVars the run machinery already binds, so every existing
    ``logger.info(...)`` in the process gains run correlation the moment its
    record passes a configured handler. :func:`log_context` covers what those
    ContextVars cannot know -- most importantly ``code``, an operator-facing
    error/stop code that is a per-call-site fact.
    """

    name = _LOG_CONTEXT_FILTER_NAME

    def __init__(self) -> None:
        # See ``TraceContextFilter.__init__``.
        super().__init__(name=_LOG_CONTEXT_FILTER_NAME)

    def filter(self, record: logging.LogRecord) -> bool:
        explicit = _log_context.get()
        resolved: dict[str, str] = {field: explicit.get(field) for field in LOG_CONTEXT_FIELDS}
        # The ambient lookup is the expensive half (two ContextVar reads through
        # lazily imported modules), so it runs only when at least one field is
        # still unanswered. Two cases matter and both are handled by testing the
        # *fields* rather than the dict: a binding that supplies only `code` must
        # not blank out the run_id a run scope is providing, and an empty binding
        # must still consult the ambient state at all (`any()` over an empty dict
        # is False, which is the bug this comment is here to prevent).
        if any(not resolved.get(field) for field in LOG_CONTEXT_FIELDS):
            ambient = _ambient_run_fields()
            for field in LOG_CONTEXT_FIELDS:
                if not resolved.get(field):
                    resolved[field] = ambient.get(field)
        for field in LOG_CONTEXT_FIELDS:
            value = resolved.get(field)
            setattr(record, field, value if isinstance(value, str) and value else LOG_CONTEXT_MISSING)
        return True


# --------------------------------------------------------------------------- #
# Handler wiring
# --------------------------------------------------------------------------- #


def _has_named_filter(handler: logging.Handler, name: str, kind: type[logging.Filter]) -> bool:
    return any(getattr(f, "name", None) == name or isinstance(f, kind) for f in handler.filters)


def _install_filter(handler: logging.Handler, name: str, kind: type[logging.Filter], factory: Any = None) -> None:
    if not _has_named_filter(handler, name, kind):
        handler.addFilter(factory() if factory is not None else kind())


def _drop_safety_filters(handler: logging.Handler) -> None:
    handler.filters = [f for f in handler.filters if not (getattr(f, "name", None) in {_REDACTION_FILTER_NAME, _LOG_CONTEXT_FILTER_NAME} or isinstance(f, (LogRedactionFilter, LogContextFilter)))]


def remove_log_safety_filters(handler: logging.Handler) -> None:
    """Remove the redaction and correlation filters from *handler*.

    A deliberate, documented opt-out. It exists because a redaction pass on
    every record is a real cost and an operator measuring that cost needs a
    supported way to turn it off; it is not a configuration shortcut, and
    nothing in the shipped Gateway calls it.
    """
    _drop_safety_filters(handler)


def _install_safety_filters(handler: logging.Handler, redactor: Redactor | None = None) -> None:
    """Install the unconditional log-safety filters, in order.

    Order matters: the trace filter runs first, so a redaction failure (which
    replaces the message wholesale) cannot also cost the record its trace id.
    An existing :class:`LogRedactionFilter` built for a different policy is
    replaced rather than kept, so re-running :func:`configure_logging` after a
    policy change actually takes effect.
    """
    _install_filter(handler, _TRACE_FILTER_NAME, TraceContextFilter)
    _drop_safety_filters(handler)
    handler.addFilter(LogRedactionFilter(redactor))
    handler.addFilter(LogContextFilter())


def _default_formatter() -> logging.Formatter:
    return logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_LOG_DATE_FORMAT)


class JsonTraceFormatter(logging.Formatter):
    """Small JSON formatter used when ``logging.enhance.format=json``."""

    _agent_workspace_trace_formatter = True

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "trace_id"):
            record.trace_id = get_current_trace_id() or "-"
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": record.name,
            "level": record.levelname,
            "trace_id": record.trace_id,
            "message": record.getMessage(),
        }
        # The correlation fields ride on every record, so a JSON consumer gets
        # the same four keys whether or not they are bound -- absence is an
        # explicit ``"-"``, not a missing key.
        for field in LOG_CONTEXT_FIELDS:
            payload[field] = getattr(record, field, LOG_CONTEXT_MISSING)
        if record.exc_info:
            # ``LogRedactionFilter`` pre-renders a scrubbed traceback into
            # ``exc_text``; only fall back to formatting it when the filter is
            # not installed on this handler.
            payload["exc_info"] = record.exc_text if record.exc_text else self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


class TraceTextFormatter(logging.Formatter):
    """Marker subclass so trace formatting can be reverted cleanly in tests."""

    _agent_workspace_trace_formatter = True


def _trace_formatter(format_name: str | None) -> logging.Formatter:
    if (format_name or "text").strip().lower() == "json":
        return JsonTraceFormatter()
    return TraceTextFormatter(LOG_CONTEXT_TEXT_LOG_FORMAT, datefmt=DEFAULT_LOG_DATE_FORMAT)


def _is_plain_default_formatter(handler: logging.Handler) -> bool:
    """Whether *handler* still carries the plain format Alpha installed itself.

    Used to decide whether the correlation bracket can be added to a
    non-enhanced handler. A caller that installed its own format string keeps
    it; only ``logging.basicConfig(format=DEFAULT_LOG_FORMAT)`` output is
    upgraded, and the safety filters are installed *before* the format is
    swapped, so there is no window in which the format references an attribute
    the record does not have.
    """
    formatter = handler.formatter
    return type(formatter) is logging.Formatter and getattr(formatter, "_fmt", None) == DEFAULT_LOG_FORMAT


def configure_logging(config: object) -> None:
    """Configure Alpha logging from an AppConfig-like object.

    The redaction and correlation filters are installed on every root handler
    unconditionally; ``logging.enhance`` only chooses between the plain-text and
    JSON *renderings* of the same scrubbed record. A secret must not depend on
    a display toggle, so redaction does not.

    An enhanced handler renders the correlation bracket plus the historical
    ``[trace_id=...]`` bracket. A non-enhanced handler keeps
    :data:`DEFAULT_LOG_FORMAT` unless that is still the plain default Alpha
    installed, in which case it is upgraded to the correlation format -- a
    caller-supplied formatter is never overwritten.
    """
    _ensure_root_handler()

    logging_config = getattr(config, "logging", None)
    enhance = getattr(logging_config, "enhance", None)
    enhanced = bool(getattr(enhance, "enabled", False))
    redactor = build_log_redactor(resolve_log_redaction_policy())

    for handler in logging.root.handlers:
        _install_safety_filters(handler, redactor)
        if enhanced:
            handler.setFormatter(_trace_formatter(getattr(enhance, "format", "text")))
        elif getattr(handler.formatter, "_agent_workspace_trace_formatter", False):
            handler.setFormatter(_default_formatter())
        elif _is_plain_default_formatter(handler):
            handler.setFormatter(TraceTextFormatter(LOG_CONTEXT_TEXT_LOG_FORMAT, datefmt=DEFAULT_LOG_DATE_FORMAT))

    apply_logging_level(getattr(config, "log_level", None))


def _ensure_root_handler() -> None:
    if logging.root.handlers:
        return
    logging.basicConfig(level=logging.INFO, format=DEFAULT_LOG_FORMAT, datefmt=DEFAULT_LOG_DATE_FORMAT)
    for handler in logging.root.handlers:
        _install_safety_filters(handler)
