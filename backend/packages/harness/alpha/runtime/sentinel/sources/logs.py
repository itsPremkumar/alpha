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

# Severity is inferred from the line itself. Checked worst-first.
_SEVERITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ModuleNotFoundError|ImportError|SyntaxError|NameError|AttributeError", re.I), "critical"),
    (re.compile(r"Traceback \(most recent call last\)", re.I), "critical"),
    (re.compile(r"\bFATAL\b|\bPANIC\b|SystemExit", re.I), "critical"),
    (re.compile(r"^FAILED|\bfailed\b|\bERROR\b|\bError\b", re.I), "high"),
    (re.compile(r"\bWARNING\b|DeprecationWarning", re.I), "low"),
)

# Kind is a coarse classifier used to route the signal to a repair strategy.
_KIND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ModuleNotFoundError|ImportError|No module named", re.I), "import_error"),
    (re.compile(r"SyntaxError|IndentationError", re.I), "syntax_error"),
    (re.compile(r"\bAssertionError\b", re.I), "test_failure"),
    # Word boundaries matter: with re.I a bare "ERROR" also matches the "Error"
    # inside ConnectionRefusedError, which would misroute a connectivity fault
    # into the test-repair path.
    (re.compile(r"^FAILED|\bFAILED\b|\bERROR\b", re.I), "test_failure"),
    (re.compile(r"ENOENT|FileNotFoundError", re.I), "missing_file"),
    (re.compile(r"EACCES|PermissionError", re.I), "permission_error"),
    (re.compile(r"ConnectionRefused|ConnectionError|timeout|timed out", re.I), "connectivity"),
    (re.compile(r"WARNING|DeprecationWarning", re.I), "warning"),
)

_TRACEBACK_START = re.compile(r"Traceback \(most recent call last\)")
# WARNING is included so warnings surface at all, but classify_severity ranks
# them "low" and the severity sort pushes them to the back of the queue.
_ERRORISH = re.compile(
    r"^FAILED|\bFAILED\b|\bERROR\b|\bError\b|Traceback|ModuleNotFoundError|"
    r"ImportError|SyntaxError|SystemExit|FileNotFoundError|PermissionError|"
    r"ConnectionRefused|timed out|\bWARNING\b",
    re.I,
)

_MAX_CONTEXT_LINES = 12


def classify_severity(line: str) -> str:
    for pattern, severity in _SEVERITY_PATTERNS:
        if pattern.search(line):
            return severity
    return "medium"


def classify_kind(line: str) -> str:
    for pattern, kind in _KIND_PATTERNS:
        if pattern.search(line):
            return kind
    return "unknown"


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

        if _ERRORISH.search(line):
            signals.append(
                Signal(
                    source=SOURCE,
                    kind=classify_kind(line),
                    message=line.strip(),
                    severity=classify_severity(line),
                    context={"origin": origin, "line_no": i + 1},
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
