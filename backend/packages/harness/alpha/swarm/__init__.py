"""Autonomous Agent Swarm Subsystem for Alpha 2.0.

Provides critical-path-optimized swarm decomposition, dependency DAGs,
hybrid workforce scheduling (Permanent Specialist Bots + Ephemeral Subagents),
background async execution, tri-tier memory, rate-limit governance,
autonomous triggers, and automated succession incident recovery.
"""

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.cnp_auction import (
    ContractAward,
    ContractNetAuctionEngine,
    LeaderCandidate,
    LeaderElection,
    SwarmWorkerAgent,
    elect_leader,
)
from alpha.swarm.communication import (
    SwarmMessage,
    SwarmMessageBus,
    SwarmMessageValidationError,
    SwarmSubscription,
)
from alpha.swarm.consensus import (
    ConsensusPolicy,
    ConsensusResult,
    ConsensusVote,
    VoteStance,
    evaluate_consensus,
)
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
    SwarmBudget,
    SwarmDecision,
    SwarmEvent,
    SwarmMode,
    SwarmPlan,
    SwarmTaskLease,
    SwarmTaskNode,
    TaskNodeState,
    is_terminal_swarm_status,
)
from alpha.swarm.reflection import SwarmReflection, SwarmReflector
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.scheduler import SwarmPlanValidationError, SwarmScheduler
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
    "ContractAward",
    "ContractNetAuctionEngine",
    "ConsensusPolicy",
    "ConsensusResult",
    "ConsensusVote",
    "EphemeralSubagentWorker",
    "HermesBotWorker",
    "LeaderCandidate",
    "LeaderElection",
    "SpecialistBotWorker",
    "SwarmAggregator",
    "SwarmBudget",
    "SwarmCoordinator",
    "SwarmDecision",
    "SwarmEvent",
    "SwarmIncident",
    "SwarmIncidentManager",
    "SwarmMemoryManager",
    "SwarmMessage",
    "SwarmMessageBus",
    "SwarmMessageValidationError",
    "SwarmMode",
    "SwarmPlan",
    "SwarmPlanValidationError",
    "SwarmReflection",
    "SwarmReflector",
    "SwarmResourceGovernor",
    "SwarmScheduler",
    "SwarmSubscription",
    "SwarmTaskDecomposer",
    "SwarmTaskLease",
    "SwarmTaskNode",
    "SwarmWatchdog",
    "SwarmWorkerAgent",
    "SwarmWorkerBackend",
    "TaskNodeState",
    "VoteStance",
    "SwarmBenefitEstimator",
    "elect_leader",
    "evaluate_consensus",
    "get_swarm_coordinator",
    "get_swarm_incident_manager",
    "get_swarm_memory_manager",
    "get_swarm_resource_governor",
    "is_terminal_swarm_status",
]
