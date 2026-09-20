"""Cron prompt guards: injection scan plus credential-exfil block.

Scheduled prompts assemble context from files, prior outputs, and operator
text. Every assembled prompt passes these pure checks before the run is
admitted: control-command smuggling is flagged, and anything shaped like a
credential fails closed.
"""

from __future__ import annotations

import re

_INSTRUCTION_OVERRIDE_RES = (
    re.compile(r"(?i)\b(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)\b"),
    re.compile(r"(?i)\b(system\s*prompt|you\s+are\s+now\s+a\s+different)\b"),
    re.compile(r"(?i)\b(exfiltrate|send\s+to\s+external|post\s+to\s+https?://)\b"),
)

_SECRET_SHAPES = (
    re.compile(r"(?i)\b(api[_-]?key|apikey|secret|passwd|password|private[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_.\-+/=]{8,}"),
    re.compile(r"\b(sk-[A-Za-z0-9]{8,}|xox[bpas]-[A-Za-z0-9-]{8,}|gh[pousr]_[A-Za-z0-9]{8,})\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def scan_prompt_injection(prompt: str) -> list[str]:
    """Return findings for control-smuggling patterns (empty = clean)."""
    text = prompt or ""
    return [f"injection pattern: {rx.pattern[:60]}" for rx in _INSTRUCTION_OVERRIDE_RES if rx.search(text)]


def contains_credential(prompt: str) -> bool:
    """True when the assembled prompt carries credential-shaped material."""
    text = prompt or ""
    return any(rx.search(text) for rx in _SECRET_SHAPES)


def guard_scheduled_prompt(prompt: str, *, max_chars: int = 20000) -> list[str]:
    """Fail-closed gate for one assembled cron prompt. Empty = admit."""
    findings: list[str] = []
    if len(prompt or "") > max_chars:
        findings.append(f"prompt exceeds {max_chars} chars; truncate context before admitting.")
    findings.extend(scan_prompt_injection(prompt))
    if contains_credential(prompt):
        findings.append("prompt contains credential-shaped material; replace with a broker reference.")
    return findings
