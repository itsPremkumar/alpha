"""At-most-once execution for side effects: keys, claims and recorded outcomes.

The rule this module encodes is the one from *Making retries safe with
idempotent APIs* (AWS Builders' Library, 2021) and the idempotency-key
contract documented by Stripe's API: a client that retries a request after an
ambiguous failure must not cause a second charge, a second email, or a second
row. The client supplies a key that identifies *the intended effect*, the
server executes it once, and every later attempt for the same key gets the
first outcome back instead of a second execution.

What this module guarantees
---------------------------
* **At-most-once.** ``begin`` on a fresh key yields exactly one winner. A
  repeated key returns the recorded first outcome (``duplicate``) or, while
  the first execution is still running, a disclosed ``in_progress`` result. It
  never yields a second execution.
* **Single-flight admission.** ``begin`` is the atomic admission point, so two
  threads racing on one key produce one winner; the loser is told why.
* **Bounded retention.** Every record carries a TTL measured on the injected
  clock. After it expires the key is free again, and that is disclosed on the
  next claim: a key is a *bounded* at-most-once window, not eternal history.

Boundary and honesty
--------------------
* ``IdempotencyKey`` makes **no PII assumptions and no redaction**: it is a
  namespaced, stably serializable identifier. The caller decides what goes in
  the namespace and value; the kit never interprets the value. Do not put
  personal data or secrets in a key.
* This is an **in-process** guarantee. A durable, multi-process exactly-once
  story needs a shared backend (Postgres unique index, Redis SETNX); that
  backend is injected through :class:`IdempotencyBackend` and is out of scope
  here. Alpha's durable run idempotency
  (``persistence/run/sql.py`` ``uq_runs_idempotency_key``) is the model for a
  future shared backend.
* ``fail`` records a failure outcome like any other: a repeated key then
  returns that first failure rather than silently re-running a request that
  may already have taken effect. A caller who *wants* a retry after a known
  non-effect failure must mint a new key (a new attempt identity) - that is the
  honest choice, not a silent re-execution.

References (paraphrased, nothing copied):
* AWS Builders' Library, *Making retries safe with idempotent APIs* (2021).
* Kleppmann, *Designing Data-Intensive Applications* (O'Reilly, 2017), ch. 8:
  a retry is only safe when the operation is idempotent or fenced by a key.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from alpha.runtime.resilience.clock import Clock, coerce_clock
from alpha.runtime.resilience.errors import IdempotencyError

__all__ = [
    "IdempotencyBackend",
    "IdempotencyClaim",
    "IdempotencyKey",
    "IdempotencyRegistry",
    "IdempotencyStatus",
    "InMemoryIdempotencyBackend",
    "RunOutcome",
]


class IdempotencyStatus(StrEnum):
    """Outcome of a ``begin`` admission. Closed set; ``ACQUIRED`` is the only
    state that permits the side effect to run."""

    ACQUIRED = "acquired"
    """This caller won the claim and may execute the action."""

    IN_PROGRESS = "in_progress"
    """A first execution holds the claim; do not execute."""

    DUPLICATE = "duplicate"
    """The key already completed; ``outcome`` holds the FIRST result."""

    FAILED = "failed"
    """The first execution recorded a failure; do not re-run automatically."""


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """A namespaced, stably serializable idempotency identity.

    ``namespace`` scopes the producer (e.g. ``"runs"``); ``value`` is the
    caller's opaque identity for the intended effect. ``serialize`` is stable
    and reversible; ``fingerprint`` is a non-reversible digest suitable for
    logs (it is *not* a redaction of the value - the kit does not know or care
    whether the value is PII; do not put PII in it in the first place).
    """

    namespace: str
    value: str

    def __post_init__(self) -> None:
        if not self.namespace:
            raise ValueError("idempotency namespace must be non-empty")
        if not self.value:
            raise ValueError("idempotency value must be non-empty")

    def serialize(self) -> str:
        """Stable ``namespace:value`` serialization (round-trips)."""
        return f"{self.namespace}:{self.value}"

    def fingerprint(self) -> str:
        """Non-reversible digest of the serialized key, for logs."""
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()

    @classmethod
    def parse(cls, raw: str) -> IdempotencyKey:
        """Inverse of :meth:`serialize`; the first colon separates the parts."""
        namespace, sep, value = raw.partition(":")
        if not sep or not namespace or not value:
            raise ValueError(f"malformed idempotency key {raw!r}; expected 'namespace:value'")
        return cls(namespace=namespace, value=value)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """The recorded result of the one execution that a key owns."""

    ok: bool
    value: object = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Result of a ``begin`` admission attempt."""

    key: IdempotencyKey
    status: IdempotencyStatus
    outcome: RunOutcome | None = None
    token: str = ""
    """Opaque per-execution token; must be passed to ``complete``/``fail``."""

    @property
    def acquired(self) -> bool:
        return self.status is IdempotencyStatus.ACQUIRED

    @property
    def should_execute(self) -> bool:
        """True only for the single winner."""
        return self.status is IdempotencyStatus.ACQUIRED


