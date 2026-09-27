"""Process supervision with a bounded, non-looping restart policy.

The problem
-----------
The naive supervisor is ``while not healthy: start()`` with a sleep between
attempts. It is a liability: a backend that cannot start — a bad config, a port
already bound, a migration that will not apply — is restarted forever, burning
CPU, writing the same error line, and making the machine unusable for the
operator who needs to fix it. The durable-runtime contract names this directly:
*never create a blind infinite restart loop.*

=========================  ===================================================
:mod:`.policy`            `SupervisorPolicy`, `RestartLedger`,
                           `SupervisorReason`, `RestartAction`,
                           `RestartDecision` — pure policy, no processes.
:mod:`.supervisor`        `ProcessSupervisor` — start, watch, health-check,
                           decide, collect diagnostics.
=========================  ===================================================

The full contract — why the limiter is a sliding window rather than another
consecutive-failure counter, why safe mode is one attempt and not a ladder, why
liveness is not health, and why the supervised child is not the source of truth
for any task's progress — is in
[AGENTS.md](AGENTS.md) next to this file.
"""

from alpha.runtime.supervisor.policy import (
    DEFAULT_SUPERVISOR_POLICY,
    RestartAction,
    RestartDecision,
    RestartLedger,
    SupervisorPolicy,
    SupervisorReason,
)
from alpha.runtime.supervisor.supervisor import (
    ProcessSupervisor,
    SupervisorReport,
    SupervisorStatus,
)

__all__ = [
    "DEFAULT_SUPERVISOR_POLICY",
    "ProcessSupervisor",
    "RestartAction",
    "RestartDecision",
    "RestartLedger",
    "SupervisorPolicy",
    "SupervisorReason",
    "SupervisorReport",
    "SupervisorStatus",
]
