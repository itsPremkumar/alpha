"""Tests for Self-Healing Runtime Watchdog and Fault Recovery."""

import json
import os
import time

from alpha.runtime.selfheal.models import FaultType, HealthReport
from alpha.runtime.selfheal.watchdog import SelfHealingWatchdog
from alpha.tools.builtins.self_heal_tool import self_heal_diagnose


def test_self_healing_stale_lock_detection_and_remediation(tmp_path):
    watchdog = SelfHealingWatchdog(max_lock_age_seconds=1)

    # Create a simulated stale lock file (.boulder.lock)
    lock_file = tmp_path / ".boulder.lock"
    lock_file.write_text("locked by pid 99999")

    # Set mtime to 10 seconds ago
    old_time = time.time() - 10
    os.utime(str(lock_file), (old_time, old_time))

    # Scan and auto-heal
    report = watchdog.scan_and_heal(workspace_dir=str(tmp_path), auto_remediate=True)

    assert isinstance(report, HealthReport)
    assert not report.healthy  # Fault was found
    assert len(report.faults) == 1
    assert report.faults[0].fault_type == FaultType.STALE_LOCK
    assert len(report.remediations_applied) == 1
    assert "Removed stale lock file" in report.remediations_applied[0]

    # Lock file should now be deleted
    assert not lock_file.exists()


def test_self_heal_tool_invocation(tmp_path):
    res_str = self_heal_diagnose.invoke({
        "auto_remediate": True,
        "workspace_dir": str(tmp_path),
    })
    data = json.loads(res_str)
    assert "healthy" in data
    assert "faults" in data
    assert "remediations_applied" in data
