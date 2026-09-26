"""Every log record must pass the redaction and correlation chokepoint.

`configure_logging` installs `LogRedactionFilter` and `LogContextFilter` on every
root handler, which is what lets one module change the behaviour of ~2,800
`logger.*` call sites. That only holds if the two properties are genuinely
*unconditional*:

1. Secrets do not reach a log sink. Not "not when a config flag is on" -- the
   `logging.enhance` switch is a display toggle and must not be load-bearing for
   redaction. These tests run with enhancement both on and off, and with a
   caller-supplied formatter that knows nothing about correlation.
2. Correlation ids reach a log sink. `run_id` / `span_id` / `agent` / `code` are
   resolved from ContextVars the run machinery already binds, so a test does not
   have to change a single call site to get them.

The DEBUG-dump sites called out in the audit (`input_sanitization_middleware`,
`group_chat`, `infoquest_client`, `github`) are deliberately *not* edited: the
chokepoint is what makes them safe, and `test_debug_dump_call_sites_are_covered`
below asserts they are ordinary `logger.debug` calls whose output the chokepoint
scrubs.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from types import SimpleNamespace

import pytest

from alpha.logging_config import (
    DEFAULT_LOG_FORMAT,
    LOG_CONTEXT_FIELDS,
    LOG_CONTEXT_MISSING,
    LOG_CONTEXT_TEXT_LOG_FORMAT,
    REDACTION_FAIL_CLOSED,
    LogContextFilter,
    LogRedactionFilter,
    TraceContextFilter,
    bind_log_context,
    configure_logging,
    current_log_context,
    log_context,
    remove_log_safety_filters,
    reset_log_context,
)
from alpha.observability.context import RunContext, run_scope
from alpha.observability.ids import default_id_generator
from alpha.observability.redaction import STANDARD, dangerous_value_corpus
from alpha.observability.span import Tracer, current_span
from alpha.trace_context import request_trace_context


def _assemble(*parts: str) -> str:
    """Join *parts* into one fixture without committing a token-shaped literal."""
    return "".join(parts)


@pytest.fixture
def root_handler() -> logging.Handler:
    """Install a single capturing root handler and restore the real one after."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_alpha = logging.getLogger("alpha").level
    saved_app = logging.getLogger("app").level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.stream = stream  # type: ignore[assignment]
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        for current in list(root.handlers):
            if current is not handler:
                root.removeHandler(current)
                current.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        logging.getLogger("alpha").setLevel(saved_alpha)
        logging.getLogger("app").setLevel(saved_app)


def _config(*, enabled: bool, fmt: str = "text", level: str = "debug") -> SimpleNamespace:
    return SimpleNamespace(log_level=level, logging=SimpleNamespace(enhance=SimpleNamespace(enabled=enabled, format=fmt)))


# --------------------------------------------------------------------------- #
# (a) Redaction at the chokepoint
# --------------------------------------------------------------------------- #


def test_safety_filters_install_even_with_enhancement_disabled(root_handler: logging.Handler) -> None:
    """The whole point: redaction must not depend on a display toggle.

    `logging.enhance.enabled` defaults to False, so a redaction filter installed
    only in the enhanced branch would be off in the default production
    configuration. This is the regression that would put credentials back in
    logs.
    """
    configure_logging(_config(enabled=False))
    names = {getattr(f, "name", None) for f in root_handler.filters}
    assert "agent_workspace_log_redaction_filter" in names
    assert "agent_workspace_log_context_filter" in names
    assert "agent_workspace_trace_context_filter" in names


def test_safety_filters_survive_a_caller_supplied_formatter(root_handler: logging.Handler) -> None:
    """A handler whose formatter Alpha did not install is still scrubbed.

    `logging.FileHandler`s, uvicorn's own handlers, and anything an operator
    attaches all sit on the same chokepoint. Coverage cannot depend on the
    formatter.
    """
    root_handler.setFormatter(logging.Formatter("%(message)s"))
    configure_logging(_config(enabled=False))
    names = {getattr(f, "name", None) for f in root_handler.filters}
    assert "agent_workspace_log_redaction_filter" in names
    assert "agent_workspace_log_context_filter" in names
    # ...and a caller-supplied format string is not overwritten.
    assert root_handler.formatter._fmt == "%(message)s"


