"""Continuous Self-Improvement & Metacognitive Evolution Engine for perpetual organizations."""

from __future__ import annotations

import logging
import time

from alpha.company.models import CompanyState, EvolutionRecord

logger = logging.getLogger(__name__)


class ContinuousSelfImprovementEngine:
    """Coordinates autonomous retrospectives, playbook synthesis, and bot profile calibration."""

    @classmethod
    def run_retrospective(
        cls,
        state: CompanyState,
        completed_tasks_count: int | None = None,
        resolved_incidents_count: int | None = None,
    ) -> EvolutionRecord:
        """Evaluates recent operational performance, synthesizes institutional learnings, and logs evolution.

        The counts are MEASUREMENTS: ``None`` (the default) means the caller
        has no real count, and the record says so. The previous signature
        defaulted to 5 completed tasks and 1 auto-remediated incident, so
        every call that passed nothing published invented performance.
        """
        cycle_num = len(state.evolution_journal) + 1

        activity = (
            f"Executed {completed_tasks_count} tasks with {resolved_incidents_count} auto-remediated incidents."
            if completed_tasks_count is not None
            else "Task and incident counts were not measured for this cycle (no real counts were supplied)."
        )

        insights = [
            f"Cycle {cycle_num}: {activity}",
            (
                f"Autonomous failover kept organizational health at {state.overall_health_percent}%."
                if state.overall_health_percent is not None
                else "Organizational health has not been measured yet; no failover health figure available."
            ),
            (
                f"Zero-wasted-compute preserved operational stamina across {state.sleeping_bots_count} idle specialist bots."
                if state.attendance_measured
                else "No attendance pulses have been recorded yet; idle-bot count is unmeasured, not zero-by-observation."
            ),
            "No playbook change or bot-profile calibration was produced this cycle: synthesis requires real evidence, and none was produced.",
        ]

        # Playbook synthesis: a playbook change is reported only when this
        # cycle actually produced evidence for one. The previous code invented
        # three specific process changes on EVERY cycle — including a
        # "tightened failover threshold from 3 to 2" that never happened.
        playbook_updates: list[str] = []

        # Bot calibration: a bot is listed only when its profile was actually
        # adjusted. Nothing adjusts profiles during a retrospective, so the
        # honest report is empty (it used to name every department lead as
        # "calibrated prompt routing & tool permissions").
        calibrated_bots: list[str] = []

        health_text = f"{state.overall_health_percent}%" if state.overall_health_percent is not None else "not yet measured"
        bots_text = str(state.active_bots_count) if state.attendance_measured else "not yet measured"

        record = EvolutionRecord(
            cycle_number=cycle_num,
            timestamp=time.time(),
            insights=insights,
            improved_playbooks=playbook_updates,
            calibrated_bots=calibrated_bots,
            kpi_delta_summary=(
                f"Health: {health_text} | Active Bots: {bots_text} | Running Tasks: {state.running_tasks_count}"
            ),
        )

        state.evolution_journal.append(record)
        state.updated_at = time.time()
        logger.info(f"Organization '{state.org_id}' completed self-improvement cycle #{cycle_num}")
        return record
