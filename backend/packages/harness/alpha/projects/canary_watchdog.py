"""Canary Staging & Auto-Rollback Watchdog.

Performs synthetic smoke checks against ephemeral local staging servers prior to
branch merge. If error thresholds, unhandled exceptions, or latency spikes are detected,
automatically aborts the merge, unleases the worktree, and triggers an autonomous rollback.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CanaryProbeResult:
    probe_id: str
    project_id: str
    target_port: int
    target_url: str
    status: str  # "healthy", "degraded", "failed"
    http_status: int | None
    latency_ms: float
    error_count: int
    recommendation: str  # "proceed_with_merge" or "trigger_auto_rollback"
    details: dict[str, Any] = field(default_factory=dict)
    tested_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CanaryWatchdog:
    """Verifies canary staging builds before promoting code to main branch."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._probe_history: list[CanaryProbeResult] = []

    def probe_staging(
        self,
        port: int = 3000,
        path: str = "/",
        timeout_seconds: float = 2.0,
        max_allowed_latency_ms: float = 1200.0,
        mock_success: bool = False,
    ) -> CanaryProbeResult:
        """Probe local staging port and assess stability."""
        probe_id = f"canary-{uuid.uuid4().hex[:8]}"
        target_url = f"http://127.0.0.1:{port}{path}"
        start = time.perf_counter()
        http_code: int | None = None
        errors: list[str] = []

        if mock_success:
            # Deterministic pass for test pipelines or dry-runs
            latency_ms = 45.2
            http_code = 200
            status = "healthy"
            recommendation = "proceed_with_merge"
        else:
            try:
                req = urllib.request.Request(target_url, headers={"User-Agent": "Alpha-Canary-Watchdog"})
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    http_code = resp.status
                    latency_ms = (time.perf_counter() - start) * 1000
                    if 200 <= http_code < 300 and latency_ms <= max_allowed_latency_ms:
                        status = "healthy"
                        recommendation = "proceed_with_merge"
                    elif latency_ms > max_allowed_latency_ms:
                        status = "degraded"
                        recommendation = "trigger_auto_rollback"
                        errors.append(f"Latency exceeded threshold: {latency_ms:.1f}ms > {max_allowed_latency_ms}ms")
                    else:
                        status = "failed"
                        recommendation = "trigger_auto_rollback"
                        errors.append(f"Unexpected HTTP status: {http_code}")
            except urllib.error.HTTPError as e:
                http_code = e.code
                latency_ms = (time.perf_counter() - start) * 1000
                status = "failed"
                recommendation = "trigger_auto_rollback"
                errors.append(f"HTTP Error {e.code}: {e.reason}")
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                status = "failed"
                recommendation = "trigger_auto_rollback"
                errors.append(f"Connection failed: {str(e)}")

        result = CanaryProbeResult(
            probe_id=probe_id,
            project_id=self.project_id,
            target_port=port,
            target_url=target_url,
            status=status,
            http_status=http_code,
            latency_ms=round(latency_ms, 2),
            error_count=len(errors),
            recommendation=recommendation,
            details={"errors": errors},
        )
        self._probe_history.append(result)

        # Record in flight recorder
        try:
            from alpha.projects.events import get_event_bus

            get_event_bus(self.project_id).emit(
                "canary_probe_completed",
                "canary_watchdog",
                {"probe_id": probe_id, "status": status, "port": port},
            )
        except Exception:
            pass

        return result

    def execute_rollback_if_failed(
        self,
        probe_result: CanaryProbeResult,
        branch_name: str,
        lock_id: str | None = None,
        requester_bot: str = "canary_watchdog",
    ) -> dict[str, Any]:
        """Trigger automatic rollback and cleanup if canary health check failed."""
        if probe_result.recommendation != "trigger_auto_rollback":
            return {"rollback_executed": False, "reason": "Canary probe was healthy; rollback not required"}

        cleaned_worktree = False
        released_lock = False

        # 1. Clean worktree branch
        try:
            from alpha.sandbox.worktrees import WorktreeManager

            wt_mgr = WorktreeManager(repo_root=".")
            cleaned_worktree = wt_mgr.remove_worktree(branch_name, force=True, delete_branch=True)
        except Exception as e:
            logger.warning(f"Could not clean worktree for rollback: {e}")

        # 2. Release resource lock
        if lock_id:
            try:
                from alpha.projects.locks import get_lock_manager

                released_lock = get_lock_manager().release(lock_id, requester_bot)
            except Exception as e:
                logger.warning(f"Could not release lock during canary rollback: {e}")

        # 3. Emit rollback event
        try:
            from alpha.projects.events import get_event_bus

            get_event_bus(self.project_id).emit(
                "canary_rollback_executed",
                "canary_watchdog",
                {
                    "probe_id": probe_result.probe_id,
                    "branch": branch_name,
                    "lock_id": lock_id,
                },
            )
        except Exception:
            pass

        return {
            "rollback_executed": True,
            "probe_id": probe_result.probe_id,
            "branch": branch_name,
            "cleaned_worktree": cleaned_worktree,
            "released_lock": released_lock,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def get_history(self) -> list[CanaryProbeResult]:
        return list(self._probe_history)


_WATCHDOGS: dict[str, CanaryWatchdog] = {}


def get_canary_watchdog(project_id: str) -> CanaryWatchdog:
    if project_id not in _WATCHDOGS:
        _WATCHDOGS[project_id] = CanaryWatchdog(project_id)
    return _WATCHDOGS[project_id]
