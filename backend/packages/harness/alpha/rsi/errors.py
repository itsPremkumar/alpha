"""§54 failure taxonomy for RSI decisions: RSI-E001–E018 + the designated unknown code.

Architecture spec §54 (``references/RSI_AGENT_ARCHITECTURE.md``)::

    Use standard failure codes:

    RSI-E001 Provider failure
    RSI-E002 Network failure
    RSI-E003 Candidate patch failure
    RSI-E004 Build failure
    RSI-E005 Unit failure
    RSI-E006 Integration failure
    RSI-E007 Benchmark regression
    RSI-E008 Security violation
    RSI-E009 Evaluator tamper
    RSI-E010 Resource exhaustion
    RSI-E011 Startup failure
    RSI-E012 Canary regression
    RSI-E013 Checkpoint/state failure
    RSI-E014 Policy violation
    RSI-E015 Non-reproducible result
    RSI-E016 Insufficient evidence
    RSI-E017 Duplicate candidate
    RSI-E018 Promotion conflict

    Provider outage should not automatically count as a candidate defect.

:data:`RSI_ERROR_CODES` is that list LITERALLY, plus ONE addition this build
designates and discloses: **RSI-E000 "Unclassified failure"** — the spec
defines only E001–E018, so a failure matching no documented rule classifies
as E000 (unknown) instead of being forced into a wrong code. E000 is this
module's own extension and is never attributed to the spec.

Classification uses REAL signals only — the exception types and reason texts
the landed modules in the promotion path actually produce:

* :func:`classify` — one exception (or free text) → its code, via
  :func:`_classify_text`: named real classes first — a genuine
  ``alpha.skills.security_static_scanner.StaticScanBlockedError`` (imported
  and ``isinstance``-checked, not name-matched) is always RSI-E008, a real
  :class:`alpha.rsi.releases.ReleaseStoreError` splits E018 (id conflict on
  an immutable directory) from E013 (everything store-shaped) — then ordered
  message keywords grounded in landed texts (``holdout regressions`` → E007,
  ``corrupt``/``mismatch`` → E013, ``approval``/``rejected`` → E014,
  ``unverified``/``missing``/``no bundle directory`` → E016 …), then real
  Python exception classes (FileNotFoundError → E016, OSError/JSONDecodeError
  → E013, ConnectionError → E002, MemoryError → E010), else **RSI-E000**.
  Rule order is specificity: the earliest matching rule wins. The spec's
  closing note is honored structurally — E001/E002 exist precisely so a
  provider/network outage reads as itself and never as a candidate defect.
  ``TimeoutError`` has NO §54 code; it lands E000 (admitted, not misfiled).
* :func:`classify_gate` — one FAILING promotion-gate entry → its code, or
  ``None``. Gate context disambiguates the same words the way the spec means
  them: a failed ``holdout`` gate reads ``holdout regressions`` → E007 while a
  missing/unverifiable payload → E016 and a corrupt file → E013; a failed
  ``human_review`` gate is always E014 (promotion blocked by policy — pending
  or rejected); an active §42 window is E018 (the new promotion conflicts
  with it). ``None`` is returned ONLY for the derived ``not routed: …``
  disclosure: its root failing gates carry the codes, and inventing a second
  code for a consequence would double-count one failure.
* :func:`describe` / :func:`annotate` — the spec's own label for a code, and
  the `` [RSI-Exxx]`` suffix ``alpha.rsi.promotion.decide`` appends to each
  FAILING segment of ``decision.reason``. Gate entries stay byte-for-byte
  verbatim (the C2c passthrough contract pinned in tests); only the composed
  reason and the WARNING logs carry codes.

No fabricated codes: if no rule matches, the answer is RSI-E000 with this
module's honest label — never a guess, never silence, never a bare except.
"""

from __future__ import annotations

import re

from alpha.rsi.releases import ReleaseStoreError
from alpha.skills.security_static_scanner import StaticScanBlockedError

__all__ = ["RSI_ERROR_CODES", "annotate", "classify", "classify_gate", "describe"]