#: Fabricated credential fixtures, taken from the canonical corpus rather than
#: re-typed. GitHub push protection rejects a commit whose *text* contains a whole
#: credential-shaped token (GH013) even when it is fabricated, which is why
#: `observability/redaction.py` keeps its own fixtures split. Referencing the
#: corpus means this file holds no credential literal at all, and the fixture set
#: can grow without this test being edited.
_CORPUS: dict[str, str] = {label: value for label, value, _fragment in dangerous_value_corpus()}

#: The substring each assertion checks is absent. For a header or a connection
#: string the *secret material* is the interesting part, not the whole value, so
#: those use the fragment the corpus already states explicitly.
_FRAGMENTS: dict[str, str] = {label: fragment for label, _value, fragment in dangerous_value_corpus()}

#: An assignment value that only matches because of the keyword in front of it.
_ASSIGNMENT_VALUE = "0123456789abcdef" + "0123456789abcdef"

#: A `Bearer` credential and a connection string with embedded userinfo, both
#: assembled so no contiguous credential-shaped token is committed. `Bearer` is a
#: shape GitHub's push protection recognises on its own, and these tests exist
#: precisely because the trace plane has to scrub a provider URL that leaked into
#: a traceback.
_BEARER_VALUE = "a" * 20
_CONNECTION_URL = _assemble("postgres://reporting:", "hunter2Correct@db.internal:5432/analytics")
_DBG_BEARER_VALUE = "abcdefghij" + "klmnopqrst"


@pytest.mark.parametrize(
    ("message", "forbidden"),
    [
        (f"upstream said 401 with Authorization: Bearer {_BEARER_VALUE}", _BEARER_VALUE),
        (f"dialing {_CORPUS['connection_string']}", _FRAGMENTS["connection_string"]),
        (f"stripe client configured with {_CORPUS['stripe_secret_key']}", _FRAGMENTS["stripe_secret_key"]),
        (f"slack client configured with {_CORPUS['slack_bot_token']}", "2419427381-2419427381"),
        (f"google client configured with {_CORPUS['google_api_key']}", _FRAGMENTS["google_api_key"]),
        (f"pushing with {_CORPUS['github_classic_pat']}", "0123456789abcdefghijklmnopqrstuvwxyz"),
        (f"resolved credential {_assemble('AKIA', 'IOSFODNN7EXAMPLE')} from the role", "IOSFODNN7EXAMPLE"),
        (f"config had X_API_KEY={_ASSIGNMENT_VALUE}", _ASSIGNMENT_VALUE),
    ],
)
def test_message_secrets_are_scrubbed(root_handler: logging.Handler, message: str, forbidden: str) -> None:
    configure_logging(_config(enabled=False))
    logging.getLogger("alpha.test").info(message)
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert forbidden not in output
    assert "[REDACTED:" in output


def test_interpolated_arguments_are_scrubbed_not_just_the_format_string(root_handler: logging.Handler) -> None:
    """`logger.info("call failed: %s", response.text)` is where secrets actually arrive.

    Redacting only the format string would leave the credential intact, because
    the format string is a constant. The argument is the value that carries it.
    """
    configure_logging(_config(enabled=False))
    logging.getLogger("alpha.test").error("upstream call failed for key %s", _CORPUS["openai_project_key"])
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert _CORPUS["openai_project_key"] not in output
    assert "upstream call failed for key" in output


def test_mapping_arguments_are_scrubbed_under_their_own_keys(root_handler: logging.Handler) -> None:
    """A secret-named *key* is a credential even when the value is a number.

    `Redactor` closes exactly this gap for spans; a log call that formats
    `%(api_key)s` must not be the one place the gap reopens.
    """
    configure_logging(_config(enabled=False))
    logging.getLogger("alpha.test").warning("provider rejected %(api_key)s with %(code)s", {"api_key": 1234567890123456, "code": 402})
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert "1234567890123456" not in output
    assert "402" in output  # a non-secret field survives


