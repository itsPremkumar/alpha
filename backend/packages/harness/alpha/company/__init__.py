"""Autonomous AI Company & Perpetual Organization OS package."""

from alpha.company.archetypes import (
    ArchetypeDefinition,
    detect_archetype_from_prompt,
    get_open_source_archetype,
    get_research_lab_archetype,
    get_security_soc_archetype,
    synthesize_custom_archetype,
)
from alpha.company.attendance import AttendanceLedgerEngine, AttendanceStatus, BotHeartbeat
from alpha.company.bot_medic import BotMedicEngine, HealingReport
from alpha.company.discovery import ContinuousWorkDiscoveryEngine
from alpha.company.executive import ExecutiveDigest, ExecutiveIntelligenceLayer
from alpha.company.group_chat import GroupChannel, GroupChatEngine, GroupMessage, GroupMessageType
from alpha.company.enterprise_kanban import EnterpriseKanbanAdapter, HermesKanbanAdapter
from alpha.company.swarm_bridge import (
    HermesBotMetadata,
    HermesLocalBridge,
    SwarmBotMetadata,
    SwarmLocalBridge,
)
from alpha.company.kanban import (
    CompanyKanbanEngine,
    KanbanActivityLog,
    KanbanTask,
    TaskPriority,
    TaskStatus,
)
from alpha.company.kpi import KPIEngine
from alpha.company.models import (
    CompanyCharter,
    CompanyProject,
    CompanyState,
    DepartmentSpec,
    DepartmentType,
    DiscoveredWorkItem,
    EvolutionRecord,
    KPISpec,
    OrgArchetype,
    OrgState,
    ResponsibilityBinding,
    StrategicObjective,
    WorkCategory,
    WorkPriority,
)
from alpha.company.organization import (
    AutonomousCompanyEngine,
    get_autonomous_company_engine,
)
from alpha.company.production_line import (
    ProductionLineEngine,
    ProductionLineRun,
    ProductionStage,
    StageArtifact,
)
from alpha.company.responsibility import ResponsibilityEngine
from alpha.company.self_improvement import ContinuousSelfImprovementEngine
from alpha.company.strategy import StrategicPlanningEngine, StrategyReplanReport

__all__ = [
    "OrgState",
    "OrgArchetype",
    "DepartmentType",
    "WorkCategory",
    "WorkPriority",
    "CompanyCharter",
    "DepartmentSpec",
    "ResponsibilityBinding",
    "StrategicObjective",
    "CompanyProject",
    "DiscoveredWorkItem",
    "KPISpec",
    "EvolutionRecord",
    "CompanyState",
    "ExecutiveDigest",
    "ExecutiveIntelligenceLayer",
    "StrategyReplanReport",
    "StrategicPlanningEngine",
    "ContinuousWorkDiscoveryEngine",
    "KPIEngine",
    "ResponsibilityEngine",
    "ContinuousSelfImprovementEngine",
    "AutonomousCompanyEngine",
    "get_autonomous_company_engine",
    "ArchetypeDefinition",
    "detect_archetype_from_prompt",
    "get_open_source_archetype",
    "get_security_soc_archetype",
    "get_research_lab_archetype",
    "synthesize_custom_archetype",
    "SwarmBotMetadata",
    "SwarmLocalBridge",
    "HermesBotMetadata",
    "HermesLocalBridge",
    "ProductionStage",
    "StageArtifact",
    "ProductionLineRun",
    "ProductionLineEngine",
    "EnterpriseKanbanAdapter",
    "HermesKanbanAdapter",
    "GroupChannel",
    "GroupMessage",
    "GroupMessageType",
    "GroupChatEngine",
    "AttendanceStatus",
    "BotHeartbeat",
    "AttendanceLedgerEngine",
    "HealingReport",
    "BotMedicEngine",
    "KanbanTask",
    "KanbanActivityLog",
    "CompanyKanbanEngine",
    "TaskStatus",
    "TaskPriority",
]