#: The spec §54 taxonomy LITERALLY + this build's designated unknown code
#: (E000 — the spec defines E001–E018 only; see the module docstring).
RSI_ERROR_CODES: dict[str, str] = {
    "RSI-E000": "Unclassified failure",  # designated by this module, not by the spec
    "RSI-E001": "Provider failure",
    "RSI-E002": "Network failure",
    "RSI-E003": "Candidate patch failure",
    "RSI-E004": "Build failure",
    "RSI-E005": "Unit failure",
    "RSI-E006": "Integration failure",
    "RSI-E007": "Benchmark regression",
    "RSI-E008": "Security violation",
    "RSI-E009": "Evaluator tamper",
    "RSI-E010": "Resource exhaustion",
    "RSI-E011": "Startup failure",
    "RSI-E012": "Canary regression",
    "RSI-E013": "Checkpoint/state failure",
    "RSI-E014": "Policy violation",
    "RSI-E015": "Non-reproducible result",
    "RSI-E016": "Insufficient evidence",
    "RSI-E017": "Duplicate candidate",
    "RSI-E018": "Promotion conflict",
}

#: Ordered ``(keywords, code)`` rules over the lower-cased "type_name + message"
#: haystack. Order is specificity: the earliest match wins (e.g. ``canary``
#: before the generic ``regression``, ``corrupt`` state text before the
#: generic ``missing`` evidence text).
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tamper",), "RSI-E009"),
    (("canary",), "RSI-E012"),
    (("cooldown active",), "RSI-E018"),
    (("already exists", "immutable"), "RSI-E018"),
    (("duplicate",), "RSI-E017"),
    (("provider",), "RSI-E001"),
    (("connection", "network", "unreachable", "dns"), "RSI-E002"),
    (("candidate patch",), "RSI-E003"),
    (("build failure", "build failed"), "RSI-E004"),
    (("unit test failure", "unit failure"), "RSI-E005"),
    (("integration failure", "integration test failed"), "RSI-E006"),
    (("regression",), "RSI-E007"),
    (("memoryerror", "out of memory", "memory limit", "disk full", "no space left", "resource exhaust"), "RSI-E010"),
    (("startup",), "RSI-E011"),
    (("corrupt", "malformed", "mismatch", "truncated"), "RSI-E013"),
    (("policy", "rejected", "approval", "autonomous", "auto-approved", "human review"), "RSI-E014"),
    (("non-reproducible", "not reproducible"), "RSI-E015"),
    (("unverified", "missing", "no bundle directory", "no such file", "not found", "lineage record"), "RSI-E016"),
)

#: Real Python exception classes checked when no message rule matched —
#: subclasses always listed before their parents (FileNotFoundError before
#: OSError) so the more specific code wins.
_TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("FileNotFoundError", "RSI-E016"),
    ("PermissionError", "RSI-E013"),
    ("ConnectionError", "RSI-E002"),
    ("TimeoutError", "RSI-E000"),  # spec §54 defines no timeout code — admitted as unknown, never misfiled
    ("JSONDecodeError", "RSI-E013"),
    ("MemoryError", "RSI-E010"),
    ("OSError", "RSI-E013"),
)

#: The gate convention's fail-closed prefix: ``failed closed: <Type>: <text>``.
_FAILED_CLOSED_RE = re.compile(r"^failed closed: (\w+):")


def _classify_text(type_name: str, text: str) -> str:
    """One code from a real exception's type name + message (or raw text): named classes, ordered keywords, real classes, else RSI-E000."""
    hay = f"{type_name} {text}".lower()
    if type_name == "StaticScanBlockedError":
        return "RSI-E008"  # deterministic findings blocked a skill write/install (the consumed scanner surface)
    if type_name == "StaticScannerError":
        return "RSI-E016"  # the scanner could not evaluate its input: the evidence to act on is insufficient
    if type_name == "ReleaseStoreError":
        return "RSI-E018" if any(keyword in hay for keyword in ("already exists", "immutable", "conflict")) else "RSI-E013"
    for keywords, code in _RULES:
        if any(keyword in hay for keyword in keywords):
            return code
    for name, code in _TYPE_RULES:
        if type_name == name:
            return code
    return "RSI-E000"


