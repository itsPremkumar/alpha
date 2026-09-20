"""Perpetual Never-Ending Autonomous Operation package."""

from __future__ import annotations

from .daemon import PerpetualDaemon, get_perpetual_daemon
from .discovery import AutonomousTaskDiscoveryEngine
from .memory_consolidator import PerpetualMemoryConsolidator
from .models import (
    AutonomousTask,
    DaemonState,
    MemoryConsolidationReport,
    PerpetualDaemonTelemetry,
    PerpetualGoal,
    StagnationIncident,
)
from .stagnation import StagnationRecoveryWatchdog

__all__ = [
    "PerpetualDaemon",
    "get_perpetual_daemon",
    "AutonomousTaskDiscoveryEngine",
    "PerpetualMemoryConsolidator",
    "StagnationRecoveryWatchdog",
    "DaemonState",
    "PerpetualGoal",
    "AutonomousTask",
    "StagnationIncident",
    "MemoryConsolidationReport",
    "PerpetualDaemonTelemetry",
]
