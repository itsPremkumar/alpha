"""Durable network-wait persistence.

A *network wait* is one parked session: a run that needed connectivity, lost it,
and is waiting for the link to return. The row makes that fact survive a restart,
so a task parked on a dead network is not lost between the moment it parks and
the moment a boot happens to observe a restored link.

It records **where the work got to**, nothing more. Park and resume remain the
decision of
:class:`app.gateway.run_recovery.SafeRunRecoveryService` through the normal
``start_run`` path, exactly as they are for a crash.
"""

from alpha.persistence.network_waits.model import (
    OPEN_NETWORK_WAIT_STATES,
    TERMINAL_NETWORK_WAIT_STATES,
    NetworkWaitRow,
)
from alpha.persistence.network_waits.sql import (
    DEFAULT_CLAIM_LEASE_SECONDS,
    NetworkWaitRepository,
)

__all__ = [
    "DEFAULT_CLAIM_LEASE_SECONDS",
    "OPEN_NETWORK_WAIT_STATES",
    "TERMINAL_NETWORK_WAIT_STATES",
    "NetworkWaitRepository",
    "NetworkWaitRow",
]