@runtime_checkable
class IdempotencyBackend(Protocol):
    """Storage seam for the registry. Injected so a shared/durable store can be
    dropped in later without touching the registry logic.

    The contract is expressed as one atomic ``begin`` (compare-and-set) plus
    completion/failure writes; it is the ``begin`` atomicity that yields the
    single winner. TTL eviction is the backend's job and must be measured on the
    clock the backend is given.
    """

    def begin(self, key: str, *, now: float, ttl: float, token: str) -> IdempotencyClaim:
        """Atomically claim ``key``; see :class:`IdempotencyClaim`."""
        ...

    def get(self, key: str, *, now: float) -> IdempotencyClaim | None:
        """Read a live record without claiming it. ``None`` when absent/expired."""
        ...

    def complete(self, key: str, *, now: float, ttl: float, token: str, outcome: RunOutcome) -> None:
        """Persist ``outcome`` for the winning token."""
        ...

    def fail(self, key: str, *, now: float, ttl: float, token: str, outcome: RunOutcome, release: bool = False) -> None:
        """Persist a failure outcome for the winning token.

        ``release=True`` drops the record instead, which is the deliberate
        "this caller wants to try again" path: a retry that is *known* not to
        have taken effect may reuse the key. It is never automatic.
        """
        ...


@dataclass
class _Record:
    outcome: RunOutcome | None
    token: str
    expires_at: float
    settled: bool = False

    def claim(self, key: IdempotencyKey) -> IdempotencyClaim:
        """Project the record onto the public claim shape."""
        if not self.settled:
            return IdempotencyClaim(key=key, status=IdempotencyStatus.IN_PROGRESS, token="")
        status = IdempotencyStatus.DUPLICATE if self.outcome is not None and self.outcome.ok else IdempotencyStatus.FAILED
        return IdempotencyClaim(key=key, status=status, outcome=self.outcome, token="")


class InMemoryIdempotencyBackend:
    """Reference backend: a lock-guarded dict with TTL on the injected clock.

    One lock makes ``begin`` a genuine compare-and-set, so concurrent begins on
    one key yield exactly one winner. Expired entries are dropped lazily on the
    next access (never by a timer thread), so the backend owns no thread, holds
    no module-level state, and :meth:`_live` - the only mutating reader - must
    be called with the lock held.
    """

    __slots__ = ("_lock", "_records", "_clock")

    def __init__(self, clock: Clock) -> None:
        self._clock = coerce_clock(clock)
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}

    def _live(self, key: str, now: float) -> _Record | None:
        record = self._records.get(key)
        if record is None:
            return None
        if now >= record.expires_at:
            del self._records[key]
            return None
        return record

    def begin(self, key: str, *, now: float, ttl: float, token: str) -> IdempotencyClaim:
        with self._lock:
            record = self._live(key, now)
            if record is not None:
                return record.claim(IdempotencyKey.parse(key))
            self._records[key] = _Record(outcome=None, token=token, expires_at=now + ttl)
            return IdempotencyClaim(key=IdempotencyKey.parse(key), status=IdempotencyStatus.ACQUIRED, token=token)

    def get(self, key: str, *, now: float) -> IdempotencyClaim | None:
        with self._lock:
            record = self._live(key, now)
            return None if record is None else record.claim(IdempotencyKey.parse(key))

    def _settle(self, key: str, now: float, ttl: float, token: str, outcome: RunOutcome, release: bool) -> None:
        with self._lock:
            record = self._live(key, now)
            if record is None or record.token != token:
                raise IdempotencyError(f"no live claim for {key!r} with token {token!r}")
            if release:
                del self._records[key]
                return
            record.outcome = outcome
            record.settled = True
            record.expires_at = now + ttl

    def complete(self, key: str, *, now: float, ttl: float, token: str, outcome: RunOutcome) -> None:
        self._settle(key, now, ttl, token, outcome, release=False)

    def fail(self, key: str, *, now: float, ttl: float, token: str, outcome: RunOutcome, release: bool = False) -> None:
        self._settle(key, now, ttl, token, outcome, release=release)

    def __len__(self) -> int:
        """Number of stored records, expired ones included (diagnostics)."""
        return len(self._records)


