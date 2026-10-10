"""Bounded primitives for a connectivity wait, kept out of the runtime engine.

Why this exists
---------------
A node whose work needs the network used to have exactly two fates: it ran and
maybe failed, or it parked forever on an ``EVENT_WAIT`` nobody could resolve.
Neither is the truth about an outage — the work is alive, the link is down, and
the distinction between "the task failed" and "the task is waiting for the
world" is the one ``alpha.runtime.network`` was built to make. The workflow
plane had no first-class state of its own for that condition.

This module holds the *policy* of a connectivity wait — the registry marker,
the release signal name, deadline parsing, and the evidence wording — as pure
functions, so the rules are testable without an engine, a probe, or a run. The
engine wiring (``runtime.py``) and the host-bound probe stay deliberately
separate: the probe is a callable a host installs, because deciding whether a
link is up is a measurement, and this package does not measure anything.

Honesty rules
-------------
* **No probe, no wait.** When no probe is bound the node FAILS with the real
  reason. A connectivity wait with no way to observe connectivity would be a
  wait that can only end by deadline — a dressed-up sleep.
* **Reachable is measured, never assumed.** Release requires a fresh
  ``probe()`` returning true; a signal is an explicit operator/host override
  that is journalled as such.
* **The deadline is a bound, not a timeout.** Past it the wait FAILS with the
  measured age, exactly like every other external wait: a wait nobody
  satisfies must end as a failure rather than parking a run forever.
"""

from __future__ import annotations

import math
import socket
import urllib.request
from typing import Any

__all__ = [
    "CONNECTIVITY_WAIT_KIND",
    "DEFAULT_RELEASE_EVENT",
    "MAX_CONNECTIVITY_WAIT_SECONDS",
    "connectivity_evidence",
    "parse_deadline_seconds",
    "release_event_for",
    "http_probe",
    "tcp_probe",
    "dns_probe",
]

#: Marker recorded in the run's external-wait registry so the sweep, the
#: signal path, and the release path can tell a connectivity wait from an
#: ordinary event wait without guessing from the node kind.
CONNECTIVITY_WAIT_KIND = "connectivity"

#: The signal name that releases a parked connectivity wait. A host (or an
#: operator) that knows the link is back can deliver it through the existing
#: ``signal_event`` seam, which matches on this name exactly — a typo'd signal
#: releases nothing, which is the point.
DEFAULT_RELEASE_EVENT = "connectivity.restored"

#: Hard ceiling on a connectivity wait's declared deadline, mirroring
#: ``MAX_WAIT_SECONDS`` for timers: an unbounded wait parks a run for as long
#: as a typo says.
MAX_CONNECTIVITY_WAIT_SECONDS = 3600.0


def parse_deadline_seconds(*candidates: Any) -> float | None:
    """The first parseable, non-negative numeric candidate, else ``None``.

    Candidates arrive in declaration order (``config.timeout_seconds``, then
    ``node.gate_timeout_seconds``). A negative or non-numeric value is refused
    with ``None`` — the caller fails the node with the real declaration — so a
    malformed deadline can never silently become an unbounded wait.
    """
    for candidate in candidates:
        if candidate is None or isinstance(candidate, bool):
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        return min(value, MAX_CONNECTIVITY_WAIT_SECONDS)
    return None


def release_event_for(config: dict[str, Any] | None) -> str:
    """The signal name that releases this wait, defaulting to the shared one."""
    declared = (config or {}).get("release_event")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return DEFAULT_RELEASE_EVENT


def connectivity_evidence(*, reachable: bool, target: str, release: str) -> str:
    """The node's evidence line for a resolved connectivity wait.

    States *how* the wait resolved — measured reachable versus an explicit
    release signal — because those are different truths about the world and an
    operator reading the evidence must be able to tell them apart.
    """
    if reachable:
        return f"connectivity to {target} measured reachable by the host-bound probe"
    return f"connectivity wait released by signal '{release}'; reachability was asserted, not measured"


# ---------------------------------------------------------------------------
# Ready-made probe implementations
#
# A host installs one of these via ``engine.connectivity_probe = ...``. Each
# callable takes no arguments and returns ``True`` when the link is measured
# reachable, ``False`` otherwise. They never raise — a probe that cannot
# measure reports unreachable, because a connectivity wait with no measurement
# is a wait that can only end at its deadline.
# ---------------------------------------------------------------------------


def http_probe(url: str, *, timeout_seconds: float = 5.0, expected_status: int = 200):
    """Build an HTTP probe that GETs ``url`` and checks the status code.

    The returned callable is suitable for ``engine.connectivity_probe``. A
    non-2xx response, a connection error, or a timeout all report unreachable.
    """

    def probe() -> bool:
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.status == expected_status
        except Exception:
            return False

    return probe


def tcp_probe(host: str, port: int, *, timeout_seconds: float = 5.0):
    """Build a TCP probe that attempts a socket connection to ``host:port``.

    The returned callable is suitable for ``engine.connectivity_probe``. A
    refused connection, DNS failure, or timeout all report unreachable.
    """

    def probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return True
        except Exception:
            return False

    return probe


def dns_probe(hostname: str, *, timeout_seconds: float = 5.0):
    """Build a DNS probe that resolves ``hostname`` and reports success.

    The returned callable is suitable for ``engine.connectivity_probe``. A
    resolution failure or timeout reports unreachable.
    """

    def probe() -> bool:
        try:
            socket.setdefaulttimeout(timeout_seconds)
            socket.getaddrinfo(hostname, None)
            return True
        except Exception:
            return False

    return probe
