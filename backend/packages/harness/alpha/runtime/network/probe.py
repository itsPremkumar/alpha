"""Connectivity probes: bounded TCP reachability, and nothing more.

What a probe may and may not do
-------------------------------
A connectivity check runs on a timer, for the whole life of the process, with
no user request attached. That makes it a poor place to be careless, so this
module is deliberately narrow:

* **TCP connect only.** No HTTP request, no TLS handshake, no payload. Opening a
  socket to ``host:443`` and closing it proves a route exists and costs one
  syscall pair. An HTTP ``GET`` would additionally prove a *server* is healthy,
  which is a provider-health question this layer must not answer, and would
  send a request identifying the user to a third party on a timer.
* **Only host/port pairs the operator declared.** The default set is public
  well-known endpoints; nothing is resolved from user data, no thread id, run
  id, prompt, or hostname leaks into a probe.
* **Every connect is bounded** by its own timeout, and the whole probe is
  bounded by the caller's deadline, so a black-holed route cannot hold the
  monitor's poll loop.
* **No caching across calls.** A stale "it was up" is worse than a fresh
  measurement for a liveness signal.

The probe returns per-target outcomes rather than a single boolean so
``DEGRADED`` is observable instead of being flattened into "up" or "down".
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from alpha.runtime.network.errors import NetworkFailureKind, classify_network_error

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PROBE_TARGETS",
    "ConnectivityProbe",
    "ProbeOutcome",
    "ProbeTarget",
    "ScriptedProbe",
    "TcpConnectivityProbe",
]


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """One endpoint to test reachability against."""

    name: str
    host: str
    port: int = 443
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ProbeTarget.name must be non-empty")
        if not self.host:
            raise ValueError("ProbeTarget.host must be non-empty")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError(f"ProbeTarget.port out of range: {self.port!r}")
        if not 0 < float(self.timeout_seconds) <= 30.0:
            raise ValueError(f"ProbeTarget.timeout_seconds must be in (0, 30], got {self.timeout_seconds!r}")


#: Two independent operators' resolvers. If both are unreachable the link is
#: almost certainly gone; if exactly one is, it is a provider-side problem, not
#: the user's connection -- which is precisely the ``DEGRADED`` case.
DEFAULT_PROBE_TARGETS: tuple[ProbeTarget, ...] = (
    ProbeTarget(name="cloudflare", host="1.1.1.1", port=443),
    ProbeTarget(name="google", host="8.8.8.8", port=443),
)


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """What one target answered.

    ``elapsed_ms`` is measured, never estimated, and is reported even on failure
    so an operator can tell a fast refusal from a black-holed route.
    """

    target: str
    reachable: bool
    elapsed_ms: float
    failure_kind: NetworkFailureKind = NetworkFailureKind.UNKNOWN
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "reachable": self.reachable,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "failure_kind": self.failure_kind.value,
            "detail": self.detail,
        }


@runtime_checkable
class ConnectivityProbe(Protocol):
    """Something that can measure reachability of a set of targets."""

    async def probe(self, targets: Sequence[ProbeTarget]) -> tuple[ProbeOutcome, ...]:
        """Return one outcome per target, in the order given.

        Implementations must not raise for an unreachable target: an
        unreachable endpoint is a *result*, not an error. Raising is reserved
        for "the probe itself could not run", which the monitor reports as
        ``UNKNOWN`` rather than ``OFFLINE``.
        """


class TcpConnectivityProbe:
    """Production probe: a bounded TCP connect per target, run concurrently.

    Coroutines are gathered so a slow target does not serialize behind a fast
    one, and each connect carries its own timeout plus a whole-probe deadline as
    a second line of defence.
    """

    __slots__ = ("_max_concurrency", "_overall_timeout_seconds")

    def __init__(self, *, max_concurrency: int = 4, overall_timeout_seconds: float = 10.0) -> None:
        self._max_concurrency = max(1, int(max_concurrency))
        self._overall_timeout_seconds = max(0.1, float(overall_timeout_seconds))

    async def probe(self, targets: Sequence[ProbeTarget]) -> tuple[ProbeOutcome, ...]:
        if not targets:
            return ()
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def one(target: ProbeTarget) -> ProbeOutcome:
            async with semaphore:
                return await self._probe_one(target)

        gathered = await asyncio.gather(*(one(target) for target in targets))
        return tuple(outcome for outcome in gathered if outcome is not None)

    async def _probe_one(self, target: ProbeTarget) -> ProbeOutcome:
        started = time.perf_counter()
        try:
            connection = asyncio.open_connection(target.host, target.port)
            reader, writer = await asyncio.wait_for(connection, timeout=target.timeout_seconds)
        except TimeoutError:
            return ProbeOutcome(target=target.name, reachable=False, elapsed_ms=_elapsed_ms(started), failure_kind=NetworkFailureKind.TIMEOUT, detail="connect timeout")
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - classified, never re-raised
            failure = classify_network_error(exc)
            logger.debug("network probe %s failed: %s", target.name, failure.kind.value)
            return ProbeOutcome(target=target.name, reachable=False, elapsed_ms=_elapsed_ms(started), failure_kind=failure.kind, detail=failure.detail)
        else:
            # Reachability is proven by the completed connect; the payload is
            # never read, so no application bytes cross the wire.
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):  # pragma: no cover - best effort teardown
                pass
            del reader
            return ProbeOutcome(target=target.name, reachable=True, elapsed_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


@dataclass
class ScriptedProbe:
    """Deterministic probe for tests: a queued list of per-call results.

    Each entry is a sequence of booleans (or :class:`ProbeOutcome`) consumed one
    per :meth:`probe` call, in order. Exhausting the script repeats its last
    entry, so a test only has to describe the interesting prefix. Recording every
    call makes "the monitor did not re-probe while offline" assertable.
    """

    script: list[Sequence[bool | ProbeOutcome]] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def probe(self, targets: Sequence[ProbeTarget]) -> tuple[ProbeOutcome, ...]:
        names = tuple(target.name for target in targets)
        self.calls.append(names)
        if not self.script:
            reachable = (True,) * len(targets)
        else:
            index = min(len(self.calls) - 1, len(self.script) - 1)
            entry = self.script[index]
            reachable = tuple(bool(item) if isinstance(item, bool) else item.reachable for item in entry) or (True,) * len(targets)
        return tuple(
            ProbeOutcome(
                target=target.name,
                reachable=bool(reachable[position]) if position < len(reachable) else True,
                elapsed_ms=1.0 if (position < len(reachable) and reachable[position]) else 0.0,
                failure_kind=NetworkFailureKind.UNKNOWN if (position < len(reachable) and reachable[position]) else NetworkFailureKind.NETWORK_UNREACHABLE,
            )
            for position, target in enumerate(targets)
        )


def normalize_targets(targets: Iterable[ProbeTarget] | None) -> tuple[ProbeTarget, ...]:
    """Return a validated, de-duplicated, deterministically ordered target set."""
    if targets is None:
        return DEFAULT_PROBE_TARGETS
    unique: dict[str, ProbeTarget] = {}
    for target in targets:
        unique.setdefault(target.name, target)
    if not unique:
        raise ValueError("network probe needs at least one target")
    return tuple(unique[name] for name in sorted(unique))
