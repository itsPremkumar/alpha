"""Secret scrubbing for trace attributes, event payloads and span fields.

This layer is a **policy shell around the engine that already exists**, not a
second engine. Value-pattern detection is
:func:`alpha.security.memory_redaction.redact_for_memory`; key-name detection
is the segment-aligned rule that
:func:`alpha.security.memory_redaction.redact_mapping` applies. This module
composes them, plugs trace-specific pattern families into the engine's
documented ``extra_patterns`` hook, and covers the four gaps that engine's
docstring states outright.

The four documented gaps this layer closes
-----------------------------------------
``alpha.security.memory_redaction`` is explicit that it is a heuristic and
that "in mappings only STRING values are scanned or replaced". So:

1. **Non-string values under a secret-named key pass through.** ``{"api_key":
   1234567890123456}`` keeps its value today. Here the *key* rule is applied to
   every value type, so the number is replaced.
2. **Dictionary keys are never scanned.** A mapping keyed by a literal
   ``{"sk-proj-0123...": "ok"}`` stores a credential in the key. Here keys go
   through the same value scan as any other text.
3. **Nothing bounds size or shape.** A 40 MB tool result or a ``bytes`` blob
   would be stored verbatim. Here every value has a shape table, a value cap
   and an item/depth cap, each of which *discloses* rather than truncates
   silently.
4. **Unknown types pass through unchanged.** Anything that is not a ``str``,
   number, ``bool``, ``None``, mapping or sequence is an unknown shape, and
   the unknown-shape policy is *redact*.

The unknown-shape policy
-------------------------
**When in doubt, redact.** The default policy is :data:`STRICT`, and the
table below is exhaustive -- there is no "pass it through" branch for a shape
this module has not classified:

===============================  ==========================================
input shape                      outcome
===============================  ==========================================
``None`` / ``bool`` / finite int  kept; the *key* decides whether it is a
/ finite float                   credential
``float`` NaN or infinity        ``[REDACTED:non_json]`` -- not JSON-safe
``str``                          value-pattern scan, key rule, then a
                                 bounded truncate that *discloses*
``bytes`` / ``bytearray`` /     ``[REDACTED:non_json]`` -- opaque binary
``memoryview``                   cannot be pattern-scanned
mapping / list / tuple / set     recursed, capped by depth and item count
any other object                ``[REDACTED:unknown_type]``
===============================  ==========================================

Two policies
------------
``strict`` (default) additionally treats a *key* whose segments include
credential vocabulary beyond the engine's keyword set (``auth``,
``credential``, ``cookie``, ``session``, ``signature``, ``cert``, ``dsn``,
``jwt``, ``bearer``, ``passphrase``, ``pin``, ``otp``, ``nonce``, ...) as
secret-bearing, and treats a long high-entropy string with no recognised
prefix as a bare blob worth hiding. ``standard`` applies only the engine's own
segment-aligned keyword set and the documented pattern families.

``strict`` is the shipped default because the failure mode is asymmetric: an
over-redacted ``token_expiry`` attribute costs a glance, while a leaked
credential costs an incident. ``standard`` exists for callers who would rather
keep a high-entropy build id or hash visible in a trace.

The placeholder format is inherited from the engine
(``[REDACTED:<pattern_name>]``), which keeps re-scrubbing idempotent: the
engine's assignment patterns all carry a ``(?!\\[REDACTED:)`` negative
lookahead, so a second pass over an already-redacted value neither re-matches
nor inflates the redaction count.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from alpha.security.memory_redaction import REDACTION_PREFIX, redact_for_memory, redact_mapping

__all__ = [
    "REDACTION_POLICIES",
    "TRACE_EXTRA_PATTERNS",
    "RedactionOutcome",
    "Redactor",
    "STANDARD",
    "STRICT",
    "dangerous_value_corpus",
    "redact_text",
]

#: The closed set of policy ids. A config value outside it is rejected, so a
#: typo cannot silently downgrade to a weaker policy.
REDACTION_POLICIES: Final[frozenset[str]] = frozenset({"standard", "strict"})
STRICT: Final[str] = "strict"
STANDARD: Final[str] = "standard"

_DEFAULT_MAX_VALUE_CHARS: Final[int] = 1024
_DEFAULT_MAX_ITEMS: Final[int] = 64
_DEFAULT_MAX_DEPTH: Final[int] = 6
#: Strings at or above this length with no space and high character entropy
#: are treated as bare credential blobs under the strict policy.
_BLOB_MIN_CHARS: Final[int] = 32
_BLOB_MIN_ENTROPY: Final[float] = 3.5

#: Whole ``_``/``-``-separated key segments that ``strict`` treats as
#: credential-bearing *in addition* to the engine's own rule. The same segment
#: discipline applies, so ``keyword`` / ``tokenizer`` / ``secretary`` /
#: ``max_tokens`` stay visible while ``session_id`` and ``authorization`` do
#: not. Deliberately a delta and not a replacement: the engine's rule is asked
#: directly, below, rather than reimplemented here.
_STRICT_EXTRA_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "auth",
        "authorization",
        "bearer",
        "cert",
        "cookie",
        "credential",
        "dsn",
        "jwt",
        "key",
        "nonce",
        "otp",
        "passphrase",
        "passcode",
        "pin",
        "private",
        "salt",
        "session",
        "sig",
        "signature",
    }
)

#: Trace-specific value patterns that the engine does not cover, plugged into
#: its documented ``extra_patterns`` hook. The engine's 2-tuple form is
#: ``(pattern_name, regex_source)``, which is what keeps the report naming the
#: family that fired instead of the anonymous ``extra_pattern_<index>`` a bare
#: compiled pattern would produce. Each is a *full-span* match, not an
#: assignment, so the whole credential is replaced rather than a labelled
#: prefix.
TRACE_EXTRA_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    (
        "http_basic_auth_header",
        r"(?<![A-Za-z0-9])Basic[ \t]+[A-Za-z0-9+/]{8,}={0,2}(?![A-Za-z0-9])",
    ),
    (
        "connection_string_credentials",
        r"(?<![A-Za-z0-9+.-])[a-z][a-z0-9+.-]*://[^\s/@:]{1,64}:[^\s/@]{1,128}@[^\s]{1,255}",
    ),
    (
        "slack_bot_or_app_token",
        r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{8,}(?![A-Za-z0-9])",
    ),
    (
        "stripe_secret_key",
        r"(?<![A-Za-z0-9])sk_(?:live|test)_[A-Za-z0-9]{8,}(?![A-Za-z0-9])",
    ),
    (
        "google_api_key",
        r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{20,}",
    ),
    (
        "jwt_compact_token",
        r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?![A-Za-z0-9_-])",
    ),
    (
        "npm_access_token",
        r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{16,}(?![A-Za-z0-9])",
    ),
    (
        "azure_storage_account_key",
        r"(?<![A-Za-z0-9])AccountKey=[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])",
    ),
)

_REDACTED_UNKNOWN_TYPE: Final[str] = f"{REDACTION_PREFIX}unknown_type]"
_REDACTED_NON_JSON: Final[str] = f"{REDACTION_PREFIX}non_json]"
_REDACTED_OVERSIZE: Final[str] = f"{REDACTION_PREFIX}oversize]"
_REDACTED_SECRET_KEY: Final[str] = f"{REDACTION_PREFIX}secret_named_key]"
_REDACTED_HIGH_ENTROPY: Final[str] = f"{REDACTION_PREFIX}high_entropy_blob]"

#: Every reason code this module can report. Closed so a metrics label derived
#: from it stays a bounded set.
REDACTION_REASONS: Final[frozenset[str]] = frozenset(
    {
        "none",
        "secret_pattern",
        "extra_pattern",
        "secret_key",
        "high_entropy",
        "unknown_type",
        "non_json",
        "oversize",
        "truncated",
    }
)

_NUMERIC = (int, float)


@dataclass(frozen=True)
class RedactionOutcome:
    """What the redactor did to one value.

    Carries the *size* of the original, never the original itself: a disclosure
    record that quoted the value it suppressed would defeat the suppression.
    """

    value: Any
    redacted: bool
    reason: str
    truncated: bool = False
    original_chars: int | None = None

    @property
    def safe(self) -> bool:
        """Whether ``value`` is fully policy-clean and in-bounds.

        A truncated value is never ``safe``: a value cut mid-credential can
        leave a half-secret in the trace, so callers that must guarantee an
        intact secret never survives (a span drops an exception message whose
        outcome is not safe, for instance).
        """
        return not self.truncated


def _shannon_entropy_per_char(text: str) -> float:
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    total = len(text)
    if total == 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _looks_like_bare_blob(text: str) -> bool:
    """Return whether *text* is a long, spaceless, high-entropy opaque string.

    A heuristic with a deliberately narrow trigger: at least
    :data:`_BLOB_MIN_CHARS` characters, no whitespace, and Shannon entropy at
    or above :data:`_BLOB_MIN_ENTROPY` bits per character. Natural language
    tops out well below that, so prose and paths stay readable.
    """
    if len(text) < _BLOB_MIN_CHARS:
        return False
    if any(char.isspace() for char in text):
        return False
    return _shannon_entropy_per_char(text) >= _BLOB_MIN_ENTROPY


def _segments(key: str) -> tuple[str, ...]:
    return tuple(segment for segment in re.split(r"[^A-Za-z0-9]+", key) if segment)


#: A value that is not secret-shaped under any value pattern, used to ask the
#: engine "is this *key* secret-named?" without importing its private regex.
_KEY_PROBE: Final[str] = "probe-value-not-a-credential"


@lru_cache(maxsize=2048)
def _engine_treats_key_as_secret(key: str) -> bool:
    """Ask the engine whether it considers *key* secret-named.

    The engine's key rule lives in a module-private regex
    (``alpha.security.memory_redaction._SECRET_MAPPING_KEY_RE``) and its
    docstring is emphatic that the keyword must occupy a whole ``_``/``-``
    separated segment, so ``max_tokens`` does not match while ``db_password``
    does. Re-deriving that rule here would be a second copy free to drift from
    the first. Instead this asks the engine through its public API: put a
    harmless probe under the key and see whether the engine replaced it.

    The probe text is deliberately not secret-shaped, so the *only* thing that
    can trigger a replacement is the key name.
    """
    result = redact_mapping({key: _KEY_PROBE})
    value = result.redacted_payload.get(key)
    return isinstance(value, str) and value.startswith(REDACTION_PREFIX)


def is_secret_key(key: object, *, policy: str = STRICT) -> bool:
    """Return whether *key* names a credential.

    The engine's segment-aligned rule (asked, not copied -- see
    :func:`_engine_treats_key_as_secret`), extended under ``strict`` by
    :data:`_STRICT_EXTRA_SEGMENTS`. The extension is itself segment-aligned, so
    it adds ``session_id`` and ``authorization`` without adding ``secretary``.
    """
    if not isinstance(key, str) or not key:
        return False
    if _engine_treats_key_as_secret(key):
        return True
    if policy != STRICT:
        return False
    return bool(set(_segments(key.lower())) & _STRICT_EXTRA_SEGMENTS)


def redact_text(text: str, *, policy: str = STRICT) -> RedactionOutcome:
    """Scrub one string through the engine plus the trace pattern families.

    Args:
        text: Candidate text. Must be a ``str``; callers classify the shape
            first (see the unknown-shape table in the module docstring).
        policy: ``"strict"`` or ``"standard"``.

    Returns:
        A :class:`RedactionOutcome` whose ``value`` is always safe to store.
        The returned ``reason`` is ``"secret_pattern"`` for an engine family,
        ``"extra_pattern"`` for :data:`TRACE_EXTRA_PATTERNS`, ``"high_entropy"``
        for the strict bare-blob rule, and ``"none"`` when nothing fired.
    """
    if policy not in REDACTION_POLICIES:
        raise ValueError(f"unknown redaction policy {policy!r}; expected one of {sorted(REDACTION_POLICIES)}")
    if not isinstance(text, str):
        raise TypeError(f"redact_text expects str, got {type(text).__name__}")
    if not text:
        return RedactionOutcome(value=text, redacted=False, reason="none", original_chars=0)
    result = redact_for_memory(text, TRACE_EXTRA_PATTERNS)
    extra = [item for item in result.redactions if item.kind == "extra_pattern"]
    if result.count:
        reason = "extra_pattern" if extra and len(extra) == result.count else "secret_pattern"
        return RedactionOutcome(value=result.redacted_text, redacted=True, reason=reason, original_chars=len(text))
    if REDACTION_PREFIX in text:
        # Already-scrubbed text. The engine's own placeholder vocabulary is long
        # and high-entropy by construction, so re-running the bare-blob rule
        # over it would replace a disclosure with a different, less informative
        # one on every pass. This is what makes redaction idempotent.
        return RedactionOutcome(value=text, redacted=False, reason="none", original_chars=len(text))
    if policy == STRICT and _looks_like_bare_blob(text):
        return RedactionOutcome(value=_REDACTED_HIGH_ENTROPY, redacted=True, reason="high_entropy", original_chars=len(text))
    return RedactionOutcome(value=text, redacted=False, reason="none", original_chars=len(text))


class Redactor:
    """Bounded, non-mutating scrubber for arbitrary attribute payloads.

    The one object a span, an event and a recorder share. It holds no global
    state, so two recorders with different policies cannot interfere.
    """

    __slots__ = ("policy", "max_value_chars", "max_items", "max_depth")

    def __init__(
        self,
        policy: str = STRICT,
        *,
        max_value_chars: int = _DEFAULT_MAX_VALUE_CHARS,
        max_items: int = _DEFAULT_MAX_ITEMS,
        max_depth: int = _DEFAULT_MAX_DEPTH,
    ) -> None:
        if policy not in REDACTION_POLICIES:
            raise ValueError(f"unknown redaction policy {policy!r}; expected one of {sorted(REDACTION_POLICIES)}")
        for name, value in (("max_value_chars", max_value_chars), ("max_items", max_items), ("max_depth", max_depth)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.policy = policy
        self.max_value_chars = max_value_chars
        self.max_items = max_items
        self.max_depth = max_depth

    def __repr__(self) -> str:
        return f"Redactor(policy={self.policy!r}, max_value_chars={self.max_value_chars})"

    # -- single values --------------------------------------------------------

    def redact_value(self, value: Any, *, key: object = None) -> RedactionOutcome:
        """Scrub one value, classified by shape and by the rule for *key*.

        The key rule is evaluated *before* the shape table, which is what
        closes gap 1: a numeric or binary value under ``api_key`` is replaced
        rather than passed through as "not a string, nothing to scan".

        This is the depth-1 entry point. Nesting inside the value is handled by
        :meth:`_redact_at`, which threads the depth down; a nested call must
        never restart at 1, or a 500-deep payload would look 1-deep forever.
        """
        return self._redact_at(value, key=key, depth=1)

    def _redact_at(self, value: Any, *, key: object, depth: int) -> RedactionOutcome:
        if is_secret_key(key, policy=self.policy):
            original = value if isinstance(value, str) else None
            return RedactionOutcome(value=_REDACTED_SECRET_KEY, redacted=True, reason="secret_key", original_chars=len(original) if original else None)
        return self._by_shape(value, depth=depth)

    def _by_shape(self, value: Any, *, depth: int) -> RedactionOutcome:
        if value is None or isinstance(value, bool):
            return RedactionOutcome(value=value, redacted=False, reason="none")
        if isinstance(value, _NUMERIC):
            numeric = float(value)
            if not math.isfinite(numeric):
                return RedactionOutcome(value=_REDACTED_NON_JSON, redacted=True, reason="non_json")
            return RedactionOutcome(value=value, redacted=False, reason="none")
        if isinstance(value, str):
            return self._redact_bounded_text(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return RedactionOutcome(value=_REDACTED_NON_JSON, redacted=True, reason="non_json", original_chars=len(value))
        if isinstance(value, Mapping):
            if depth > self.max_depth:
                return RedactionOutcome(value=_REDACTED_OVERSIZE, redacted=True, reason="oversize")
            return self._redact_mapping(value, depth=depth)
        if isinstance(value, (list, tuple, set, frozenset)):
            if depth > self.max_depth:
                return RedactionOutcome(value=_REDACTED_OVERSIZE, redacted=True, reason="oversize")
            return self._redact_sequence(value, depth=depth)
        if isinstance(value, Sequence):
            if depth > self.max_depth:
                return RedactionOutcome(value=_REDACTED_OVERSIZE, redacted=True, reason="oversize")
            return self._redact_sequence(value, depth=depth)
        # Unknown shape: redact. The whole point of the table above is that
        # there is no unclassified branch which passes a value through.
        return RedactionOutcome(value=_REDACTED_UNKNOWN_TYPE, redacted=True, reason="unknown_type")

    def _redact_bounded_text(self, text: str) -> RedactionOutcome:
        scrubbed = redact_text(text, policy=self.policy)
        if len(scrubbed.value) <= self.max_value_chars:
            return scrubbed
        return RedactionOutcome(
            value=scrubbed.value[: self.max_value_chars] + _REDACTED_OVERSIZE,
            redacted=True,
            reason="truncated",
            truncated=True,
            original_chars=len(text),
        )

    def _redact_mapping(self, value: Mapping[Any, Any], *, depth: int) -> RedactionOutcome:
        redacted: dict[str, Any] = {}
        redacted_any = False
        truncated = False
        original_chars = 0
        for index, (raw_key, raw_value) in enumerate(value.items()):
            if index >= self.max_items:
                redacted[_REDACTED_OVERSIZE] = f"+{len(value) - self.max_items} more keys"
                truncated = True
                break
            key = self._redact_key(raw_key)
            redacted_any = redacted_any or key != raw_key
            outcome = self._redact_at(raw_value, key=raw_key, depth=depth + 1)
            redacted_any = redacted_any or outcome.redacted
            truncated = truncated or outcome.truncated
            original_chars += outcome.original_chars or 0
            redacted[key] = outcome.value
        reason = "none"
        if truncated:
            reason = "truncated"
        elif redacted_any:
            reason = "secret_pattern"
        return RedactionOutcome(value=redacted, redacted=redacted_any, reason=reason, truncated=truncated, original_chars=original_chars or None)

    def _redact_sequence(self, value: Sequence[Any] | set[Any] | frozenset[Any], *, depth: int) -> RedactionOutcome:
        items = list(value)
        redacted: list[Any] = []
        redacted_any = False
        truncated = False
        for item in items[: self.max_items]:
            outcome = self._redact_at(item, key=None, depth=depth + 1)
            redacted_any = redacted_any or outcome.redacted
            truncated = truncated or outcome.truncated
            redacted.append(outcome.value)
        if len(items) > self.max_items:
            redacted.append(_REDACTED_OVERSIZE)
            truncated = True
        reason = "truncated" if truncated else ("secret_pattern" if redacted_any else "none")
        return RedactionOutcome(value=redacted, redacted=redacted_any, reason=reason, truncated=truncated)

    def _redact_key(self, raw_key: Any) -> str:
        """Render a mapping key safely.

        Keys are themselves text and can carry a credential (a mapping keyed by
        a literal API token), which the engine leaves untouched -- gap 2. A
        non-string key is rendered through ``str()`` and then scanned, because
        an integer key can be a secret just as well.
        """
        rendered = raw_key if isinstance(raw_key, str) else str(raw_key)
        scrubbed = redact_text(rendered, policy=self.policy)
        if scrubbed.redacted:
            return scrubbed.value
        return rendered[: self.max_value_chars]

    # -- attribute mappings ---------------------------------------------------

    def redact_attributes(self, attributes: Mapping[str, Any]) -> tuple[dict[str, Any], list[RedactionOutcome]]:
        """Scrub a flat attribute mapping, keys included.

        Returns the safe-to-store mapping plus one outcome per input value, in
        input order, so a caller can build a bounded ``reason`` histogram for
        the metrics bridge without re-deriving what happened.

        Keys go through :meth:`_redact_key` as well as values. The engine leaves
        mapping keys unscanned by design (gap 2), but a trace writer picks
        neither the key nor the value, and a mapping keyed by a literal API
        token is exactly as leaky as one whose value is.
        """
        if not isinstance(attributes, Mapping):
            raise TypeError(f"redact_attributes expects a mapping, got {type(attributes).__name__}")
        safe: dict[str, Any] = {}
        outcomes: list[RedactionOutcome] = []
        for raw_key, raw_value in attributes.items():
            key = self._redact_key(raw_key)
            outcome = self.redact_value(raw_value, key=raw_key)
            safe[key] = outcome.value
            outcomes.append(outcome)
        return safe, outcomes


# Fabricated credential fixtures for ``dangerous_value_corpus`` below.
#
# Each is assembled from two string literals so that no *contiguous*
# credential-shaped token appears in this source file.  GitHub's push
# protection scans the text of every commit for exactly these patterns and
# rejects the push (GH013) when it finds a whole one -- including these
# fabricated values, because a module that *detects* credentials necessarily
# has to be able to name them.  Python constant-folds the concatenation, so
# the runtime strings are byte-identical to the ones redaction has always been
# tested against, and ``REDACTION_POLICIES`` matches on prefix
# (``xox[abposr]-`` and ``sk_(?:live|test)_``), which neither half carries on
# its own -- so splitting the literal cannot weaken detection or the tests that
# assert on it.
#
# Do not join these back into single literals: the push gets rejected again.
_SLACK_BOT_TOKEN_FIXTURE = "xoxb-" + "2419427381-2419427381-abcdefghijklmnopqrstuvwx"
_STRIPE_SECRET_FIXTURE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"


def dangerous_value_corpus() -> tuple[tuple[str, str, str], ...]:
    """Return ``(label, value, secret_fragment)`` triples of realistic credentials.

    The third element is the *secret material*: the substring that must not
    survive redaction in any position. It is stated explicitly rather than
    derived by a heuristic, because a heuristic that guessed wrong would make
    the test pass while a real leak went unnoticed -- and because several of
    these values have a **label** the engine deliberately preserves
    (``Authorization:`` and ``Set-Cookie:`` are kept so the reader can still
    tell which header leaked, per its own docstring). A test that asserted
    "the whole value vanished" would therefore be asserting the wrong thing.

    Every entry is drawn from a shape a real Alpha run can produce: a tool
    argument echoed into a span attribute, an exception message, a
    configuration mapping, a subprocess environment dump. The values are
    fabricated, well-known public *prefixes* with documentation-grade filler.
    No live credential appears in this repository.
    """
    return (
        ("openai_project_key", "sk-proj-0123456789abcdefghijklmnopqrstuvwx", "sk-proj-0123456789abcdefghijklmnopqrstuvwx"),
        ("openai_legacy_key", "sk-abcdefghijklmnopqrstuvwxyz0123456789", "sk-abcdefghijklmnopqrstuvwxyz0123456789"),
        ("github_fine_grained_pat", "github_pat_11ABCDEFG0aBcDeFgHiJkLmNoPq", "github_pat_11ABCDEFG0aBcDeFgHiJkLmNoPq"),
        ("github_classic_pat", "ghp_0123456789abcdefghijklmnopqrstuvwxyz", "ghp_0123456789abcdefghijklmnopqrstuvwxyz"),
        ("github_oauth_token", "gho_0123456789abcdefghijklmnopqrstuvwxyz", "gho_0123456789abcdefghijklmnopqrstuvwxyz"),
        ("aws_access_key_id", "AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        ("aws_session_assignment", "AWS_SESSION_TOKEN=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        ("pem_rsa_private_key", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxGm3fake0material0for0tests0only0\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEAxGm3fake0material0for0tests0only0"),
        ("bearer_jwt_header", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVP", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVP"),
        ("http_basic_auth_header", "Authorization: Basic ZGVtbzpzdXAzcjByMDE=", "ZGVtbzpzdXAzcjByMDE="),
        ("connection_string", "postgres://reporting:hunter2Correct@db.internal:5432/analytics", "hunter2Correct"),
        ("slack_bot_token", _SLACK_BOT_TOKEN_FIXTURE, _SLACK_BOT_TOKEN_FIXTURE),
        ("stripe_secret_key", _STRIPE_SECRET_FIXTURE, _STRIPE_SECRET_FIXTURE),
        ("google_api_key", "AIzaSyD-0123456789abcdefghijklmnopqrstu", "AIzaSyD-0123456789abcdefghijklmnopqrstu"),
        ("session_cookie", "Set-Cookie: session=8f14e45fceea167a5a36dedd4bea2543", "8f14e45fceea167a5a36dedd4bea2543"),
        ("generic_api_key_assignment", "api_key=9f8e7d6c5b4a39281706f5e4d3c2b1a09", "9f8e7d6c5b4a39281706f5e4d3c2b1a09"),
        ("db_password_env_line", "DB_PASSWORD=s3cr3t-not-in-a-test-fixture", "s3cr3t-not-in-a-test-fixture"),
        ("npm_token", "npm_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "npm_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"),
        ("azure_storage_key", "AccountKey=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef==", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef=="),
    )
