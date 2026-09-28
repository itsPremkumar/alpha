"""Network-aware execution: connectivity as a first-class runtime state.

The gap
-------
Alpha had no internet-connectivity awareness at all. There was no probe, no
network-state store, and no way to say "this task is alive but parked because
the link is down". An outage therefore surfaced as an opaque provider
exception, the run terminalized as ``error``, and the work was lost even though
nothing about the *task* had failed. That is exactly the failure the
durable-runtime contract forbids: **an internet outage must not become a task
failure**.

What lives here
---------------
=========================  =================================================
:mod:`.states`            The four-state vocabulary and what each one permits.
:mod:`.errors`            Transport failure -> a claim about the *link*, never
                          about the work.
:mod:`.probe`             Bounded TCP reachability. No payload, no user data.
:mod:`.monitor`           Poll, fold, apply hysteresis, back off, publish.
:mod:`.wait_registry`     Durable parked sessions: park, bounded resume, and
                          the join back to the recovery owner.
=========================  =================================================

The four rules that matter
--------------------------
1. ``UNKNOWN`` is a real state and is **never** rounded to ``OFFLINE``. A probe
   that cannot run is a broken probe, and announcing "you are offline" because
   the probe is misconfigured would park every session on a lie.
2. ``UNKNOWN`` still **allows** a network attempt. Not knowing is not the same
   as knowing the link is down; the ordinary provider retry path is better at
   handling a failure than a global state flip is.
3. ``DEGRADED`` never parks a session. Partial connectivity is not an outage.
4. A single failed probe never flips anything. Entering ``OFFLINE`` and
   re-entering ``ONLINE`` each need corroborating consecutive observations,
   because those are the two moves that stop and restart work.

Everything here is a *measurement and a decision*. Nothing here cancels a run,
resumes a run, or writes durable state -- that is
:mod:`alpha.runtime.network.wait_registry` plus the existing
``app.gateway.run_recovery.SafeRunRecoveryService``, which remain the owners.
"""

from alpha.runtime.network.errors import (
    DEFINITIVE_LINK_FAILURE_KINDS,
    NetworkFailure,
    NetworkFailureKind,
    classify_network_error,
    proves_link_unavailable,
)
from alpha.runtime.network.monitor import (
    NETWORK_LOST_EVENT,
    NETWORK_RESTORED_EVENT,
    NETWORK_STATE_CHANGED_EVENT,
    NetworkMonitor,
    NetworkMonitorConfig,
    NetworkObservation,
    NetworkWaitDecision,
)
from alpha.runtime.network.probe import (
    DEFAULT_PROBE_TARGETS,
    ConnectivityProbe,
    ProbeOutcome,
    ProbeTarget,
    ScriptedProbe,
    TcpConnectivityProbe,
    normalize_targets,
)
from alpha.runtime.network.states import (
    CONNECTED_NETWORK_STATES,
    NETWORK_STATE_DETAIL,
    NETWORK_STATE_ORDER,
    NetworkState,
    is_connected,
    is_offline,
)
from alpha.runtime.network.wait_registry import (
    NetworkWaitPolicy,
    NetworkWaitService,
    NetworkWaitStatus,
    NetworkWaitStore,
    ParkOutcome,
    ResumeOutcome,
    get_network_wait_service,
    park_session_if_available,
    set_network_wait_service,
)

__all__ = [
    "CONNECTED_NETWORK_STATES",
    "DEFAULT_PROBE_TARGETS",
    "DEFINITIVE_LINK_FAILURE_KINDS",
    "NETWORK_LOST_EVENT",
    "NETWORK_RESTORED_EVENT",
    "NETWORK_STATE_CHANGED_EVENT",
    "NETWORK_STATE_DETAIL",
    "NETWORK_STATE_ORDER",
    "ConnectivityProbe",
    "NetworkFailure",
    "NetworkFailureKind",
    "NetworkMonitor",
    "NetworkMonitorConfig",
    "NetworkObservation",
    "NetworkState",
    "NetworkWaitPolicy",
    "NetworkWaitService",
    "NetworkWaitStatus",
    "NetworkWaitStore",
    "NetworkWaitDecision",
    "ParkOutcome",
    "ProbeOutcome",
    "ProbeTarget",
    "ResumeOutcome",
    "ScriptedProbe",
    "TcpConnectivityProbe",
    "classify_network_error",
    "get_network_wait_service",
    "is_connected",
    "is_offline",
    "normalize_targets",
    "park_session_if_available",
    "proves_link_unavailable",
    "set_network_wait_service",
]
