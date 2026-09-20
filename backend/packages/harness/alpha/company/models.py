"""Data models, contracts, and enums for Autonomous AI Company & Organization OS."""

from __future__ import annotations

import time
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


class OrgState(StrEnum):
    DRAFT = "draft"
    BOOTSTRAPPING = "bootstrapping"
    ACTIVE = "active"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    PAUSED = "paused"
    STOPPED = "stopped"
    ARCHIVED = "archived"


class OrgArchetype(StrEnum):
    COMPANY = "company"
    OPEN_SOURCE = "open_source"
    SECURITY_SOC = "security_soc"
    RESEARCH_LAB = "research_lab"
    DEVOPS_SRE = "devops_sre"
    CUSTOM = "custom"


class DepartmentType(StrEnum):
    EXECUTIVE = "executive"
    PRODUCT = "product"
    ENGINEERING = "engineering"
    RESEARCH = "research"
    SECURITY = "security"
    SRE = "sre"
    MARKETING = "marketing"
    OPERATIONS = "operations"
    CUSTOMER_SUPPORT = "customer_support"


class WorkCategory(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    MAINTENANCE = "maintenance"
    OPTIMIZATION = "optimization"
    RESEARCH = "research"
    FEATURE = "feature"
    TECHNICAL_DEBT = "technical_debt"
    COST_REDUCTION = "cost_reduction"
    OPPORTUNITY = "opportunity"


class WorkPriority(StrEnum):
    DO_NOW = "do_now"
    QUEUE = "queue"
    MONITOR = "monitor"
    IGNORE = "ignore"


class CompanyCharter(BaseModel):
    org_id: str = Field(default_factory=lambda: f"org-{uuid.uuid4().hex[:8]}")
    name: str = "Autonomous Enterprise"
    archetype: OrgArchetype = OrgArchetype.COMPANY
    mission_statement: str
    vision: str = ""
    duration_years: float = 5.0
    autonomy_level: str = "L4"  # L0 to L5
    owner: str = "human-owner"
    budget_usd: float = 50000.0
    created_at: float = Field(default_factory=time.time)


class DepartmentSpec(BaseModel):
    department_id: str
    name: str
    department_type: DepartmentType
    lead_bot_name: str
    member_bot_names: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)


class ResponsibilityBinding(BaseModel):
    responsibility_id: str = Field(default_factory=lambda: f"resp-{uuid.uuid4().hex[:8]}")
    name: str
    department: str
    primary_bot_name: str
    backup_bot_name: str
    recovery_bot_name: str
    status: str = "active"  # active, failed_over, degraded
    active_bot_name: str = ""
    last_health_check: float = Field(default_factory=time.time)


class StrategicObjective(BaseModel):
    objective_id: str = Field(default_factory=lambda: f"obj-{uuid.uuid4().hex[:8]}")
    title: str
    target_kpi: str = ""
    target_metric_value: float = 0.0
    current_metric_value: float = 0.0
    status: str = "active"  # active, achieved, superseded, cancelled
    deadline_timestamp: float | None = None


class CompanyProject(BaseModel):
    project_id: str = Field(default_factory=lambda: f"proj-{uuid.uuid4().hex[:8]}")
    name: str
    department: str
    objective_id: str | None = None
    lead_bot_name: str
    status: str = "active"  # active, completed, paused, cancelled
    kanban_tasks_count: int = 0
    created_at: float = Field(default_factory=time.time)


class DiscoveredWorkItem(BaseModel):
    item_id: str = Field(default_factory=lambda: f"work-{uuid.uuid4().hex[:8]}")
    title: str
    category: WorkCategory
    description: str
    priority: WorkPriority = WorkPriority.QUEUE
    score: float = 0.0
    target_department: str = "engineering"
    assigned_bot_name: str | None = None
    created_at: float = Field(default_factory=time.time)


class KPISpec(BaseModel):
    kpi_id: str
    name: str
    current_value: float
    target_value: float
    unit: str = "%"
    trend: str = "stable"  # improving, stable, deteriorating
    threshold_critical: float
    last_evaluated: float = Field(default_factory=time.time)


class EvolutionRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"evo-{uuid.uuid4().hex[:8]}")
    cycle_number: int = 1
    timestamp: float = Field(default_factory=time.time)
    insights: list[str] = Field(default_factory=list)
    improved_playbooks: list[str] = Field(default_factory=list)
    calibrated_bots: list[str] = Field(default_factory=list)
    kpi_delta_summary: str = ""


class CompanyState(BaseModel):
    org_id: str
    name: str
    archetype: OrgArchetype = OrgArchetype.COMPANY
    state: OrgState = OrgState.BOOTSTRAPPING
    charter: CompanyCharter
    departments: list[DepartmentSpec] = Field(default_factory=list)
    responsibilities: list[ResponsibilityBinding] = Field(default_factory=list)
    objectives: list[StrategicObjective] = Field(default_factory=list)
    projects: list[CompanyProject] = Field(default_factory=list)
    kpis: list[KPISpec] = Field(default_factory=list)
    active_bots_count: int = 0
    sleeping_bots_count: int = 0
    running_tasks_count: int = 0
    overall_health_percent: float = 100.0
    evolution_journal: list[EvolutionRecord] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)
