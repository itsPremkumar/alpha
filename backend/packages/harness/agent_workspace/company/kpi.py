"""Organizational KPI Engine & Autonomous Corrective Task Generator."""

from __future__ import annotations

import logging
import time
from typing import Any

from agent_workspace.company.models import KPISpec

logger = logging.getLogger(__name__)

DEFAULT_ORGANIZATION_KPIS = [
    KPISpec(
        kpi_id="kpi-availability",
        name="System Availability",
        current_value=99.95,
        target_value=99.90,
        unit="%",
        trend="stable",
        threshold_critical=99.0,
    ),
    KPISpec(
        kpi_id="kpi-security-score",
        name="Enterprise Security Score",
        current_value=96.0,
        target_value=95.0,
        unit="pts",
        trend="improving",
        threshold_critical=85.0,
    ),
    KPISpec(
        kpi_id="kpi-deployment-freq",
        name="Deployment Velocity",
        current_value=7.0,
        target_value=5.0,
        unit="deploys/wk",
        trend="stable",
        threshold_critical=2.0,
    ),
    KPISpec(
        kpi_id="kpi-customer-retention",
        name="Customer Retention",
        current_value=92.5,
        target_value=90.0,
        unit="%",
        trend="improving",
        threshold_critical=80.0,
    ),
]


class KPIEngine:
    """Monitors organizational health KPIs and synthesizes autonomous corrective tasks upon degradation."""

    def __init__(self, initial_kpis: list[KPISpec] | None = None):
        # kpi_id -> KPISpec
        self._kpis: dict[str, KPISpec] = {k.kpi_id: k for k in (initial_kpis or DEFAULT_ORGANIZATION_KPIS)}
        # History of generated corrective tasks
        self._triggered_corrective_tasks: list[dict[str, Any]] = []

    def get_kpi(self, kpi_id: str) -> KPISpec | None:
        return self._kpis.get(kpi_id)

    def list_kpis(self) -> list[KPISpec]:
        return list(self._kpis.values())

    def update_metric(self, kpi_id: str, new_value: float) -> tuple[KPISpec, dict[str, Any] | None]:
        """Updates a metric, assesses trend, and auto-triggers corrective work if breached."""
        kpi = self._kpis.get(kpi_id)
        if not kpi:
            raise KeyError(f"KPI '{kpi_id}' not found.")

        old_value = kpi.current_value
        kpi.current_value = new_value
        kpi.last_evaluated = time.time()

        # Update trend
        if new_value > old_value:
            kpi.trend = "improving"
        elif new_value < old_value:
            kpi.trend = "deteriorating"
        else:
            kpi.trend = "stable"

        # Check breach against critical threshold
        corrective_task = None
        if new_value < kpi.threshold_critical:
            task_title = f"Autonomous Corrective Action: Recover {kpi.name} (Current: {new_value}{kpi.unit} < Threshold {kpi.threshold_critical}{kpi.unit})"
            dept = "sre" if "avail" in kpi_id else ("security" if "sec" in kpi_id else "product")
            corrective_task = {
                "kpi_id": kpi_id,
                "title": task_title,
                "department": dept,
                "urgency": "critical",
                "target_delta": round(kpi.target_value - new_value, 2),
                "timestamp": time.time(),
            }
            self._triggered_corrective_tasks.append(corrective_task)
            logger.warning(f"KPI Breach on {kpi.name}: Triggered corrective task '{task_title}'")

        return kpi, corrective_task

    def get_triggered_tasks(self) -> list[dict[str, Any]]:
        return list(self._triggered_corrective_tasks)
