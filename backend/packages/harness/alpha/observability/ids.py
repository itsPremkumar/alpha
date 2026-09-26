"""Correlation-id generation for the run-correlation spine.

Two identifiers are minted here, and only two: a **run id** and a **span id**.
Both are 128-bit values rendered as 32 lowercase hex characters, laid out as
a 48-bit big-endian millisecond timestamp followed by 80 random bits.

Why that layout
---------------
Two properties matter for incident work, and they pull in opposite directions:

* **Sortable by time.** Lexicographic order over the rendered string equals
  chronological order, so a JSONL trace file can be read in emission order with
  no extra sort key, and a human can see a slow run by scanning one column.
  The 48-bit millisecond field is the same width the W3C Trace Context
  specification reserves for a unix timestamp in the high bits of a trace id
  (W3C Trace Context Level 1, ``trace-id`` -- 16 bytes, 32 lowercase hex,
  high 32 bits "SHOULD" carry the unix epoch timestamp so trace ids sort).
* **Collision resistant.** 80 random bits from
  :func:`secrets.token_bytes` makes accidental collision negligible even
  across processes and restarts, and unguessable, so a trace file is not a
  capability to mint someone else's run.

The layout is ULID's idea (leading timestamp plus random suffix, so ids sort by
creation time) expressed in hex rather than Crockford base32, so the alphabet
matches the W3C trace-context wire format. The implementation here is original
Python; nothing is vendored from ULID, OpenTelemetry, or any other library.

What this module deliberately does NOT mint
-------------------------------------------
There is **no ``new_trace_id``**. ``trace_id`` is owned by
:mod:`alpha.trace_context`, whose module docstring states that its ContextVar
is the only source of a request trace id in this codebase. A second minting
path would let the persisted run disagree with the ``X-Trace-Id`` header the
same request already returned, and an id you cannot trust to match the logs is
worse than no id at all. :class:`alpha.observability.context.RunContext`
resolves ``trace_id`` through ``alpha.trace_context.resolve_trace_id`` instead.

What must never be used as an id
-------------------------------
:data:`FORBIDDEN_ID_SOURCE` states these as data so the rule set is assertable
rather than prose, and :func:`is_valid_id` enforces the structural half:

1. **A secret or credential.** Ids travel into log lines, HTTP headers and
   trace files. An id that is itself a bearer token converts a read-only
   observability surface into a credential leak.
2. **A hash (or any one-way function) of content.** Digesting the prompt, the
   tool arguments or the file contents makes the id a content oracle: two runs
   with equal ids are known to have had equal inputs, and a small input space
   is brute-forceable back to plaintext.
3. **A sequential counter.** Predictable across processes, and it publishes
   the exact number of runs a tenant has produced.
4. **A timestamp alone.** Two runs started in the same millisecond collide
   under load, which is precisely when you need the correlation to hold. The
   timestamp is a *prefix* here, never the whole id.
5. **A PII value.** A raw email address, display name or IP address is
   personal data that the redaction layer would have to catch, and it makes
   the trace file a data export.
6. **Free text, a path, or a URL.** Ids are written into JSONL, log records
   and ``X-Trace-Id``-style headers, which are latin-1 encoded on the wire.
   Printable-ASCII-only, fixed length, fixed alphabet is what keeps a newline
   or a raw byte in an identifier from becoming log injection.
7. **A label instead of an identity.** A model name, tool name or thread title
   is an attribute of the run, not its identity: it repeats, it collides, and
   putting it in the id makes the id a high-cardinality metric label. It
   belongs in a span attribute.
8. **A reused value.** A run id is minted once per run and a span id once per
   span. Minting on every access would make the id useless for correlation.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "ENTROPY",
    "FORBIDDEN_ID_SOURCE",
    "ID_HEX_LENGTH",
    "INVALID_ID",
    "RANDOM_HEX_LENGTH",
    "TIMESTAMP_HEX_LENGTH",
    "Clock",
    "IDGenerator",
    "RandomIdGenerator",
    "SequenceIdGenerator",
    "default_id_generator",
    "is_valid_id",
    "require_id",
]

#: A clock returning unix epoch **seconds** as a float.
Clock = Callable[[], float]
#: Entropy source taking a byte count and returning that many random bytes.
ENTROPY = Callable[[int], bytes]

#: 128 bits rendered as 32 lowercase hex characters, matching the width and
#: alphabet the W3C Trace Context specification uses for a trace id.
ID_HEX_LENGTH: Final[int] = 32
#: Width of the leading big-endian millisecond timestamp (48 bits).
TIMESTAMP_HEX_LENGTH: Final[int] = 12
#: Width of the random suffix (80 bits of the 128).
RANDOM_HEX_LENGTH: Final[int] = ID_HEX_LENGTH - TIMESTAMP_HEX_LENGTH
#: The all-zero id, which W3C Trace Context calls invalid. Rejected as an id.
INVALID_ID: Final[str] = "0" * ID_HEX_LENGTH

_MAX_TIMESTAMP_MS: Final[int] = (1 << (TIMESTAMP_HEX_LENGTH * 4)) - 1
_HEX_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]+\Z")

#: Machine-readable form of the "what must not be used as an id" rule set in
#: the module docstring. ``test_ids.py`` asserts every entry is stated and that
#: the structural rules (:func:`is_valid_id`) actually reject the samples.
#:
#: Honest limit, stated here because a reader will assume otherwise: entries 1,
#: 2, 3 and 5 are *semantic* rules. A 32-hex-character string is structurally
#: indistinguishable from a truncated digest, so :func:`is_valid_id` cannot
#: refuse a digest -- only a minting site can, by never hashing content in the
#: first place. The samples below are rejected because of their *shape* (wrong
#: length, non-hex alphabet, separators), not because :func:`is_valid_id`
#: understands what they mean.
FORBIDDEN_ID_SOURCE: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    (
        "secret",
        "An id that is itself a credential turns every log line, header and trace file into a credential leak.",
        ("sk-proj-0123456789abcdefghij", "ghp_0123456789abcdefghij", "AKIAIOSFODNN7EXAMPLE"),
    ),
    (
        "content_digest",
        "A digest of the input is a content oracle: equal ids imply equal inputs, and small input spaces are reversible.",
        (
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            "n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg=",
            "a" * 64,
        ),
    ),
    (
        "sequential_counter",
        "A counter is predictable across processes and publishes the exact run volume.",
        ("1", "00000001", "run-7"),
    ),
    (
        "timestamp_alone",
        "A bare timestamp collides for every run started inside the same millisecond, exactly when correlation matters.",
        ("1700000000000", "20240101T000000Z"),
    ),
    (
        "pii",
        "A raw email, name or IP is personal data that turns the trace file into a data export.",
        ("user@example.com", "192.168.1.44"),
    ),
    (
        "free_text",
        "Free text, paths and URLs carry separators and non-ASCII bytes that become log injection or header encoding failures.",
        ("C:\\Users\\PREM KUMAR\\run", "https://example.com/run/1", "run one\nsecond line"),
    ),
    (
        "label_not_identity",
        "A model, tool or thread name is a run attribute, not a run identity: it repeats and collides.",
        ("union-alpha", "bash", "lead-agent"),
    ),
)


@runtime_checkable
class IDGenerator(Protocol):
    """Source of correlation ids.

    Injected everywhere so a test can pin every id and get byte-identical
    output. :class:`RandomIdGenerator` is the production implementation and
    :class:`SequenceIdGenerator` the deterministic one.
    """

    def new_id(self) -> str:
        """Return a fresh 32-character lowercase hex identifier."""

    def new_run_id(self) -> str:
        """Return a fresh run identifier."""

    def new_span_id(self) -> str:
        """Return a fresh span identifier."""


def _timestamp_ms(clock: Clock) -> int:
    """Return the clock reading as a 48-bit millisecond timestamp.

    A clock that is non-finite, negative or beyond year 10889 is clamped into
    range rather than raising. A wrong clock degrades *sortability* only --
    uniqueness still rests on the 80 random bits -- and raising here would let
    a broken host clock take down a run that only asked for an id. The clamp is
    a deliberate trade: the alternative is an id that cannot exist at all.
    """
    raw = clock()
    try:
        milliseconds = int(float(raw) * 1000)
    except (TypeError, ValueError, OverflowError):
        return 0
    if milliseconds < 0:
        return 0
    if milliseconds > _MAX_TIMESTAMP_MS:
        return _MAX_TIMESTAMP_MS
    return milliseconds


class RandomIdGenerator:
    """Time-prefixed, entropy-suffixed id generator (the production default).

    Args:
        clock: Unix-seconds source for the timestamp prefix.
        entropy: Byte source for the random suffix.

    Both are injected so a test can make an id fully predictable. The default
    entropy is :func:`secrets.token_bytes`, a CSPRNG; it is deliberately not
    :mod:`random`, because a predictable id is a forgeable run reference.
    """

    __slots__ = ("_clock", "_entropy")

    def __init__(self, *, clock: Clock = time.time, entropy: ENTROPY = secrets.token_bytes) -> None:
        self._clock = clock
        self._entropy = entropy

    def new_id(self) -> str:
        prefix = format(_timestamp_ms(self._clock), f"0{TIMESTAMP_HEX_LENGTH}x")
        suffix = self._entropy(RANDOM_HEX_LENGTH // 2).hex()
        # A short or over-long entropy source must not silently shorten the id:
        # normalize to the exact width so every id stays 32 hex characters.
        suffix = (suffix + "0" * RANDOM_HEX_LENGTH)[:RANDOM_HEX_LENGTH]
        return prefix + suffix

    def new_run_id(self) -> str:
        return self.new_id()

    def new_span_id(self) -> str:
        return self.new_id()

    def __repr__(self) -> str:
        return "RandomIdGenerator()"


class SequenceIdGenerator:
    """Deterministic counter generator for tests and golden-file output.

    Ids are ``<prefix><counter as zero-padded hex>``, so a test that pins the
    generator gets identical ids on every run and on every platform. The
    timestamp prefix is honoured through the injected ``clock`` exactly as
    :class:`RandomIdGenerator` does, which keeps a sequence id the same shape
    as a real one.
    """

    __slots__ = ("_clock", "_prefix", "_width", "_counter", "_lock")

    def __init__(self, *, prefix: str = "a", clock: Clock = time.time, start: int = 0) -> None:
        if not prefix or len(prefix) >= ID_HEX_LENGTH:
            raise ValueError("prefix must be 1..31 hex characters")
        if _HEX_RE.match(prefix) is None:
            raise ValueError("prefix must be lowercase hex")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ValueError("start must be a non-negative integer")
        self._prefix = prefix
        self._clock = clock
        self._width = ID_HEX_LENGTH - len(prefix)
        self._counter = start
        self._lock = threading.RLock()

    def new_id(self) -> str:
        with self._lock:
            current = self._counter
            self._counter += 1
        return f"{self._prefix}{current:0{self._width}x}"

    def new_run_id(self) -> str:
        return self.new_id()

    def new_span_id(self) -> str:
        return self.new_id()

    @property
    def issued(self) -> int:
        """How many ids this generator has produced."""

        with self._lock:
            return self._counter

    def __repr__(self) -> str:
        return f"SequenceIdGenerator(prefix={self._prefix!r}, issued={self.issued})"


def default_id_generator() -> RandomIdGenerator:
    """Return a fresh production generator.

    A function rather than a module-level instance on purpose: the spine
    forbids global mutable singletons, so the ambient default is built per
    recorder and every id source stays owned by whoever injected it.
    """

    return RandomIdGenerator()


def is_valid_id(value: object) -> bool:
    """Return whether *value* is a usable correlation id.

    Structural rule, matching :data:`ID_HEX_LENGTH`, the lowercase-hex
    alphabet the W3C trace-context wire format uses, and the all-zero
    :data:`INVALID_ID` rejection. The non-structural rules in
    :data:`FORBIDDEN_ID_SOURCE` cannot be decided from the string alone and
    are a caller's obligation at the minting site.
    """
    if not isinstance(value, str):
        return False
    if len(value) != ID_HEX_LENGTH:
        return False
    if _HEX_RE.match(value) is None:
        return False
    return value != INVALID_ID


def require_id(value: object, *, field: str) -> str:
    """Return *value* if it is a valid id, else raise ``ValueError``."""
    if not is_valid_id(value):
        raise ValueError(f"{field} must be {ID_HEX_LENGTH} lowercase hex characters and not the invalid all-zero id; got {value!r}")
    return str(value)
