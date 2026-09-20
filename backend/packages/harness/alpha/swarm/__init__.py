"""Autonomous Agent Swarm Subsystem for Alpha 2.0.

Provides critical-path-optimized swarm decomposition, dependency DAGs,
hybrid workforce scheduling (Permanent Specialist Bots + Ephemeral Subagents),
background async execution, tri-tier memory, rate-limit governance,
autonomous triggers, and automated succession incident recovery.
"""

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.coordinator import SwarmCoordinator, get_swarm_coordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.estimator import SwarmBenefitEstimator
from alpha.swarm.governor import SwarmResourceGovernor, get_swarm_resource_governor
from alpha.swarm.incidents import (
    SwarmIncident,
    SwarmIncidentManager,
    get_swarm_incident_manager,
)
from alpha.swarm.memory import SwarmMemoryManager, get_swarm_memory_manager
from alpha.swarm.models import (
    SwarmDecision,
    SwarmEvent,
    SwarmMode,
    SwarmPlan,
    SwarmTaskNode,
    TaskNodeState,
)
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.scheduler import SwarmScheduler
from alpha.swarm.triggers import AutonomousWorkTrigger
from alpha.swarm.watchdog import SwarmWatchdog
from alpha.swarm.worker import (
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