def classify(failure: object) -> str:
    """Map a real failure signal (exception or text) to its §54 code.

    Never guesses: a signal matching no documented rule is ``RSI-E000``
    (unknown), never a code that would misfile the failure.
    """
    if isinstance(failure, StaticScanBlockedError):  # the real consumed surface: alpha.skills.security_static_scanner
        return "RSI-E008"
    if isinstance(failure, ReleaseStoreError):  # the real release-store surface: alpha.rsi.releases
        text = str(failure).lower()
        return "RSI-E018" if any(keyword in text for keyword in ("already exists", "immutable", "conflict")) else "RSI-E013"
    if isinstance(failure, BaseException):
        return _classify_text(type(failure).__name__, str(failure))
    return _classify_text("", str(failure))


def classify_gate(gate: str, reason: str) -> str | None:
    """Code for one FAILING gate entry — or ``None`` when the entry is only a derived disclosure.

    Grounded in the landed modules' real reason texts (holdout_gate,
    review_decision, verify_bundle, the lineage store, the evolution engine,
    the cooldown/release stores); anything unclassifiable falls through to
    :func:`classify`, which admits ``RSI-E000`` rather than forcing a code.
    """
    text = str(reason)
    lowered = text.lower()
    if gate == "evolution_route" and lowered.startswith("not routed:"):
        return None  # consequence of earlier failing gates — they carry the codes; never double-counted
    if gate == "cooldown" and lowered.startswith("cooldown active"):
        return "RSI-E018"  # a new promotion conflicting with an active §42 stabilization window
    if lowered.startswith("failed closed:"):
        match = _FAILED_CLOSED_RE.match(text)
        return _classify_text(match.group(1) if match else "", text)
    # Gate-rooted rules over the landed modules' real reason texts.
    if gate == "lineage":
        return "RSI-E013" if "corrupt" in lowered else "RSI-E016"
    if gate == "bundle_integrity":
        return "RSI-E013" if any(keyword in lowered for keyword in ("corrupt", "malformed", "mismatch", "unsupported", "unexpected file")) else "RSI-E016"
    if gate == "evidence_standard":
        return "RSI-E016"  # measured-only standard unmet (or unreadable index): the evidence is insufficient or not measurable
    if gate == "holdout":
        if "regression" in lowered:
            return "RSI-E007"
        if "corrupt" in lowered:
            return "RSI-E013"
        return "RSI-E016"  # unverified / missing / unreadable payload: insufficient evidence
    if gate == "human_review":
        return "RSI-E014"  # promotion blocked by the human-review policy: pending or rejected alike
    if gate == "cooldown":
        return "RSI-E013"  # cooldown store unavailable (corrupt/unreadable record): checkpoint/state failure
    if gate in ("release_store", "cooldown_record"):
        if any(keyword in lowered for keyword in ("already exists", "immutable")):
            return "RSI-E018"
        return "RSI-E013"
    if gate == "evolution_route":
        if "regression" in lowered:
            return "RSI-E007"
        if "approval" in lowered or "policy" in lowered or "autonomous" in lowered:
            return "RSI-E014"
        if "missing" in lowered:
            return "RSI-E016"
        if "conflict" in lowered:
            return "RSI-E018"
        return _classify_text("", text)  # e.g. "not strictly better than baseline" — no §54 code fits: honest RSI-E000
    return _classify_text("", text)


def describe(code: str) -> str:
    """The spec's own label for a code; unknown codes are reported as unknown, never relabeled."""
    return RSI_ERROR_CODES.get(code, f"unknown failure code {code!r} (not in the §54 taxonomy)")


def annotate(reason: str, code: str | None) -> str:
    """Append the §54 code as `` [RSI-Exxx]`` (a no-op for ``None``) — decision-reason/log disclosure only."""
    if not code:
        return reason
    return f"{reason} [{code}]"
