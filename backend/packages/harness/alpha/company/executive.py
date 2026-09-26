"""Executive Intelligence Layer: High-level Command Center digests & explainability."""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from alpha.company.models import CompanyState

logger = logging.getLogger(__name__)


class ExecutiveDigest(BaseModel):
    org_id: str
    company_name: str
    #: None = no health computation has been run for this digest. A value is
    #: only present when generate_digest() computed it from real inputs.
    overall_health_percent: float | None = None
    active_bots_count: int = 0
    sleeping_bots_count: int = 0
    recovering_bots_count: int = 0
    active_projects_count: int = 0
    running_tasks_count: int = 0
    blocked_tasks_count: int = 0
    critical_incidents_count: int = 0
    warnings_count: int = 0
    human_approvals_required_count: int = 0
    kpi_summary: list[dict[str, Any]] = Field(default_factory=list)
    explainability: dict[str, str] = Field(default_factory=dict)
    summary_markdown: str = ""
    timestamp: float = Field(default_factory=time.time)


class ExecutiveIntelligenceLayer:
    """Compiles real-time organization metrics and explainability for the human owner."""

    @classmethod
    def generate_digest(
        cls,
        state: CompanyState,
        recovering_count: int = 0,
        blocked_tasks: int = 0,
        critical_incidents: int = 0,
        warnings: int = 0,
        pending_approvals: int = 0,
    ) -> ExecutiveDigest:
        """Computes high-level health score and structured executive digest."""
        # Calculate composite health score (0-100)
        health = 100.0
        health -= min(30.0, critical_incidents * 15.0)
        health -= min(15.0, recovering_count * 5.0)
        health -= min(10.0, blocked_tasks * 2.0)
        health -= min(10.0, warnings * 2.0)
        final_health = round(max(10.0, min(100.0, health)), 1)

        kpi_summaries = [
            {
                "name": k.name,
                "current": f"{k.current_value}{k.unit}",
                "target": f"{k.target_value}{k.unit}",
                "trend": k.trend,
                "healthy": k.current_value >= k.threshold_critical,
                "basis": k.basis,
            }
            for k in state.kpis
        ]

        explainability = {
            "why_idle_agents": "Sleeping to conserve compute: no pending backlog or scheduled routines require execution.",
            "why_tasks_blocked": "Waiting on upstream prerequisite dependencies in the execution graph.",
            "why_new_agents_spawned": "Autonomous workforce expansion triggered to meet specialized domain requirements.",
        }

        # Format human-friendly executive summary
        md_lines = [
            f"# Executive Digest: {state.name}",
            f"**Overall Health**: {final_health}% | **State**: `{state.state.value.upper()}`",
            "",
            "### Workforce & Fleet Status",
            f"- **Active Specialists**: {state.active_bots_count}",
            f"- **Sleeping / Idle**: {state.sleeping_bots_count}",
            f"- **In Recovery**: {recovering_count}",
            "",
            "### Projects & Tasks",
            f"- **Active Projects**: {len(state.projects)}",
            f"- **Running Tasks**: {state.running_tasks_count}",
            f"- **Blocked Tasks**: {blocked_tasks}",
            "",
            "### Governance & Incidents",
            f"- **Critical Incidents**: {critical_incidents}",
            f"- **Warnings**: {warnings}",
            f"- **Pending Human Approvals**: {pending_approvals}",
            "",
            "### Strategic KPIs",
        ]
        for k in kpi_summaries:
            if k["basis"] == "seed_demo_data":
                # Seed reading: no healthy/unhealthy claim, disclose provenance.
                md_lines.append(
                    f"- ⚪ **{k['name']}**: {k['current']} (Target: {k['target']}) — `{k['trend']}` (seed example — not a live measurement)"
                )
            else:
                status_icon = "🟢" if k["healthy"] else "🔴"
                md_lines.append(f"- {status_icon} **{k['name']}**: {k['current']} (Target: {k['target']}) — `{k['trend']}`")

        digest = ExecutiveDigest(
            org_id=state.org_id,
            company_name=state.name,
            overall_health_percent=final_health,
            active_bots_count=state.active_bots_count,
            sleeping_bots_count=state.sleeping_bots_count,
            recovering_bots_count=recovering_count,
            active_projects_count=len(state.projects),
            running_tasks_count=state.running_tasks_count,
            blocked_tasks_count=blocked_tasks,
            critical_incidents_count=critical_incidents,
            warnings_count=warnings,
            human_approvals_required_count=pending_approvals,
            kpi_summary=kpi_summaries,
            explainability=explainability,
            summary_markdown="\n".join(md_lines),
        )
        return digest
