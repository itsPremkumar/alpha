"""Configuration for the run stall watchdog.

A run that stays ``status=running`` without a progress heartbeat for longer
than ``timeout_seconds`` is cancelled and terminalised with an explanatory
error. The defaults were chosen against a live incident: a run hung for 20+
minutes with nothing watching it. ``timeout_seconds`` must exceed the longest
legitimate silent phase (a single slow model call or tool execution);
``interval_seconds`` bounds how long a stall can linger before detection.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["RunStallSettings"]


class RunStallSettings(BaseModel):
    """Stall-watchdog budgets. Defaults are production-sane; override in
    ``config.yaml`` under ``run_stall:``."""

    timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "Seconds without a progress heartbeat (updated_at) after which a "
            "running run is treated as stalled, cancelled, and terminalised "
            "with an error. Must exceed the longest legitimate silent phase "
            "(one slow model call or tool execution)."
        ),
    )
    interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Seconds between stall scans. Bounds detection latency for a newly stalled run.",
    )