class IdempotencyRegistry:
    """Key -> outcome store with TTL and at-most-once execution.

    The registry owns policy (TTL, execution wrapper); the backend owns storage
    and the atomic admission. Everything is per-instance: no module-level
    registry exists, so two hosts in one process cannot share state by
    accident.
    """

    __slots__ = ("_backend", "_clock", "_ttl", "_counter")
    _counter: int

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        ttl: float = 3600.0,
        backend: IdempotencyBackend | None = None,
    ) -> None:
        self._clock = coerce_clock(clock)
        if ttl <= 0:
            raise ValueError("idempotency ttl must be > 0")
        self._ttl = float(ttl)
        self._counter = 0
        self._backend = backend if backend is not None else InMemoryIdempotencyBackend(self._clock)

    @property
    def ttl(self) -> float:
        """Retention window for a record, in clock seconds."""
        return self._ttl

    def _next_token(self, key: IdempotencyKey) -> str:
        # Token is a non-secret correlation id, not a lock. Deterministic per
        # key + attempt counter so a debug log can match a claim to its settle.
        self._counter += 1
        return f"{key.fingerprint()[:12]}-{self._counter}"

    def begin(self, key: IdempotencyKey) -> IdempotencyClaim:
        """Attempt to win ``key``. See :class:`IdempotencyClaim`."""
        now = self._clock.now()
        token = self._next_token(key)
        return self._backend.begin(key.serialize(), now=now, ttl=self._ttl, token=token)

    def complete(self, key: IdempotencyKey, claim: IdempotencyClaim, outcome: RunOutcome) -> None:
        """Record the first successful (or settled) outcome for ``key``."""
        self._backend.complete(key.serialize(), now=self._clock.now(), ttl=self._ttl, token=claim.token, outcome=outcome)

    def fail(self, key: IdempotencyKey, claim: IdempotencyClaim, outcome: RunOutcome, *, release: bool = False) -> None:
        """Record a first failure for ``key`` (returned to later duplicates).

        ``release=True`` deletes the record instead: the deliberate "this
        attempt provably did not take effect, so the same key may be retried"
        path. It is never automatic.
        """
        self._backend.fail(
            key.serialize(),
            now=self._clock.now(),
            ttl=self._ttl,
            token=claim.token,
            outcome=outcome,
            release=release,
        )

    def execute(
        self,
        key: IdempotencyKey,
        action: Callable[[], object],
    ) -> IdempotencyClaim:
        """Run ``action`` at most once for ``key`` and return the claim.

        The winner runs the action and settles the record. Every other caller
        receives a non-``ACQUIRED`` claim carrying the first outcome (or the
        in-progress disclosure) and does **not** run the action. Exceptions from
        the action are settled as a failure and re-raised to the winner's
        caller - the kit does not swallow them.
        """
        claim = self.begin(key)
        if not claim.should_execute:
            return claim
        try:
            result = action()
        except Exception as exc:
            outcome = RunOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")
            self.fail(key, claim, outcome)
            raise
        outcome = RunOutcome(ok=True, value=result)
        self.complete(key, claim, outcome)
        return IdempotencyClaim(
            key=claim.key,
            status=IdempotencyStatus.ACQUIRED,
            outcome=outcome,
            token=claim.token,
        )

    def peek(self, key: IdempotencyKey) -> IdempotencyClaim | None:
        """Non-mutating lookup of a live record (no claim is taken, none created)."""
        return self._backend.get(key.serialize(), now=self._clock.now())

    def __len__(self) -> int:
        """Number of live records in an in-memory backend (diagnostics/tests)."""
        counter = getattr(self._backend, "__len__", None)
        if counter is None:
            raise TypeError(f"backend {type(self._backend).__name__} does not support len()")
        return int(counter())

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"IdempotencyRegistry(ttl={self._ttl!r}, backend={type(self._backend).__name__})"