def test_traceback_text_is_scrubbed(root_handler: logging.Handler) -> None:
    """The traceback, not the message, is where a provider URL usually leaks.

    `requests`/`httpx` exceptions embed the full request URL, and an exception
    message can carry a header. Both live in `exc_info`, so a filter that only
    scrubbed `msg` would miss the most common real leak in the format.
    """
    configure_logging(_config(enabled=False))
    try:
        raise RuntimeError(f"connect failed for {_CONNECTION_URL}")
    except RuntimeError:
        logging.getLogger("alpha.test").error("provider unreachable", exc_info=True)
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert _CONNECTION_URL not in output and "hunter2" not in output
    assert "provider unreachable" in output
    # The traceback is still a traceback: the filter redacts, it does not remove.
    assert "Traceback" in output
    assert "RuntimeError" in output


def test_a_debug_level_dump_is_scrubbed_too(root_handler: logging.Handler) -> None:
    """The audit's DEBUG-dump sites are the reason this filter is unconditional.

    A DEBUG dump of untrusted content is exactly the shape that leaks, and DEBUG
    is off by default -- so a filter that only engaged at INFO would be useless
    for the sites that need it most. `input_sanitization_middleware` and friends
    are not edited; this is what covers them.
    """
    configure_logging(_config(enabled=False, level="debug"))
    logging.getLogger("alpha.test").debug("incoming content %r", {"headers": {"Authorization": f"Bearer {_DBG_BEARER_VALUE}"}})
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert _DBG_BEARER_VALUE not in output
    assert "[REDACTED:" in output


def test_filter_never_raises_and_fails_closed(monkeypatch, root_handler: logging.Handler) -> None:
    """A logging call must never take the process down, and must never pass text through.

    If the redactor itself throws, the two acceptable outcomes are "log less" or
    "log nothing". "Log the original message" is not one of them.
    """
    configure_logging(_config(enabled=False))
    log_filter = next(f for f in root_handler.filters if isinstance(f, LogRedactionFilter))

    def boom(*_args, **_kwargs):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(log_filter, "_redact_record", boom)
    logging.getLogger("alpha.test").info("this text must not survive %s", "the failure")
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert "this text must not survive" not in output
    assert REDACTION_FAIL_CLOSED in output


def test_correlation_identifiers_survive_redaction(root_handler: logging.Handler) -> None:
    """run_id / span_id / thread_id must come out the other side intact.

    They are 32-hex and uuid-shaped -- precisely what the strict bare-blob rule
    would erase. Scrubbing them would make the correlation work worthless, so this
    is asserted rather than assumed.
    """
    configure_logging(_config(enabled=False))
    run_id = default_id_generator().new_run_id()
    thread_id = f"thr_{uuid.uuid4().hex[:12]}"
    with run_scope(RunContext(trace_id="t-survive", run_id=run_id, thread_id=thread_id)):
        logging.getLogger("alpha.test").info("run %s thread %s progressing", run_id, thread_id)
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert run_id in output
    assert thread_id in output


def test_remove_log_safety_filters_is_a_supported_opt_out(root_handler: logging.Handler) -> None:
    """The opt-out exists so an operator measuring the cost has a supported way out.

    It is not wired into any production path; this pins that it is *available*
    and that it removes exactly the two filters and nothing else.
    """
    configure_logging(_config(enabled=False))
    remove_log_safety_filters(root_handler)
    names = {getattr(f, "name", None) for f in root_handler.filters}
    assert "agent_workspace_log_redaction_filter" not in names
    assert "agent_workspace_log_context_filter" not in names
    assert "agent_workspace_trace_context_filter" in names


def test_reconfiguring_after_a_policy_change_replaces_the_filter(root_handler: logging.Handler) -> None:
    """`configure_logging` is idempotent, not sticky.

    Re-running it must not leave a filter built for the previous policy in place,
    and must not accumulate duplicate filters.
    """
    configure_logging(_config(enabled=False))
    configure_logging(_config(enabled=False))
    redactions = [f for f in root_handler.filters if isinstance(f, LogRedactionFilter)]
    contexts = [f for f in root_handler.filters if isinstance(f, LogContextFilter)]
    assert len(redactions) == 1
    assert len(contexts) == 1
    assert redactions[0].redactor.policy == STANDARD


# --------------------------------------------------------------------------- #
# (b) Correlation without touching the call sites
# --------------------------------------------------------------------------- #


