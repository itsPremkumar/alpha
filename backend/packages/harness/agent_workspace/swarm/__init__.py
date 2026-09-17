"""Autonomous Agent Swarm Subsystem for DeerFlow 2.0.

Provides critical-path-optimized swarm decomposition, dependency DAGs,
hybrid workforce scheduling (Permanent Specialist Bots + Ephemeral Subagents),
background async execution, tri-tier memory, rate-limit governance,
autonomous triggers, and automated succession incident recovery.
"""

from agent_workspace.swarm.aggregator import SwarmAggregator
from agent_workspace.swarm.coordinator import SwarmCoordinator, get_swarm_coordinator
from agent_workspace.swarm.decomposer import SwarmTaskDecomposer
from agent_workspace.swarm.estimator import SwarmBenefitEstimator
from agent_workspace.swarm.governor import SwarmResourceGovernor, get_swarm_resource_governor
from agent_workspace.swarm.incidents import (
    SwarmIncident,
    SwarmIncidentManager,
    get_swarm_incident_manager,
)
from agent_workspace.swarm.memory import SwarmMemoryManager, get_swarm_memory_manager
from agent_workspace.swarm.models import (
    SwarmDecision,
    SwarmEvent,
    SwarmMode,
    SwarmPlan,
    SwarmTaskNode,
    TaskNodeState,
)
from agent_workspace.swarm.runner import AsyncSwarmRunner
from agent_workspace.swarm.scheduler import SwarmScheduler
from agent_workspace.swarm.triggers import AutonomousWorkTrigger
from agent_workspace.swarm.watchdog import SwarmWatchdog
from agent_workspace.swarm.worker import (
    CodingWorktreeWorker,
    EphemeralSubagentWorker,
    HermesBotWorker,
    SpecialistBotWorker,
    SwarmWorkerBackend,
)

__all__ = [
    "AsyncSwarmRunner",
    "AutonomousWorkTrigger",
    "CodingWorktreeWorker",
    "EphemeralSubagentWorker",
    "HermesBotWorker",
    "SpecialistBotWorker",
    "SwarmAggregator",
    "SwarmCoordinator",
    "SwarmDecision",
    "SwarmEvent",
    "SwarmIncident",
    "SwarmIncidentManager",
    "SwarmMemoryManager",
    "SwarmMode",
    "SwarmPlan",
    "SwarmResourceGovernor",
    "SwarmScheduler",
    "SwarmTaskDecomposer",
    "SwarmTaskNode",
    "SwarmWatchdog",
    "SwarmWorkerBackend",
    "TaskNodeState",
    "SwarmBenefitEstimator",
    "get_swarm_coordinator",
    "get_swarm_incident_manager",
    "get_swarm_memory_manager",
    "get_swarm_resource_governor",
]
