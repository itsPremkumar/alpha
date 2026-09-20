"""Models for Self-Healing Runtime and Fault Recovery."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class FaultType(str, Enum):
    STALE_LOCK = "stale_lock"
    HANGING_PROCESS = "hanging_process"
    ZOMBIE_TASK = "zombie_task"
    UNHANDLED_EXCEPTION = "unhandled_exception"
    HIGH_MEMORY = "high_memory"


class HealingAction(str, Enum):
    CLEAR_LOCK = "clear_lock"
    TERMINATE_PROCESS = "terminate_process"
    RESET_STATE = "reset_state"
    NO_ACTION = "no_action"


@dataclass
class HealthFault:
    fault_type: FaultType
    description: str
    target_resource: str
    recommended_action: HealingAction
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fault_type"] = self.fault_type.value
        data["recommended_action"] = self.recommended_action.value
        return data


@dataclass
class HealthReport:
    healthy: bool
    faults: list[HealthFault] = field(default_factory=list)
    remediations_applied: list[str] = field(default_factory=list)
    scan_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "faults": [f.to_dict() for f in self.faults],
            "remediations_applied": self.remediations_applied,
            "scan_duration_ms": self.scan_duration_ms,
        }
