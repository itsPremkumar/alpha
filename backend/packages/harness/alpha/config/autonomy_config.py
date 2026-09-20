"""Autonomy subsystem configuration.

Single owner for the self-running parts of Alpha: the in-process event bus, the
observe-only agent middlewares, and the background loops orchestrated by the
AutonomySupervisor (``app/gateway/autonomy/supervisor.py``).

Design invariants:
* ``enabled: false`` on the master switch or on a loop means ZERO activity —
  no tasks are created, nothing ticks.
* Loops are additive observability/maintenance; none of them may gate a user
  request, and a missing API key must never make the system report unhealthy.
* Every loop is restart-budgeted: repeated crashes inside the window disable the
  loop for the process lifetime instead of spinning.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AutonomyLoopConfig(BaseModel):
    """Per-loop scheduling and safety envelope."""

    enabled: bool = Field(default=False, description="Whether this loop runs at all.")
    interval_seconds: float = Field(default=300.0, gt=0, description="Seconds between ticks.")
    jitter_seconds: float = Field(default=15.0, ge=0, description="Random jitter added per interval.")
    max_concurrent: int = Field(default=1, ge=1, description="Maximum overlapping ticks for this loop.")
    stop_timeout_seconds: float = Field(default=10.0, gt=0, description="Grace period before a tick is cancelled on shutdown.")
    restart_budget: int = Field(default=5, ge=1, description="Crash restarts allowed inside the window before the loop is parked.")
    restart_window_seconds: float = Field(default=600.0, gt=0, description="Sliding window for the restart budget.")


class AutonomyBusConfig(BaseModel):
    """In-process event bus sizing."""

    enabled: bool = Field(default=True, description="Master switch for the in-process event bus.")
    queue_maxsize: int = Field(default=256, ge=1, description="Per-subscriber bounded queue size (drop-oldest beyond it).")
    handler_timeout_seconds: float = Field(default=30.0, gt=0, description="Per-event handler timeout before the dispatch is abandoned.")


class MetacognitionConfig(BaseModel):
    """Observe-only metacognitive middleware."""

    enabled: bool = Field(default=True, description="Attach the metacognitive observer to the lead chain.")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Baseline confidence fed to the monitor.")
    window: int = Field(default=5, ge=3, description="Recent tool outcomes considered per assessment.")


class ContinualHarnessConfig(BaseModel):
    """Continual Harness reminder injection into the lead chain."""

    enabled: bool = Field(default=True, description="Inject learned directives/project memories into context.")


class AutonomyConfig(BaseModel):
    """Self-running subsystems: single supervisor, single event bus."""

    enabled: bool = Field(
        default=True,
        description="Master switch for the AutonomySupervisor and the event bus. False = no background activity.",
    )
    bus: AutonomyBusConfig = Field(default_factory=AutonomyBusConfig, description="In-process event bus configuration.")
    metacognition: MetacognitionConfig = Field(default_factory=MetacognitionConfig, description="Metacognitive observer middleware.")
    continual_harness: ContinualHarnessConfig = Field(default_factory=lambda: ContinualHarnessConfig(enabled=True), description="Continual Harness context injection middleware.")
    loops: dict[str, AutonomyLoopConfig] = Field(
        default_factory=dict,
        description=("Per-loop overrides keyed by loop id (sentinel, perpetual, review_queue, skill_curator, enterprise_heartbeat). A loop id absent here uses the supervisor default, which is DISABLED."),
    )

    def loop_config(self, loop_id: str) -> AutonomyLoopConfig:
        """Config for one loop id; absent ids resolve to a disabled default."""
        return self.loops.get(loop_id, AutonomyLoopConfig(enabled=False))
