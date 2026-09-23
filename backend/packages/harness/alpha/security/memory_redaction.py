"""Pattern-based sensitive-content redaction for long-term memory writes.

``redact_for_memory(text)`` scans text with a fixed registry of secret-shaped
patterns and returns the redacted text plus an honest report
(``RedactionResult``: ``redacted_text``, ``redactions`` [{kind, pattern_name}],
``count``) of what was ACTUALLY replaced. ``redact_mapping(payload)`` applies
the same scan to structured payloads and additionally replaces any STRING
value whose KEY looks secret-bearing (``api_key`` / ``secret`` / ``password`` /
``token`` / ... as a whole ``_``/``-``-separated segment; list elements inherit
the enclosing key's context).

Covered pattern families (one test per family in
``backend/tests/test_memory_secret_filter.py``): ``openai_key`` (``sk-...``),
``github_token`` (``ghp_``/``gho_``/``ghs_``/``ghr_``/``ghu_`` prefixes plus
``github_pat_``), ``aws_access_key_id`` (``AKIA``/``ASIA`` + 16 chars),
``private_key`` (PEM ``-----BEGIN ... PRIVATE KEY-----`` blocks and headers),
``authorization_header`` (``Bearer <value>``), ``session_cookie``
(``session=``/``JSESSIONID=``/``Set-Cookie:``-style assignments),
``secret_assignment`` (generic ``API_KEY``/``SECRET``/``PASSWORD``/``TOKEN``
assignments anywhere in text — keywords must occupy a whole segment, so
``max_tokens=1024`` is NOT matched), ``env_assignment`` (line-anchored
``X_API_KEY=`` / ``export ...`` env-file lines), ``mapping_secret_key``
(secret-named keys in mappings), and caller-supplied ``extra_patterns``
(reported as ``extra_pattern``).

Coverage limits — this is a HEURISTIC and is NOT exhaustive; it does not and
cannot promise that all secrets are removed:

- Only the families listed above are recognized. Unknown vendor formats,
  high-entropy strings with no known prefix and no secret-looking key name
  (bare hex/base64 blobs), and encoded or obfuscated values (base64 wrapping,
  split across lines, built at runtime) pass through UNREDACTED.
- ``extra_patterns`` defaults to empty and nothing is wired to config yet.
- Key-name detection can OVER-redact non-secret config values (e.g.
  ``TOKEN_EXPIRY=3600``) and session-shaped prose (``session: active``); the
  key/structure is kept and only the value span is replaced.
- Plural-only key names (``secrets:``, ``tokens:``) do not match the singular
  keyword segments, so such mapping keys are only caught via their VALUE
  patterns, not via the key name.
- In mappings only STRING values are scanned or replaced; non-string values
  and the mapping keys themselves are left as-is. A DICT held under a
  secret-named key is traversed with its INNER keys (not replaced wholesale);
  list elements do inherit the enclosing key. In messages only textual content
  is scanned — image/file parts pass through untouched.
- ``Authorization: Basic ...`` (non-Bearer) credentials are not covered.
- ``count`` is the number of merged spans after overlapping matches collapse
  (longest span wins, ties go to the earlier registry entry), not the raw
  number of pattern hits.
- Re-running the built-in patterns over already-redacted output is a no-op
  (``count`` 0) — placeholders never re-match. Caller-supplied
  ``extra_patterns`` are NOT guaranteed idempotent against placeholder text.

Every replacement is ``[REDACTED:<pattern_name>]``; matched text never enters
``Redaction`` entries, so reports and log lines built from this module cannot
re-leak the secret.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

REDACTION_PLACEHOLDER = "[REDACTED:{pattern_name}]"
#: Prefix every replacement shares; used to recognize (and not re-count)
#: already-redacted values so a second redaction pass is a no-op.
REDACTION_PREFIX = "[REDACTED:"

#: Extra pattern accepted by :func:`redact_for_memory` / :func:`redact_mapping`:
#: a bare regex (reported as ``extra_pattern_0``-style names), a
#: ``(pattern_name, regex)`` pair, or an already-compiled pattern.
ExtraPatternSpec = str | tuple[str, str] | list[str] | re.Pattern[str]


def _placeholder(pattern_name: str) -> str:
    return REDACTION_PLACEHOLDER.format(pattern_name=pattern_name)


@dataclass(frozen=True)
class Redaction:
    """One redacted span: family (kind) plus the exact regex that fired."""

    kind: str
    pattern_name: str


@dataclass(frozen=True)
class RedactionResult:
    """Honest report of what :func:`redact_for_memory` actually replaced."""

    redacted_text: str
    redactions: list[Redaction]
    count: int


@dataclass(frozen=True)
class MappingRedactionResult:
    """The same report for :func:`redact_mapping`, with the redacted copy."""

    redacted_payload: Any
    redactions: list[Redaction]
    count: int


@dataclass(frozen=True)
class _PatternSpec:
    kind: str
    pattern_name: str
    regex: re.Pattern[str]
    #: Assignment patterns capture ``group(1)`` = key/label prefix (including
    #: the separator) and ``group(2)`` = the secret value, so the key label
    #: survives redaction; full-match patterns replace their whole span.
    is_assignment: bool


# Shared keyword alternation for assignment-style patterns. Keywords must end
# on a segment boundary (the following ``[_-]``-separated suffix or the value
# separator), which is what keeps ``max_tokens=1024`` and ``tokenizer = ...``
# out while matching ``auth_token=...`` and ``DB_PASSWORD=...``.
_KEY_WORDS = r"(?:api[_-]?key|secret|password|passwd|token|credentials?|private[_-]?key)"

# Registry order == tie-breaking priority after span length: the most specific
# family comes first so an equal-length overlap is reported under the more
# specific pattern name. env_file_line intentionally precedes the generic
# key-assignment pattern so a line-start ``X_API_KEY=...`` tie reports
# ``env_assignment``.
_PATTERNS: tuple[_PatternSpec, ...] = (
    _PatternSpec(
        "openai_key",
        "openai_sk_key",
        re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{10,}"),
        False,
    ),
    _PatternSpec(
        "github_token",
        "github_fine_grained_pat",
        re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}"),
        False,
    ),
    _PatternSpec(
        "github_token",
        "github_token_prefix",
        re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
        False,
    ),
    _PatternSpec(
        "aws_access_key_id",
        "aws_access_key_id",
        re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
        False,
    ),
    _PatternSpec(
        "private_key",
        "pem_private_key_block",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]{0,8192}?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
        False,
    ),
    _PatternSpec(
        "private_key",
        "pem_private_key_header",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
        False,
    ),
    _PatternSpec(
        "authorization_header",
        "bearer_token",
        re.compile(r"(?<![A-Za-z0-9])Bearer[ \t]+[A-Za-z0-9\-._~+/]{8,}={0,2}", re.IGNORECASE),
        False,
    ),
    _PatternSpec(
        "session_cookie",
        "session_cookie_assignment",
        re.compile(
            r"(?<![A-Za-z0-9_])((?:set-cookie|jsessionid|phpsessid|sessionid|session_id|connect_sid|session|cookie)[ \t]*[:=][ \t]*)(?!\[REDACTED:)([^\s;,\"']{4,})",
            re.IGNORECASE,
        ),
        True,
    ),
    _PatternSpec(
        "env_assignment",
        "env_file_line",
        # Line-anchored (``^`` in MULTILINE) env-file assignment. The name is
        # segment-aligned on the keyword, so a line-start ``max_tokens=1024``
        # is NOT an env secret while ``X_API_KEY=...`` / ``export AWS_...`` are.
        re.compile(
            r"^((?:export[ \t]+)?(?:[A-Za-z0-9]*[_-])*" + _KEY_WORDS + r"(?:[_-][A-Za-z0-9]+)*[ \t]*=[ \t]*[\"']?)(?!\[REDACTED:)([^\s\"']{4,})",
            re.IGNORECASE | re.MULTILINE,
        ),
        True,
    ),
    _PatternSpec(
        "secret_assignment",
        "generic_key_assignment",
        # Same segment rule anywhere in text; the value lookahead keeps an
        # already-redacted value from re-matching (idempotent re-scans).
        re.compile(
            r"(?<![A-Za-z0-9_])((?:[A-Za-z0-9]*[_-])*" + _KEY_WORDS + r"(?:[_-][A-Za-z0-9]+)*[\"']?[ \t]*[:=][ \t]*[\"']?)(?!\[REDACTED:)([^\s\"']{4,})",
            re.IGNORECASE,
        ),
        True,
    ),
)

# Mapping-key rule: the keyword must form a whole segment of the key, so
# ``api_key`` / ``db_password`` / ``access_token`` match while ``max_tokens``
# (segment ``tokens``) and ``secretary`` / ``tokenizer`` do not.
_SECRET_MAPPING_KEY_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_-])*" + _KEY_WORDS + r"(?:[_-][A-Za-z0-9]+)*(?![A-Za-z0-9])", re.IGNORECASE)
_SECRET_KEY_PLACEHOLDER = _placeholder("secret_named_key")


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    replacement: str
    redaction: Redaction
    priority: int


def _normalize_extra_pattern(index: int, extra: ExtraPatternSpec) -> tuple[str, str, re.Pattern[str]]:
    if isinstance(extra, re.Pattern):
        return "extra_pattern", f"extra_pattern_{index}", extra
    if isinstance(extra, str):
        return "extra_pattern", f"extra_pattern_{index}", re.compile(extra)
    if isinstance(extra, (tuple, list)) and len(extra) == 2 and all(isinstance(item, str) for item in extra):
        pattern_name, source = extra
        return "extra_pattern", pattern_name, re.compile(source)
    raise TypeError(f"extra_patterns[{index}] must be a regex str, a (pattern_name, regex) pair, or a compiled pattern")


def _find_candidates(text: str, extra_patterns: Sequence[ExtraPatternSpec]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for priority, spec in enumerate(_PATTERNS):
        replacement = _placeholder(spec.pattern_name)
        for match in spec.regex.finditer(text):
            if spec.is_assignment:
                # Keep the key/label prefix; replace only the value span.
                match_replacement = match.group(1) + replacement
            else:
                match_replacement = replacement
            candidates.append(_Candidate(match.start(), match.end(), match_replacement, Redaction(spec.kind, spec.pattern_name), priority))
    base_priority = len(_PATTERNS)
    for index, extra in enumerate(extra_patterns):
        kind, pattern_name, regex = _normalize_extra_pattern(index, extra)
        replacement = _placeholder(pattern_name)
        for match in regex.finditer(text):
            candidates.append(_Candidate(match.start(), match.end(), replacement, Redaction(kind, pattern_name), base_priority + index))
    return candidates


def _merge_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    """Collapse overlapping matches: longest span wins, ties go by priority.

    The accepted set is strictly non-overlapping, so one secret region
    produces exactly one reported redaction no matter how many families fire.
    """
    ordered = sorted(candidates, key=lambda candidate: (-(candidate.end - candidate.start), candidate.priority))
    accepted: list[_Candidate] = []
    for candidate in ordered:
        if any(candidate.start < accepted_candidate.end and accepted_candidate.start < candidate.end for accepted_candidate in accepted):
            continue
        accepted.append(candidate)
    accepted.sort(key=lambda candidate: candidate.start)
    return accepted


def redact_for_memory(text: str, extra_patterns: Sequence[ExtraPatternSpec] = ()) -> RedactionResult:
    """Redact secret-shaped spans from ``text``; report exactly what changed.

    Args:
        text: The candidate memory content.
        extra_patterns: Optional caller-supplied detection patterns (empty by
            default; future config wiring plugs in here). Invalid regexes
            raise ``re.error``; invalid entries raise ``TypeError``.

    Returns:
        :class:`RedactionResult` with the redacted copy, one
        :class:`Redaction` per merged span, and ``count`` = number of merged
        spans actually replaced (see the module docstring for coverage limits).
    """
    if not isinstance(text, str):
        raise TypeError(f"redact_for_memory expects str, got {type(text).__name__}")
    if not text:
        return RedactionResult(redacted_text=text, redactions=[], count=0)
    accepted = _merge_candidates(_find_candidates(text, extra_patterns))
    pieces: list[str] = []
    cursor = 0
    for candidate in accepted:
        pieces.append(text[cursor : candidate.start])
        pieces.append(candidate.replacement)
        cursor = candidate.end
    pieces.append(text[cursor:])
    redactions = [candidate.redaction for candidate in accepted]
    return RedactionResult(redacted_text="".join(pieces), redactions=redactions, count=len(redactions))


def redact_mapping(payload: Any, extra_patterns: Sequence[ExtraPatternSpec] = ()) -> MappingRedactionResult:
    """Redact a structured memory payload, returning a redacted copy.

    Walks dicts/lists/tuples without mutating the input. String values are
    scanned with :func:`redact_for_memory`; a string whose key is secret-named
    (segment-aligned keyword, see module docstring) is replaced wholesale with
    ``[REDACTED:secret_named_key]``. Non-string values and keys themselves are
    left as-is (documented coverage limit).
    """
    redactions: list[Redaction] = []

    def _walk(value: Any, key: Any = None) -> Any:
        if isinstance(value, dict):
            return {child_key: _walk(child_value, child_key) for child_key, child_value in value.items()}
        if isinstance(value, list):
            # List elements inherit the enclosing key's context.
            return [_walk(item, key) for item in value]
        if isinstance(value, tuple):
            return tuple(_walk(item, key) for item in value)
        if isinstance(value, str):
            if isinstance(key, str) and _SECRET_MAPPING_KEY_RE.search(key):
                if value.startswith(REDACTION_PREFIX):
                    # Already redacted on a previous pass: leave it (idempotent,
                    # no phantom re-count).
                    return value
                redactions.append(Redaction(kind="mapping_secret_key", pattern_name="secret_named_key"))
                return _SECRET_KEY_PLACEHOLDER
            result = redact_for_memory(value, extra_patterns)
            redactions.extend(result.redactions)
            return result.redacted_text
        return value

    redacted_payload = _walk(payload)
    return MappingRedactionResult(redacted_payload=redacted_payload, redactions=redactions, count=len(redactions))
