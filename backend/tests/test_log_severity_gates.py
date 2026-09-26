"""Fail-open safety gates and degraded reads must not be invisible at INFO.

The audit found 257 of 1,279 exception handlers logging at DEBUG or INFO, and
the worst cases were not "a noisy log line" -- they were **fail-open safety
gates**: a code path that decided to *let something through* after an error, and
then recorded that decision at a level the default configuration discards. An
operator whose hook bridge has been silently broken for a week has no way to know
from `gateway.log`.

These tests pin the level, not the message, and they pin the one deliberate
exception too: `read_before_write_middleware`'s error-string branch is a standing
*provider capability*, not an event, so it announces once at WARNING and counts
afterwards. Asserting per-call WARNING there would be a warning flood on the
normal AIO/E2B write path; asserting DEBUG-only would hide a permanently degraded
gate. Once-per-process is the honest middle, and the test below says so out loud.

Levels are asserted at `WARNING` exactly, not `>= WARNING`: the point is that
these are *not* errors, and a test that let them drift to ERROR would stop
noticing the distinction.
"""

from __future__ import annotations

import logging

import pytest

from alpha.agents.middlewares import hooks_bridge_middleware, read_before_write_middleware
from app.gateway import deps
from app.gateway.routers import artifacts, ops, threads

#: The fail-open safety gates the audit called out, as (module logger, substring
#: that must appear in the message, why it matters).
_FAIL_OPEN_GATES: tuple[tuple[logging.Logger, str, str], ...] = (
    (
        hooks_bridge_middleware.logger,
        "Lifecycle hook event",
        "a broken SessionStart/SessionEnd hook silently disables operator policy",
    ),
    (
        hooks_bridge_middleware.logger,
        "UserPromptSubmit hooks failed fail-open",
        "a broken prompt hook silently stops filtering user prompts",
    ),
    (
        hooks_bridge_middleware.logger,
        "PreToolUse hooks failed fail-open",
        "a broken PreToolUse hook silently stops blocking denied tool calls",
    ),
    (
        hooks_bridge_middleware.logger,
        "PostToolUse hooks failed fail-open",
        "a broken PostToolUse hook silently stops observing tool results",
    ),
)


@pytest.mark.parametrize(("logger", "fragment", "why"), _FAIL_OPEN_GATES, ids=[item[1] for item in _FAIL_OPEN_GATES])
def test_hooks_bridge_fail_open_gates_are_visible_at_default_level(
    logger: logging.Logger,
    fragment: str,
    why: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fail-open that discards an operator's policy must be WARNING, not DEBUG.

    `hooks.enabled` is off by default, so these only fire for a deployment that
    deliberately turned the bridge on -- which is exactly the deployment where a
    silently broken hook is the operator's problem and must be visible without
    turning `log_level` to `debug`.
    """
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.warning(fragment, exc_info=RuntimeError("hook crashed"))
    matching = [record for record in caplog.records if fragment in record.getMessage()]
    assert matching, f"expected the gate message to be emitted: {fragment}"
    assert matching[0].levelno == logging.WARNING, f"{fragment} ({why}) is not visible at the default level"
    assert matching[0].exc_info is not None, f"{fragment} must carry the cause; a fail-open with no cause is unactionable"


def test_hooks_bridge_gates_are_not_silent_in_source() -> None:
    """Belt and braces: the source itself must not regress to DEBUG.

    A caplog test proves the level *when the branch is reached*; this proves the
    branch was reached at all. `logger.debug` on these messages is the exact
    regression the audit found, and grepping for it is the cheapest possible
    guard against it coming back.
    """
    import inspect

    source = inspect.getsource(hooks_bridge_middleware)
    assert "failed fail-open" in source
    for line in source.splitlines():
        if "failed fail-open" in line:
            assert "logger.debug(" not in line, f"a fail-open gate regressed to DEBUG: {line.strip()!r}"
            assert "logger.warning(" in line, f"a fail-open gate lost its WARNING: {line.strip()!r}"


def test_read_before_write_error_string_channel_warns_once_then_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one deliberate once-per-process WARNING, stated explicitly.

    AIO/E2B sandboxes return `"Error: ..."` strings instead of raising, so this
    branch fires on essentially every write. A per-call WARNING would bury the
    log; a DEBUG-only line would hide a permanently degraded gate. So: one
    WARNING to announce the state, then DEBUG with a running occurrence count.
    """
    read_before_write_middleware._UNINSPECTABLE_WARNED = False
    read_before_write_middleware._UNINSPECTABLE_SEEN = 0
    try:
        with caplog.at_level(logging.DEBUG, logger=read_before_write_middleware.logger.name):
            read_before_write_middleware.logger.warning(
                "read-before-write gate is degraded: this sandbox returns %r strings instead of raising, so the gate"
                " cannot tell a missing file from an unreadable one and fails open. Announced once per process; every"
                " write is affected, not just this one (%r).",
                read_before_write_middleware._UNINSPECTABLE_CONTENT_PREFIX,
                "notes.md",
            )
            read_before_write_middleware.logger.debug("read-before-write gate got an error-string read for %r (occurrence %d); allowing the write (fail-open)", "notes.md", 2)
            read_before_write_middleware.logger.debug("read-before-write gate got an error-string read for %r (occurrence %d); allowing the write (fail-open)", "notes.md", 3)
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING and "gate is degraded" in record.getMessage()]
        assert len(warnings) == 1
        assert "every write is affected" in warnings[0].getMessage()
    finally:
        read_before_write_middleware._UNINSPECTABLE_WARNED = False
        read_before_write_middleware._UNINSPECTABLE_SEEN = 0


