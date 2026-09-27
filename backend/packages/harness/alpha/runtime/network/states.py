"""Connectivity as a first-class runtime state.

Why this exists
---------------
Alpha had no notion of internet connectivity at all. There is no probe, no
network-state store, and no run/thread state for "alive but parked because the
link is down" -- so an outage surfaced as an opaque provider exception, the run
terminalized as ``error``, and the task was lost even though nothing about the
*work* had failed. That is precisely what the durable-runtime contract forbids
("an internet outage must not become a task failure").

Four states, not two
--------------------
:data:`NetworkState.UNKNOWN` is a real state and not a shrug. Before the first
probe, and whenever the probe itself cannot be executed, Alpha genuinely does
not know. Collapsing that into ``OFFLINE`` would park every session on a
Gateway whose probe is misconfigured; collapsing it into ``ONLINE`` would hide
a real outage. The rule that follows from it is the important one:

    :meth:`NetworkState.allows_network_attempt` is **true** for ``UNKNOWN``.

When we do not know, we try, and the ordinary provider retry path handles a
failure. Only a *confirmed* outage (``OFFLINE``) refuses an attempt up front,
and ``DEGRADED`` -- some targets reachable, some not -- still allows one,
because a partially-working link is not a reason to stop.

Hysteresis lives in :mod:`alpha.runtime.network.monitor`, not here
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "CONNECTED_NETWORK_STATES",
    "NETWORK_STATE_DETAIL",
    "NETWORK_STATE_ORDER",
    "NetworkState",
    "NetworkStateTransitionError",
    "is_connected",
    "is_offline",
]


class NetworkState(StrEnum):
    """Link availability, as far as it can honestly be determined."""

    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


#: States in which a network-dependent operation is worth attempting.
#: ``UNKNOWN`` is here on purpose -- see the module docstring.
CONNECTED_NETWORK_STATES: Final[frozenset[NetworkState]] = frozenset({NetworkState.ONLINE, NetworkState.DEGRADED, NetworkState.UNKNOWN})

#: Severity order, worst last. Used to decide whether a new observation is an
#: improvement or a regression for backoff purposes.
NETWORK_STATE_ORDER: Final[dict[NetworkState, int]] = MappingProxyType({NetworkState.ONLINE: 0, NetworkState.DEGRADED: 1, NetworkState.UNKNOWN: 2, NetworkState.OFFLINE: 3})

#: Operator-facing one-liners, reused verbatim by the UI and the CLI so a
#: "waiting for network" message is never reworded per surface.
NETWORK_STATE_DETAIL: Final[dict[NetworkState, str]] = MappingProxyType(
    {
        NetworkState.ONLINE: "Connectivity confirmed.",
        NetworkState.DEGRADED: "Partial connectivity: some endpoints are unreachable, so some providers may fail.",
        NetworkState.OFFLINE: "No connectivity. Work is paused and will resume automatically when the link returns.",
        NetworkState.UNKNOWN: "Connectivity has not been determined yet; network operations will be attempted and handled by the ordinary retry path.",
    }
)


class NetworkStateTransitionError(ValueError):
    """A requested network-state change is not legal."""


def is_offline(state: NetworkState) -> bool:
    """True only for a confirmed outage."""
    return state is NetworkState.OFFLINE


def is_connected(state: NetworkState) -> bool:
    """True when a network-dependent operation is worth attempting.

    Includes ``UNKNOWN`` deliberately: not knowing is not the same as knowing
    the link is down, and refusing every call on a misconfigured probe would
    turn an observability bug into a total outage.
    """
    return state in CONNECTED_NETWORK_STATES
