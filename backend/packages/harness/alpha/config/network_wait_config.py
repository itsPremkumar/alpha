"""``config.yaml -> network_wait`` — how long a parked session keeps waiting.

Startup-only, for the same reason :mod:`alpha.config.network_resilience_config`
is: the registry owns a background recovery pass, an in-flight backoff schedule,
and a `network_waits` row that other subsystems already read. Swapping the
policy mid-flight would split the process across two answers to "how long do we
wait?" — and a row that was admitted under one ceiling could be surrendered
under another.

The dataclass this mirrors, :class:`alpha.runtime.network.NetworkWaitPolicy`,
is the runtime's own dependency-free policy object. This pydantic model exists
only to validate operator input and translate it.

The load-bearing field
----------------------
``max_attempts`` defaults to ``0``, which means **unbounded**. That is a
deliberate reversal of the usual instinct that everything must have a ceiling:

- a wait is a *parked task*, not a retry loop. The task did nothing wrong; the
  internet did.
- how loudly Alpha re-checks the link is already bounded, by the monitor's own
  ``network.backoff_*`` ladder, which caps at 300s by default and **never stops
  polling**;
- how many times a continuation that keeps dying may be relaunched is bounded
  separately, by ``run_ownership.max_resume_attempts``.

Charging the *wait* against a fixed count is what turns "the internet was gone
for an hour" into "gave up after 24 tries" — a session abandoned for surviving
an outage that outlasted a constant. An operator who genuinely wants a ceiling
sets a positive number, and exhaustion is then reported as ``gave_up`` with the
reason rather than silently dropping the session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from alpha.runtime.network.wait_registry import NetworkWaitPolicy

__all__ = ["NetworkWaitConfig", "to_wait_policy"]


class NetworkWaitConfig(BaseModel):
    """Operator policy for durable parked-session bookkeeping."""

    model_config = {"extra": "forbid"}

    enabled: bool = Field(default=True, description="When false, no recovery pass is started and no park is recorded. Connectivity is still measured by the network monitor.")
    max_attempts: int = Field(
        default=0,
        ge=0,
        le=100000,
        description=(
            "startup-only: Resume attempts before a wait is surrendered as `gave_up`. "
            "0 (the default) means UNBOUNDED — an internet outage that outlasts a counter "
            "must not abandon a task that did nothing wrong. Raise the bound only when an "
            "operator is expected to intervene."
        ),
    )
    backoff_initial_seconds: float = Field(default=15.0, gt=0.0, le=86400.0, description="First delay before a parked session is retried after a failed resume.")
    backoff_max_seconds: float = Field(default=900.0, gt=0.0, le=86400.0, description="Ceiling for the retry ladder. Real ceiling: the multiplier is applied before the clamp, never after.")
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0, description="Growth factor per failed resume attempt.")
    poll_interval_seconds: float = Field(default=30.0, gt=0.0, le=3600.0, description="How often the recovery pass sweeps for waits whose backoff has elapsed.")
    claim_lease_seconds: float = Field(default=30.0, gt=0.0, le=600.0, description="Lease on a wait being resumed, so two gateway instances cannot both launch a continuation for the same row.")
    max_claims_per_pass: int = Field(default=5, ge=1, le=500, description="Waits one pass may take. Bounded so a large backlog does not stampede the provider the instant the link returns.")

    @model_validator(mode="after")
    def _validate_ladder(self) -> NetworkWaitConfig:
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("network_wait.backoff_max_seconds must be >= network_wait.backoff_initial_seconds")
        return self


def to_wait_policy(config: NetworkWaitConfig) -> NetworkWaitPolicy:
    """Translate the validated operator policy into the runtime policy object.

    The import is inside the function, never at module scope: ``alpha.runtime``
    pulls in the agent/sandbox chain, which imports ``alpha.config`` back, so a
    module-level import from config into runtime is a cycle. The same hazard is
    documented in :mod:`alpha.config.network_resilience_config`.
    """
    from alpha.runtime.network.wait_registry import NetworkWaitPolicy as _NetworkWaitPolicy

    return _NetworkWaitPolicy(
        max_attempts=config.max_attempts,
        backoff_initial_seconds=config.backoff_initial_seconds,
        backoff_max_seconds=config.backoff_max_seconds,
        backoff_multiplier=config.backoff_multiplier,
        poll_interval_seconds=config.poll_interval_seconds,
        claim_lease_seconds=config.claim_lease_seconds,
        max_claims_per_pass=config.max_claims_per_pass,
    )