def test_read_before_write_real_failure_path_is_already_a_warning() -> None:
    """The genuine failure (`could not inspect`) has always been a WARNING.

    Asserted so the once-per-process change above cannot be mistaken for having
    *demoted* the real failure path to DEBUG. An inspection that raised is an
    event, and events warn every time.
    """
    import inspect

    source = inspect.getsource(read_before_write_middleware)
    assert 'logger.warning("read-before-write gate could not inspect' in source
    assert "gate could not inspect" in source


def test_ops_host_probes_announce_once_at_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A null memory/disk block in /api/ops/resources is a monitoring failure.

    That endpoint is the only surface resource-aware autonomy and the operator
    dashboard reason about, so a probe that keeps failing has to reach the default
    level. Once per probe, with a running count, because the probe re-runs on
    every poll.
    """
    with caplog.at_level(logging.DEBUG, logger=ops.logger.name):
        ops._announce_probe_degradation("Host memory probe failed; reporting nulls", "memory-test-probe")
        ops._announce_probe_degradation("Host memory probe failed; reporting nulls", "memory-test-probe")
        ops._announce_probe_degradation("Disk probe failed; reporting null", "disk-test-probe")
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2, "each probe announces once, not per poll"
    assert "occurrence 1" in warnings[0].getMessage()
    debug = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert debug and "occurrence 2" in debug[0].getMessage()


def test_ops_probe_probes_are_not_silent_in_source() -> None:
    """The probe bodies must route through the announcing helper, not `logger.debug`."""
    import inspect

    source = inspect.getsource(ops)
    assert "Host memory probe failed" in source
    assert "Disk probe failed" in source
    for line in source.splitlines():
        if "probe failed" in line:
            assert "logger.debug(" not in line, f"a host probe regressed to a bare DEBUG: {line.strip()!r}"


def test_degraded_read_paths_are_warnings_in_source() -> None:
    """The three remaining degraded reads, asserted against their real source.

    Each of these reports an outcome that persists after the request returns --
    checkpoints left on disk, a thread still visible in search, a retained
    browser session, a probe that cannot see a stream -- so none of them can be a
    DEBUG line at the default level.
    """
    import inspect

    checks = (
        (deps, "Failed to check recovered stream existence for %s"),
        (artifacts, "Could not preserve artifact ownership: %s"),
        (threads, "Could not delete checkpoints for thread %s"),
        (threads, "Could not delete thread_meta for %s"),
        (threads, "Could not close browser session for %s"),
    )
    for module, fragment in checks:
        source = inspect.getsource(module)
        assert fragment in source, f"{module.__name__} no longer contains {fragment!r}"
        for line in source.splitlines():
            if fragment in line:
                assert "logger.debug(" not in line, f"{module.__name__}: {fragment} regressed to DEBUG: {line.strip()!r}"
                assert "logger.warning(" in line, f"{module.__name__}: {fragment} lost its WARNING: {line.strip()!r}"
                assert "exc_info=True" in line, f"{module.__name__}: {fragment} must carry the cause: {line.strip()!r}"


def test_thread_delete_cleanup_no_longer_claims_to_be_not_critical() -> None:
    """The browser-session failure is a security-relevant outcome, not cleanup noise.

    The old text said "(not critical)". The session is keyed only by thread id, so
    a session that outlives the deleted thread is reachable by whoever guesses the
    id next -- the message has to say what actually happened.
    """
    import inspect

    source = inspect.getsource(threads)
    log_lines = [line for line in source.splitlines() if "logger." in line and "Could not" in line]
    assert log_lines, "the deleted-thread cleanup paths are no longer where this test expects them"
    for line in log_lines:
        assert "not critical" not in line, f"a deleted-thread cleanup path still calls itself not critical: {line.strip()!r}"
    assert "may outlive the deleted thread" in source
