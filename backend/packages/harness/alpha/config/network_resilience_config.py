"""``config.yaml -> network`` — connectivity monitoring policy.

Startup-only, deliberately. The monitor owns a background poll task, an
in-flight backoff schedule, and a published state that other subsystems have
already read; swapping any of those mid-flight would split the process across two
policies. The section is registered in
:mod:`alpha.config.reload_boundary` as a restart-required field.

The dataclass this mirrors, :class:`alpha.runtime.network.NetworkMonitorConfig`,
is the runtime's own dependency-free policy object. This pydantic model exists
only to validate operator input and translate it; validation lives here so the
runtime never has to import pydantic to read a number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from alpha.runtime.network.probe import ProbeTarget

__all__ = ["NetworkProbeTargetConfig", "NetworkResilienceConfig", "to_monitor_config"]


class NetworkProbeTargetConfig(BaseModel):
    """One operator-declared reachability endpoint."""

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=64, description="Stable identifier reported in observations and events.")
    host: str = Field(min_length=1, max_length=253, description="Hostname or IP literal to open a TCP connection against.")
    port: int = Field(default=443, ge=1, le=65535, description="TCP port. 443 keeps the check to provider-grade endpoints.")
    timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0, description="Per-target connect deadline. One black-holed target cannot stall the poll beyond this.")

    def to_target(self) -> ProbeTarget:
        # Imported here, never at module scope: ``alpha.runtime`` pulls in the
        # agent/sandbox chain, which imports ``alpha.config`` back, so a
        # module-level import from config into runtime is a cycle. The same
        # hazard is why the harness root guide asks internal modules to import
        # concrete submodules lazily.
        from alpha.runtime.network.probe import ProbeTarget as _ProbeTarget

        return _ProbeTarget(name=self.name, host=self.host, port=self.port, timeout_seconds=self.timeout_seconds)


class NetworkResilienceConfig(BaseModel):
    """Operator policy for internet-connectivity awareness."""

    model_config = {"extra": "forbid"}

    enabled: bool = Field(default=True, description="When false, no background poll task is started. A one-shot probe is still available through the monitor API.")
    poll_interval_seconds: float = Field(default=15.0, gt=0.0, le=3600.0, description="Base interval between probes while connectivity is ONLINE.")
    offline_after_consecutive: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Consecutive unreachable observations required before publishing OFFLINE. Raising it avoids parking work on a single dropped packet; lowering it detects an outage sooner at the cost of a false positive.",
    )
    online_after_consecutive: int = Field(default=2, ge=1, le=20, description="Consecutive reachable observations required before publishing ONLINE again. Raising it avoids resuming work on one lucky packet.")
    unknown_after_consecutive: int = Field(default=3, ge=1, le=20, description="Consecutive probe executions that fail before a known-good link is reported as UNKNOWN.")
    backoff_initial_seconds: float = Field(default=5.0, gt=0.0, le=3600.0, description="First backoff step used while connectivity is not ONLINE.")
    backoff_max_seconds: float = Field(default=300.0, gt=0.0, le=86400.0, description="Ceiling for the backoff ladder. This is a real ceiling: jitter only ever reduces the delay.")
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0, description="Growth factor per offline poll.")
    backoff_jitter_ratio: float = Field(default=0.25, ge=0.0, le=1.0, description="Fraction by which each delay may be randomly reduced, so a fleet of hosts that lost the link together does not retry in lockstep.")
    max_observations: int = Field(default=50, ge=1, le=1000, description="Per-monitor in-memory observation history bound.")
    targets: list[NetworkProbeTargetConfig] | None = Field(default=None, description="Reachability endpoints. Omit to use the built-in public resolvers on provider-grade ports.")

    @model_validator(mode="after")
    def _validate_backoff_ladder(self) -> NetworkResilienceConfig:
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("network.backoff_max_seconds must be >= network.backoff_initial_seconds")
        if self.targets is not None and not self.targets:
            raise ValueError("network.targets must be omitted or non-empty; an empty list would leave connectivity permanently unknown")
        names = [target.name for target in self.targets or ()]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"network.targets has duplicate names: {duplicates}")
        return self

    def to_probe_targets(self) -> tuple[ProbeTarget, ...]:
        """Return the resolved target set, defaulting to the built-in endpoints."""
        if not self.targets:
            from alpha.runtime.network.probe import DEFAULT_PROBE_TARGETS

            return DEFAULT_PROBE_TARGETS
        return tuple(target.to_target() for target in self.targets)


def to_monitor_config(config: NetworkResilienceConfig) -> object:
    """Translate the validated operator policy into the runtime policy object.

    Typed as ``object`` at the boundary to keep this module free of a hard
    dependency on the runtime package; the returned value is an
    :class:`alpha.runtime.network.NetworkMonitorConfig`.
    """
    from alpha.runtime.network.monitor import NetworkMonitorConfig

    return NetworkMonitorConfig(
        enabled=config.enabled,
        poll_interval_seconds=config.poll_interval_seconds,
        offline_after_consecutive=config.offline_after_consecutive,
        online_after_consecutive=config.online_after_consecutive,
        unknown_after_consecutive=config.unknown_after_consecutive,
        backoff_initial_seconds=config.backoff_initial_seconds,
        backoff_max_seconds=config.backoff_max_seconds,
        backoff_multiplier=config.backoff_multiplier,
        backoff_jitter_ratio=config.backoff_jitter_ratio,
        max_observations=config.max_observations,
    )
