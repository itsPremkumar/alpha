"""Continuous Strategy and Strategic Pivot Re-planning Engine."""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel, Field

from agent_workspace.company.models import CompanyProject, StrategicObjective

logger = logging.getLogger(__name__)


class StrategyReplanReport(BaseModel):
    directive: str
    impacted_projects_count: int = 0
    cancelled_projects: list[str] = Field(default_factory=list)
    new_objectives: list[StrategicObjective] = Field(default_factory=list)
    new_projects: list[CompanyProject] = Field(default_factory=list)
    realigned_departments: list[str] = Field(default_factory=list)
    rationale: str = ""
    timestamp: float = Field(default_factory=time.time)


class StrategicPlanningEngine:
    """Manages strategic goal review, impact analysis, and autonomous pivoting."""

    @classmethod
    def replan_strategy(
        cls,
        current_projects: list[CompanyProject],
        new_directive: str,
    ) -> StrategyReplanReport:
        """Evaluates a new strategic directive, cancels obsolete projects, and creates new initiatives."""
        dir_lower = new_directive.lower()
        cancelled_projects: list[str] = []
        kept_projects: list[CompanyProject] = []

        # Analyze existing projects against directive
        for p in current_projects:
            # If project is consumer/chat focused and directive is developer/security focused
            if "consumer" in p.name.lower() and ("developer" in dir_lower or "enterprise" in dir_lower):
                cancelled_projects.append(p.name)
            else:
                kept_projects.append(p)

        # Formulate new strategic objectives based on directive keywords
        new_objectives: list[StrategicObjective] = []
        new_projects: list[CompanyProject] = []
        realigned_depts: list[str] = []

        if "security" in dir_lower or "compliance" in dir_lower:
            obj = StrategicObjective(
                title="Attain Enterprise SOC-2 & Zero-Trust Architecture",
                target_kpi="kpi-security-score",
                target_metric_value=99.0,
            )
            proj = CompanyProject(
                name="Project Aegis: Zero-Trust Security Overhaul",
                department="security",
                objective_id=obj.objective_id,
                lead_bot_name="bot-security-lead",
            )
            new_objectives.append(obj)
            new_projects.append(proj)
            realigned_depts.append("security")

        if "developer" in dir_lower or "sdk" in dir_lower or "api" in dir_lower:
            obj = StrategicObjective(
                title="Launch Developer CLI, Python SDK, and Open API Gateway",
                target_kpi="kpi-deployment-freq",
                target_metric_value=12.0,
            )
            proj = CompanyProject(
                name="Project DevCore: High-Throughput Developer SDK",
                department="engineering",
                objective_id=obj.objective_id,
                lead_bot_name="bot-cto",
            )
            new_objectives.append(obj)
            new_projects.append(proj)
            realigned_depts.append("engineering")

        if not new_objectives:
            # Default objective synthesis
            obj = StrategicObjective(
                title=f"Strategic Initiative: {new_directive[:60]}",
                target_kpi="kpi-availability",
                target_metric_value=99.9,
            )
            proj = CompanyProject(
                name=f"Project Nexus: {new_directive[:40]}",
                department="product",
                objective_id=obj.objective_id,
                lead_bot_name="bot-product-manager",
            )
            new_objectives.append(obj)
            new_projects.append(proj)
            realigned_depts.append("product")

        return StrategyReplanReport(
            directive=new_directive,
            impacted_projects_count=len(cancelled_projects) + len(new_projects),
            cancelled_projects=cancelled_projects,
            new_objectives=new_objectives,
            new_projects=new_projects,
            realigned_departments=list(set(realigned_depts)),
            rationale=f"Realigned roadmap and reprioritized initiatives to align with owner directive: '{new_directive}'.",
        )
