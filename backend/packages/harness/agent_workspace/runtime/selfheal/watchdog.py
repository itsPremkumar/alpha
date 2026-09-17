"""SelfHealingWatchdog: Automated fault detection, lock release, and recovery supervisor."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from agent_workspace.runtime.selfheal.models import (
    FaultType,
    HealingAction,
    HealthFault,
    HealthReport,
)

logger = logging.getLogger(__name__)


class SelfHealingWatchdog:
    """Supervisory watchdog detecting runtime anomalies, stale locks, and applying self-healing remedies."""

    KNOWN_LOCK_PATTERNS = [".git/index.lock", ".pytest_cache/.lock", ".boulder.lock", ".task.lock"]

    def __init__(self, max_lock_age_seconds: int = 120):
        self.max_lock_age_seconds = max_lock_age_seconds

    def scan_and_heal(
        self,
        workspace_dir: str | None = None,
        auto_remediate: bool = True,
    ) -> HealthReport:
        start_t = time.time()
        target_path = Path(workspace_dir or os.getcwd()).resolve()
        faults: list[HealthFault] = []
        remediations: list[str] = []

        # 1. Scan for stale locks
        for rel_lock in self.KNOWN_LOCK_PATTERNS:
            lock_fp = target_path / rel_lock
            if lock_fp.exists():
                try:
                    stat = lock_fp.stat()
                    age = time.time() - stat.st_mtime
                    if age > self.max_lock_age_seconds:
                        fault = HealthFault(
                            fault_type=FaultType.STALE_LOCK,
                            description=f"Stale lock file '{rel_lock}' detected (age: {int(age)}s).",
                            target_resource=str(lock_fp),
                            recommended_action=HealingAction.CLEAR_LOCK,
                        )
                        faults.append(fault)

                        if auto_remediate:
                            try:
                                os.remove(str(lock_fp))
                                remediations.append(f"Auto-remediated: Removed stale lock file '{rel_lock}'.")
                            except OSError as e:
                                remediations.append(f"Failed to remove lock '{rel_lock}': {e}")
                except Exception as e:
                    logger.debug(f"Error checking lock {lock_fp}: {e}")

        scan_dur = (time.time() - start_t) * 1000.0
        healthy = len(faults) == 0

        return HealthReport(
            healthy=healthy,
            faults=faults,
            remediations_applied=remediations,
            scan_duration_ms=round(scan_dur, 2),
        )
