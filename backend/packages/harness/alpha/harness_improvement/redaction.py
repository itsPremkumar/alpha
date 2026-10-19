"""Secret scrubbing for traces, evidence and anything a model editor sees.

Two boundaries use this module, for two different reasons:

* **Persistence** — a trace or evidence bundle written to disk must not carry
  API keys, bearer tokens, cookies or private key material, because the
  experience library outlives the task that produced it.
* **Editor exposure** — an LLM editor receives baseline evidence to write a
  proposal; whatever the trace captured must be redacted *before* it is
  serialized into that context, so a prompt-injected tool output cannot smuggle
  a credential into a candidate diff.

The scrubber is deliberately pattern-based and conservative: it replaces a
matched secret with ``[REDACTED:<kind>]`` and records the *kind* and location,
never the value. False positives cost a little evidence; false negatives leak a
credential into a durable store, and the asymmetry decides the design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Pattern table: (kind, compiled regex). Order matters only in that more
#: specific patterns should run first so the reported kind is the most precise
#: one. Each pattern must capture the whole secret value so the replacement can
#: remove it entirely.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # OpenAI/Anthropic/OpenRouter-style keys and common prefixes.
    ("api_key", re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|sk-or-v1-[A-Za-z0-9]{8,}|ak-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[A-Za-z0-9_-]{20,})")),
    # Authorization headers and query/payload fields.
    ("bearer_token", re.compile(r"(?i)\b(?:authorization\s*[:=]\s*bearer\s+|bearer\s+)([A-Za-z0-9._~+/=-]{20,})")),
    ("password_field", re.compile(r"(?i)\b(?:password|passwd|secret|client_secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*[\"']?([^\s\"',;]{6,})[\"']?")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    # Cookies only when the value is long enough to plausibly be a session.
    ("cookie", re.compile(r"(?i)\b(?:set-cookie|cookie)\s*:\s*([^\n]{16,})")),
    # Environment-variable assignments that look like credentials.
    ("env_secret", re.compile(r"\b([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*)\s*=\s*[\"']?([^\s\"']{8,})[\"']?")),
)

#: File-name shaped references to credential stores. The scrubber reports these
#: as `credential_path` findings but does not try to rewrite the text: a path in
#: a script is an instruction, not a value, and the patch validator turns the
#: finding into a refusal before the content is ever stored.
_CREDENTIAL_PATH = re.compile(r"(?i)(?:^|[\s\"'(=:@])((?:~?/?\.?(?:ssh|aws|gnupg)/[^\s\"')]+)|(?:[^\s\"')/]*(?:id_rsa|id_ed25519|\.env|\.netrc|credentials(?:\.json)?|cookies?\.(?:txt|sqlite))(?:[^\s\"')]*)))")

_REDACTED = "[REDACTED:{kind}]"


@dataclass(frozen=True, slots=True)
class RedactionFinding:
    """One redaction, described without the value it removed."""

    kind: str
    placeholder: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "placeholder": self.placeholder}


def redact_text(text: str) -> tuple[str, list[RedactionFinding]]:
    """Return ``(clean_text, findings)`` with every matched secret replaced.

    The findings list never contains the secret value, so the result is safe to
    persist verbatim.
    """
    findings: list[RedactionFinding] = []
    clean = text
    for kind, pattern in _SECRET_PATTERNS:

        def _replace(match: re.Match[str], _kind: str = kind) -> str:
            placeholder = _REDACTED.format(kind=_kind)
            findings.append(RedactionFinding(kind=_kind, placeholder=placeholder))
            # Replace only the secret group when the pattern defines one; a
            # whole-match pattern (private keys) is replaced entirely.
            if match.groups():
                start, end = match.span(1)
                whole_start = match.start()
                relative_start = start - whole_start
                relative_end = end - whole_start
                whole = match.group(0)
                return whole[:relative_start] + placeholder + whole[relative_end:]
            return placeholder

        clean = pattern.sub(_replace, clean)
    return clean, findings


def redact_mapping(payload: Any) -> tuple[Any, list[RedactionFinding]]:
    """Recursively redact every string in a JSON-shaped payload."""
    findings: list[RedactionFinding] = []

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            clean, found = redact_text(value)
            findings.extend(found)
            return clean
        if isinstance(value, dict):
            return {str(k): _walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_walk(v) for v in value]
        return value

    return _walk(payload), findings


def scan_for_credential_paths(text: str) -> list[str]:
    """Return credential-store path references found in *text*.

    Used by the patch validator to refuse a candidate script/skill that tries to
    read ``.env``, SSH keys, browser cookies or cloud credential files.
    """
    seen: list[str] = []
    for match in _CREDENTIAL_PATH.finditer(text):
        found = match.group(1)
        if found and found not in seen:
            seen.append(found)
    return seen


def scan_for_network_commands(text: str) -> list[str]:
    """Return network-capable command invocations found in *text*.

    Generated scripts run under a deny-by-default network policy
    (``sandbox.network_mode``), so a script that *needs* the network must say so
    through an explicit, reviewable component edit — not by quietly shelling out
    to ``curl``. This is a screen, not a sandbox: it fails closed at validation
    time, and the sandbox remains the enforcement point.
    """
    pattern = re.compile(
        r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|nc|ncat|netcat|telnet|ssh|scp|sftp|ftp)\b",
        re.IGNORECASE,
    )
    seen: list[str] = []
    for match in pattern.finditer(text):
        token = match.group(1).lower()
        if token not in seen:
            seen.append(token)
    return seen
