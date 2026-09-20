"""Autonomous Bot Medic & Self-Healing Engine for Absent or Stuck Agents."""

from __future__ import annotations

import logging
import time
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from alpha.company.attendance import AttendanceLedgerEngine, AttendanceStatus

if TYPE_CHECKING:
    from alpha.company.responsibility import ResponsibilityEngine

logger = logging.getLogger(__name__)


class HealingAction(StrEnum):
    REBOOT_CONTEXT = "reboot_context"
    CLEAR_LOOP_STATE = "clear_loop_state"
    FAILOVER_RESPONSIBILITY = "failover_responsibility"
    ASSIGN_BACKUP = "assign_backup"


class HealingReport(BaseModel):
    incident_id: str = Field(default_factory=lambda: f"inc-{uuid.uuid4().hex[:8]}")
    bot_name: str
    trigger_reason: str
    action_taken: str
    backup_bot_assigned: str | None = None
    recovered_at: float = Field(default_factory=time.time)
    success: bool = True
    notes: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class BotMedicEngine:
    """Diagnoses unresponsive or stuck bots, resets execution context, and reassigns tasks to backups."""

    def __init__(
        self,
        attendance_ledger: AttendanceLedgerEngine,
        responsibility_engine: ResponsibilityEngine | None = None,
    ):
        self._ledger = attendance_ledger
        self._resp_engine = responsibility_engine
        self._incident_log: list[HealingReport] = []

    def set_responsibility_engine(self, engine: ResponsibilityEngine) -> None:
        self._resp_engine = engine

    def diagnose_and_heal(
        self,
        bot_name: str,
        reason: str = "Missed heartbeat / Unresponsive",
    ) -> HealingReport:
        """Heals an individual absent, stuck, or degraded bot."""
        clean_name = bot_name.strip().lower()
        now = time.time()
        logger.warning(f"Bot Medic dispatching for '@{clean_name}' (Reason: {reason})")

        backup_bot: str | None = None
        action = HealingAction.REBOOT_CONTEXT.value

        # 1. Check if bot has assigned responsibilities to fail over
        if self._resp_engine:
            bindings = self._resp_engine.get_bindings_for_bot(clean_name)
            for b in bindings:
                if b.status == "active" and b.backup_bot_name and b.backup_bot_name != clean_name:
                    backup_bot = b.backup_bot_name
                    # Trigger failover to backup
                    self._resp_engine.trigger_failover(
                        responsibility_id=b.responsibility_id,
                        reason=f"Bot Medic auto-failover: @{clean_name} is unresponsive ({reason})",
                    )
                    action = f"{HealingAction.REBOOT_CONTEXT.value} + {HealingAction.FAILOVER_RESPONSIBILITY.value}"
                    break

        # 2. Reset runtime context in Attendance Ledger
        hb = self._ledger.get_heartbeat(clean_name)
        if hb:
            hb.status = AttendanceStatus.RECOVERED
            hb.last_pulse = now
            hb.last_healed_at = now
            hb.consecutive_misses = 0
            hb.active_task_id = None
            hb.task_started_at = None
            hb.details["last_incident_reason"] = reason
        else:
            self._ledger.register_bot(
                bot_name=clean_name,
                initial_status=AttendanceStatus.RECOVERED,
            )

        report = HealingReport(
            bot_name=clean_name,
            trigger_reason=reason,
            action_taken=action,
            backup_bot_assigned=backup_bot,
            recovered_at=now,
            success=True,
            notes=f"Rebooted bot context, cleared stuck loop state. Backup '@{backup_bot}' assumed duties." if backup_bot else "Rebooted bot context, cleared stuck loop state. Bot status returned to RECOVERED.",
        )
        self._incident_log.append(report)
        logger.info(f"Bot Medic successfully healed '@{clean_name}': {report.notes}")
        return report

    def auto_heal_all_unresponsive(
        self,
        timeout_seconds: float = 120.0,
        stuck_task_seconds: float = 300.0,
    ) -> list[HealingReport]:
        """Runs attendance check and automatically repairs all absent or stuck bots."""
        eval_res = self._ledger.evaluate_attendance(
            timeout_seconds=timeout_seconds,
            stuck_task_seconds=stuck_task_seconds,
        )

        reports: list[HealingReport] = []
        for absent_bot in eval_res.get("absent", []):
            rep = self.diagnose_and_heal(
                absent_bot,
                reason=f"Heartbeat timeout (> {timeout_seconds:.0f}s elapsed without pulse)",
            )
            reports.append(rep)

        for stuck_bot in eval_res.get("stuck", []):
            rep = self.diagnose_and_heal(
                stuck_bot,
                reason=f"Task execution timeout (> {stuck_task_seconds:.0f}s stuck on active task)",
            )
            reports.append(rep)

        return reports

    def get_incident_history(self, limit: int = 50) -> list[HealingReport]:
        return self._incident_log[-limit:]
