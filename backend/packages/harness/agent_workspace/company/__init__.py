"""Autonomous AI Company & Perpetual Organization OS package."""

from agent_workspace.company.archetypes import (
    ArchetypeDefinition,
    detect_archetype_from_prompt,
    get_open_source_archetype,
    get_research_lab_archetype,
    get_security_soc_archetype,
    synthesize_custom_archetype,
)
from agent_workspace.company.attendance import AttendanceLedgerEngine, AttendanceStatus, BotHeartbeat
from agent_workspace.company.bot_medic import BotMedicEngine, HealingReport
from agent_workspace.company.discovery import ContinuousWorkDiscoveryEngine
from agent_workspace.company.executive import ExecutiveDigest, ExecutiveIntelligenceLayer
from agent_workspace.company.group_chat import GroupChannel, GroupChatEngine, GroupMessage, GroupMessageType
from agent_workspace.company.enterprise_kanban import EnterpriseKanbanAdapter, HermesKanbanAdapter
from agent_workspace.company.swarm_bridge import (
    HermesBotMetadata,
    HermesLocalBridge,
    SwarmBotMetadata,
    SwarmLocalBridge,
)
from agent_workspace.company.kanban import (
    CompanyKanbanEngine,
    KanbanActivityLog,
    KanbanTask,
    TaskPriority,
    TaskStatus,
)
from agent_workspace.company.kpi import KPIEngine
from agent_workspace.company.models import (
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
from agent_workspace.company.organization import (
    AutonomousCompanyEngine,
    get_autonomous_company_engine,
)
from agent_workspace.company.production_line import (
    ProductionLineEngine,
    ProductionLineRun,
    ProductionStage,
    StageArtifact,
)
from agent_workspace.company.responsibility import ResponsibilityEngine
from agent_workspace.company.self_improvement import ContinuousSelfImprovementEngine
from agent_workspace.company.strategy import StrategicPlanningEngine, StrategyReplanReport

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