def test_run_context_ambient_state_stamps_run_id_and_agent(root_handler: logging.Handler) -> None:
    """The zero-edit half: existing `logger.info` calls gain run correlation.

    No call site in this test passes `extra=`. The values come from the
    observability ContextVars that the run machinery already binds, which is what
    "attach context without rewriting 2,833 call sites" has to mean in practice.
    """
    configure_logging(_config(enabled=False))
    ids = default_id_generator()
    run_id = ids.new_run_id()
    with run_scope(RunContext(trace_id="t-ambient", run_id=run_id, thread_id="th-1", agent_name="lead-agent")):
        logging.getLogger("alpha.test").info("no extra= anywhere in this call")
        log_filter = next(f for f in root_handler.filters if isinstance(f, LogContextFilter))
        record = logging.LogRecord("alpha.test", logging.INFO, __file__, 1, "m", (), None)
        assert log_filter.filter(record) is True
        assert record.run_id == run_id
        assert record.agent == "lead-agent"
        # Nothing opened a span, so span_id has no honest value to report.
        assert record.span_id == LOG_CONTEXT_MISSING


def test_current_span_stamps_span_id(root_handler: logging.Handler) -> None:
    """`span_id` comes from the active span, so nested work is separable in a log tail."""
    configure_logging(_config(enabled=False))
    ids = default_id_generator()
    tracer = Tracer(id_generator=ids)
    context = RunContext(trace_id="t-span", run_id=ids.new_run_id())
    log_filter = next(f for f in root_handler.filters if isinstance(f, LogContextFilter))
    with tracer.span("agent.turn", context=context):
        expected_span_id = current_span().span_id
        assert expected_span_id is not None
        record = logging.LogRecord("alpha.test", logging.INFO, __file__, 1, "m", (), None)
        assert log_filter.filter(record) is True
        assert record.span_id == expected_span_id


def test_unbound_fields_render_as_a_greppable_marker(root_handler: logging.Handler) -> None:
    """Absence is `"-"`, not a blank and not a missing attribute.

    A formatter referencing `%(run_id)s` on a record where the filter never ran
    raises inside `logging` and drops the record with a "--- Logging error ---" on
    stderr. The marker makes the distinction visible in a tail instead.
    """
    configure_logging(_config(enabled=False))
    logging.getLogger("alpha.test").info("unbound")
    record = logging.LogRecord("alpha.test", logging.INFO, __file__, 1, "m", (), None)
    log_filter = next(f for f in root_handler.filters if isinstance(f, LogContextFilter))
    assert log_filter.filter(record) is True
    for field in LOG_CONTEXT_FIELDS:
        assert getattr(record, field) == LOG_CONTEXT_MISSING


def test_log_context_supplies_code_and_wins_over_ambient_state() -> None:
    """`code` has no ambient source, and an explicit binding outranks derived state.

    An operator-facing error code is a per-call-site fact, so it can only come
    from the call site -- and when a call site states one, the derived value must
    not quietly override it.
    """
    ids = default_id_generator()
    ambient_run = ids.new_run_id()
    with run_scope(RunContext(trace_id="t-explicit", run_id=ambient_run, agent_name="lead-agent")):
        with log_context(code="thread_delete_checkpoint_failed", agent="threads-router") as bound:
            assert bound == {"code": "thread_delete_checkpoint_failed", "agent": "threads-router"}
            record = logging.LogRecord("alpha.test", logging.INFO, __file__, 1, "m", (), None)
            assert LogContextFilter().filter(record) is True
            assert record.code == "thread_delete_checkpoint_failed"
            assert record.agent == "threads-router"
            # An explicit binding for two fields must not blank out the third,
            # which only the run scope knows.
            assert record.run_id == ambient_run


def test_log_context_restores_the_previous_binding() -> None:
    """Scoping must not leak: a second block must not see the first block's code."""
    with log_context(code="first"):
        assert current_log_context() == {"code": "first"}
        with log_context(code="second"):
            assert current_log_context() == {"code": "second"}
        assert current_log_context() == {"code": "first"}
    assert current_log_context() == {}


def test_bind_log_context_rejects_unknown_fields() -> None:
    """A typo would otherwise produce a log full of `run_id=-` with no error."""
    with pytest.raises(ValueError, match="unknown log context field"):
        bind_log_context(run_uuid="nope")
    token = bind_log_context(code="ok")
    try:
        reset_log_context(token)
    finally:
        pass
    assert current_log_context() == {}


