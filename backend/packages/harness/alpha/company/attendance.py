"""Silent Tick/Pulse Attendance Ledger & Health Monitoring Engine."""

from __future__ import annotations

import logging
import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    BUSY = "busy"
    IDLE = "idle"
    STUCK_LOOP = "stuck_loop"
    ABSENT = "absent"
    RECOVERED = "recovered"


class BotHeartbeat(BaseModel):
    bot_name: str
    role: str = "Worker"
    department: str = "Engineering"
    last_pulse: float = Field(default_factory=time.time)
    status: AttendanceStatus = AttendanceStatus.PRESENT
    active_task_id: str | None = None
    task_started_at: float | None = None
    consecutive_misses: int = 0
    last_healed_at: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AttendanceLedgerEngine:
    """Zero-spam protocol-level attendance ledger.

    Bots register silent heartbeats/ticks without sending spam messages into conversation channels.
    """

    def __init__(self, org_id: str = "org-default"):
        self.org_id = org_id
        # bot_name -> BotHeartbeat
        self._ledger: dict[str, BotHeartbeat] = {}

    def register_bot(
        self,
        bot_name: str,
        role: str = "Worker",
        department: str = "Engineering",
        initial_status: AttendanceStatus = AttendanceStatus.PRESENT,
    ) -> BotHeartbeat:
        """Enrolls a bot in the attendance ledger."""
        clean_name = bot_name.strip().lower()
        now = time.time()
        hb = BotHeartbeat(
            bot_name=clean_name,
            role=role,
            department=department,
            last_pulse=now,
            status=initial_status,
        )
        self._ledger[clean_name] = hb
        return hb

    def record_pulse(
        self,
        bot_name: str,
        status: AttendanceStatus | str = AttendanceStatus.PRESENT,
        active_task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> BotHeartbeat:
        """Records a silent non-conversational heartbeat tick for a bot."""
        clean_name = bot_name.strip().lower()
        now = time.time()
        parsed_status = AttendanceStatus(status.lower()) if isinstance(status, str) else status

        hb = self._ledger.get(clean_name)
        if not hb:
            hb = BotHeartbeat(
                bot_name=clean_name,
                last_pulse=now,
                status=parsed_status,
                active_task_id=active_task_id,
                details=details or {},
            )
            self._ledger[clean_name] = hb
        else:
            hb.last_pulse = now
            hb.status = parsed_status
            hb.consecutive_misses = 0
            if active_task_id:
                if hb.active_task_id != active_task_id:
                    hb.active_task_id = active_task_id
                    hb.task_started_at = now
            else:
                hb.active_task_id = None
                hb.task_started_at = None
            if details:
                hb.details.update(details)

        return hb

    def batch_pulse(self, pulses: list[dict[str, Any]]) -> int:
        """Batch-updates heartbeats for multiple bots simultaneously."""
        count = 0
        for p in pulses:
            bname = p.get("bot_name")
            if bname:
                self.record_pulse(
                    bot_name=bname,
                    status=p.get("status", AttendanceStatus.PRESENT),
                    active_task_id=p.get("active_task_id"),
                    details=p.get("details"),
                )
                count += 1
        return count

    def evaluate_attendance(
        self,
        timeout_seconds: float = 120.0,
        stuck_task_seconds: float = 300.0,
    ) -> dict[str, list[str]]:
        """Evaluates all registered bots against heartbeat thresholds.

        Returns categorized lists of problematic bots:
        {'absent': [...], 'stuck': [...], 'healthy': [...]}
        """
        now = time.time()
        absent_bots: list[str] = []
        stuck_bots: list[str] = []
        healthy_bots: list[str] = []

        for bot_name, hb in self._ledger.items():
            age = now - hb.last_pulse

            # Check if bot has been stuck on a task for too long
            if hb.active_task_id and hb.task_started_at and (now - hb.task_started_at) > stuck_task_seconds:
                hb.status = AttendanceStatus.STUCK_LOOP
                stuck_bots.append(bot_name)
                logger.warning(f"Bot '{bot_name}' stuck on task '{hb.active_task_id}' for {now - hb.task_started_at:.1f}s")
                continue

            # Check if bot heartbeat is stale
            if age > timeout_seconds:
                hb.consecutive_misses += 1
                hb.status = AttendanceStatus.ABSENT
                absent_bots.append(bot_name)
                logger.warning(f"Bot '{bot_name}' missed heartbeat! Last pulse was {age:.1f}s ago")
            else:
                healthy_bots.append(bot_name)

        return {
            "absent": absent_bots,
            "stuck": stuck_bots,
            "healthy": healthy_bots,
        }

    def generate_roll_call_digest(self, company_name: str = "Enterprise") -> str:
        """Generates a clean, single standup summary without spamming chat."""
        total = len(self._ledger)
        if total == 0:
            return f"**Roll Call ({company_name})**: No bots currently registered."

        present = sum(1 for hb in self._ledger.values() if hb.status in (AttendanceStatus.PRESENT, AttendanceStatus.BUSY, AttendanceStatus.IDLE))
        recovered = sum(1 for hb in self._ledger.values() if hb.status == AttendanceStatus.RECOVERED)
        absent = sum(1 for hb in self._ledger.values() if hb.status == AttendanceStatus.ABSENT)
        stuck = sum(1 for hb in self._ledger.values() if hb.status == AttendanceStatus.STUCK_LOOP)

        status_flag = "🟢 Optimal" if (absent == 0 and stuck == 0) else "🟡 Warning" if absent > 0 else "🔴 Critical"

        lines = [
            f"### 📋 Attendance Roll Call Digest — {company_name}",
            f"- **Status**: {status_flag}",
            f"- **Total Workforce**: {total} bots",
            f"- **Present & Active**: {present} ({present / total * 100:.1f}%)",
            f"- **Self-Healed**: {recovered}",
            f"- **Absent / Missed Pulse**: {absent}",
            f"- **Stuck Loops**: {stuck}",
        ]

        if absent > 0 or stuck > 0:
            unhealthy = [b for b, h in self._ledger.items() if h.status in (AttendanceStatus.ABSENT, AttendanceStatus.STUCK_LOOP)]
            lines.append(f"- **Attention Needed**: {', '.join('@' + u for u in unhealthy)}")

        return "\n".join(lines)

    def get_heartbeat(self, bot_name: str) -> BotHeartbeat | None:
        return self._ledger.get(bot_name.strip().lower())

    def list_heartbeats(self) -> list[BotHeartbeat]:
        return list(self._ledger.values())
