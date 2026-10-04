"""Log-file signal source.

Scans log files for failure lines and turns them into Signals. This is the
cheapest source to stand up because the repo already writes gateway, backend and
build logs to ``logs/*.txt``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from alpha.runtime.sentinel.signals import Signal

SOURCE = "logs"

# ---------------------------------------------------------------------------
# What counts as a report of a fault
# ---------------------------------------------------------------------------
#
# This scanner used to prefilter on `_ERRORISH`, a case-insensitive alternation
# of `failed|error|warning|timeout|traceback`, and then classify severity and
# kind by searching the line for the same bare words. Both steps were wrong in
# the same way: **a line that mentions a word is not a line that reports a
# fault.**
#
# Measured against this repository's own `logs/`, that design produced 103
# signals of which **0 were real**. 89 were pytest *parametrization ids* --
# `tests/test_goal_contracts.py::test_attempt_transition_matrix[pending-failed]`
# -- reported as high-severity `test_failure`, because `re.I` made `\bfailed\b`
# match the `[pending-failed]` label. Three more were "critical" for the same
# reason, one of them a test whose id literally contains the fixture string
# `"Error (ModuleNotFoundError): No module named 'bash'-"`. The rest were test
# *names* containing `failing`, `traceback` or `warning`.
#
# That is worse than an absent scanner. `test_failure` is the kind that routes
# to the test-repair strategy, so the sentinel was manufacturing 89 phantom
# high-severity incidents aimed at the auto-repair path, and burying the real
# ones. The rules below only accept a **structural** report of a fault.
#
# The four accepted forms:
#   1. a traceback header,
#   2. an exception class name followed by a colon (`ValueError: ...`),
#   3. a structured log level in the record Alpha itself writes
#      (`<ts> - <logger> - ERROR - msg`),
#   4. a test-runner's own failure report (`FAILED tests/...`, `E   assert`,
#      `=== FAILURES ===`, `1 failed, 300 passed`).

_TRACEBACK_START = re.compile(r"^\s*Traceback \(most recent call last\)")

#: An exception class name at a token boundary, immediately followed by a colon.
#: The trailing colon is what distinguishes a *raised* class from a mention of
#: it: `ConnectionRefusedError: [Errno 111]` is a fault, while a docstring that
#: says "raises ConnectionRefusedError" is not.
_EXCEPTION_CLASS = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*(?:Error|Exception))\s*:")

#: Alpha's own log-record shape, e.g.
#: ``2026-10-04 15:35:59 - alpha.config.app_config - WARNING - message``.
_LOG_LEVEL = re.compile(r"\s-\s(?:CRITICAL|ERROR|WARNING)\s-\s")

#: Test-runner report forms. Anchored at line start so a node id that merely
#: contains the word cannot match.
_RUNNER_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+\S+")
_RUNNER_SHORT_SUMMARY = re.compile(r"^E{1,4}\s+(?:assert\b|[A-Za-z_]*(?:Error|Exception)\b)")
_RUNNER_BANNER = re.compile(r"^=+\s+(?:FAILURES|ERRORS)\s+=+")
_RUNNER_TALLY = re.compile(r"^\s*\d+\s+(?:failed|error|errors)\b")

#: A test node id: ``tests/test_x.py::Class::test_name[param-label]``.
#:
#: A node id is a *name*, never a report. This has to be an explicit rule
#: because a parametrization label is free-form text: this repository contains
#: ``...::test_every_spec_class_is_reachable[TimeoutError: model timeout]``, which
#: the exception-class rule alone reads as a raised ``TimeoutError``. Such a line
#: is a *list of tests that exist*. The runner's own report forms
#: (``FAILED <node id>``) are matched before this and are genuine.
_NODE_ID = re.compile(r"^\s*[\w./\\-]*\.py::\S")

#: Exception class -> signal kind. The class name *is* the diagnosis, so it is
#: the only input; a word scan over the whole line cannot tell
#: ``AssertionError: assert 1 == 2`` from ``[pending-failed]``.
_EXCEPTION_KINDS: dict[str, str] = {
    "ModuleNotFoundError": "import_error",
    "ImportError": "import_error",
    "SyntaxError": "syntax_error",
    "IndentationError": "syntax_error",
    "TabError": "syntax_error",
    "AssertionError": "test_failure",
    "FileNotFoundError": "missing_file",
    "NotADirectoryError": "missing_file",
    "IsADirectoryError": "missing_file",
    "PermissionError": "permission_error",
    "ConnectionError": "connectivity",
    "ConnectionRefusedError": "connectivity",
    "ConnectionResetError": "connectivity",
    "TimeoutError": "connectivity",
    "socket.timeout": "connectivity",
}

#: Exception classes that end the process. Checked before the generic class rule.
#: These do not end in ``Error``/``Exception``, so they need their own shape --
#: dropping them silently lost real ``SystemExit: 1`` detection when the
#: word-scanning rules were removed.
_FATAL_CLASSES = frozenset({"SystemExit", "SystemExitException", "KeyboardInterrupt"})
_FATAL_CLASS = re.compile(r"(?<![\w.])(SystemExit|KeyboardInterrupt)\s*:")

#: Warning classes are real diagnostics but never high severity.
_WARNING_CLASS = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*Warning)\s*:")

_MAX_CONTEXT_LINES = 12


def exception_class_in(line: str) -> str | None:
    """The exception class a line reports raising, if it reports one."""
    match = _EXCEPTION_CLASS.search(line)
    if match:
        return match.group(1)
    fatal = _FATAL_CLASS.search(line)
    return fatal.group(1) if fatal else None


def is_fault_report(line: str) -> bool:
    """True when ``line`` is a *report* of a fault rather than a mention of one."""
    # Runner report forms first: `FAILED tests/x.py::y - ValueError: z` is a real
    # report even though it contains a node id.
    if _RUNNER_FAILED.search(line) or _RUNNER_SHORT_SUMMARY.search(line):
        return True
    if _RUNNER_BANNER.search(line) or _RUNNER_TALLY.search(line):
        return True
    # A bare node id is the *name* of a test, never its result.
    if _NODE_ID.match(line):
        return False
    if _TRACEBACK_START.search(line):
        return True
    if _EXCEPTION_CLASS.search(line) or _WARNING_CLASS.search(line) or _FATAL_CLASS.search(line):
        return True
    if _LOG_LEVEL.search(line):
        return True
    return False


def classify_severity(line: str) -> str:
    """Severity of a line already accepted by :func:`is_fault_report`.

    Kept total for any input so a direct caller cannot get an exception, but a
    line that reports nothing is ``medium`` -- there is no evidence to rank it
    by, and inventing ``high`` is how prose became an incident.
    """
    if _TRACEBACK_START.search(line):
        return "critical"
    exc = exception_class_in(line)
    if exc in _FATAL_CLASSES:
        return "critical"
    if exc:
        return "high"
    if re.search(r"\s-\sCRITICAL\s-\s", line) or re.search(r"\b(?:FATAL|PANIC)\b", line):
        return "critical"
    if re.search(r"\s-\sERROR\s-\s", line):
        return "high"
    if _WARNING_CLASS.search(line) or re.search(r"\s-\sWARNING\s-\s", line):
        return "low"
    if _RUNNER_FAILED.search(line) or _RUNNER_SHORT_SUMMARY.search(line):
        return "high"
    if _RUNNER_BANNER.search(line) or _RUNNER_TALLY.search(line):
        return "high"
    return "medium"


def classify_kind(line: str) -> str:
    """Coarse kind used to route the signal to a repair strategy."""
    # A runner report outranks a class name embedded in it: `FAILED x.py::y -
    # ValueError: z` is a test failure that happens to mention a class, and
    # routing it to the generic `unknown` handler would lose the repair target.
    if _RUNNER_FAILED.search(line) or _RUNNER_TALLY.search(line) or _RUNNER_BANNER.search(line):
        return "test_failure"
    exc = exception_class_in(line)
    if exc is not None:
        return _EXCEPTION_KINDS.get(exc, "unknown")
    if _TRACEBACK_START.search(line):
        return "unknown"
    if _RUNNER_SHORT_SUMMARY.search(line):
        return _classify_short_summary(line)
    if _WARNING_CLASS.search(line):
        return "warning"
    if _LOG_LEVEL.search(line):
        return "warning" if re.search(r"\s-\sWARNING\s-\s", line) else "unknown"
    return "unknown"


def _classify_short_summary(line: str) -> str:
    """``E   ValueError: ...`` -- reuse the class-name mapping when present."""
    tail = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else line
    exc = exception_class_in(tail)
    return _EXCEPTION_KINDS.get(exc, "unknown") if exc else "test_failure"


def scan_text(text: str, *, origin: str = "<text>") -> list[Signal]:
    """Extract Signals from log text."""
    lines = text.splitlines()
    signals: list[Signal] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if _TRACEBACK_START.search(line):
            # Capture the traceback through its final exception line.
            block: list[str] = [line]
            j = i + 1
            while j < len(lines) and j - i <= _MAX_CONTEXT_LINES:
                block.append(lines[j])
                # The exception line ends the traceback.
                if re.match(r"^\s*\S*(Error|Exception|Exit)\b", lines[j]):
                    break
                j += 1
            body = "\n".join(block)
            last = block[-1].strip() or line
            signals.append(
                Signal(
                    source=SOURCE,
                    kind=classify_kind(last),
                    message=last,
                    severity="critical",
                    context={"origin": origin, "line_no": i + 1, "excerpt": body},
                )
            )
            i = j + 1
            continue

        if is_fault_report(line):
            context: dict[str, Any] = {"origin": origin, "line_no": i + 1}
            exc = exception_class_in(line)
            if exc:
                # Carry the class so an operator sees *what* was raised rather
                # than re-deriving it from prose.
                context["exception"] = exc
            signals.append(
                Signal(
                    source=SOURCE,
                    kind=classify_kind(line),
                    message=line.strip(),
                    severity=classify_severity(line),
                    context=context,
                )
            )
        i += 1

    return signals


def scan_file(path: str | Path, *, max_bytes: int = 2_000_000) -> list[Signal]:
    """Extract Signals from one log file.

    Reads only the tail when the file is huge, so a multi-hundred-MB gateway log
    cannot stall the scan.
    """
    p = Path(path)
    if not p.is_file():
        return []

    data = p.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    text = data.decode("utf-8", errors="replace")
    return scan_text(text, origin=str(p))


def scan_directory(
    directory: str | Path,
    *,
    pattern: str = "*.txt",
    max_bytes: int = 2_000_000,
) -> list[Signal]:
    """Extract Signals from every matching log file in a directory."""
    root = Path(directory)
    if not root.is_dir():
        return []
    signals: list[Signal] = []
    for p in sorted(root.glob(pattern)):
        if p.is_file():
            signals.extend(scan_file(p, max_bytes=max_bytes))
    return signals


def from_records(records: list[dict[str, Any]]) -> list[Signal]:
    """Build Signals from already-parsed log records (e.g. a JSON log stream)."""
    out: list[Signal] = []
    for r in records:
        msg = str(r.get("message") or r.get("msg") or "").strip()
        if not msg:
            continue
        out.append(
            Signal(
                source=SOURCE,
                kind=str(r.get("kind") or classify_kind(msg)),
                message=msg,
                severity=str(r.get("severity") or classify_severity(msg)),
                context=dict(r.get("context") or {}),
            )
        )
    return out