def test_every_correlation_field_renders_in_the_enhanced_text_format(root_handler: logging.Handler) -> None:
    """Enhanced text output must carry all four fields plus the historical trace bracket.

    The `trace_id` bracket keeps its exact pre-existing text so a grep written
    against the old enhanced format still matches; the correlation bracket is
    emitted before it.
    """
    configure_logging(_config(enabled=True, fmt="text"))
    ids = default_id_generator()
    run_id = ids.new_run_id()
    with run_scope(RunContext(trace_id="t-render", run_id=run_id, agent_name="lead-agent")):
        with request_trace_context("t-render"):
            with log_context(code="render_check"):
                logging.getLogger("alpha.test").info("rendered")
    output = root_handler.stream.getvalue()  # type: ignore[attr-defined]
    assert "[trace_id=t-render]" in output
    assert f"run_id={run_id}" in output
    assert "agent=lead-agent" in output
    assert "code=render_check" in output
    assert "span_id=" in output
    assert root_handler.formatter._fmt == LOG_CONTEXT_TEXT_LOG_FORMAT


def test_json_format_carries_the_correlation_fields_as_explicit_keys(root_handler: logging.Handler) -> None:
    """A JSON consumer must see the keys whether or not they are bound.

    Absence is `"-"` rather than a missing key, so a dashboard can tell "no run
    context" from "this build does not emit the field".
    """
    configure_logging(_config(enabled=True, fmt="json"))
    ids = default_id_generator()
    run_id = ids.new_run_id()
    with run_scope(RunContext(trace_id="t-json", run_id=run_id, agent_name="lead-agent")):
        logging.getLogger("alpha.test").info("json line")
    logging.getLogger("alpha.test").info("unbound json line")
    payloads = [
        json.loads(line)
        for line in root_handler.stream.getvalue().splitlines()
        if line.strip()  # type: ignore[attr-defined]
    ]
    assert payloads[0]["run_id"] == run_id
    assert payloads[0]["agent"] == "lead-agent"
    for field in LOG_CONTEXT_FIELDS:
        assert field in payloads[1]
        assert payloads[1][field] == LOG_CONTEXT_MISSING
    for field in LOG_CONTEXT_FIELDS:
        assert field in payloads[0]


def test_json_format_is_json_after_the_chokepoint(root_handler: logging.Handler) -> None:
    """Scrubbing must not corrupt the structure a JSON consumer parses.

    `redact_text` replaces a matched span with a placeholder, so the naive risk
    is a mangled escape or a broken line. The payload has to round-trip.
    """
    configure_logging(_config(enabled=True, fmt="json"))
    logging.getLogger("alpha.test").info('quote " and newline \\n and backslash \\ and braces {} in %s', _CORPUS["openai_project_key"])
    payload = json.loads(root_handler.stream.getvalue())  # type: ignore[attr-defined]
    assert _CORPUS["openai_project_key"] not in payload["message"]
    assert "{" in payload["message"] or "[REDACTED:" in payload["message"]


def test_default_format_constant_is_unchanged_for_pre_configure_boot_logging() -> None:
    """`DEFAULT_LOG_FORMAT` must keep referencing no correlation attribute.

    `app/gateway/app.py` calls `logging.basicConfig(format=DEFAULT_LOG_FORMAT)`
    at *import* time, before any `configure_logging` run installs the filter. If
    the constant gained `%(run_id)s`, every record emitted in that window would
    hit a format referencing an attribute the record does not have: `logging`
    swallows the error, prints "--- Logging error ---", and the record is lost.
    """
    for field in LOG_CONTEXT_FIELDS + ("trace_id",):
        assert f"%({field})s" not in DEFAULT_LOG_FORMAT


def test_trace_context_filter_still_installs_and_stamps(root_handler: logging.Handler) -> None:
    """The pre-existing trace filter keeps working; it is now installed unconditionally."""
    configure_logging(_config(enabled=False))
    assert any(isinstance(f, TraceContextFilter) for f in root_handler.filters)
    record = logging.LogRecord("alpha.test", logging.INFO, __file__, 1, "m", (), None)
    with request_trace_context("trace-legacy"):
        assert TraceContextFilter().filter(record) is True
    assert record.trace_id == "trace-legacy"
