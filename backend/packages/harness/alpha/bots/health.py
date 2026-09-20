"""Agent Heartbeat, Liveness, and Stall Detection Engine (Master Inventory #27-#31, #74, #76, #181).

Provides real-time heartbeat tracking, liveness evaluation (healthy, stale, stalled, dead),
task lease monitoring, and fleet-wide organization health metrics.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from alpha.bots.profile import BotProfile, _now

logger = logging.getLogger(__name__)

# Thresholds in seconds
HEALTHY_THRESHOLD_SECONDS = 60
STALE_THRESHOLD_SECONDS = 300
DEFAULT_LEASE_SECONDS = 300


@dataclass
class HeartbeatRecord:
    bot_name: str
    timestamp: str = field(default_factory=_now)
    current_task_id: str | None = None
    lease_expires_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BotHealthMonitor:
    """Thread-safe liveness monitor and heartbeat store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._heartbeats: dict[str, HeartbeatRecord] = {}

    def record_heartbeat(
        self,
        bot_name: str,
        *,
        task_id: str | None = None,
        lease_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> HeartbeatRecord:
        key = bot_name.lower().strip()
        now_dt = datetime.now(UTC)
        now_str = now_dt.isoformat()
        lease_exp = None
        if task_id and lease_seconds:
            lease_exp = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        elif task_id:
            lease_exp = (now_dt + timedelta(seconds=DEFAULT_LEASE_SECONDS)).isoformat()

        record = HeartbeatRecord(
            bot_name=key,
            timestamp=now_str,
            current_task_id=task_id,
            lease_expires_at=lease_exp,
            details=details or {},
        )
        with self._lock:
            self._heartbeats[key] = record

        # Also update profile last_active and heartbeat in registry if available
        try:
            from alpha.bots.registry import get_bot_registry

            reg = get_bot_registry()
            reg.update_bot(key, heartbeat=now_str, last_active=now_str, bump_version=False)
        except Exception:
            logger.debug("Failed to propagate heartbeat to registry for %s", key, exc_info=True)

        return record

    def clear_task_lease(self, bot_name: str) -> None:
        key = bot_name.lower().strip()
        with self._lock:
            if key in self._heartbeats:
                record = self._heartbeats[key]
                record.current_task_id = None
                record.lease_expires_at = None

    def get_heartbeat(self, bot_name: str) -> HeartbeatRecord | None:
        with self._lock:
            return self._heartbeats.get(bot_name.lower().strip())

    def evaluate_liveness(self, profile: BotProfile) -> dict[str, Any]:
        """Evaluate detailed liveness state for a single bot profile."""
        key = profile.name.lower().strip()
        now_dt = datetime.now(UTC)

        # Inactive lifecycle states take precedence
        if profile.status in ("suspended", "archived"):
            return {
                "bot_name": key,
                "status": profile.status,
                "liveness": profile.status,
                "is_responsive": False,
                "active_task_id": None,
                "last_heartbeat": profile.heartbeat,
                "lease_expired": False,
            }
        if profile.status == "sleeping":
            return {
                "bot_name": key,
                "status": "sleeping",
                "liveness": "sleeping",
                "is_responsive": True,  # Wakes on demand
                "active_task_id": None,
                "last_heartbeat": profile.heartbeat,
                "lease_expired": False,
            }

        with self._lock:
            record = self._heartbeats.get(key)

        last_hb_str = record.timestamp if record else profile.heartbeat
        active_task = record.current_task_id if record else None
        lease_exp_str = record.lease_expires_at if record else None

        if not last_hb_str:
            # Newly provisioned active bot with no ping yet
            return {
                "bot_name": key,
                "status": profile.status,
                "liveness": "healthy",
                "is_responsive": True,
                "active_task_id": active_task,
                "last_heartbeat": None,
                "lease_expired": False,
            }

        try:
            hb_dt = datetime.fromisoformat(last_hb_str)
            elapsed_sec = (now_dt - hb_dt).total_seconds()
        except Exception:
            elapsed_sec = 999999.0

        lease_expired = False
        if lease_exp_str and active_task:
            try:
                exp_dt = datetime.fromisoformat(lease_exp_str)
                if now_dt > exp_dt:
                    lease_expired = True
            except Exception:
                lease_expired = True

        if lease_expired:
            liveness = "stalled"
            is_responsive = False
        elif elapsed_sec <= HEALTHY_THRESHOLD_SECONDS:
            liveness = "healthy"
            is_responsive = True
        elif elapsed_sec <= STALE_THRESHOLD_SECONDS:
            liveness = "stale"
            is_responsive = True
        else:
            liveness = "dead"
            is_responsive = False

        return {
            "bot_name": key,
            "status": profile.status,
            "liveness": liveness,
            "is_responsive": is_responsive,
            "active_task_id": active_task,
            "last_heartbeat": last_hb_str,
            "seconds_since_heartbeat": round(elapsed_sec, 1),
            "lease_expired": lease_expired,
        }

    def get_fleet_health(self, profiles: list[BotProfile]) -> dict[str, Any]:
        """Aggregate health and liveness summary across all bots."""
        results = [self.evaluate_liveness(p) for p in profiles]
        counts = {
            "total": len(results),
            "healthy": 0,
            "stale": 0,
            "stalled": 0,
            "dead": 0,
            "sleeping": 0,
            "suspended": 0,
            "archived": 0,
        }
        stalled_workers = []
        for r in results:
            liv = r["liveness"]
            counts[liv] = counts.get(liv, 0) + 1
            if liv == "stalled":
                stalled_workers.append(r)

        return {
            "timestamp": _now(),
            "summary": counts,
            "fleet_health_score": round((counts["healthy"] + counts["sleeping"] * 0.9) / max(counts["total"], 1), 2),
            "bots": results,
            "stalled_workers": stalled_workers,
        }

    def check_stalled_tasks(self, profiles: list[BotProfile]) -> list[dict[str, Any]]:
        """Identify tasks stuck with stalled or dead workers needing recovery."""
        stalled = []
        for p in profiles:
            info = self.evaluate_liveness(p)
            if (info["liveness"] in ("stalled", "dead")) and info["active_task_id"]:
                stalled.append(
                    {
                        "task_id": info["active_task_id"],
                        "worker": p.name,
                        "worker_liveness": info["liveness"],
                        "succession_fallback": p.succession_fallback or p.reports_to,
                        "recommended_action": "reclaim_and_reassign",
                    }
                )
        return stalled


_global_monitor: BotHealthMonitor | None = None


def get_health_monitor() -> BotHealthMonitor:
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = BotHealthMonitor()
    return _global_monitor
