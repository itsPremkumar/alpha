"""Stable, prefixed, **sortable** identifiers for the graph plane.

Why this module exists
----------------------
Every relationship in :mod:`alpha.graph` -- a goal to its tasks, an attempt to
its task, an artifact to the node that made it -- is carried by an id, so an id
is an **address**. The repository has already paid for learning what happens
when an address can repeat: ``alpha.workflow.events`` used a bare microsecond
timestamp, and ``node_attempt_started`` / ``node_completed`` written back to
back routinely landed in the same microsecond and shared an id, so a fork
anchored on the wrong event and **repeated a side effect it had reported as
inherited**. An id that is merely *probably* unique is worse than no id at all,
because it converts a visible failure into a silent wrong answer.

So the shape is fixed here, once, for the whole plane:

    goal_1k3n8q2w-4f7a-01J9 ...

``{prefix}``  namespaces the entity, so a task id can never be read as a run id.
``1k3n8q2w``  is a Crockford base32 encoding of a millisecond timestamp. This
is what makes ids *sortable*: lexicographic order over ids issued by one process
is chronological order, which is why the event log can page by id without
sorting on a second column that no index covers.
``4f7a``       is a per-process monotonic counter, **not** randomness. Two ids
raised in the same millisecond by the same process are distinguished by it.
``01J9``       is 32 bits of ``secrets`` entropy, which is what separates two
*processes* -- the case a counter cannot see and the case the workflow event log
got wrong.

The counter is why this is not ``uuid4``. Randomness alone is unordered, so a
consumer paging ``WHERE id > $cursor`` would skip or repeat rows whenever the
cursor was drawn from a different shard; and the whole point of these ids is
that they are addresses into append-only logs.

What this module does **not** do
--------------------------------
It does not authenticate anything. An id arriving from a request body is
untrusted input exactly like any other string, and the ledger re-checks it
against the row it claims to address. Uniqueness is not authorisation.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Final

__all__ = [
    "AttemptId",
    "ClaimId",
    "EventId",
    "GoalId",
    "GraphId",
    "TaskId",
    "make_id",
    "new_artifact_id",
    "new_attempt_id",
    "new_claim_id",
    "new_event_id",
    "new_goal_id",
    "new_graph_id",
    "new_task_id",
    "timestamp_of",
]

#: Crockford base32: no I, L, O or U, so an id read aloud or copied by hand
#: cannot gain or lose a character. Alphabet order is ASCII order, which is what
#: makes the timestamp prefix sort lexicographically.
_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE: Final[dict[str, int]] = {char: index for index, char in enumerate(_ALPHABET)}

#: 25 bits of entropy per id. Five base32 characters carry exactly 25 bits
#: (5 x 5), so the width and the bit count agree. 25 bits is 33 million
#: distinct values per process per millisecond, which is far more than any
#: single process will ever mint -- the entropy exists to separate *processes*,
#: not to make same-process ids unique (the counter does that).
_ENTROPY_BITS: Final[int] = 25
_ENTROPY_CHARS: Final[int] = 5

#: Monotonic per-process counter, guarded by a lock. ``time.monotonic_ns`` is
#: deliberately not used here: it is not comparable across processes, so it
#: would not give ids a stable global order, and the counter is only ever a
#: same-millisecond tiebreaker within one process anyway.
_counter_lock: Final[threading.Lock] = threading.Lock()
_counter: Final[list[int]] = [0]


def _b32(value: int, width: int) -> str:
    """Encode ``value`` as ``width`` base32 characters, most significant first."""
    if value < 0:
        raise ValueError("cannot base32-encode a negative value as an address")
    out: list[str] = []
    for _ in range(width):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    if value:
        raise ValueError(f"value does not fit in {width} base32 characters")
    return "".join(reversed(out))


def _next_counter() -> int:
    with _counter_lock:
        _counter[0] += 1
        return _counter[0]


def make_id(prefix: str, *, timestamp_ms: int | None = None, entropy: str | None = None) -> str:
    """Return one fresh identifier of the form ``{prefix}_{time}{counter}{rand}``.

    ``prefix`` is validated rather than trusted: an id with a space or a slash
    in it is not an address anything can be looked up by, and the failure would
    surface much later as an unrelated lookup miss. It is refused here.

    ``timestamp_ms`` and ``entropy`` exist so tests can pin a shape. Production
    callers use neither.
    """
    if not prefix or not prefix.isalnum():
        raise ValueError(f"id prefix must be alphanumeric, got {prefix!r}")

    millis = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    if millis < 0:
        raise ValueError("timestamp_ms must not be negative")

    # The counter is masked to 12 bits (4 base32 chars) and wraps. A wrap only
    # matters if the same process issues 4096 ids inside one millisecond *and*
    # draws no entropy, which is why entropy is always added below.
    counter = _b32(_next_counter() & 0xFFF, 4)

    if entropy is None:
        raw = secrets.randbits(_ENTROPY_BITS)
        rand = _b32(raw, _ENTROPY_CHARS)
    else:
        rand = _normalize_entropy(entropy)

    return f"{prefix}_{_b32(millis, 10)}{counter}{rand}"


def _normalize_entropy(entropy: str) -> str:
    """Accept a caller-supplied entropy token and render it at the fixed width.

    Test-supplied values are usually shorter than the real width. Zero-padding
    on the left keeps every id the same length -- a length invariant is what
    lets a reader reject a truncated address -- while preserving a caller-chosen
    value exactly.
    """
    token = "".join(char.upper() for char in entropy if char.upper() in _DECODE)
    if not token:
        raise ValueError(f"entropy {entropy!r} contains no base32 characters")
    if len(token) > _ENTROPY_CHARS:
        raise ValueError(f"entropy {entropy!r} exceeds {_ENTROPY_CHARS} base32 characters")
    return token.rjust(_ENTROPY_CHARS, "0")


# Typed aliases. These exist so a function signature can say *which* id it wants:
# ``task_id: TaskId`` cannot be passed an ``EventId`` by accident, which is the
# mistake a bare ``str`` invites. They are ``NewType``s, so they cost nothing at
# runtime and ``json``/``pydantic`` still serialise them as plain strings.
from typing import NewType  # noqa: E402  (kept beside its aliases, after the helpers)

GoalId = NewType("GoalId", str)
TaskId = NewType("TaskId", str)
GraphId = NewType("GraphId", str)
AttemptId = NewType("AttemptId", str)
EventId = NewType("EventId", str)
ClaimId = NewType("ClaimId", str)

ArtifactId = NewType("ArtifactId", str)
ApprovalId = NewType("ApprovalId", str)


def new_goal_id() -> GoalId:
    return GoalId(make_id("goal"))


def new_task_id() -> TaskId:
    return TaskId(make_id("task"))


def new_graph_id() -> GraphId:
    return GraphId(make_id("graph"))


def new_attempt_id() -> AttemptId:
    return AttemptId(make_id("attempt"))


def new_event_id() -> EventId:
    return EventId(make_id("evt"))


def new_claim_id() -> ClaimId:
    return ClaimId(make_id("claim"))


def new_artifact_id() -> ArtifactId:
    return ArtifactId(make_id("art"))


def new_approval_id() -> ApprovalId:
    return ApprovalId(make_id("apr"))


def timestamp_of(identifier: str) -> int | None:
    """Recover the millisecond timestamp an id was minted at, or ``None``.

    Returns ``None`` for anything that is not one of our ids rather than
    raising: this is used on ids arriving from persistence and from request
    bodies, and a caller asking "when was this minted?" wants an answer, not an
    exception. It is a *hint* for ordering and retention sweeps -- never an
    authority on anything. The caller still has to validate the id against the
    row it claims to address.
    """
    _, separator, remainder = identifier.partition("_")
    if not separator or len(remainder) < 10:
        return None
    value = 0
    for char in remainder[:10]:
        digit = _DECODE.get(char.upper())
        if digit is None:
            return None
        value = (value << 5) | digit
    return value
